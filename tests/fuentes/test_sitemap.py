"""Catálogo por `sitemap.xml`: lector propio, candidatos, sitemaps de producto y palabras del slug.

Los sitemaps son de una web no confiable: el lector es lineal, rechaza DTD y entidades, tiene
topes y nunca lanza una excepción. Los casos hostiles se cronometran.
"""

import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

import pytest

import precia.fuentes.sitemap as modulo_sitemap
from precia.emparejado.referencias import referencias_en
from precia.emparejado.texto import normalizar_titulo
from precia.extraccion.listado import MAX_URL
from precia.fuentes.plataformas import Plataforma
from precia.fuentes.sitemap import (
    MAX_CARACTERES_SITEMAP,
    MAX_DECLARADOS,
    MAX_ENTRADAS_SITEMAP,
    MAX_FICHEROS_SITEMAP,
    MAX_PALABRAS_URL,
    MAX_RUTA_PALABRAS,
    EntradaSitemap,
    Sitemap,
    candidatos,
    con_palabras,
    de_la_tienda,
    de_producto,
    leer_sitemap,
    palabras_url,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fuentes" / "sitemaps"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
T = TypeVar("T")


def _fixture(nombre: str) -> Sitemap:
    return leer_sitemap((FIXTURES / nombre).read_text(encoding="utf-8"))


def _urlset(cuerpo: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset {NS}>{cuerpo}</urlset>'


def _urls(sitemap: Sitemap) -> list[str]:
    return [entrada.url for entrada in sitemap.entradas]


def _indice(*urls: str) -> Sitemap:
    return Sitemap("indice", tuple(EntradaSitemap(url) for url in urls))


# --- leer_sitemap: sitemaps reales de cada plataforma -----------------------------------------


def test_indice_de_yoast() -> None:
    sitemap = _fixture("yoast_indice.xml")
    assert sitemap.tipo == "indice"
    assert not sitemap.recortado
    assert len(sitemap.entradas) == 7
    assert sitemap.entradas[2] == EntradaSitemap(
        "https://tienda.es/product-sitemap.xml", "2026-10-19T17:40:02+00:00"
    )


def test_urlset_de_yoast_con_imagenes() -> None:
    sitemap = _fixture("yoast_productos.xml")
    assert sitemap.tipo == "urlset"
    # Las `image:loc` de dentro de cada `url` no son entradas del catálogo.
    assert _urls(sitemap) == [
        "https://tienda.es/tienda/",
        "https://tienda.es/producto/faro-delantero-izquierdo-1k0941005-golf-v/",
        "https://tienda.es/producto/pastillas-de-freno-delanteras-bosch-0986494104/",
        "https://tienda.es/producto/caudal%C3%ADmetro-ford-focus-1-8-tdci/",
    ]
    assert sitemap.entradas[1].lastmod == "2026-10-18T11:22:33+00:00"
    assert sitemap.entradas[3].lastmod is None


def test_indice_de_wordpress_en_una_linea() -> None:
    sitemap = _fixture("wordpress_indice.xml")
    assert sitemap.tipo == "indice"
    assert len(sitemap.entradas) == 7
    assert all(entrada.lastmod is None for entrada in sitemap.entradas)


def test_indice_de_shopify_decodifica_amp() -> None:
    sitemap = _fixture("shopify_indice.xml")
    assert sitemap.tipo == "indice"
    assert sitemap.entradas[0].url == (
        "https://tienda.es/sitemap_products_1.xml?from=7012345678901&to=7098765432109"
    )


def test_urlset_de_shopify_con_hreflang_e_imagenes() -> None:
    sitemap = _fixture("shopify_productos.xml")
    # Ni los `xhtml:link` de idiomas ni las imágenes del CDN son entradas.
    assert _urls(sitemap) == [
        "https://tienda.es/",
        "https://tienda.es/products/retrovisor-derecho-seat-leon-iii",
        "https://tienda.es/products/kit-distribucion-gates-k015603xs",
    ]
    assert sitemap.entradas[0].lastmod is None
    assert sitemap.entradas[1].lastmod == "2026-10-15T14:05:11+02:00"


def test_prestashop_con_cdata_y_comentarios() -> None:
    sitemap = _fixture("prestashop_urlset.xml")
    assert not sitemap.recortado
    assert _urls(sitemap) == [
        "https://tienda.es/",
        "https://tienda.es/es/faros/123-faro-1k0941005-golf.html",
        # En CDATA no se decodifica nada: el `&` va tal cual.
        "https://tienda.es/es/frenos/124-disco-de-freno-brembo-09.9772.11.html"
        "?utm_source=sitemap&id=7",
        "https://tienda.es/es/content/1-entrega",
    ]
    assert sitemap.entradas[1].lastmod == "2026-10-01"


def test_indice_de_prestashop() -> None:
    sitemap = _fixture("prestashop_indice.xml")
    assert sitemap.tipo == "indice"
    assert _urls(sitemap)[0] == "https://tienda.es/1_es_0_sitemap.xml"


def test_magento_con_pagemap_en_otro_espacio_de_nombres() -> None:
    sitemap = _fixture("magento_sitemap.xml")
    assert _urls(sitemap) == [
        "https://tienda.es/",
        "https://tienda.es/amortiguador-delantero-monroe-g16547.html",
        "https://tienda.es/bomba-de-agua-skf-vkpc-81230.html",
        "https://tienda.es/catalog/product/view/id/4521/",
    ]
    assert sitemap.entradas[2].lastmod == "2026-10-03"


def test_con_prefijo_en_el_espacio_de_nombres_del_sitemap() -> None:
    texto = (
        '<sm:urlset xmlns:sm="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sm:url><sm:loc>https://tienda.es/producto/faro-golf/</sm:loc>"
        "<sm:lastmod>2026-10-01</sm:lastmod></sm:url></sm:urlset>"
    )
    assert leer_sitemap(texto).entradas == (
        EntradaSitemap("https://tienda.es/producto/faro-golf/", "2026-10-01"),
    )


def test_un_loc_con_otro_prefijo_no_es_la_url_de_la_entrada() -> None:
    texto = _urlset(
        "<url><image:loc>https://tienda.es/imagen.jpg</image:loc>"
        "<loc>https://tienda.es/producto/faro/</loc></url>"
    )
    assert _urls(leer_sitemap(texto)) == ["https://tienda.es/producto/faro/"]


def test_con_bom_y_atributos_con_mayor_que_y_comillas_simples() -> None:
    texto = "﻿" + _urlset(
        "<url><loc>https://tienda.es/a/</loc>"
        "<xhtml:link rel='alternate' hreflang=\"pt\" href='https://tienda.es/pt/a/?x>y'/>"
        "</url><url><loc>https://tienda.es/b/</loc></url>"
    )
    assert _urls(leer_sitemap(texto)) == ["https://tienda.es/a/", "https://tienda.es/b/"]


def test_espacios_alrededor_de_loc_se_quitan() -> None:
    texto = _urlset("<url><loc>\n   https://tienda.es/a/ \t\n</loc></url>")
    assert _urls(leer_sitemap(texto)) == ["https://tienda.es/a/"]


def test_solo_el_primer_loc_de_cada_url() -> None:
    texto = _urlset("<url><loc>https://tienda.es/a/</loc><loc>https://tienda.es/b/</loc></url>")
    assert _urls(leer_sitemap(texto)) == ["https://tienda.es/a/"]


def test_lo_que_va_despues_de_la_raiz_no_cuenta() -> None:
    texto = _urlset("<url><loc>https://tienda.es/a/</loc></url>") + (
        "<url><loc>https://tienda.es/colada/</loc></url>"
    )
    assert _urls(leer_sitemap(texto)) == ["https://tienda.es/a/"]


def test_url_anidada_no_es_una_entrada() -> None:
    texto = _urlset("<url><url><loc>https://tienda.es/a/</loc></url></url>")
    assert leer_sitemap(texto).entradas == ()


# --- leer_sitemap: entidades ------------------------------------------------------------------


def test_decodifica_las_cinco_entidades_y_las_numericas() -> None:
    texto = _urlset(
        "<url><loc>https://tienda.es/a?x=1&amp;y=&apos;2&apos;&amp;z=&quot;&lt;&gt;&quot;</loc>"
        "</url><url><loc>https:&#47;&#x2F;tienda.es&#x2f;ca&#241;a/</loc></url>"
    )
    assert _urls(leer_sitemap(texto)) == [
        "https://tienda.es/a?x=1&y='2'&z=\"<>\"",
        "https://tienda.es/caña/",
    ]


@pytest.mark.parametrize(
    "loc",
    [
        "https://tienda.es/a&nbsp;b/",  # Entidad no predefinida: necesitaría una DTD.
        "https://tienda.es/a&b/",  # `&` sin escapar.
        "https://tienda.es/a&#0;/",
        "https://tienda.es/a&#xD800;/",  # Sustituto suelto.
        "https://tienda.es/a&#x110000;/",  # Fuera de Unicode.
        "https://tienda.es/a&#" + "9" * 40 + ";/",
        "https://tienda.es/a&#x" + "0" * 30 + "41;/",
    ],
)
def test_entidades_desconocidas_o_invalidas_descartan_la_entrada(loc: str) -> None:
    texto = _urlset(f"<url><loc>{loc}</loc></url><url><loc>https://tienda.es/b/</loc></url>")
    assert _urls(leer_sitemap(texto)) == ["https://tienda.es/b/"]


@pytest.mark.parametrize(
    "texto",
    [
        (FIXTURES / "con_dtd.xml").read_text(encoding="utf-8"),
        f"<!doctype urlset><urlset {NS}><url><loc>https://tienda.es/a/</loc></url></urlset>",
        f'<urlset {NS}><!ENTITY x "y"><url><loc>https://tienda.es/a/</loc></url></urlset>',
    ],
)
def test_una_dtd_o_una_entidad_declarada_da_un_sitemap_vacio(texto: str) -> None:
    sitemap = leer_sitemap(texto)
    assert sitemap.entradas == ()


# --- leer_sitemap: URL y lastmod no fiables ---------------------------------------------------


@pytest.mark.parametrize(
    "loc",
    [
        "",
        "/producto/relativo/",
        "javascript:alert(1)",
        "ftp://tienda.es/a",
        "https://tienda.es/con espacio/",
        "https://tienda.es/con\ttab/",
        "https://tienda.es/control\x01/",
        "https://tienda.es/" + "a" * MAX_URL,
        "<b>https://tienda.es/a/</b>",  # Un elemento dentro de `loc`.
    ],
    ids=lambda valor: repr(valor)[:40],
)
def test_locs_no_validos_se_descartan(loc: str) -> None:
    texto = _urlset(f"<url><loc>{loc}</loc></url><url><loc>https://tienda.es/b/</loc></url>")
    assert _urls(leer_sitemap(texto)) == ["https://tienda.es/b/"]


def test_mayusculas_en_el_esquema_valen() -> None:
    texto = _urlset("<url><loc>HTTPS://tienda.es/a/</loc></url>")
    assert _urls(leer_sitemap(texto)) == ["HTTPS://tienda.es/a/"]


@pytest.mark.parametrize(
    ("lastmod", "esperado"),
    [
        ("2026", "2026"),
        ("2026-10", "2026-10"),
        ("2026-10-20", "2026-10-20"),
        ("2026-10-20T10:00Z", "2026-10-20T10:00Z"),
        ("2026-10-20T10:00:00.123+02:00", "2026-10-20T10:00:00.123+02:00"),
        ("2026-10-20 10:00:00", "2026-10-20 10:00:00"),
        (" 2026-10-20 ", "2026-10-20"),
        ("ayer", None),
        ("", None),
        ("2026-10-20<x/>", None),
        ("2026-10-20" + "0" * 10_000, None),
        ("20261020", None),
    ],
    ids=lambda valor: str(valor)[:30],
)
def test_lastmod_solo_si_es_una_fecha_w3c(lastmod: str, esperado: str | None) -> None:
    texto = _urlset(f"<url><loc>https://tienda.es/a/</loc><lastmod>{lastmod}</lastmod></url>")
    assert leer_sitemap(texto).entradas == (EntradaSitemap("https://tienda.es/a/", esperado),)


# --- leer_sitemap: XML roto, recortado o que no es un sitemap ---------------------------------


def test_recortado_da_las_entradas_completas_anteriores() -> None:
    sitemap = _fixture("recortado.xml")
    assert sitemap.recortado
    assert sitemap.entradas == (
        EntradaSitemap("https://tienda.es/producto/faro-golf/", "2026-10-18"),
        EntradaSitemap("https://tienda.es/producto/piloto-golf/", None),
    )


@pytest.mark.parametrize(
    "resto",
    [
        "<url><loc>https://tienda.es/b/</url>",  # Cierre que no corresponde.
        "<url><loc>https://tienda.es/b/</loc>",  # Sin cerrar.
        "<url><!-- sin cerrar <loc>https://tienda.es/b/</loc></url>",
        "<url><loc><![CDATA[https://tienda.es/b/</loc></url>",
        "<url><loc>https://tienda.es/b/</loc></url",
        '<url a="sin cerrar><loc>https://tienda.es/b/</loc></url>',
        "<url><loc>https://tienda.es/b/</loc><",
        "<url><!ELEMENT x ANY><loc>https://tienda.es/b/</loc></url>",
    ],
)
def test_xml_roto_da_lo_completo_anterior_y_recortado(resto: str) -> None:
    texto = f"<urlset {NS}><url><loc>https://tienda.es/a/</loc></url>{resto}"
    sitemap = leer_sitemap(texto)
    assert _urls(sitemap) == ["https://tienda.es/a/"]
    assert sitemap.recortado


def test_cortado_justo_despues_de_una_entrada_la_conserva() -> None:
    texto = f"<urlset {NS}><url><loc>https://tienda.es/a/</loc></url><"
    sitemap = leer_sitemap(texto)
    assert _urls(sitemap) == ["https://tienda.es/a/"]
    assert sitemap.recortado


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        "no es xml",
        "<html><body><a href='https://tienda.es/a/'>a</a></body></html>",
        "<rss><channel><item><link>https://tienda.es/a/</link></item></channel></rss>",
        "<url><loc>https://tienda.es/a/</loc></url>",  # Sin raíz.
        "<urlset",
        "\x00\x01\x02",
    ],
)
def test_lo_que_no_es_un_sitemap_da_vacio(texto: str) -> None:
    sitemap = leer_sitemap(texto)
    assert sitemap.entradas == ()
    assert sitemap.tipo == "urlset"


