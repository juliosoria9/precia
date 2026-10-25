"""Causa R4-E de la campaña multi-sector: un enlace CMS con «busqueda» dentro del slug.

La portada de Kenay Home enlaza a `/es/content/344-busquedas-interesantes` («Búsquedas
destacadas»), una página de contenido. `plantillas_de_enlaces` la tomaba por la ruta del buscador
porque buscaba las palabras de `RUTAS_DE_BUSQUEDA` como subcadena de la ruta: salían 6 plantillas
(una por cada `PARAMETROS_ENLACE`), 12 descargas inútiles entre HTTP y Chromium. Ahora una ruta es
de búsqueda solo si algún tramo EMPIEZA por una de esas palabras; `ruta_tras_redirigir` usa la
misma regla.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from precia.navegador.tipos import Pagina
from precia.perfiles.descubrimiento import plantillas_de_enlaces, ruta_tras_redirigir

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
KENAY_HTML = (REALES / "kenay_portada_enlace_cms_busquedas.html").read_text(encoding="utf-8")

KENAY = "kenayhome.com"
KENAY_PORTADA = f"https://{KENAY}/es/"
DOMINIO = "tienda.es"
TIENDA = f"https://www.{DOMINIO}"


def pagina(url: str, html: str, url_final: str | None = None) -> Pagina:
    return Pagina(
        url=url,
        url_final=url_final or url,
        estado=200,
        html=html,
        tipo_contenido="text/html",
        obtenida=datetime(2026, 10, 24, tzinfo=UTC),
    )


def enlaces(href: str) -> list[str]:
    portada = pagina(f"{TIENDA}/", f"<html><body><a href='{href}'>Enlace</a></body></html>")
    return plantillas_de_enlaces(portada, DOMINIO)


def test_enlace_cms_de_kenay_no_es_una_ruta_de_busqueda() -> None:
    assert plantillas_de_enlaces(pagina(KENAY_PORTADA, KENAY_HTML), KENAY) == []


@pytest.mark.parametrize(
    "ruta", ["/search-nwx/", "/es/busqueda", "/search.html", "/catalogsearch/result", "/buscar"]
)
def test_tramo_que_empieza_por_la_palabra_sigue_valiendo(ruta: str) -> None:
    assert enlaces(ruta), ruta


@pytest.mark.parametrize(
    "ruta",
    [
        "/es/content/344-busquedas-interesantes",
        "/opensearch.xml",
        "/blog/yogasearcher",
        "/blog/market-research-web-scraping",
    ],
)
def test_palabra_dentro_de_un_tramo_no_cuenta(ruta: str) -> None:
    assert enlaces(ruta) == []


def test_redireccion_a_un_enlace_cms_no_es_una_ruta_de_busqueda() -> None:
    final = f"https://{KENAY}/es/content/344-busquedas-interesantes"
    redirigida = pagina(f"https://{KENAY}/es/buscar?q=filtro", "<html></html>", final)
    assert ruta_tras_redirigir(redirigida, KENAY, "filtro", None) is None


def test_redireccion_a_search_nwx_sigue_valiendo() -> None:
    # R3-G: El Corte Inglés manda `/search?q=filtro` a `/search-nwx/` sin la consulta.
    redirigida = pagina(f"{TIENDA}/search?q=filtro", "<html></html>", f"{TIENDA}/search-nwx/")
    assert ruta_tras_redirigir(redirigida, DOMINIO, "filtro", None) == f"{TIENDA}/search-nwx/"
