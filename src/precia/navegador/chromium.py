"""Descargador con Chromium (Playwright) para las tiendas que montan la página con JavaScript.

La página es código ajeno ejecutándose en nuestra máquina, así que no puede usar el navegador para
un SSRF:

- `route("**/*")` intercepta **todas** las peticiones del contexto (también iframes y ventanas
  emergentes) y aborta las que no pasan `validar_url`, además de imágenes, fuentes y medios.
- Las redirecciones nunca las sigue Chromium: si se le entrega un 302 con `fulfill`, sigue el
  `Location` con su propia pila de red **sin volver a pasar por `route`** (comprobado en los tests:
  en una cadena de dos saltos el segundo llegaba al servidor sin validar). Por eso cada petición
  se hace con `route.fetch(max_redirects=0)`:
  - si redirige la navegación principal, se aborta y `descargar` navega de nuevo al destino ya
    validado, así cada salto vuelve a pasar por el filtro y la URL de la página es la correcta;
  - si redirige un subrecurso (script, XHR, CSS, iframe), el filtro sigue la cadena él mismo
    validando cada salto y entrega solo la respuesta final.
- Los service workers se bloquean (sus peticiones no pasarían por `route`) y los WebSocket se
  cierran sin conectar.
- Un contexto nuevo por descarga: sin cookies ni almacenamiento entre páginas.

Con un `Proxy` (`navegador.proxy`), Chromium arranca contra un relé local sin credenciales
(`navegador.rele`), que reenvía cada túnel al proxy añadiendo `Proxy-Authorization`: a Playwright
nunca se le dan el usuario ni la contraseña, porque los usaría para contestar a cualquier `401
Basic` de un destino. Todo lo que pide la página, también `route.fetch`, sale por el relé. El
User-Agent sigue siendo el honesto y no hay ningún añadido para disimular que es un navegador
automático.

Si la tienda corta la conexión de la navegación principal sin responder (un `ECONNRESET`, como
algunos WAF con las IP de centros de datos), se lanza `ConexionCortada`: con proxy, cuenta como
bloqueo (`navegador.con_proxy`).
"""

import asyncio
import contextlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urljoin

from playwright.async_api import (
    Browser,
    Playwright,
    WebSocketRoute,
    async_playwright,
)
from playwright.async_api import Error as ErrorPlaywright
from playwright.async_api import TimeoutError as TiempoPlaywright

from precia.navegador.http import (
    MAXIMO_BYTES,
    MAXIMO_REDIRECCIONES,
    REDIRECCIONES,
    TIPOS_ADMITIDOS,
)
from precia.navegador.proxy import Proxy
from precia.navegador.rele import ReleProxy
from precia.navegador.tipos import ConexionCortada, ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import Validador, validar_url

RECURSOS_BLOQUEADOS = frozenset({"image", "font", "media"})
ESPERA_RED_S = 2.0
_CORTE = re.compile(
    r"ECONNRESET|ECONNABORTED|socket hang up|ERR_CONNECTION_(?:RESET|CLOSED|ABORTED)"
    r"|ERR_EMPTY_RESPONSE"
)
"""Cómo cuenta Playwright que la tienda cortó la conexión: el error de Node en `route.fetch`
(«read ECONNRESET», «socket hang up») o el de red de Chromium («net::ERR_CONNECTION_RESET»)."""


class _Marco(Protocol):
    @property
    def parent_frame(self) -> Any: ...


