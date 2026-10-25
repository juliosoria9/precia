"""Causa R3-F1 de la campaña multi-sector: los enlaces de acción de una tarjeta.

En las tarjetas de WooCommerce (y de muchos temas) el precio va junto al botón del carrito,
`<a href="?add-to-cart=272815" rel="nofollow">`, que resuelve a otra URL de la tienda (la propia
página con otros parámetros). `_enlaces_con_precio` subía desde el enlace del producto y se paraba
en la tarjeta porque «enlazaba a otra página», y registraba el botón del carrito como producto
con el texto «60,50€ 50,00€». En modo estricto la consulta no salía en ningún resultado y se
descartaba la búsqueda real de desguacesdli (`/?s=filtro&post_type=product` por Chromium).

Ahora un enlace de acción no cuenta como otra página ni como producto. Lo es si su `href` solo
cambia la consulta de la página (empieza por `?`) o si es `rel="nofollow"`, sin texto visible y
con la misma ruta que la página (un botón de icono: carrito, favoritos, comparar). Compartir la
ruta con la página no basta: en una tienda que enruta por la consulta (`/?product=…`) los
productos la comparten y siguen contando.
"""

from pathlib import Path

import pytest

from precia.extraccion.dom import analizar
from precia.navegador.tipos import Pagina
from precia.perfiles.descubrimiento import _enlaces_con_precio, parece_listado
from precia.perfiles.tipos import ConfiguracionFallida
from tests.perfiles.sitio import pagina
from tests.perfiles.test_descubrimiento_url_final import (
    BUSQUEDA,
    CATALOGO,
    CONSULTA,
    DOMINIO,
    FORMULARIO_PORTADA,
    TIENDA,
    WOOCOMMERCE,
    con_chromium,
    descubrir_con,
    redirecciones,
    tienda,
)

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
DLI_HTML = (REALES / "desguacesdli_busqueda_filtro_add_to_cart.html").read_text(encoding="utf-8")
DLI_FINAL = f"{TIENDA}/recambios/?locale=es&q=filtro"
DLI_PRODUCTOS = {
    f"{TIENDA}/recambios/filtro-gasoil-toyota-land-cruiser-j9-td-vx-5-ptas-kzj95-409808",
    f"{TIENDA}/recambios/filtro-aire-toyota-rav4-a3-sol-388332",
    f"{TIENDA}/recambios/filtro-gasoil-toyota-rav4-a3-sol-388333",
}


def busqueda_dli() -> Pagina:
    """La respuesta real por Chromium de WooCommerce, que acaba en `/recambios/?…&q=filtro`."""
    return pagina(WOOCOMMERCE, DLI_HTML, url_final=DLI_FINAL)


def productos(p: Pagina, dominio: str = DOMINIO) -> dict[str, str]:
    return _enlaces_con_precio(analizar(p.html), p, dominio)


# --- La página real de desguacesdli -------------------------------------------------------------


def test_desguacesdli_los_productos_son_los_enlaces_de_la_tarjeta_y_no_el_carrito() -> None:
    encontrados = productos(busqueda_dli())
    assert set(encontrados) == DLI_PRODUCTOS
    assert not any("add-to-cart" in url for url in encontrados)
    primero = encontrados[
        f"{TIENDA}/recambios/filtro-gasoil-toyota-land-cruiser-j9-td-vx-5-ptas-kzj95-409808"
    ]
    assert primero.startswith("FILTRO GASOIL TOYOTA LAND CRUISER J9 TD VX")
    assert "60,50" in primero


def test_desguacesdli_la_busqueda_real_pasa_el_modo_estricto() -> None:
    assert parece_listado(busqueda_dli(), DOMINIO, CONSULTA, portada=None, estricto=True)


def test_desguacesdli_el_descubrimiento_acepta_la_busqueda_por_chromium() -> None:
    # Por HTTP la búsqueda sale vacía (la pinta el JavaScript); con Chromium, la página real.
    navegador = tienda(paginas_js={BUSQUEDA: DLI_HTML})
    busqueda = descubrir_con(navegador)
    assert busqueda.plantilla == f"{TIENDA}/?s={{consulta}}"
    assert busqueda.necesita_js
    assert con_chromium(navegador) == [FORMULARIO_PORTADA]


def test_desguacesdli_el_catalogo_sin_la_consulta_sigue_sin_aceptarse() -> None:
    # Con R3-F2: aunque ahora el catálogo general sí ve sus tarjetas (alguna con «FILTRO…»), la
    # tienda tiró la consulta al redirigir y no es la búsqueda. Nada acaba en la búsqueda real.
    redir = {**redirecciones(), **dict.fromkeys((WOOCOMMERCE, FORMULARIO_PORTADA), CATALOGO)}
    navegador = tienda(paginas_js={}, redirecciones=redir)
    with pytest.raises(ConfiguracionFallida):
        descubrir_con(navegador)
    assert BUSQUEDA not in navegador.urls