def test_como_mucho_max_entradas_por_fichero() -> None:
    cuerpo = "".join(
        f"<url><loc>https://tienda.es/p/{i}</loc></url>" for i in range(MAX_ENTRADAS_SITEMAP + 1)
    )
    sitemap = leer_sitemap(_urlset(cuerpo))
    assert len(sitemap.entradas) == MAX_ENTRADAS_SITEMAP
    assert sitemap.entradas[-1].url == f"https://tienda.es/p/{MAX_ENTRADAS_SITEMAP - 1}"
    assert sitemap.recortado


def test_exactamente_max_entradas_no_es_recortado() -> None:
    cuerpo = "".join(
        f"<url><loc>https://tienda.es/p/{i}</loc></url>" for i in range(MAX_ENTRADAS_SITEMAP)
    )
    sitemap = leer_sitemap(_urlset(cuerpo))
    assert len(sitemap.entradas) == MAX_ENTRADAS_SITEMAP
    assert not sitemap.recortado


def test_texto_mas_largo_que_el_tope_se_lee_recortado() -> None:
    entrada = "<url><loc>https://tienda.es/a/</loc></url>"
    texto = f"<urlset {NS}>{entrada}<!--" + "x" * MAX_CARACTERES_SITEMAP + "--></urlset>"
    sitemap = leer_sitemap(texto)
    assert _urls(sitemap) == ["https://tienda.es/a/"]
    assert sitemap.recortado


