"""`Navegador.obtener(..., grande=True)` y `Navegador.sitemaps` (nota 16, sección B).

Un sitemap pasa por lo mismo que una página (validar, robots, turno, bloqueo), con un tope de
bytes mayor, sin la caché de páginas y nunca con Chromium.
"""

import asyncio
from pathlib import Path

import pytest

import precia.navegador.navegador as modulo_navegador
from precia.navegador.bloqueo import detectar_bloqueo
from precia.navegador.cache import CachePaginas
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.http import MAX_BYTES_SITEMAP, MAXIMO_BYTES
from precia.navegador.navegador import Navegador
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import (
    Bloqueado,
    Descargador,
    ErrorDescarga,
    Pagina,
    ProhibidoPorRobots,
    URLNoPermitida,
)
from tests.navegador.falsos import (
    DescargadorFalso,
    DescargadorGrandeFalso,
    Reloj,
    RelojFecha,
    pagina,
    validar_con_dns_publico,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
TIENDA = "https://www.recambios-ejemplo.example"
ROBOTS = f"{TIENDA}/robots.txt"
SITEMAP = f"{TIENDA}/sitemap_index.xml"
PRODUCTOS = f"{TIENDA}/product-sitemap.xml"
PRIVADO = f"{TIENDA}/privado/sitemap.xml"
XML = '<?xml version="1.0"?><sitemapindex><sitemap><loc>x</loc></sitemap></sitemapindex>'
ROBOTS_TXT = (
    f"User-agent: *\nDisallow: /privado/\nCrawl-delay: 3\nSitemap: {SITEMAP}\nSitemap: {PRIVADO}\n"
)


def xml(url: str, *, url_final: str | None = None) -> Pagina:
    return pagina(url, XML, tipo="application/xml", url_final=url_final)


def desafio() -> str:
    return (FIXTURES / "cloudflare_just_a_moment.html").read_text(encoding="utf-8")


class Montaje:
    def __init__(
        self,
        paginas: dict[str, Pagina | Exception],
        *,
        robots: Pagina | None = None,
        respetar_robots: bool = True,
        cache: Path | None = None,
        http: Descargador | None = None,
    ) -> None:
        self.reloj = Reloj()
        self.robots_http = DescargadorFalso(
            {ROBOTS: robots or pagina(ROBOTS, ROBOTS_TXT, tipo="text/plain")}
        )
        self.http = DescargadorGrandeFalso(paginas)
        self.chromium = DescargadorFalso(paginas)
        self.cache = CachePaginas(cache, 3600, reloj=RelojFecha()) if cache else None
        self.navegador = Navegador(
            http=http or self.http,
            chromium=self.chromium,
            robots=ServicioRobots(self.robots_http, UA, reloj=self.reloj),
            limitador=LimitadorDominio(1.0, reloj=self.reloj, dormir=self.reloj.dormir),
            cache=self.cache,
            validador=validar_con_dns_publico,
            respetar_robots=respetar_robots,
        )

    def grande(self, url: str, *, js: bool = False, usar_cache: bool = True) -> Pagina:
        return asyncio.run(self.navegador.obtener(url, js=js, usar_cache=usar_cache, grande=True))


def test_grande_usa_el_tope_de_los_sitemaps() -> None:
    m = Montaje({SITEMAP: xml(SITEMAP)})
    assert m.grande(SITEMAP).html == XML
    assert m.http.grandes == [(SITEMAP, MAX_BYTES_SITEMAP)]
    assert m.chromium.pedidas == []


def test_sin_grande_se_descarga_como_siempre() -> None:
    m = Montaje({SITEMAP: xml(SITEMAP)})
    asyncio.run(m.navegador.obtener(SITEMAP))
    assert m.http.pedidas == [SITEMAP] and m.http.grandes == []


def test_js_y_grande_a_la_vez_es_un_error_de_programacion() -> None:
    m = Montaje({SITEMAP: xml(SITEMAP)})
    with pytest.raises(ValueError, match="Chromium"):
        m.grande(SITEMAP, js=True)
    assert m.robots_http.pedidas == [] and m.http.pedidas == [] and m.chromium.pedidas == []


def test_grande_no_lee_la_cache(tmp_path: Path) -> None:
    m = Montaje({SITEMAP: xml(SITEMAP)}, cache=tmp_path)
    assert m.cache is not None
    m.cache.guardar(pagina(SITEMAP, "<viejo/>", tipo="application/xml"))
    assert m.grande(SITEMAP).html == XML
    assert m.http.grandes == [(SITEMAP, MAX_BYTES_SITEMAP)]


def test_grande_no_guarda_en_la_cache(tmp_path: Path) -> None:
    m = Montaje({SITEMAP: xml(SITEMAP)}, cache=tmp_path)
    m.grande(SITEMAP)
    m.grande(SITEMAP, usar_cache=True)
    assert list(tmp_path.iterdir()) == []
    assert len(m.http.grandes) == 2


def test_grande_valida_la_url_antes_de_todo() -> None:
    m = Montaje({})
    with pytest.raises(URLNoPermitida):
        m.grande("http://169.254.169.254/sitemap.xml")
    assert m.robots_http.pedidas == [] and m.http.pedidas == []


def test_grande_normaliza_la_url() -> None:
    m = Montaje({SITEMAP: xml(SITEMAP)})
    m.grande("HTTPS://WWW.Recambios-Ejemplo.example:443/sitemap_index.xml#x")
    assert m.http.grandes == [(SITEMAP, MAX_BYTES_SITEMAP)]


def test_grande_respeta_robots_si_se_pide() -> None:
    m = Montaje({PRIVADO: xml(PRIVADO)}, respetar_robots=True)
    with pytest.raises(ProhibidoPorRobots):
        m.grande(PRIVADO)
    assert m.http.pedidas == []


def test_grande_con_robots_ignorado_se_descarga_y_se_anota() -> None:
    m = Montaje({PRIVADO: xml(PRIVADO)}, respetar_robots=False)
    m.grande(PRIVADO)
    assert m.http.grandes == [(PRIVADO, MAX_BYTES_SITEMAP)]
    assert m.navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_grande_redireccion_a_ruta_prohibida() -> None:
    m = Montaje({SITEMAP: xml(SITEMAP, url_final=PRIVADO)})
    with pytest.raises(ProhibidoPorRobots):
        m.grande(SITEMAP)


def test_grande_respeta_el_turno_y_el_crawl_delay() -> None:
    m = Montaje({SITEMAP: xml(SITEMAP), PRODUCTOS: xml(PRODUCTOS)})

    async def dos() -> None:
        await m.navegador.obtener(SITEMAP, grande=True)
        await m.navegador.obtener(PRODUCTOS, grande=True)

    asyncio.run(dos())
    assert m.reloj.esperas == [pytest.approx(3.0)]


def test_sitemap_con_el_desafio_de_cloudflare_es_bloqueado(tmp_path: Path) -> None:
    m = Montaje({SITEMAP: pagina(SITEMAP, desafio(), estado=403)}, cache=tmp_path)
    with pytest.raises(Bloqueado) as error:
        m.grande(SITEMAP)
    assert error.value.url == SITEMAP
    assert m.http.grandes == [(SITEMAP, MAX_BYTES_SITEMAP)]  # una vez: no se reintenta
    assert list(tmp_path.iterdir()) == []


def test_sitemap_con_limite_de_peticiones_es_bloqueado() -> None:
    m = Montaje({SITEMAP: pagina(SITEMAP, "<error/>", estado=429, tipo="application/xml")})
    with pytest.raises(Bloqueado):
        m.grande(SITEMAP)


def test_robots_con_desafio_bloquea_tambien_el_sitemap() -> None:
    m = Montaje(
        {SITEMAP: xml(SITEMAP)},
        robots=pagina(ROBOTS, desafio(), estado=403),
        respetar_robots=False,
    )
    with pytest.raises(Bloqueado):
        m.grande(SITEMAP)
    assert m.http.pedidas == []


def test_grande_con_un_descargador_sin_descarga_grande() -> None:
    m = Montaje({}, http=DescargadorFalso({SITEMAP: xml(SITEMAP)}))
    with pytest.raises(ErrorDescarga, match="grande"):
        m.grande(SITEMAP)


def test_errores_de_la_descarga_grande_se_propagan() -> None:
    m = Montaje({SITEMAP: ErrorDescarga("El fichero comprimido está dañado.")})
    with pytest.raises(ErrorDescarga, match="dañado"):
        m.grande(SITEMAP)


def _espiar_bloqueos(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Apunta cuántos caracteres recibe `detectar_bloqueo` en cada llamada del navegador."""
    analizados: list[int] = []

    def espia(recibida: Pagina) -> str | None:
        analizados.append(len(recibida.html))
        return detectar_bloqueo(recibida)

    monkeypatch.setattr(modulo_navegador, "detectar_bloqueo", espia)
    return analizados


def test_bloqueo_de_un_fichero_grande_mira_como_mucho_lo_de_una_pagina(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`detectar_bloqueo` corre en el bucle de eventos: con 20 MiB de HTML tardaba unos 2 s por
    sitemap, y con 10 por tienda paraba la web y las demás tiendas (revisión adversarial). Una
    página de bloqueo nunca pasa del tope de una página normal: se mira eso, sin cronómetro."""
    analizados = _espiar_bloqueos(monkeypatch)
    enorme = "<html><body>" + "a" * MAX_BYTES_SITEMAP + "</body></html>"
    m = Montaje({SITEMAP: pagina(SITEMAP, enorme)})
    assert m.grande(SITEMAP).html == enorme  # La página entera llega a quien la pidió.
    assert analizados and max(analizados) <= MAXIMO_BYTES


def test_desafio_seguido_de_mucho_relleno_sigue_siendo_bloqueo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analizados = _espiar_bloqueos(monkeypatch)
    m = Montaje({SITEMAP: pagina(SITEMAP, desafio() + " " * MAX_BYTES_SITEMAP, estado=503)})
    with pytest.raises(Bloqueado):
        m.grande(SITEMAP)
    assert analizados and max(analizados) <= MAXIMO_BYTES


# --- Navegador.sitemaps -------------------------------------------------------------------------


def test_sitemaps_del_robots() -> None:
    m = Montaje({})
    assert asyncio.run(m.navegador.sitemaps(f"{TIENDA}/")) == (SITEMAP, PRIVADO)
    assert m.robots_http.pedidas == [ROBOTS] and m.http.pedidas == []


def test_sitemaps_valida_la_url() -> None:
    m = Montaje({})
    with pytest.raises(URLNoPermitida):
        asyncio.run(m.navegador.sitemaps("http://127.0.0.1/"))
    assert m.robots_http.pedidas == []


def test_sitemaps_se_leen_aunque_se_ignore_el_robots() -> None:
    m = Montaje({}, respetar_robots=False)
    assert asyncio.run(m.navegador.sitemaps(TIENDA)) == (SITEMAP, PRIVADO)


def test_sitemaps_con_robots_bloqueado() -> None:
    m = Montaje({}, robots=pagina(ROBOTS, desafio(), estado=403))
    with pytest.raises(Bloqueado):
        asyncio.run(m.navegador.sitemaps(TIENDA))
