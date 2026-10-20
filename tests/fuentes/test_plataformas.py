"""Detección de la plataforma de una tienda por su portada y plantillas de búsqueda HTML."""

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from precia.fuentes.plataformas import (
    Plataforma,
    detectar_plataforma,
    moneda_shopify,
    plantillas_html,
)
from precia.navegador.tipos import Pagina

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fuentes"


def _pagina(html: str, url: str = "https://tienda.es/") -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html; charset=utf-8",
        obtenida=datetime(2026, 10, 20, tzinfo=UTC),
    )


def _fixture(nombre: str) -> Pagina:
    return _pagina((FIXTURES / nombre).read_text(encoding="utf-8"))


# --- Portadas reales de cada plataforma -------------------------------------------------------


@pytest.mark.parametrize(
    ("fichero", "esperada"),
    [
        ("portada_shopify.html", Plataforma.SHOPIFY),
        ("portada_woocommerce.html", Plataforma.WOOCOMMERCE),
        ("portada_prestashop17.html", Plataforma.PRESTASHOP),
        ("portada_prestashop8.html", Plataforma.PRESTASHOP),
        ("portada_magento2.html", Plataforma.MAGENTO),
        ("portada_wordpress.html", Plataforma.WORDPRESS),
    ],
)
def test_detecta_la_plataforma_de_portadas_reales(fichero: str, esperada: Plataforma) -> None:
    assert detectar_plataforma(_fixture(fichero)) is esperada


def test_prestashop_16_por_meta_generator() -> None:
    html = (
        '<html><head><meta name="generator" content="PrestaShop" />'
        '<script type="text/javascript" src="/js/jquery/jquery-1.11.0.min.js"></script>'
        '</head><body id="index"></body></html>'
    )
    assert detectar_plataforma(_pagina(html)) is Plataforma.PRESTASHOP


def test_shopify_solo_con_el_cdn_propio_y_las_secciones() -> None:
    html = (
        '<html><head><link href="//tienda.es/cdn/shop/t/3/assets/base.css" rel="stylesheet">'
        '</head><body><div id="shopify-section-header" class="shopify-section"></div>'
        "</body></html>"
    )
    assert detectar_plataforma(_pagina(html)) is Plataforma.SHOPIFY


def test_woocommerce_solo_por_las_clases_del_body() -> None:
    html = (
        '<html><head><link rel="stylesheet" href="/wp-content/themes/astra/style.css"></head>'
        '<body class="home woocommerce-js woocommerce-page"></body></html>'
    )
    assert detectar_plataforma(_pagina(html)) is Plataforma.WOOCOMMERCE


# --- Casos ambiguos ---------------------------------------------------------------------------


def test_blog_wordpress_que_habla_de_otras_plataformas_sigue_siendo_wordpress() -> None:
    """El texto de un artículo («Shopify.theme», «cdn.shopify.com»...) no es una señal."""
    assert detectar_plataforma(_fixture("portada_wordpress.html")) is Plataforma.WORDPRESS


def test_prestashop_con_imagenes_de_un_blog_wordpress_es_prestashop() -> None:
    assert detectar_plataforma(_fixture("prestashop_con_blog_wordpress.html")) is (
        Plataforma.PRESTASHOP
    )


def test_web_sin_plataforma_conocida() -> None:
    """«/img/image/logo.png» contiene «mage/», pero no es Magento."""
    assert detectar_plataforma(_fixture("sin_plataforma.html")) is None


def test_senales_empatadas_de_dos_plataformas_no_deciden() -> None:
    assert detectar_plataforma(_fixture("mezcla_shopify_woocommerce.html")) is None


def test_pagina_vacia_o_json() -> None:
    assert detectar_plataforma(_pagina("")) is None
    assert detectar_plataforma(_pagina('{"products": []}')) is None


def test_atributo_hostil_no_cuela_una_plataforma() -> None:
    """Un `alt` o un `title` con las palabras clave no son rutas de recursos ni clases."""
    html = (
        '<html><body><img alt="cdn.shopify.com Shopify.theme" src="/logo.png">'
        '<p title="wp-content/plugins/woocommerce">Hola</p></body></html>'
    )
    assert detectar_plataforma(_pagina(html)) is None


