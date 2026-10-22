"""Descargador con Chromium (Playwright): red propia de Chrome detrás del relé SOCKS5 (ADR-023).

Chromium hace sus peticiones con su propia pila de red (HTTP/2 y TLS de Chrome, ADR-022), sin
pasar por `route.fetch` de Node. La página es código ajeno ejecutándose en nuestra máquina, así que
no puede usar el navegador para un SSRF:

- **Relé SOCKS5 siempre activo** (`navegador.rele`): Chromium arranca contra
  `socks5://127.0.0.1:N`, también sin proxy, y el relé valida **cada conexión TCP** (navegación,
  saltos de redirección, subrecursos, iframes, workers) con el mismo `Validador` que este
  descargador. Es la única salida de red: `route` no ve los saltos de una redirección.
  `bypass: "<-loopback>"` obliga a que 127.0.0.1, `localhost` y `[::1]` pasen también por el relé
  (que los rechaza), aunque alguien defina `PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK`.
  Playwright añade `--host-resolver-rules="MAP * ~NOTFOUND"`: Chromium nunca resuelve nombres.
- **Headless shell** (`launch` sin `channel`). Chrome for Testing queda descartado: hace DoH
  automático a `dns.google` saltándose el relé y no admite `--force-webrtc-ip-handling-policy`.
  Con `ARGUMENTOS`, WebRTC no manda UDP fuera del proxy y no hay QUIC. Lo comprueba un test con el
  netlog de Chromium (ninguna conexión fuera de 127.0.0.1).
- `route("**/*")` es la primera criba, por URL: aborta lo que no pasa el validador y las imágenes,
  fuentes y medios; el resto sigue con `continue_` por la red de Chromium. Los service workers se
  bloquean, los WebSocket se cierran sin conectar y cada descarga usa un contexto nuevo (sin
  cookies ni almacenamiento entre páginas).
- Si el relé rechaza un salto de la navegación (`ERR_SOCKS_CONNECTION_FAILED`), se vuelve a validar
  la última URL de la navegación: si estaba prohibida, `URLNoPermitida`. Las redirecciones las
  sigue Chromium (cada salto validado por el relé) y después se comprueba que no pasen de
  `MAXIMO_REDIRECCIONES`.

Huella: el User-Agent y los `userAgentMetadata` (client hints) son los del propio binario sin la
marca «HeadlessChrome», con la versión reducida como el Chrome real (`Chrome/156.0.0.0`). Se leen
una vez al arrancar, de una página servida con `route.fulfill` (sin red), y se aplican por CDP
(`Emulation.setUserAgentOverride`) en cada página antes de navegar. `new_context(user_agent=...)`
no sirve: deja «HeadlessChrome» en las marcas.

Con un `Proxy`, el relé encadena cada conexión al proxy residencial y es el único que conoce las
credenciales: Playwright las usaría para contestar a cualquier `401 Basic` de un destino.

Si la tienda corta la conexión de la navegación principal sin responder (un `ECONNRESET`, como
algunos WAF con las IP de centros de datos), se lanza `ConexionCortada`: con proxy, cuenta como
bloqueo (`navegador.con_proxy`).
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from playwright.async_api import (
    Browser,
    Playwright,
    Request,
    Route,
    WebSocketRoute,
    async_playwright,
)
from playwright.async_api import Error as ErrorPlaywright
from playwright.async_api import TimeoutError as TiempoPlaywright

from precia.navegador.http import MAXIMO_BYTES, MAXIMO_REDIRECCIONES, TIPOS_ADMITIDOS
from precia.navegador.proxy import Proxy
from precia.navegador.rele import ReleProxy
from precia.navegador.tipos import ConexionCortada, ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import Validador, validar_url

registro = logging.getLogger(__name__)

RECURSOS_BLOQUEADOS = frozenset({"image", "font", "media"})
ESPERA_RED_S = 2.0
ARGUMENTOS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-quic")
"""Los dos existen en el headless shell. Nunca `--disable-features`: pisa la lista de Playwright."""
IDIOMA = "es-ES"
URL_METADATOS = "https://metadatos.invalid/"
"""Página que se sirve con `route.fulfill` para leer la huella: https (contexto seguro), sin red."""
VALORES_HUELLA = (
    "architecture",
    "bitness",
    "fullVersionList",
    "model",
    "platformVersion",
    "uaFullVersion",
    "wow64",
)
_SOCKS_RECHAZADO = "ERR_SOCKS_CONNECTION_FAILED"
_CORTE = re.compile(
    r"ECONNRESET|ECONNABORTED|socket hang up|ERR_CONNECTION_(?:RESET|CLOSED|ABORTED)"
    r"|ERR_EMPTY_RESPONSE"
)
"""Cómo cuenta Chromium que la tienda cortó la conexión («net::ERR_CONNECTION_RESET»). Cuando el
corte llega por el relé (la tienda cierra con RST y el relé cierra el túnel), Chromium ve una
respuesta vacía («net::ERR_EMPTY_RESPONSE»). Los mensajes de Node quedan de cuando las peticiones
se hacían con `route.fetch`."""
_HEADLESS = re.compile(r"HeadlessChrome/(\d+)[\d.]*")


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


class _Ruta(Protocol):
    """Lo que el filtro usa de `playwright.async_api.Route` (así se puede probar sin navegador)."""

    @property
    def request(self) -> _Peticion: ...

    async def abort(self, error_code: str | None = None) -> None: ...

    async def continue_(self) -> None: ...


class FiltroPeticiones:
    """Manejador de `route`: primera criba por URL de cada petición que ve Playwright."""

    def __init__(self, validador: Validador) -> None:
        self._validador = validador
        self.bloqueo_principal: URLNoPermitida | None = None
        """Por qué se abortó la navegación principal (para dar el error correcto)."""

    async def __call__(self, ruta: _Ruta) -> None:
        peticion = ruta.request
        if peticion.resource_type in RECURSOS_BLOQUEADOS:
            await ruta.abort("blockedbyclient")
            return
        try:
            await self._validador(peticion.url)
        except URLNoPermitida as error:
            if _es_navegacion_principal(peticion):
                self.bloqueo_principal = error
            await ruta.abort("blockedbyclient")
            return
        # Por la red de Chromium, que solo sale por el relé (que vuelve a validar la conexión).
        await ruta.continue_()


def ua_de_chrome(ua: str) -> str:
    """El UA del headless shell como el de Chrome: sin «Headless» y con la versión reducida."""
    return _HEADLESS.sub(r"Chrome/\1.0.0.0", ua)


def metadatos_de_chrome(valores: Mapping[str, Any]) -> dict[str, Any]:
    """`userAgentMetadata` de CDP desde `getHighEntropyValues`, sin la marca HeadlessChrome."""

    def marcas(clave: str) -> list[dict[str, str]]:
        return [
            {"brand": str(marca["brand"]), "version": str(marca["version"])}
            for marca in valores.get(clave, [])
            if "HeadlessChrome" not in str(marca.get("brand", ""))
        ]

    metadatos: dict[str, Any] = {
        "brands": marcas("brands"),
        "fullVersionList": marcas("fullVersionList"),
        "platform": str(valores.get("platform", "")),
        "platformVersion": str(valores.get("platformVersion", "")),
        "architecture": str(valores.get("architecture", "")),
        "model": str(valores.get("model", "")),
        "mobile": bool(valores.get("mobile", False)),
        "bitness": str(valores.get("bitness", "")),
        "wow64": bool(valores.get("wow64", False)),
    }
    if "uaFullVersion" in valores:
        metadatos["fullVersion"] = str(valores["uaFullVersion"])
    return metadatos


def _es_corte(mensaje: str | None) -> bool:
    return mensaje is not None and _CORTE.search(mensaje) is not None


def _es_navegacion_principal(peticion: _Peticion) -> bool:
    return peticion.is_navigation_request() and peticion.frame.parent_frame is None


def _saltos(peticion: Request) -> int:
    """Redirecciones que llevaron a `peticion` (se deja de contar pasado el máximo)."""
    saltos = 0
    anterior = peticion.redirected_from
    while anterior is not None and saltos <= MAXIMO_REDIRECCIONES:
        saltos += 1
        anterior = anterior.redirected_from
    return saltos


async def _rechazar_websocket(websocket: WebSocketRoute) -> None:
    # Sin `connect_to_server()` el WebSocket nunca llega a abrirse contra ningún servidor.
    await websocket.close()


async def _servir_metadatos(ruta: Route) -> None:
    if ruta.request.url == URL_METADATOS:
        await ruta.fulfill(status=200, content_type="text/html", body="<!doctype html><title>")
    else:
        await ruta.abort("blockedbyclient")


class DescargadorChromium:
    def __init__(
        self,
        user_agent: str | None = None,
        *,
        timeout: float = 30.0,
        max_bytes: int = MAXIMO_BYTES,
        validador: Validador = validar_url,
        espera_red_s: float = ESPERA_RED_S,
        proxy: Proxy | None = None,
        crear_rele: Callable[[Proxy | None], ReleProxy] | None = None,
    ) -> None:
        """Sin `user_agent`, el de Chrome sacado del propio binario (ver arriba).

        `crear_rele` solo se cambia en los tests; el relé por defecto usa este mismo `validador`.
        """
        self._user_agent = user_agent
        self._proxy = proxy
        self._crear_rele: Callable[[Proxy | None], ReleProxy] = crear_rele or (
            lambda p: ReleProxy(p, validador=self._validador)
        )
        self._rele: ReleProxy | None = None
        self._timeout_ms = timeout * 1000
        self._max_bytes = max_bytes
        self._validador = validador
        self._espera_red_ms = espera_red_s * 1000
        self._playwright: Playwright | None = None
        self._navegador: Browser | None = None
        self._huella: dict[str, Any] = {}
        self._candado = asyncio.Lock()

    @property
    def proxy(self) -> Proxy | None:
        return self._proxy

    async def descargar(self, url: str) -> Pagina:
        url = await self._validador(url)
        navegador = await self._abrir()
        try:
            return await self._descargar(navegador, url)
        except ErrorPlaywright as error:
            # Lo que no se tradujo antes (crear el contexto, la página o la sesión CDP): casi
            # siempre, que Chromium ha muerto. `_abrir` lo relanzará en la próxima descarga.
            raise ErrorDescarga("Chromium no pudo preparar la página.") from error

    async def _descargar(self, navegador: Browser, url: str) -> Pagina:
        filtro = FiltroPeticiones(self._validador)
        contexto = await navegador.new_context(
            locale=IDIOMA, accept_downloads=False, service_workers="block"
        )
        try:
            contexto.set_default_timeout(self._timeout_ms)
            await contexto.route("**/*", filtro)
            await contexto.route_web_socket(lambda _: True, _rechazar_websocket)
            pagina = await contexto.new_page()
            # La sesión CDP se queda abierta con la página: al soltarla se perdería la huella.
            cdp = await contexto.new_cdp_session(pagina)
            await cdp.send("Emulation.setUserAgentOverride", self._huella)
            navegaciones: list[str] = []

            def apuntar(peticion: Request) -> None:
                if _es_navegacion_principal(peticion):
                    navegaciones.append(peticion.url)

            pagina.on("request", apuntar)
            try:
                respuesta = await pagina.goto(url, wait_until="domcontentloaded")
            except ErrorPlaywright as error:
                ultima = navegaciones[-1] if navegaciones else url
                raise await self._error_de_navegacion(error, filtro, ultima) from error
            if respuesta is None:
                raise ErrorDescarga("Chromium no recibió respuesta de la tienda.")
            if _saltos(respuesta.request) > MAXIMO_REDIRECCIONES:
                raise ErrorDescarga("La tienda encadena demasiadas redirecciones.")
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
            # Si Chromium murió, cerrar el contexto también falla: que no tape el error de arriba.
            with contextlib.suppress(ErrorPlaywright):
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

    async def _error_de_navegacion(
        self, error: ErrorPlaywright, filtro: FiltroPeticiones, ultima: str
    ) -> ErrorDescarga | URLNoPermitida:
        if filtro.bloqueo_principal is not None:
            return filtro.bloqueo_principal
        if _SOCKS_RECHAZADO in error.message:
            # El relé no abrió la conexión: o el salto estaba prohibido, o la tienda no contesta.
            try:
                await self._validador(ultima)
            except URLNoPermitida as rechazo:
                return rechazo
            return ErrorDescarga("Chromium no pudo conectar con la tienda.")
        if _es_corte(error.message):
            return ConexionCortada("La tienda cortó la conexión.")
        return ErrorDescarga("Chromium no pudo cargar la página.")

    async def cerrar(self) -> None:
        async with self._candado:
            if self._navegador is not None:
                await self._navegador.close()
                self._navegador = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            await self._cerrar_rele()

    async def _soltar(self) -> None:
        """Como `cerrar` (con el candado ya tomado), sin fallar si Chromium ya no está."""
        navegador, self._navegador = self._navegador, None
        playwright, self._playwright = self._playwright, None
        if navegador is not None:
            with contextlib.suppress(ErrorPlaywright):
                await navegador.close()
        if playwright is not None:
            with contextlib.suppress(ErrorPlaywright):
                await playwright.stop()
        await self._cerrar_rele()

    async def _cerrar_rele(self) -> None:
        rele, self._rele = self._rele, None
        if rele is not None:
            await rele.cerrar()

    async def _abrir(self) -> Browser:
        async with self._candado:
            if self._navegador is not None and not self._navegador.is_connected():
                # El proceso murió (falta de memoria, un fallo): se suelta todo y se relanza.
                registro.warning("Chromium se cerró solo: se vuelve a arrancar.")
                await self._soltar()
            if self._navegador is None:
                playwright = self._playwright = await async_playwright().start()
                try:
                    self._rele = self._crear_rele(self._proxy)
                    servidor = await self._rele.abrir()
                    # Solo la dirección del relé: ni usuario ni contraseña (ver arriba).
                    navegador = await playwright.chromium.launch(
                        headless=True,
                        proxy={"server": servidor, "bypass": "<-loopback>"},
                        args=list(ARGUMENTOS),
                    )
                    try:
                        self._huella = await self._leer_huella(navegador)
                    except BaseException:
                        with contextlib.suppress(ErrorPlaywright):
                            await navegador.close()
                        raise
                    self._navegador = navegador
                except BaseException as error:
                    await playwright.stop()
                    self._playwright = None
                    await self._cerrar_rele()
                    if isinstance(error, OSError):
                        raise ErrorDescarga(
                            "No se pudo abrir el relé local de Chromium."
                        ) from error
                    if isinstance(error, ErrorPlaywright):
                        primera = error.message.splitlines()[0] if error.message else ""
                        raise ErrorDescarga(f"No se pudo arrancar Chromium: {primera}") from error
                    raise
            return self._navegador

    async def _leer_huella(self, navegador: Browser) -> dict[str, Any]:
        """UA y client hints del binario sin «HeadlessChrome», para `setUserAgentOverride`."""
        cdp = await navegador.new_browser_cdp_session()
        try:
            version = await cdp.send("Browser.getVersion")
        finally:
            await cdp.detach()
        contexto = await navegador.new_context(accept_downloads=False, service_workers="block")
        try:
            await contexto.route("**/*", _servir_metadatos)
            pagina = await contexto.new_page()
            await pagina.goto(URL_METADATOS)
            valores = await pagina.evaluate(
                "(claves) => navigator.userAgentData.getHighEntropyValues(claves)",
                list(VALORES_HUELLA),
            )
        finally:
            await contexto.close()
        return {
            "userAgent": self._user_agent or ua_de_chrome(str(version["userAgent"])),
            "acceptLanguage": IDIOMA,
            "userAgentMetadata": metadatos_de_chrome(valores),
        }
