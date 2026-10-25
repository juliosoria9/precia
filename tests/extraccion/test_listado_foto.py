"""Foto de cada tarjeta del listado: el primer `<img>` válido de la tarjeta, con carga diferida."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from precia.extraccion.listado import extraer_listado
from precia.extraccion.tipos import ResultadoListado
from precia.navegador.tipos import Pagina
from precia.perfiles.tipos import Perfil

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
URL = "https://www.tienda.es/buscar?q=faro"

PERFIL = Perfil(
    dominio="tienda.es",
    version=1,
    url_busqueda="https://www.tienda.es/buscar?q={consulta}",
    sel_resultado="div.resultado",
    sel_titulo="h3",
    sel_enlace="a @href",
    sel_precio_listado=None,
    sel_campos={},
    necesita_js=False,
    autor="manual",
    revisado=True,
    creado=datetime(2026, 10, 1, tzinfo=UTC),
)


def _pagina(html: str, url: str = URL) -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html",
        obtenida=datetime(2026, 10, 24, tzinfo=UTC),
    )


def test_resultado_sin_foto_por_defecto() -> None:
    assert ResultadoListado("Faro", "https://www.tienda.es/p/1").foto is None


def test_foto_de_la_tarjeta_con_carga_diferida() -> None:
    html = (
        '<div class="resultado"><a href="/p/1"><img src="/img/placeholder.gif"'
        ' data-src="/img/faro-1.jpg"></a><h3>Faro 1</h3></div>'
        '<div class="resultado"><a href="/p/2"><img src="https://pixel.example/a.jpg"></a>'
        "<h3>Faro 2</h3></div>"
        '<div class="resultado"><a href="/p/3"><img src="javascript:alert(1)"></a>'
        "<h3>Faro 3</h3></div>"
    )
    resultados = extraer_listado(_pagina(html), PERFIL)
    assert [r.foto for r in resultados] == ["https://www.tienda.es/img/faro-1.jpg", None, None]


def test_real_oscaro_foto_de_cada_tarjeta_y_sin_marcador() -> None:
    url = "https://www.oscaro.es/es/search?q=faro"
    html = (REALES / "oscaro_busqueda_faro_ofertas.html").read_text(encoding="utf-8")
    perfil = replace(
        PERFIL,
        dominio="oscaro.es",
        url_busqueda="https://www.oscaro.es/es/search?q={consulta}",
        sel_resultado="article.productbox",
        sel_titulo="h2",
        sel_enlace="a @href",
    )
    resultados = extraer_listado(_pagina(html, url), perfil)
    assert [r.foto for r in resultados] == [
        "https://oscaro.media/catalog/images/jpg/normal/30/1987302271ph01whco0000.jpg",
        "https://oscaro.media/catalog/images/jpg/normal/4657/216-1143.jpg",
        # «img-not-available.png» es el marcador de la tienda, no una foto.
        None,
    ]


def test_real_webscraper_foto_relativa() -> None:
    url = "https://webscraper.io/test-sites/e-commerce/allinone/computers/laptops"
    html = (REALES / "webscraper_listado_laptops.html").read_text(encoding="utf-8")
    perfil = replace(
        PERFIL,
        dominio="webscraper.io",
        url_busqueda="https://webscraper.io/test-sites/e-commerce/allinone/search?q={consulta}",
        sel_resultado="div.thumbnail",
        sel_titulo="a.title",
        sel_enlace="a.title @href",
    )
    resultados = extraer_listado(_pagina(html, url), perfil)
    assert resultados
    assert {r.foto for r in resultados} == {
        "https://webscraper.io/images/test-sites/e-commerce/items/cart2.png"
    }
