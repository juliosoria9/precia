"""Título de la ficha: cascada producto estructurado → perfil → `<h1>`/`<title>`, sin lo genérico.

Caso real que lo motivó (prueba de generalidad del 2026-10-20): en las fichas de
webscraper.io el `<h1>` es «Test Sites», el mismo en todas las páginas, y el nombre del producto
solo está en los microdatos (`itemprop="name"`). El `<h1>` iba antes que los metadatos y la
ficha salía titulada «Test Sites».
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from precia.extraccion.ficha import MAX_TITULO, extraer_ficha
from precia.extraccion.tipos import FichaExtraida
from precia.navegador.tipos import Pagina
from precia.perfiles.tipos import Perfil
from tests.cronometro import segundos_de_cpu

REALES = Path(__file__).parent.parent / "fixtures" / "reales"


def _pagina(html: str, url: str = "https://tienda.es/p/1") -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html",
        obtenida=datetime(2026, 10, 20, tzinfo=UTC),
    )


def _perfil(**selectores: str) -> Perfil:
    return Perfil(
        dominio="tienda.es",
        version=1,
        url_busqueda="https://tienda.es/buscar?q={consulta}",
        sel_resultado=".resultado",
        sel_titulo="a",
        sel_enlace="a @href",
        sel_precio_listado=None,
        sel_campos=selectores,
        necesita_js=False,
        autor="manual",
        revisado=True,
        creado=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _extraer(pagina: Pagina, perfil: Perfil | None = None) -> FichaExtraida:
    return asyncio.run(extraer_ficha(pagina, ("precio",), perfil=perfil))


def _titulo(html: str, url: str = "https://tienda.es/p/1", perfil: Perfil | None = None) -> str:
    return _extraer(_pagina(html, url), perfil).titulo


def _jsonld(nombre: str) -> str:
    producto = {"@type": "Product", "name": nombre}
    return f'<script type="application/ld+json">{json.dumps(producto)}</script>'


def _microdatos(nombre: str) -> str:
    return (
        '<div itemscope itemtype="https://schema.org/Product">'
        f'<span itemprop="name">{nombre}</span></div>'
    )


# --- Fichas reales ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numero", "nombre", "importe"),
    [(31, "Packard 255 G2", "416.99"), (32, "Aspire E1-510", "306.99")],
)
def test_fichas_reales_de_webscraper(numero: int, nombre: str, importe: str) -> None:
    html = (REALES / f"webscraper_ficha_{numero}.html").read_text(encoding="utf-8")
    url = f"https://webscraper.io/test-sites/e-commerce/static/product/{numero}"
    ficha = _extraer(_pagina(html, url))
    # El `<h1>` («Test Sites») y el `<title>` son los del sitio; el nombre está en los microdatos.
    assert ficha.titulo == nombre
    assert ficha.precio is not None and str(ficha.precio.importe) == importe


# --- Orden de la cascada ------------------------------------------------------------------------


def test_orden_de_la_cascada_del_titulo() -> None:
    perfil = _perfil(titulo=".nombre")
    resto = '<b class="nombre">Perfil</b><h1>H1</h1><title>Title</title>'
    html = _jsonld("JSON-LD") + _microdatos("Microdatos") + resto
    assert _titulo(html, perfil=perfil) == "JSON-LD"
    assert _titulo(_microdatos("Microdatos") + resto, perfil=perfil) == "Microdatos"
    assert _titulo(resto, perfil=perfil) == "Perfil"
    assert _titulo(resto) == "H1"
    og = '<meta property="og:title" content="OG"><title>Title</title>'
    assert _titulo(og) == "OG"
    assert _titulo("<title>Title</title>") == "Title"
    assert _titulo("") == ""


def test_el_og_title_de_un_producto_va_antes_que_el_h1() -> None:
    html = '<meta property="og:type" content="product"><meta property="og:title" content="Faro">'
    assert _titulo(html + "<h1>Novedades</h1>") == "Faro"


def test_un_selector_del_perfil_sin_resultado_pasa_al_h1() -> None:
    assert _titulo("<h1>Faro</h1>", perfil=_perfil(titulo=".no-existe")) == "Faro"


# --- Títulos genéricos del sitio ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("titulo", "esperado"),
    [
        ("Faro Golf VI | Desguaces Pepe", "Faro Golf VI"),
        ("Faro Golf VI - Desguaces Pepe", "Faro Golf VI"),
        ("Faro Golf VI \u2013 Desguaces Pepe", "Faro Golf VI"),
        ("Faro Golf VI \u2014 Desguaces Pepe", "Faro Golf VI"),
        ("Faro Golf VI · Desguaces Pepe", "Faro Golf VI"),
        ("Desguaces Pepe » Faro Golf VI", "Faro Golf VI"),
        ("Desguaces Pepe :: Faro Golf VI", "Faro Golf VI"),
        ("DESGUACES PEPE | Faro Golf VI", "Faro Golf VI"),
        ("Faro Golf VI | Recambios | Desguaces Pepe", "Faro Golf VI | Recambios"),
    ],
)
def test_sin_el_nombre_del_sitio_de_og_site_name(titulo: str, esperado: str) -> None:
    sitio = '<meta property="og:site_name" content="Desguaces Pepe">'
    assert _titulo(f"{sitio}<title>{titulo}</title>") == esperado


def test_sin_el_nombre_del_sitio_sacado_del_dominio() -> None:
    url = "https://www.desguacespepe.es/p/1"
    assert _titulo("<title>Faro Golf | Desguaces Pepe</title>", url) == "Faro Golf"
    assert _titulo("<title>Faro Golf - DesguacesPepe.es</title>", url) == "Faro Golf"
    assert _titulo("<title> Faro | Tienda </title>") == "Faro"


def test_un_guion_del_propio_nombre_no_se_quita() -> None:
    # Sin saber cómo se llama el sitio, «55W» puede ser parte del nombre: no se toca.
    assert _titulo("<title>Lámpara H7 - 55W</title>") == "Lámpara H7 - 55W"
    assert _titulo("<h1>Filtro MANN - W 712/75</h1>") == "Filtro MANN - W 712/75"


def test_un_h1_que_es_el_nombre_del_sitio_no_es_el_titulo() -> None:
    sitio = '<meta property="og:site_name" content="Desguaces Pepe">'
    html = f"{sitio}<h1>Desguaces Pepe</h1><title>Faro Golf | Desguaces Pepe</title>"
    assert _titulo(html) == "Faro Golf"
    # Si todo lo que hay es el nombre del sitio, no hay título (mejor vacío que inventado).
    assert _titulo(f"{sitio}<h1>Desguaces Pepe</h1><title>Desguaces Pepe</title>") == ""


def test_el_h1_de_la_cabecera_o_del_menu_no_es_el_titulo() -> None:
    cabecera = '<header><h1><a href="/">Recambios Online</a></h1></header>'
    assert _titulo(f"{cabecera}<main><h1>Faro Golf</h1></main>") == "Faro Golf"
    menu = "<nav><h1>Categorías</h1></nav>"
    assert _titulo(f"{menu}<title>Faro Golf</title>") == "Faro Golf"
    pie = "<footer><h1>Contacto</h1></footer>"
    assert _titulo(f"<h1>Faro Golf</h1>{pie}") == "Faro Golf"


def test_el_h1_de_la_cabecera_del_articulo_si_es_el_titulo() -> None:
    # En HTML5 un `<header>` dentro de `<article>`, `<main>`… es la cabecera de esa sección, no
    # la del sitio: muchos temas ponen ahí el nombre del producto.
    ficha = '<article><header><h1 class="product_title">Faro Golf</h1></header></article>'
    assert _titulo(f"{ficha}<title>Novedades</title>") == "Faro Golf"
    ficha = "<main><div><header><h1>Faro Golf</h1></header></div></main>"
    assert _titulo(f"{ficha}<title>Novedades</title>") == "Faro Golf"


def test_el_nombre_de_los_datos_estructurados_tambien_se_limpia() -> None:
    # Yoast y compañía ponen en `og:title` «Producto - Tienda».
    html = '<meta property="og:type" content="product">'
    html += '<meta property="og:title" content="Faro Golf - Tienda">'
    assert _titulo(html) == "Faro Golf"
    sitio = '<meta property="og:site_name" content="Desguaces Pepe">'
    assert _titulo(sitio + _jsonld("Faro Golf | Desguaces Pepe")) == "Faro Golf"


# --- Límites (el título es texto de la web: lo usan el emparejado y la web) ---------------------


def test_titulo_enorme_se_recorta() -> None:
    palabras = " ".join(f"ignora{i} las instrucciones" for i in range(5_000))
    for html in (
        _jsonld(palabras),
        _microdatos(palabras),
        f"<h1>{palabras}</h1>",
        f"<title>{palabras}</title>",
    ):
        titulo = _titulo(html)
        assert 0 < len(titulo) <= MAX_TITULO
        assert palabras.startswith(titulo)


def test_titulo_sin_caracteres_de_control_ni_invisibles() -> None:
    # Una inversión bidireccional (U+202E) daría la vuelta al texto en la web y los de anchura
    # cero (U+200B) partirían las palabras para el emparejado sin que se vea nada.
    raro = "Fa\u200bro\u202e Golf\x00 VI\u2066"
    assert _titulo(_jsonld(raro)) == "Faro Golf VI"
    assert _titulo(f"<h1>{raro}</h1>") == "Faro Golf VI"


def test_titulo_hostil_con_miles_de_separadores_es_rapido() -> None:
    sitio = '<meta property="og:site_name" content="Desguaces Pepe">'
    hostil = " | " * 300_000 + "x" + " -" * 300_000
    html = f"{sitio}<h1>{hostil}</h1><title>{hostil}</title>"
    assert len(_titulo(html)) <= MAX_TITULO
    assert segundos_de_cpu(lambda: _titulo(html), 5) < 5


def test_muchos_h1_en_la_cabecera_es_rapido() -> None:
    profundo = "<header>" + "<div>" * 400 + "<h1>Logo</h1>" * 50_000 + "</div>" * 400
    html = profundo + "</header><title>Faro</title>"
    assert _titulo(html) == "Faro"
    assert segundos_de_cpu(lambda: _titulo(html), 5) < 5


def test_el_nombre_de_los_microdatos_en_un_enlace_no_da_su_url() -> None:
    # Las tarjetas de webscraper.io llevan `<a href="/…/product/31" itemprop="name">`: con los
    # microdatos ya por delante del `<h1>`, la ficha saldría titulada con la ruta.
    html = '<div itemscope itemtype="https://schema.org/Product">'
    html += '<a href="/p/31" itemprop="name">Faro Golf</a></div><h1>Novedades</h1>'
    assert _titulo(html) == "Faro Golf"