def test_html_hostil_enorme_se_analiza_en_tiempo_lineal() -> None:
    hostil = "<script>" + "prestashop " * 200_000 + "</script>" + '<div class="x">' * 50_000
    inicio = time.perf_counter()
    detectar_plataforma(_pagina(hostil))
    assert time.perf_counter() - inicio < 5


# --- Moneda de Shopify ------------------------------------------------------------------------


def test_moneda_shopify_de_la_portada() -> None:
    assert moneda_shopify(_fixture("portada_shopify.html")) == "EUR"


def test_moneda_shopify_otra_moneda_y_ausente() -> None:
    html = '<script>Shopify.currency = {"active":"GBP","rate":"0.85"};</script>'
    assert moneda_shopify(_pagina(html)) == "GBP"
    assert moneda_shopify(_fixture("portada_woocommerce.html")) is None
    assert moneda_shopify(_pagina('<script>Shopify.currency = {"active":"euro"};</script>')) is None


def test_moneda_shopify_con_entrada_hostil() -> None:
    hostil = "<script>" + "Shopify.currency = {" * 100_000 + "</script>"
    inicio = time.perf_counter()
    assert moneda_shopify(_pagina(hostil)) is None
    assert time.perf_counter() - inicio < 2


# --- Plantillas de búsqueda HTML --------------------------------------------------------------


@pytest.mark.parametrize(
    ("plataforma", "esperadas"),
    [
        (Plataforma.SHOPIFY, ["https://tienda.es/search?q={consulta}&type=product"]),
        (Plataforma.WOOCOMMERCE, ["https://tienda.es/?s={consulta}&post_type=product"]),
        (
            Plataforma.PRESTASHOP,
            [
                "https://tienda.es/busqueda?controller=search&s={consulta}",
                "https://tienda.es/index.php?controller=search&s={consulta}",
            ],
        ),
        (Plataforma.MAGENTO, ["https://tienda.es/catalogsearch/result/?q={consulta}"]),
        (Plataforma.WORDPRESS, ["https://tienda.es/?s={consulta}"]),
    ],
)
def test_plantillas_html_por_plataforma(plataforma: Plataforma, esperadas: list[str]) -> None:
    assert plantillas_html(plataforma, "https://tienda.es") == esperadas


def test_plantillas_html_usan_el_origen_de_la_base() -> None:
    assert plantillas_html(Plataforma.MAGENTO, "https://www.tienda.es/es/inicio?x=1#a") == [
        "https://www.tienda.es/catalogsearch/result/?q={consulta}"
    ]


@pytest.mark.parametrize(
    "base",
    ["", "ftp://tienda.es", "javascript:alert(1)", "https://", "https://usuario:clave@tienda.es"],
)
def test_plantillas_html_con_base_no_valida(base: str) -> None:
    assert plantillas_html(Plataforma.SHOPIFY, base) == []


# --- Revisión adversarial ---------------------------------------------------------------------


def test_blog_con_imagen_enlazada_de_una_tienda_shopify_sigue_siendo_wordpress() -> None:
    """Un artículo que enlaza la foto de otra tienda (`cdn.shopify.com`) no es Shopify.

    Las imágenes, `source` e `iframe` de otro sitio son contenido, no recursos de la plataforma.
    """
    html = (
        '<html><head><meta name="generator" content="WordPress 6.6.2" />'
        "<link rel='stylesheet' href='https://tallerpaco.es/wp-content/themes/tt4/style.css' />"
        '</head><body class="single-post"><article class="post">'
        "<p>El faro que compramos en otra tienda:</p>"
        '<img src="https://cdn.shopify.com/s/files/1/0612/3456/7890/files/faro.jpg?v=17" alt="">'
        '<picture><source srcset="x" src="https://otra.example/cdn/shop/files/faro.webp"></picture>'
        '<iframe src="https://otra-tienda.example/static/version17/frontend/Magento/luma/x"></iframe>'
        '<img src="https://otra.example/wp-content/plugins/woocommerce/assets/images/p.png">'
        "</article></body></html>"
    )
    assert detectar_plataforma(_pagina(html, "https://tallerpaco.es/")) is Plataforma.WORDPRESS