# --- Casos sintéticos ---------------------------------------------------------------------------


def tarjetas(base: str, n: int, accion: str) -> str:
    return "".join(
        f'<li class="producto"><a href="{base}{i}">Filtro de aceite modelo {i}</a>'
        f'<span class="precio">1{i},50 €</span>{accion.format(i=i)}</li>'
        for i in range(1, n + 1)
    )


def listado(cuerpo: str, titulo: str = "Resultados") -> str:
    return (
        f"<html><head><title>{titulo}</title></head><body>"
        f'<ul class="productos">{cuerpo}</ul></body></html>'
    )


def test_tienda_que_enruta_por_la_consulta_cuenta_sus_productos() -> None:
    # Los productos (`/?product=…`) comparten la ruta `/` con la búsqueda (`/?s=filtro`); el
    # carrito es un icono `nofollow` con la misma ruta. La tarjeta agotada, sin precio, no se
    # queda con el de sus vecinas.
    carrito = '<a href="/?product=filtro-{i}&amp;add-to-cart={i}" rel="nofollow"><i></i></a>'
    agotada = '<li class="producto"><a href="/?product=filtro-agotado">Filtro agotado</a></li>'
    html = listado(tarjetas("/?product=filtro-", 8, carrito) + agotada)
    url = "https://tienda.example/?s=filtro"
    encontrados = productos(pagina(url, html), "tienda.example")
    assert set(encontrados) == {f"https://tienda.example/?product=filtro-{i}" for i in range(1, 9)}
    assert parece_listado(
        pagina(url, html), "tienda.example", "filtro", portada=None, estricto=True
    )


def test_el_boton_del_carrito_con_texto_no_es_un_producto() -> None:
    # El enlace de WooCommerce de siempre: `?add-to-cart=` relativo, con «Añadir al carrito».
    boton = '<a href="?add-to-cart={i}" rel="nofollow" class="button">Añadir al carrito</a>'
    html = listado(tarjetas("/producto/filtro-", 3, boton))
    url = "https://tienda.example/tienda/?s=filtro"
    encontrados = productos(pagina(url, html), "tienda.example")
    assert set(encontrados) == {f"https://tienda.example/producto/filtro-{i}" for i in range(1, 4)}


def test_opencart_con_el_enlace_de_comparar_en_cada_tarjeta() -> None:
    comparar = (
        '<a href="index.php?route=product/compare&amp;product_id={i}" rel="nofollow" '
        'title="Comparar"><i class="fa fa-exchange"></i></a>'
    )
    html = listado(tarjetas("index.php?route=product/product&amp;product_id=", 4, comparar))
    url = "https://tienda.example/index.php?route=product/search&search=filtro"
    encontrados = productos(pagina(url, html), "tienda.example")
    esperados = {
        f"https://tienda.example/index.php?route=product/product&product_id={i}"
        for i in range(1, 5)
    }
    assert set(encontrados) == esperados


def test_un_enlace_nofollow_con_texto_a_otra_ruta_sigue_siendo_otra_pagina() -> None:
    # Regresión: `nofollow` solo no lo convierte en acción; un enlace con texto a otra ruta (la
    # marca, una categoría) sigue marcando el borde de la tarjeta.
    marca = '<a href="/marca/bosch-{i}" rel="nofollow">Bosch</a>'
    html = listado(tarjetas("/producto/filtro-", 3, marca))
    encontrados = productos(
        pagina("https://tienda.example/buscar?q=filtro", html), "tienda.example"
    )
    assert encontrados == {}


def test_una_ficha_con_enlaces_a_sus_variantes_no_pasa_por_listado() -> None:
    # Una ficha de Shopify con las tallas como `?variant=…`, cada una con su precio: son la
    # propia página con otro parámetro, no resultados.
    variantes = "".join(
        f'<li><a href="?variant={i}">Talla {40 + i}</a> <span>59,95 €</span></li>'
        for i in range(1, 6)
    )
    html = (
        "<html><head><title>Zapatillas Runner Pro</title></head><body>"
        f"<h1>Zapatillas Runner Pro</h1><ul>{variantes}</ul></body></html>"
    )
    url = "https://tienda.example/products/zapatillas-runner-pro"
    ficha = pagina(url, html)
    assert productos(ficha, "tienda.example") == {}
    assert not parece_listado(ficha, "tienda.example", "zapatillas", portada=None, estricto=True)