# --- leer_sitemap: casos hostiles cronometrados -----------------------------------------------


def _cronometrar(texto: str, maximo_s: float = 5) -> Sitemap:
    inicio = time.perf_counter()
    sitemap = leer_sitemap(texto)
    assert time.perf_counter() - inicio < maximo_s
    return sitemap


def test_hostil_20_mb_de_etiquetas_dentro_de_una_url() -> None:
    texto = _urlset(
        "<url><loc>https://tienda.es/a/</loc></url><url><loc>https://tienda.es/b/</loc>"
        + "<priority>0.5</priority>" * (20 * 1024 * 1024 // 24)
        + "</url>"
    )
    sitemap = _cronometrar(texto)
    assert _urls(sitemap)[0] == "https://tienda.es/a/"


def test_hostil_millones_de_loc_vacios() -> None:
    texto = _urlset("<url>" + "<loc></loc>" * 2_000_000 + "</url>")
    assert _cronometrar(texto).entradas == ()


def test_hostil_millones_de_url_con_loc_vacio() -> None:
    texto = _urlset("<url><loc/></url>" * 1_200_000)
    assert _cronometrar(texto).entradas == ()


def test_hostil_anidamiento_absurdo() -> None:
    texto = _urlset(
        "<url><loc>https://tienda.es/a/</loc></url><url>"
        + "<a>" * 3_000_000
        + "</a>" * 3_000_000
        + "</url>"
    )
    sitemap = _cronometrar(texto)
    assert _urls(sitemap) == ["https://tienda.es/a/"]
    assert sitemap.recortado


def test_tope_de_profundidad() -> None:
    # El test anterior se corta antes por el tope de caracteres: este mira el de profundidad.
    texto = _urlset(
        "<url><loc>https://tienda.es/a/</loc></url><url>"
        + "<a>" * 20
        + "</a>" * 20
        + "</url><url><loc>https://tienda.es/b/</loc></url>"
    )
    sitemap = leer_sitemap(texto)
    assert _urls(sitemap) == ["https://tienda.es/a/"]
    assert sitemap.recortado


def test_tope_de_etiquetas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo_sitemap, "MAX_ETIQUETAS_SITEMAP", 8)
    texto = _urlset(
        "<url><loc>https://tienda.es/a/</loc></url>"
        + "<url><loc>https://tienda.es/b/</loc><priority>1</priority></url>"
    )
    sitemap = leer_sitemap(texto)
    assert _urls(sitemap) == ["https://tienda.es/a/"]
    assert sitemap.recortado


