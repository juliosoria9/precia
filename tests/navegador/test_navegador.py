"""Navegador: validar → robots → caché → turno → descargar → detectar bloqueo → guardar."""

import asyncio
from pathlib import Path

import pytest

from precia.navegador.cache import CachePaginas
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.navegador import Navegador
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import (
    Bloqueado,
    ErrorDescarga,
    Pagina,
    ProhibidoPorRobots,
    URLNoPermitida,
)
from tests.navegador.falsos import (
    DescargadorFalso,
    Reloj,
    RelojFecha,
    pagina,
    validar_con_dns_publico,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
TIENDA = "https://www.recambios-ejemplo.example"
ROBOTS = f"{TIENDA}/robots.txt"
BUSQUEDA = f"{TIENDA}/es/busqueda?s=1K0698151"
FICHA = f"{TIENDA}/frenos/pastillas-brembo-p85075.html"


def fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


class Montaje:
    """Un navegador completo con todo lo de fuera falseado."""

    def __init__(
        self,
        paginas: dict[str, Pagina | Exception],
        *,
        robots: str = "User-agent: *\nDisallow: /es/carrito\n",
        chromium: bool = False,
        cache: Path | None = None,
    ) -> None:
        self.reloj = Reloj()
        self.robots_http = DescargadorFalso({ROBOTS: pagina(ROBOTS, robots, tipo="text/plain")})
        self.http = DescargadorFalso(paginas)
        self.chromium = DescargadorFalso(paginas) if chromium else None
        self.cache = CachePaginas(cache, 3600, reloj=RelojFecha()) if cache else None
        self.navegador = Navegador(
            http=self.http,
            chromium=self.chromium,
            robots=ServicioRobots(self.robots_http, UA, reloj=self.reloj),
            limitador=LimitadorDominio(1.0, reloj=self.reloj, dormir=self.reloj.dormir),
            cache=self.cache,
            validador=validar_con_dns_publico,
        )

    def obtener(self, url: str, **opciones: bool) -> Pagina:
        return asyncio.run(self.navegador.obtener(url, **opciones))


def test_obtiene_una_pagina() -> None:
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA, fixture("listado_desguace.html"))})
    resultado = m.obtener(BUSQUEDA)
    assert "PASTILLAS FRENO" in resultado.html
    assert m.robots_http.pedidas == [ROBOTS]
    assert m.http.pedidas == [BUSQUEDA]


def test_normaliza_la_url_antes_de_todo() -> None:
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA)})
    m.obtener("HTTPS://WWW.Recambios-Ejemplo.example:443/es/busqueda?s=1K0698151#resultados")
    assert m.http.pedidas == [BUSQUEDA]


def test_url_no_permitida_no_toca_la_red() -> None:
    m = Montaje({})
    with pytest.raises(URLNoPermitida):
        m.obtener("http://169.254.169.254/latest/meta-data/")
    assert m.robots_http.pedidas == [] and m.http.pedidas == []


def test_robots_prohibe() -> None:
    carrito = f"{TIENDA}/es/carrito"
    m = Montaje({carrito: pagina(carrito)})
    with pytest.raises(ProhibidoPorRobots):
        m.obtener(carrito)
    assert m.http.pedidas == []


def test_respeta_el_crawl_delay() -> None:
    m = Montaje(
        {BUSQUEDA: pagina(BUSQUEDA), FICHA: pagina(FICHA)},
        robots="User-agent: precia\nCrawl-delay: 4\nDisallow: /es/carrito\n",
    )

    async def dos() -> None:
        await m.navegador.obtener(BUSQUEDA)
        await m.navegador.obtener(FICHA)

    asyncio.run(dos())
    assert m.reloj.esperas == [pytest.approx(4.0)]


@pytest.mark.parametrize(
    ("nombre", "estado"),
    [
        ("cloudflare_just_a_moment.html", 403),
        ("recaptcha_trafico_inusual.html", 200),
        ("datadome.html", 403),
    ],
)
def test_bloqueo_lanza_y_no_reintenta(tmp_path: Path, nombre: str, estado: int) -> None:
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA, fixture(nombre), estado=estado)}, cache=tmp_path)
    with pytest.raises(Bloqueado) as error:
        m.obtener(BUSQUEDA)
    assert error.value.url == BUSQUEDA
    assert error.value.motivo
    assert m.http.pedidas == [BUSQUEDA]
    assert list(tmp_path.iterdir()) == []  # el desafío no se guarda en caché


def test_usa_la_cache(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA, fixture("ficha_con_recaptcha.html"))}, cache=tmp_path)
    primera = m.obtener(FICHA)
    segunda = m.obtener(FICHA)
    assert not primera.desde_cache
    assert segunda.desde_cache and segunda.html == primera.html
    assert m.http.pedidas == [FICHA]


def test_sin_cache_si_se_pide(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA)}, cache=tmp_path)
    m.obtener(FICHA)
    m.obtener(FICHA, usar_cache=False)
    assert m.http.pedidas == [FICHA, FICHA]


def test_la_cache_no_salta_robots(tmp_path: Path) -> None:
    carrito = f"{TIENDA}/es/carrito"
    m = Montaje({}, cache=tmp_path)
    assert m.cache is not None
    m.cache.guardar(pagina(carrito))
    with pytest.raises(ProhibidoPorRobots):
        m.obtener(carrito)


def test_errores_http_no_se_guardan(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA, "<h1>No encontrada</h1>", estado=404)}, cache=tmp_path)
    assert m.obtener(FICHA).estado == 404
    assert m.obtener(FICHA).estado == 404
    assert m.http.pedidas == [FICHA, FICHA]


def test_js_usa_chromium(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA, "renderizada")}, chromium=True, cache=tmp_path)
    m.obtener(FICHA, js=True)
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]
    assert m.http.pedidas == []
    # La versión con JavaScript no se sirve a quien pide la descarga directa.
    m.obtener(FICHA)
    assert m.http.pedidas == [FICHA]


def test_js_sin_chromium() -> None:
    m = Montaje({FICHA: pagina(FICHA)})
    with pytest.raises(ErrorDescarga):
        m.obtener(FICHA, js=True)


def test_redireccion_final_prohibida_por_robots() -> None:
    carrito = f"{TIENDA}/es/carrito"
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA, url_final=carrito)})
    with pytest.raises(ProhibidoPorRobots):
        m.obtener(BUSQUEDA)


def test_errores_del_descargador_se_propagan() -> None:
    m = Montaje({FICHA: ErrorDescarga("La página es demasiado grande.")})
    with pytest.raises(ErrorDescarga, match="grande"):
        m.obtener(FICHA)


def test_cerrar_cierra_los_descargadores() -> None:
    m = Montaje({}, chromium=True)
    asyncio.run(m.navegador.cerrar())
    assert m.http.cerrado
    assert m.chromium is not None and m.chromium.cerrado
