"""Descargador que manda por el proxy las tiendas que bloquean la conexión directa.

Envuelve dos descargadores iguales (HTTP con HTTP, Chromium con Chromium): uno directo y otro que
sale por el proxy (`navegador.proxy`). Según el modo:

- `siempre`: todo por el proxy. `nunca`: todo directo.
- `bloqueo` (por defecto): directo. Si la tienda bloquea la conexión, la tienda queda marcada y
  la petición se repite **una vez** por el proxy, tras la espera de cortesía; lo siguiente de esa
  tienda ya va siempre por el proxy. Lo que devuelva el proxy se entrega tal cual: si también es
  un bloqueo, `Navegador` lo detecta y lanza `Bloqueado` como siempre. Nunca hay más de una
  repetición por petición, ni se resuelve un CAPTCHA o un desafío: solo cambia la IP de salida.

La espera de cortesía es la de dos peticiones seguidas a la tienda: dentro del turno del
`LimitadorDominio` (lo normal, desde `Navegador`), el mayor del retardo y el Crawl-delay, con la
plaza global suelta mientras tanto (`cortesia.pausa_de_cortesia`); fuera de turno (el robots.txt),
`espera_reintento_s`.

Cuenta como bloqueo de la conexión directa:

- una página que `detectar_bloqueo` reconoce (desafío o rechazo de un WAF, CAPTCHA), salvo el
  límite de peticiones (un 429 que no es de un anti-bot, `MOTIVO_LIMITE`): la tienda pide bajar
  el ritmo, y repetir desde otra IP sería saltarse su límite. Se devuelve tal cual y `Navegador`
  lanza `Bloqueado`. Un 429 con la firma de un anti-bot (Kasada lo usa para su desafío) sí cuenta;
- una conexión que la tienda corta: así rechazan algunos WAF a las IP de centros de datos, sin
  página de bloqueo que reconocer. En HTTP, un `ConnectionResetError` o `ConnectionAbortedError`
  en la causa del error; en Chromium, `ConexionCortada` (el motivo que da Playwright).

No cuentan los tiempos agotados (tampoco el corte que hace el propio transporte cuando se pasa de
tiempo: se lanza sin el error del socket), las conexiones rechazadas ni los fallos de DNS: suelen
ser una tienda lenta o caída, y repetirlos por el proxy (que se paga por MB) no arregla nada.

Las tiendas marcadas, las peticiones y los bytes por el proxy se guardan en un `UsoProxy` que
comparten los envoltorios de HTTP y de Chromium. Los bytes son los del HTML o los datos entregados:
una estimación por lo bajo del gasto (no cuenta cabeceras, TLS ni, con Chromium, los scripts y
estilos que pide la página).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from precia.navegador.bloqueo import MOTIVO_LIMITE, detectar_bloqueo
from precia.navegador.cortesia import pausa_de_cortesia
from precia.navegador.http import MAXIMO_BYTES
from precia.navegador.proxy import ModoProxy
from precia.navegador.tipos import (
    ConexionCortada,
    Descargador,
    DescargadorGrande,
    ErrorDescarga,
    Pagina,
)

registro = logging.getLogger(__name__)

Detector = Callable[[Pagina], str | None]
_CORTES = (ConnectionResetError, ConnectionAbortedError)
_MAX_CADENA = 10
"""Excepciones encadenadas que se miran como mucho al buscar el corte de la conexión."""


@dataclass(slots=True)
class UsoProxy:
    """Uso del proxy en esta ejecución (en memoria: al reiniciar empieza de cero)."""

    peticiones: int = 0
    bytes: int = 0
    dominios: set[str] = field(default_factory=set)
    """Tiendas (dominio sin «www.») que van por el proxy porque bloquearon la conexión directa."""

    @property
    def mb(self) -> float:
        return self.bytes / (1024 * 1024)


def dominio_de(url: str) -> str:
    """El dominio con el que se marca una tienda: en minúsculas y sin «www.»."""
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def _conexion_cortada(error: BaseException) -> bool:
    """Si en la cadena del error hay un corte de la conexión por parte de la tienda.

    Se sigue la cadena como la enseña un traceback: la causa (`from`) o, si no se ha suprimido
    (`from None`), el contexto.
    """
    actual: BaseException | None = error
    for _ in range(_MAX_CADENA):
        if actual is None:
            return False
        if isinstance(actual, (ConexionCortada, *_CORTES)):
            return True
        # `urllib.error.URLError` guarda el error de red en `reason`, no siempre en la cadena.
        motivo = getattr(actual, "reason", None)
        if isinstance(motivo, _CORTES):
            return True
        if actual.__cause__ is not None:
            actual = actual.__cause__
        elif actual.__suppress_context__:
            return False
        else:
            actual = actual.__context__
    return False


class DescargadorConProxy:
    """Implementa `Descargador` y, si los dos internos la tienen, la descarga grande."""

    def __init__(
        self,
        directo: Descargador,
        por_proxy: Descargador,
        *,
        modo: ModoProxy = ModoProxy.BLOQUEO,
        detectar: Detector = detectar_bloqueo,
        uso: UsoProxy | None = None,
        espera_reintento_s: float = 0.0,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`espera_reintento_s`: la espera entre el intento directo y el del proxy cuando no hay
        un turno del `LimitadorDominio` abierto (dentro de uno manda el del limitador)."""
        self._directo = directo
        self._proxy = por_proxy
        self._modo = modo
        self._detectar = detectar
        self._uso = uso if uso is not None else UsoProxy()
        self._espera = espera_reintento_s
        self._dormir = dormir

    @property
    def modo(self) -> ModoProxy:
        return self._modo

    @property
    def directo(self) -> Descargador:
        return self._directo

    @property
    def por_proxy(self) -> Descargador:
        return self._proxy

    @property
    def espera_reintento_s(self) -> float:
        return self._espera

    @property
    def peticiones_proxy(self) -> int:
        return self._uso.peticiones

    @property
    def bytes_proxy(self) -> int:
        return self._uso.bytes

    def dominios_por_proxy(self) -> tuple[str, ...]:
        return tuple(sorted(self._uso.dominios))

    async def descargar(self, url: str) -> Pagina:
        return await self._pedir(
            url, lambda por_proxy: (self._proxy if por_proxy else self._directo).descargar(url)
        )

    async def descargar_grande(self, url: str, *, max_bytes: int) -> Pagina:
        directo, proxy = self._directo, self._proxy
        if not (isinstance(directo, DescargadorGrande) and isinstance(proxy, DescargadorGrande)):
            raise ErrorDescarga("Este navegador no puede descargar ficheros grandes (sitemaps).")
        return await self._pedir(
            url,
            lambda por_proxy: (proxy if por_proxy else directo).descargar_grande(
                url, max_bytes=max_bytes
            ),
        )

    async def cerrar(self) -> None:
        try:
            await self._directo.cerrar()
        finally:
            await self._proxy.cerrar()

    async def _pedir(self, url: str, pedir: Callable[[bool], Awaitable[Pagina]]) -> Pagina:
        """`pedir(por_proxy)` hace la descarga con el descargador directo o con el del proxy."""
        dominio = dominio_de(url)
        if self._modo is ModoProxy.NUNCA:
            return await pedir(False)
        if self._modo is ModoProxy.SIEMPRE or dominio in self._uso.dominios:
            return await self._por_proxy(pedir)
        try:
            pagina = await pedir(False)
        except ErrorDescarga as error:
            if not _conexion_cortada(error):
                raise
            motivo = "cortó la conexión"
        else:
            # Como en `Navegador`: de un fichero grande basta el principio para ver un bloqueo.
            motivo_bloqueo = self._detectar(replace(pagina, html=pagina.html[:MAXIMO_BYTES]))
            if motivo_bloqueo is None or motivo_bloqueo == MOTIVO_LIMITE:
                return pagina
            motivo = motivo_bloqueo
        if dominio not in self._uso.dominios:
            # Sin credenciales: solo el dominio y el motivo, que ya se enseñan en la web.
            registro.warning(
                "%s bloqueó la conexión directa (%s): se reintenta por el proxy", dominio, motivo
            )
            self._uso.dominios.add(dominio)
        host = (urlsplit(url).hostname or "").lower()
        if not await pausa_de_cortesia(host) and self._espera > 0:
            await self._dormir(self._espera)
        return await self._por_proxy(pedir)

    async def _por_proxy(self, pedir: Callable[[bool], Awaitable[Pagina]]) -> Pagina:
        self._uso.peticiones += 1
        pagina = await pedir(True)
        self._uso.bytes += len(pagina.html.encode("utf-8"))
        return replace(pagina, via_proxy=True)