def test_hostil_entidad_numerica_gigante() -> None:
    texto = _urlset(
        "<url><loc>https://tienda.es/a&#" + "9" * 10_000_000 + ";</loc></url>"
        "<url><loc>https://tienda.es/b&#x" + "f" * 5_000_000 + ";</loc></url>"
        "<url><loc>https://tienda.es/c/</loc></url>"
    )
    assert _urls(_cronometrar(texto)) == ["https://tienda.es/c/"]


def test_hostil_muchas_entidades_numericas_pequenas() -> None:
    texto = _urlset("<url><loc>https://tienda.es/" + "&#97;" * 4_000_000 + "</loc></url>")
    assert _cronometrar(texto).entradas == ()


@pytest.mark.parametrize(
    ("inicio", "unidad", "veces"),
    [
        ("", "<!--", 4_000_000),
        ("", "<![CDATA[", 2_000_000),
        ("", "<?", 8_000_000),
        ("", "<", 15_000_000),
        ('<url a="', "x", 15_000_000),
        ("<url ", "a", 15_000_000),
        ("<" + "u" * 1_000 + ' a="', "x", 15_000_000),  # Nombre largo: sin retroceso.
        ("", "&#", 8_000_000),
        ("", "]]>", 5_000_000),
    ],
    ids=["comentarios", "cdata", "pi", "menor", "comilla", "atributo", "nombre", "amp", "fin"],
)
def test_hostil_marcado_sin_cerrar(inicio: str, unidad: str, veces: int) -> None:
    relleno = inicio + unidad * veces
    texto = f"<urlset {NS}><url><loc>https://tienda.es/a/</loc></url>" + relleno
    assert _urls(_cronometrar(texto)) == ["https://tienda.es/a/"]