class _Peticion(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def resource_type(self) -> str: ...

    @property
    def frame(self) -> _Marco: ...

    def is_navigation_request(self) -> bool: ...


class _Respuesta(Protocol):
    @property
    def status(self) -> int: ...

    @property
    def headers(self) -> dict[str, str]: ...


class _Ruta(Protocol):
    """Lo que el filtro usa de `playwright.async_api.Route` (así se puede probar sin navegador)."""

    @property
    def request(self) -> _Peticion: ...

    async def abort(self, error_code: str | None = None) -> None: ...

    async def fetch(
        self, *, url: str | None = None, max_redirects: int | None = None
    ) -> _Respuesta: ...

    async def fulfill(self, *, response: Any) -> None: ...


class FiltroPeticiones:
    """Manejador de `route`: decide cada petición que hace la página."""

    def __init__(self, validador: Validador) -> None:
        self._validador = validador
        self.bloqueo_principal: URLNoPermitida | None = None
        """Por qué se abortó la navegación principal (para dar el error correcto)."""
        self.redireccion: str | None = None
        """Destino ya validado de una redirección de la navegación principal."""
        self.fallo_principal: str | None = None
        """Error de red de la navegación principal (para reconocer un corte de la conexión)."""

    async def __call__(self, ruta: _Ruta) -> None:
        peticion = ruta.request
        principal = _es_navegacion_principal(peticion)
        if peticion.resource_type in RECURSOS_BLOQUEADOS:
            await ruta.abort("blockedbyclient")
            return
        if await self._validar(peticion.url, principal) is None:
            await ruta.abort("blockedbyclient")
            return
        actual = peticion.url
        respuesta = await self._pedir(ruta, None, principal)
        for salto in range(MAXIMO_REDIRECCIONES + 1):
            if respuesta is None:
                break
            destino = respuesta.headers.get("location")
            if respuesta.status not in REDIRECCIONES or not destino:
                await ruta.fulfill(response=respuesta)
                return
            if salto == MAXIMO_REDIRECCIONES:
                break
            siguiente = await self._validar(urljoin(actual, destino), principal)
            if siguiente is None:
                await ruta.abort("blockedbyclient")
                return
            if principal:
                self.redireccion = siguiente
                await ruta.abort("aborted")
                return
            actual = siguiente
            respuesta = await self._pedir(ruta, actual, principal)
        await ruta.abort("failed")  # error de red o demasiadas redirecciones

    async def _pedir(self, ruta: _Ruta, url: str | None, principal: bool) -> _Respuesta | None:
        try:
            return await ruta.fetch(url=url, max_redirects=0)
        except ErrorPlaywright as error:
            if principal:
                self.fallo_principal = error.message
            return None

    async def _validar(self, url: str, principal: bool) -> str | None:
        try:
            return await self._validador(url)
        except URLNoPermitida as error:
            if principal:
                self.bloqueo_principal = error
            return None


def _es_corte(mensaje: str | None) -> bool:
    return mensaje is not None and _CORTE.search(mensaje) is not None


def _es_navegacion_principal(peticion: _Peticion) -> bool:
    return peticion.is_navigation_request() and peticion.frame.parent_frame is None


async def _rechazar_websocket(websocket: WebSocketRoute) -> None:
    # Sin `connect_to_server()` el WebSocket nunca llega a abrirse contra ningún servidor.
    await websocket.close()


class DescargadorChromium:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 30.0,
        max_bytes: int = MAXIMO_BYTES,
        validador: Validador = validar_url,
        espera_red_s: float = ESPERA_RED_S,
        proxy: Proxy | None = None,
        crear_rele: Callable[[Proxy], ReleProxy] = ReleProxy,
    ) -> None:
        """`crear_rele` solo se cambia en los tests (el proxy falso está en 127.0.0.1)."""
        self._user_agent = user_agent
        self._proxy = proxy
        self._crear_rele = crear_rele
        self._rele: ReleProxy | None = None
        self._timeout_ms = timeout * 1000
        self._max_bytes = max_bytes
        self._validador = validador
        self._espera_red_ms = espera_red_s * 1000
        self._playwright: Playwright | None = None
        self._navegador: Browser | None = None
        self._candado = asyncio.Lock()

    @property
    def proxy(self) -> Proxy | None:
        return self._proxy

    async def descargar(self, url: str) -> Pagina:
        url = await self._validador(url)
        navegador = await self._abrir()
        filtro = FiltroPeticiones(self._validador)
        contexto = await navegador.new_context(
            user_agent=self._user_agent,
            locale="es-ES",
            accept_downloads=False,
            service_workers="block",
        )
        try:
            contexto.set_default_timeout(self._timeout_ms)
            await contexto.route("**/*", filtro)
            await contexto.route_web_socket(lambda _: True, _rechazar_websocket)
            pagina = await contexto.new_page()
            actual = url
            for _ in range(MAXIMO_REDIRECCIONES + 1):
                filtro.redireccion = None
                try:
                    respuesta = await pagina.goto(actual, wait_until="domcontentloaded")
                    break
                except ErrorPlaywright as error:
                    if filtro.bloqueo_principal is not None:
                        raise filtro.bloqueo_principal from error
                    if filtro.redireccion is None:
                        if _es_corte(filtro.fallo_principal) or _es_corte(error.message):
                            raise ConexionCortada("La tienda cortó la conexión.") from error
                        raise ErrorDescarga("Chromium no pudo cargar la página.") from error
                    actual = filtro.redireccion
            else:
                raise ErrorDescarga("La tienda encadena demasiadas redirecciones.")
            if respuesta is None:
                raise ErrorDescarga("Chromium no recibió respuesta de la tienda.")
            # Analítica o chats que nunca dejan la red quieta: vale con lo ya cargado.
            with contextlib.suppress(TiempoPlaywright):
                await pagina.wait_for_load_state("networkidle", timeout=self._espera_red_ms)
            try:
                # `content()` no tiene timeout: se ejecuta en el hilo de la página y, si su
                # JavaScript se queda en un bucle infinito, no volvería nunca.
                html = await asyncio.wait_for(pagina.content(), self._timeout_ms / 1000)
            except TimeoutError as error:
                raise ErrorDescarga("La página no responde (se agotó el tiempo).") from error
            except ErrorPlaywright as error:
                raise ErrorDescarga("Chromium no pudo leer la página.") from error
            url_final = await self._validador(pagina.url)
            estado = respuesta.status
            tipo = respuesta.headers.get("content-type", "").split(";")[0].strip().lower()
        finally:
            await contexto.close()

        if tipo not in TIPOS_ADMITIDOS:
            raise ErrorDescarga("La respuesta no es una página web.")
        if len(html.encode("utf-8")) > self._max_bytes:
            raise ErrorDescarga("La página es demasiado grande.")
        return Pagina(
            url=url,
            url_final=url_final,
            estado=estado,
            html=html,
            tipo_contenido=tipo,
            obtenida=datetime.now(UTC),
        )

    async def cerrar(self) -> None:
        async with self._candado:
            if self._navegador is not None:
                await self._navegador.close()
                self._navegador = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            await self._cerrar_rele()

    async def _cerrar_rele(self) -> None:
        rele, self._rele = self._rele, None
        if rele is not None:
            await rele.cerrar()

    async def _abrir(self) -> Browser:
        async with self._candado:
            if self._navegador is None:
                self._playwright = await async_playwright().start()
                try:
                    if self._proxy is None:
                        self._navegador = await self._playwright.chromium.launch(headless=True)
                    else:
                        self._rele = self._crear_rele(self._proxy)
                        servidor = await self._rele.abrir()
                        # Solo la dirección del relé: ni usuario ni contraseña (ver arriba).
                        self._navegador = await self._playwright.chromium.launch(
                            headless=True, proxy={"server": servidor}
                        )
                except (ErrorPlaywright, OSError) as error:
                    await self._playwright.stop()
                    self._playwright = None
                    await self._cerrar_rele()
                    if isinstance(error, OSError):
                        raise ErrorDescarga("No se pudo abrir el relé local del proxy.") from error
                    primera_linea = error.message.splitlines()[0] if error.message else ""
                    raise ErrorDescarga(f"No se pudo arrancar Chromium: {primera_linea}") from error
            return self._navegador
