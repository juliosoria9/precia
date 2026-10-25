"""A7: timeouts más cortos. Páginas 10 s (sitemaps, el triple: 30 s) y robots.txt 10 s.

La p99 de las peticiones HTTP medidas es 5,1 s y solo 2 de 842 pasan de 10 s; una tienda lenta
(19,6 s en dar el robots.txt) frenaba toda la búsqueda. El tiempo lo corta el propio transporte,
en su hilo: así no queda una petición en vuelo tras el corte (una por dominio, ADR-017).
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from precia.navegador.con_proxy import DescargadorConProxy
from precia.navegador.http import (
    FACTOR_TIEMPO_GRANDE,
    TIMEOUT_HTTP_S,
    DescargadorHTTP,
    RespuestaCruda,
)
from precia.navegador.proxy import ModoProxy
from precia.navegador.robots import TIEMPO_ROBOTS_S, ServicioRobots
from precia.navegador.tipos import DescargadorConTiempo, Pagina
from precia.nucleo.fuente_real import MARGEN_CANCELADO_S
from tests.navegador.falsos import DescargadorFalso, pagina, validar_con_dns_publico
from tests.navegador.test_http import Cuerpo

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
URL = "https://www.tienda.example/p/1"


def _html() -> RespuestaCruda:
    return RespuestaCruda(
        estado=200,
        cabeceras={"content-type": "text/html; charset=utf-8"},
        cuerpo=Cuerpo(b"<html><body>ok</body></html>"),
    )


@dataclass
class TransporteNavegadorFalso:
    """Como `TransporteCurl`: recibe el tiempo total de cada petición."""

    tiempos: list[float] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        return self.pedir(url, cabeceras, timeout, 0)

    def pedir(
        self, url: str, cabeceras: Mapping[str, str], timeout: float, max_bytes: int
    ) -> RespuestaCruda:
        self.tiempos.append(timeout)
        return _html()

    def cerrar(self) -> None:
        pass


@dataclass
class TransporteURLLibFalso:
    """Como `TransporteURLLib`: recibe el tiempo de cada lectura."""

    tiempos: list[float] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        self.tiempos.append(timeout)
        return _html()


def test_timeout_por_defecto_10_s_y_30_en_los_sitemaps() -> None:
    assert TIMEOUT_HTTP_S == 10.0
    transporte = TransporteNavegadorFalso()
    http = DescargadorHTTP(UA, validador=validar_con_dns_publico, transporte=transporte)
    asyncio.run(http.descargar(URL))
    asyncio.run(http.descargar_grande("https://www.tienda.example/sitemap.xml"))
    assert transporte.tiempos == [10.0, 10.0 * FACTOR_TIEMPO_GRANDE]


def test_descargar_con_tiempo_lo_pasa_al_transporte() -> None:
    transporte = TransporteNavegadorFalso()
    http = DescargadorHTTP(UA, validador=validar_con_dns_publico, transporte=transporte)
    assert isinstance(http, DescargadorConTiempo)
    asyncio.run(http.descargar_con_tiempo(URL, 5.0))
    assert transporte.tiempos == [5.0]


def test_con_urllib_el_tiempo_de_lectura_tampoco_pasa_del_pedido() -> None:
    transporte = TransporteURLLibFalso()
    http = DescargadorHTTP(UA, validador=validar_con_dns_publico, transporte=transporte)
    asyncio.run(http.descargar_con_tiempo(URL, 5.0))
    asyncio.run(http.descargar(URL))
    assert transporte.tiempos == [5.0, 10.0]


class DescargadorConTiempoFalso(DescargadorFalso):
    def __init__(self, respuestas: Mapping[str, Pagina]) -> None:
        super().__init__(respuestas)
        self.tiempos: list[float] = []

    async def descargar_con_tiempo(self, url: str, tiempo_s: float) -> Pagina:
        self.tiempos.append(tiempo_s)
        return await self.descargar(url)


def test_el_robots_txt_se_pide_con_10_s() -> None:
    # Con 5 s fallaban robots.txt válidos de la campaña (tiendanimal.es 8,18 s, amazon.es
    # 8,31 s): un fallo prohíbe todo 10 min y la tienda salía como «Lo prohíbe; se ignora».
    assert TIEMPO_ROBOTS_S == 10.0
    robots = "https://www.tienda.example/robots.txt"
    descargador = DescargadorConTiempoFalso({robots: pagina(robots, "User-agent: *\nAllow: /")})
    servicio = ServicioRobots(descargador, UA)
    assert asyncio.run(servicio.permitido(URL)) is True
    assert descargador.tiempos == [10.0]


def test_el_envoltorio_del_proxy_pasa_el_tiempo() -> None:
    directo = DescargadorConTiempoFalso({URL: pagina(URL)})
    por_proxy = DescargadorConTiempoFalso({URL: pagina(URL)})
    envoltorio = DescargadorConProxy(directo, por_proxy, modo=ModoProxy.BLOQUEO)
    assert isinstance(envoltorio, DescargadorConTiempo)
    asyncio.run(envoltorio.descargar_con_tiempo(URL, 5.0))
    assert directo.tiempos == [5.0]
    assert por_proxy.tiempos == []


def test_envoltorio_sin_tiempo_en_los_internos_descarga_normal() -> None:
    directo = DescargadorFalso({URL: pagina(URL)})
    envoltorio = DescargadorConProxy(directo, DescargadorFalso(), modo=ModoProxy.BLOQUEO)
    asyncio.run(envoltorio.descargar_con_tiempo(URL, 5.0))
    assert directo.pedidas == [URL]


def test_margen_tras_cancelar_cubre_el_timeout_de_un_sitemap() -> None:
    # Una descarga cancelada sigue en su hilo hasta su timeout: el dominio sigue ocupado ese rato.
    # Con urllib, la última lectura puede pasarse del total lo que dura una lectura (10 s).
    assert MARGEN_CANCELADO_S == TIMEOUT_HTTP_S * FACTOR_TIEMPO_GRANDE + TIMEOUT_HTTP_S == 40.0