def test_hostil_loc_gigante() -> None:
    texto = _urlset(
        "<url><loc>https://tienda.es/" + "a" * 15_000_000 + "</loc></url>"
        "<url><loc>https://tienda.es/b/</loc></url>"
    )
    assert _urls(_cronometrar(texto)) == ["https://tienda.es/b/"]


def test_hostil_dtd_al_final_de_20_mb() -> None:
    texto = _urlset("<url><loc>https://tienda.es/a/</loc></url>" * 400_000) + "<!DOCTYPE x>"
    assert _cronometrar(texto).entradas == ()


# --- candidatos ---------------------------------------------------------------------------------


def test_candidatos_declarados_primero_y_solo_del_mismo_host() -> None:
    declarados = [
        "https://tienda.es/mapa-del-sitio.xml",
        "https://otra.example/sitemap.xml",
        "https://tienda.es.evil.example/sitemap.xml",
        "https://usuario@tienda.es/sitemap.xml",
        "https://tienda.es:8443/sitemap.xml",
        "javascript:alert(1)",
        "/relativo.xml",
        "https://www.tienda.es/sitemap_index.xml#fragmento",
        "https://tienda.es/mapa-del-sitio.xml",
    ]
    assert candidatos("https://tienda.es/alguna/ruta?x=1", declarados, None) == [
        "https://tienda.es/mapa-del-sitio.xml",
        "https://www.tienda.es/sitemap_index.xml",
        "https://tienda.es/sitemap.xml",
        "https://tienda.es/sitemap_index.xml",
        "https://tienda.es/wp-sitemap.xml",
    ]