def test_imagenes_de_la_propia_tienda_o_su_subdominio_si_cuentan() -> None:
    html = (
        '<html><body><img src="/modules/ps_imageslider/images/banner.jpg">'
        '<img src="https://static1.tienda.es/modules/ps_banner/img/x.jpg"></body></html>'
    )
    assert detectar_plataforma(_pagina(html, "https://www.tienda.es/")) is Plataforma.PRESTASHOP


def test_json_ld_de_un_articulo_no_es_codigo_de_la_plataforma() -> None:
    """El `articleBody` de un JSON-LD es texto del artículo aunque vaya en un `<script>`."""
    cuerpo = (
        "Una tienda Shopify define Shopify.theme = {...}; una PrestaShop, var prestashop = {...}; "
        "y Magento carga Magento_Ui. WooCommerce declara wc_add_to_cart_params."
    )
    html = (
        '<html><head><meta name="generator" content="WordPress 6.6.2" />'
        '<script type="application/ld+json">{"@type":"BlogPosting","articleBody":"'
        + cuerpo
        + '"}</script><script type="text/template">Shopify.currency = {"active":"GBP"}</script>'
        "</head><body></body></html>"
    )
    pagina = _pagina(html, "https://tallerpaco.es/")
    assert detectar_plataforma(pagina) is Plataforma.WORDPRESS
    assert moneda_shopify(pagina) is None


def test_script_de_javascript_con_tipo_explicito_si_cuenta() -> None:
    html = (
        '<html><head><script type="text/javascript">var prestashop = {"currency":{}};</script>'
        '<script type="module">Shopify.theme = {"name":"Dawn"};</script></head></html>'
    )
    assert detectar_plataforma(_pagina(html)) is None  # Empate: 2 y 2.


@pytest.mark.parametrize(
    "base",
    [
        r"https://evil.example\.tienda.es/",
        "https://tien da.es/",
        'https://tienda".es/',
        "https://tienda.es<script>/",
    ],
)
def test_plantillas_html_con_host_no_valido(base: str) -> None:
    """Un host con barra invertida o espacios lo leen distinto Chromium y `urllib`."""
    assert plantillas_html(Plataforma.SHOPIFY, base) == []


def test_plantillas_html_con_host_internacional_en_idna() -> None:
    assert plantillas_html(Plataforma.MAGENTO, "https://España.es:8443/x") == [
        "https://xn--espaa-rta.es:8443/catalogsearch/result/?q={consulta}"
    ]


@pytest.mark.parametrize(
    "tipo",
    [
        "5f1a2b3c4d5e6f7a8b9c0d1e-text/javascript",  # Cloudflare Rocket Loader.
        "litespeed/javascript",  # LiteSpeed Cache.
        "rocketlazyloadscript",  # WP Rocket.
        "text/javascript; charset=utf-8",
        "application/x-javascript",
    ],
)
def test_scripts_retrasados_por_optimizadores_si_cuentan(tipo: str) -> None:
    """Los optimizadores cambian el `type` del JavaScript para cargarlo después: sigue siendo
    código de la plataforma (Rocket Loader es muy común en tiendas PrestaShop)."""
    html = (
        '<html><head><script type="' + tipo + '">var prestashop = {"currency":{"iso_code":"EUR"}};'
        "</script></head><body></body></html>"
    )
    assert detectar_plataforma(_pagina(html)) is Plataforma.PRESTASHOP


@pytest.mark.parametrize(
    "tipo",
    ["application/ld+json", "application/json", "text/template", "text/x-template", "importmap"],
)
def test_scripts_de_datos_o_plantillas_no_cuentan(tipo: str) -> None:
    html = '<html><head><script type="' + tipo + '">var prestashop = {};</script></head></html>'
    assert detectar_plataforma(_pagina(html)) is None