@pytest.mark.parametrize(
    ("plataforma", "extra"),
    [
        (None, []),
        (Plataforma.SHOPIFY, []),  # Su `/sitemap.xml` ya está entre los fijos.
        (Plataforma.WORDPRESS, []),
        (Plataforma.WOOCOMMERCE, ["https://tienda.es/product-sitemap.xml"]),
        (Plataforma.PRESTASHOP, ["https://tienda.es/1_index_sitemap.xml"]),
        (Plataforma.MAGENTO, ["https://tienda.es/sitemap/sitemap.xml"]),
    ],
)
def test_candidatos_de_la_plataforma_al_final_y_sin_duplicados(
    plataforma: Plataforma | None, extra: list[str]
) -> None:
    fijos = [
        "https://tienda.es/sitemap.xml",
        "https://tienda.es/sitemap_index.xml",
        "https://tienda.es/wp-sitemap.xml",
    ]
    assert candidatos("https://tienda.es", [], plataforma) == fijos + extra


def test_candidatos_con_www_e_idna() -> None:
    assert candidatos("https://www.españa.es/", ["https://españa.es/s.xml"], None)[:2] == [
        "https://xn--espaa-rta.es/s.xml",
        "https://www.xn--espaa-rta.es/sitemap.xml",
    ]


@pytest.mark.parametrize(
    "base", ["", "tienda.es", "ftp://tienda.es", "https://u:p@tienda.es", "https://[::1", "http://"]
)
def test_candidatos_de_una_base_no_valida(base: str) -> None:
    assert candidatos(base, ["https://tienda.es/sitemap.xml"], Plataforma.MAGENTO) == []


def test_candidatos_como_mucho_max_declarados() -> None:
    declarados = [f"https://tienda.es/s{i}.xml" for i in range(MAX_DECLARADOS * 50)]
    resultado = candidatos("https://tienda.es", declarados, None)
    assert resultado[:MAX_DECLARADOS] == declarados[:MAX_DECLARADOS]
    assert len(resultado) == MAX_DECLARADOS + 3


# --- de_producto -----------------------------------------------------------------------------


def test_de_producto_en_yoast() -> None:
    assert de_producto(_fixture("yoast_indice.xml")) == [
        "https://tienda.es/product-sitemap.xml",
        "https://tienda.es/product-sitemap2.xml",
    ]


def test_de_producto_en_el_sitemap_de_wordpress() -> None:
    assert de_producto(_fixture("wordpress_indice.xml")) == [
        "https://tienda.es/wp-sitemap-posts-product-1.xml",
        "https://tienda.es/wp-sitemap-posts-product-2.xml",
    ]


def test_de_producto_en_shopify() -> None:
    assert de_producto(_fixture("shopify_indice.xml")) == [
        "https://tienda.es/sitemap_products_1.xml?from=7012345678901&to=7098765432109"
    ]


def test_de_producto_sin_distinguir_da_todos() -> None:
    assert de_producto(_fixture("prestashop_indice.xml")) == [
        "https://tienda.es/1_es_0_sitemap.xml",
        "https://tienda.es/1_es_1_sitemap.xml",
        "https://tienda.es/1_pt_0_sitemap.xml",
    ]


@pytest.mark.parametrize(
    ("url", "de_productos"),
    [
        ("https://t.es/sitemap-products-3.xml", True),
        ("https://t.es/productos-sitemap.xml", True),
        ("https://t.es/item-sitemap.xml", True),
        ("https://t.es/sitemap/articulos.xml", True),
        ("https://t.es/art%C3%ADculos-sitemap.xml", True),
        ("https://t.es/catalog/sitemap.xml", True),
        ("https://t.es/es/sitemap_products_1.xml?from=1&to=9", True),
        ("https://t.es/product_brand-sitemap.xml", False),
        ("https://t.es/product_cat-sitemap.xml", False),
        ("https://t.es/image-sitemap.xml", False),
        ("https://t.es/sitemap_blogs_1.xml", False),
        ("https://t.es/categorias-sitemap.xml", False),
        ("https://t.es/etiquetas.xml", False),
        ("https://t.es/wp-sitemap-posts-post-1.xml", False),
        ("https://t.es/sitemap.xml?type=product_cat", False),
        # De la consulta cuentan los valores, no los nombres: `page` es la paginación
        # (BigCommerce: `xmlsitemap.php?type=products&page=1`), no un sitemap de páginas.
        ("https://t.es/xmlsitemap.php?type=products&page=2", True),
        ("https://t.es/sitemap.php?tipo=productos&pagina=1", True),
        ("https://t.es/xmlsitemap.php?type=pages&page=1", False),
        ("https://t.es/xmlsitemap.php?type=categories&page=1", False),
    ],
)
def test_de_producto_por_el_nombre(url: str, de_productos: bool) -> None:
    neutro = "https://t.es/sitemap-1.xml"
    assert de_producto(_indice(url, neutro)) == ([url] if de_productos else [neutro])


def test_de_producto_sin_ninguno_de_productos_ni_neutro() -> None:
    indice = _indice("https://t.es/page-sitemap.xml", "https://t.es/post-sitemap.xml")
    assert de_producto(indice) == []


def test_de_producto_de_un_urlset_no_da_nada() -> None:
    assert de_producto(_fixture("yoast_productos.xml")) == []


def test_de_producto_sin_duplicados_y_con_el_tope_de_ficheros() -> None:
    urls = [f"https://t.es/product-sitemap{i}.xml" for i in range(MAX_FICHEROS_SITEMAP * 3)]
    resultado = de_producto(_indice(urls[0], *urls))
    assert resultado == urls[:MAX_FICHEROS_SITEMAP]


# --- de_la_tienda ------------------------------------------------------------------------------


def test_de_la_tienda_filtra_otros_hosts_y_normaliza() -> None:
    urls = [
        "https://tienda.es/producto/a/?utm_source=x",
        "https://cdn.shopify.com/s/files/sitemap.xml",
        "https://www.tienda.es/producto/b/#ficha",
        "https://tienda.es/producto/a/",
        "https://tienda.es@otra.example/producto/c/",
        "http://tienda.es:8080/producto/d/",
        "/producto/relativo/",
    ]
    assert de_la_tienda(urls, "https://tienda.es/") == [
        "https://tienda.es/producto/a/",
        "https://www.tienda.es/producto/b/",
    ]


def test_de_la_tienda_con_base_no_valida() -> None:
    assert de_la_tienda(["https://tienda.es/a/"], "no es una url") == []


# --- palabras_url ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        (
            "https://tienda.es/producto/faro-delantero-izquierdo-1k0941005-golf-v/",
            "producto faro delantero izquierdo 1k0941005 golf v",
        ),
        ("https://tienda.es/faro-1k0941005-golf", "faro 1k0941005 golf"),
        ("https://tienda.es/es/faros/123-faro-golf.html", "es faros faro golf"),
        (
            "https://tienda.es/producto/caudal%C3%ADmetro-ford-focus/",
            "producto caudalimetro ford focus",
        ),
        ("https://tienda.es/Productos/%C3%91AND%C3%9A_Rojo", "productos nandu rojo"),
        ("https://tienda.es/p/123456?id=99&nombre=faro", "p"),
        ("https://tienda.es/pastillas-bosch-0986494104/", "pastillas bosch 0986494104"),
        (
            "https://tienda.es/products/kit-distribucion-gates-k015603xs",
            "products kit distribucion gates k015603xs",
        ),
        ("https://tienda.es/faro-faro-golf/faro", "faro golf"),
        ("https://tienda.es/", ""),
        ("", ""),
        ("https://[::1", ""),
    ],
)
def test_palabras_url(url: str, esperado: str) -> None:
    assert palabras_url(url) == esperado


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        # Un número dentro del slug es el modelo o la medida, no un id: la consulta lo pide.
        ("https://tienda.es/pastillas-freno-peugeot-308.html", "pastillas freno peugeot 308"),
        (
            "https://tienda.es/producto/neumatico-michelin-205-55-r16/",
            "producto neumatico michelin 205 55 r16",
        ),
        ("https://tienda.es/filtro-aceite-mann-w-712-75", "filtro aceite mann w 712 75"),
        # Los ids siguen fuera: un tramo que es solo un número y el id delante del slug.
        (
            "https://tienda.es/es/frenos/123-pastillas-peugeot-308.html",
            "es frenos pastillas peugeot 308",
        ),
        ("https://tienda.es/es/12-345-faro-golf.html", "es faro golf"),
        ("https://tienda.es/catalog/product/view/id/4521/", "catalog product view id"),
        ("https://tienda.es/p/123456/faro-golf", "p faro golf"),
    ],
)
def test_palabras_url_conserva_los_numeros_del_modelo(url: str, esperado: str) -> None:
    assert palabras_url(url) == esperado


def test_palabras_url_une_la_referencia_escrita_por_grupos() -> None:
    palabras = palabras_url("https://tienda.es/faro-1k0-941-005-golf/").split()
    assert "1k0941005" in palabras
    assert {"faro", "golf"} <= set(palabras)


def test_palabras_url_como_mucho_max_caracteres_sin_cortar_palabras() -> None:
    todas = [f"pal{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(200)]
    for barra in ("", "/"):
        palabras = palabras_url("https://tienda.es/" + "-".join(todas) + barra)
        assert MAX_PALABRAS_URL - 6 <= len(palabras) <= MAX_PALABRAS_URL
        assert palabras.split() == todas[: len(palabras.split())]


def test_palabras_url_hostil() -> None:
    inicio = time.perf_counter()
    for url in (
        "https://tienda.es/" + "a-" * 1_000_000,
        "https://tienda.es/" + "%" * 1_000_000,
        "https://tienda.es/" + "1 " * 1_000_000,
        "https://tienda.es/" + "%C3%A1" * 300_000,
    ):
        assert len(palabras_url(url)) <= MAX_PALABRAS_URL
    assert time.perf_counter() - inicio < 2


def test_palabras_url_y_con_palabras_miran_una_ruta_acotada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coste acotado por URL: en 10 sitemaps de 20 MiB caben 100 000 URL de 2000 caracteres, y
    analizar cada ruta entera (normalizar y buscar referencias) costaba unos 23 s de CPU por
    fichero (revisión adversarial). Las palabras no pasan de `MAX_PALABRAS_URL`: no hace falta
    mirar más ruta que eso. Sin cronómetro: se mira el tamaño de lo que se analiza."""
    analizados: list[int] = []

    def espia(funcion: Callable[[str], T]) -> Callable[[str], T]:
        def envuelta(texto: str) -> T:
            analizados.append(len(texto))
            return funcion(texto)

        return envuelta

    monkeypatch.setattr(modulo_sitemap, "normalizar_titulo", espia(normalizar_titulo))
    monkeypatch.setattr(modulo_sitemap, "referencias_en", espia(referencias_en))
    url = "https://tienda.es/1" + "-".join(["1k0"] * 480)
    assert len(url) > 1_900
    palabras_url(url)
    con_palabras([url])
    assert analizados and max(analizados) <= MAX_RUTA_PALABRAS
    # Una ruta normal se sigue leyendo entera.
    assert palabras_url("https://tienda.es/producto/faro-golf-1k0-941-005/") == (
        "producto faro golf 1k0 941 005 1k0941005"
    )


def test_ruta_larga_conserva_el_slug_del_final() -> None:
    """El slug va al final de la ruta: si hay que recortar, se pierden las categorías del
    principio y no el producto (revisión adversarial). Tampoco queda un tramo cortado a medias."""
    categorias = "/".join(f"categoria-{n}" for n in range(40))
    url = f"https://tienda.es/{categorias}/faro-delantero-golf-vii/"
    assert len(urlsplit(url).path) > MAX_RUTA_PALABRAS
    palabras = palabras_url(url).split()
    assert {"faro", "delantero", "golf", "vii"} <= set(palabras)
    assert "categoria" in palabras
    assert "goria" not in palabras and "egoria" not in palabras
    assert con_palabras([url]) == 1.0


# --- con_palabras -----------------------------------------------------------------------------


def test_con_palabras_mira_el_slug() -> None:
    urls = [
        "https://tienda.es/producto/faro-golf/",
        "https://tienda.es/p/123456",
        "https://tienda.es/products/kit-distribucion-gates",
        "https://tienda.es/catalog/product/view/id/4521/",
        "https://tienda.es/shop/product/123456",
        "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        "https://tienda.es/es/faros/123-faro-golf.html",
        "https://tienda.es/producto/123456.html",
    ]
    assert con_palabras(urls) == pytest.approx(4 / 8)


def test_con_palabras_vacio() -> None:
    assert con_palabras([]) == 0.0


def test_con_palabras_de_los_sitemaps_reales() -> None:
    assert con_palabras(_urls(_fixture("magento_sitemap.xml"))) == pytest.approx(2 / 4)
    assert con_palabras(_urls(_fixture("yoast_productos.xml"))) == pytest.approx(3 / 4)
