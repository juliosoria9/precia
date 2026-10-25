"""Catálogo por `sitemap.xml`: lector propio, candidatos, sitemaps de producto y palabras del slug.

Los sitemaps son de una web no confiable: el lector es lineal, rechaza DTD y entidades, tiene
topes y nunca lanza una excepción. Los casos hostiles se cronometran.
"""

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
    PRIORIDAD_NEUTRO,
    PRIORIDAD_PRODUCTO,
    PRIORIDAD_SECUNDARIO,
    EntradaSitemap,
    Sitemap,
    candidatos,
    con_palabras,
    de_la_tienda,
    de_producto,
    es_listado,
    hijos_de_la_consulta,
    hijos_de_producto,
    leer_sitemap,
    palabras_url,
    prioridad,
    sitemaps_de_la_tienda,
    url_de_sitemap,
)
from precia.navegador.robots import analizar_robots
from tests.cronometro import segundos_de_cpu

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fuentes" / "sitemaps"
REALES = Path(__file__).parent.parent / "fixtures" / "reales"
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


def _cronometrar(texto: str, maximo_s: float = 8) -> Sitemap:
    """Lee el sitemap con un tope de `maximo_s` segundos de CPU (mejor de 3 intentos, bug 6).

    Leer 20 MB cuesta hasta 1,8 s de CPU en la suite completa: el tope deja más de 4 veces eso,
    y un coste cuadrático sobre 20 MB no acabaría.
    """
    leidos: list[Sitemap] = []
    assert segundos_de_cpu(lambda: leidos.append(leer_sitemap(texto)), maximo_s) < maximo_s
    return leidos[-1]


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


def _declarados(fixture: str) -> tuple[str, ...]:
    texto = (REALES / fixture).read_bytes().decode("utf-8")
    return analizar_robots(texto, "precia").sitemaps


def test_candidatos_de_coolmod_el_de_productos_primero() -> None:
    # Robots.txt real de Coolmod (ronda 1 de la campaña, recortado): declara primero
    # `/sitemap.xml`, que solo trae categorías, y su `sitemap_products.xml` nunca se leía.
    t = "https://www.coolmod.com"
    assert candidatos(f"{t}/", _declarados("coolmod_robots_sitemaps.txt"), None) == [
        f"{t}/sitemap_products.xml",
        f"{t}/sitemap.xml",
        f"{t}/sitemap2.xml",
        f"{t}/sitemap_contents.xml",
        f"{t}/sitemap_promotions.xml",
        f"{t}/sitemap_index.xml",
        f"{t}/wp-sitemap.xml",
    ]


def test_candidatos_de_pccomponentes_el_de_articulos_primero() -> None:
    t = "https://www.pccomponentes.com"
    assert candidatos(f"{t}/", _declarados("pccomponentes_robots_sitemaps.txt"), None) == [
        f"{t}/sitemap/sitemap_articles.xml",
        f"{t}/sitemap/sitemap.xml",
        f"{t}/sitemap.xml",
        f"{t}/sitemap_index.xml",
        f"{t}/wp-sitemap.xml",
    ]


def test_candidatos_como_mucho_max_declarados() -> None:
    declarados = [f"https://tienda.es/s{i}.xml" for i in range(MAX_DECLARADOS * 50)]
    resultado = candidatos("https://tienda.es", declarados, None)
    assert resultado[:MAX_DECLARADOS] == declarados[:MAX_DECLARADOS]
    assert len(resultado) == MAX_DECLARADOS + 3


# --- Sitemap declarado en un subdominio de la tienda (causa R4-B de la campaña) ---------------


def test_candidatos_de_xtralife_el_declarado_en_su_subdominio_static() -> None:
    # Robots.txt real de Xtralife (ronda 4, recortado): su único `Sitemap:` está en
    # `static.xtralife.com`. Se descartaba y solo quedaban las rutas habituales, que dan 403.
    t = "https://www.xtralife.com"
    declarados = _declarados("xtralife_robots_sitemap_subdominio.txt")
    assert declarados == ("https://static.xtralife.com/sitemap.xml",)
    assert candidatos(f"{t}/", declarados, None) == [
        "https://static.xtralife.com/sitemap.xml",
        f"{t}/sitemap.xml",
        f"{t}/sitemap_index.xml",
        f"{t}/wp-sitemap.xml",
    ]


@pytest.mark.parametrize(
    "declarado",
    [
        "https://evil-xtralife.com/sitemap.xml",
        "https://xtralife.com.attacker.net/sitemap.xml",
        "https://xtralife.com.evil.net/sitemap.xml",
        "https://otra-tienda.es/sitemap.xml",
        "https://usuario@static.xtralife.com/sitemap.xml",
        "https://usuario:clave@static.xtralife.com/sitemap.xml",
        "https://static.xtralife.com:8443/sitemap.xml",
        "ftp://static.xtralife.com/sitemap.xml",
        "//static.xtralife.com/sitemap.xml",
        "https://xtralife.com@evil.net/sitemap.xml",
    ],
)
def test_candidatos_no_admite_otros_dominios_aunque_se_parezcan(declarado: str) -> None:
    t = "https://www.xtralife.com"
    assert candidatos(t, [declarado], None) == [
        f"{t}/sitemap.xml",
        f"{t}/sitemap_index.xml",
        f"{t}/wp-sitemap.xml",
    ]


def test_un_subdominio_solo_vale_debajo_del_dominio_tal_cual_se_dio() -> None:
    # Sin lista de sufijos públicos (sería una dependencia nueva): la tienda
    # `compraonline.alcampo.es` solo cubre `*.compraonline.alcampo.es`, nunca `static.alcampo.es`.
    base = "https://compraonline.alcampo.es"
    assert url_de_sitemap("https://static.alcampo.es/sitemap.xml", base) is None
    assert (
        url_de_sitemap("https://static.compraonline.alcampo.es/sitemap.xml", base)
        == "https://static.compraonline.alcampo.es/sitemap.xml"
    )
    # Con `www.` en la tienda o en el subdominio, igual que en `url_de_la_tienda`.
    assert (
        url_de_sitemap("https://www.static.xtralife.com/s.xml#x", "https://www.xtralife.com")
        == "https://www.static.xtralife.com/s.xml"
    )
    assert url_de_sitemap("https://xtralife.com/s.xml", "https://www.xtralife.com") == (
        "https://xtralife.com/s.xml"
    )
    assert url_de_sitemap("/relativo.xml", "https://www.xtralife.com") is None


def test_el_subdominio_solo_vale_para_ficheros_sitemap_no_para_fichas() -> None:
    base = "https://www.xtralife.com"
    urls = [
        "https://static.xtralife.com/sitemap-productos.xml",
        "https://www.xtralife.com/sitemap-juegos.xml",
        "https://evil-xtralife.com/sitemap.xml",
        "https://static.xtralife.com.evil.net/sitemap.xml",
    ]
    assert sitemaps_de_la_tienda(urls, base) == urls[:2]
    # El filtro de las URL que se guardan en el catálogo sigue siendo el estricto.
    assert de_la_tienda(urls, base) == [urls[1]]


def test_candidatos_con_el_sitemap_en_el_mismo_host_no_cambian() -> None:
    # Regresión: una tienda de recambios que declara su sitemap en su propio host.
    t = "https://www.recambios-prueba.es"
    assert candidatos(t, [f"{t}/sitemap_index.xml"], None) == [
        f"{t}/sitemap_index.xml",
        f"{t}/sitemap.xml",
        f"{t}/wp-sitemap.xml",
    ]


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
        # PcComponentes declara así su índice de fichas (causa C3 de la campaña multi-sector).
        ("https://t.es/sitemap/sitemap_articles.xml", True),
        ("https://t.es/sitemap-cdl-subcolecciones.xml.gz", False),
    ],
)
def test_de_producto_por_el_nombre(url: str, de_productos: bool) -> None:
    # El de producto va delante del neutro; uno que no es de producto no se sigue.
    neutro = "https://t.es/sitemap-1.xml"
    assert de_producto(_indice(neutro, url)) == ([url, neutro] if de_productos else [neutro])


def test_de_producto_no_descarta_los_neutros_si_hay_alguno_de_producto() -> None:
    # Índice de artículos de PcComponentes (ronda 1 de la campaña): cientos de feeds de categoría
    # y solo dos que casan por accidente con «articulos» y «productos». Antes se perdían todos
    # los demás y el catálogo se quedaba en 477 URL de limpieza.
    feed = "https://www.pccomponentes.com/async-feed/sitemap-{}-es.xml"
    portatiles, limpieza, monitores, sanitarios = (
        feed.format(nombre)
        for nombre in (
            "portatiles",
            "articulos-de-limpieza",
            "monitores",
            "esterilizadores-y-productos-sanitarios",
        )
    )
    indice = _indice(portatiles, limpieza, monitores, sanitarios)
    assert de_producto(indice) == [limpieza, sanitarios, portatiles, monitores]


def test_de_producto_deja_al_final_landings_promociones_y_comparativas() -> None:
    # Nombres reales de Casa del Libro, PcComponentes y Coolmod (ronda 1): las landings,
    # temáticas, comparativas, promociones y contenidos no se excluyen (pueden enlazar fichas),
    # pero van detrás de los neutros; las subcolecciones son colecciones.
    t = "https://www.tienda.es"
    landings = f"{t}/sitemap-cdl-landings.xml.gz"
    tematicas = f"{t}/sitemap-cdl-libros-tematicas.xml"
    novedades = f"{t}/sitemap-novedades.xml"
    comparativas = f"{t}/sitemap/comparatives.xml"
    black_friday = f"{t}/async-feed/black-friday-sitemap-es.xml"
    subcolecciones = f"{t}/sitemap-cdl-subcolecciones.xml.gz"
    papeleria = f"{t}/sitemap-cdl-papeleria.xml.gz"
    promociones = f"{t}/sitemap_promotions.xml"
    contenidos = f"{t}/sitemap_contents.xml"
    indice = _indice(
        landings,
        tematicas,
        novedades,
        comparativas,
        black_friday,
        subcolecciones,
        papeleria,
        promociones,
        contenidos,
    )
    assert de_producto(indice) == [
        novedades,
        papeleria,
        landings,
        tematicas,
        comparativas,
        black_friday,
        promociones,
        contenidos,
    ]


def test_de_producto_el_tope_es_compartido_entre_producto_y_neutros() -> None:
    productos = [f"https://t.es/product-sitemap{i}.xml" for i in range(3)]
    neutros = [f"https://t.es/sitemap-{i}.xml" for i in range(MAX_FICHEROS_SITEMAP * 2)]
    resultado = de_producto(_indice(*neutros, *productos))
    assert resultado == productos + neutros[: MAX_FICHEROS_SITEMAP - 3]


def test_de_producto_sin_ninguno_de_productos_ni_neutro() -> None:
    indice = _indice("https://t.es/page-sitemap.xml", "https://t.es/post-sitemap.xml")
    assert de_producto(indice) == []


def test_de_producto_de_un_urlset_no_da_nada() -> None:
    assert de_producto(_fixture("yoast_productos.xml")) == []


def test_de_producto_sin_duplicados_y_con_el_tope_de_ficheros() -> None:
    urls = [f"https://t.es/product-sitemap{i}.xml" for i in range(MAX_FICHEROS_SITEMAP * 3)]
    resultado = de_producto(_indice(urls[0], *urls))
    assert resultado == urls[:MAX_FICHEROS_SITEMAP]


def test_hijos_de_producto_da_todos_sin_el_tope() -> None:
    urls = [f"https://t.es/product-sitemap{i}.xml" for i in range(MAX_FICHEROS_SITEMAP * 3)]
    neutro = "https://t.es/sitemap-1.xml"
    indice = _indice(neutro, "https://t.es/page-sitemap.xml", urls[0], *urls)
    assert hijos_de_producto(indice) == [*urls, neutro]
    assert de_producto(indice) == hijos_de_producto(indice)[:MAX_FICHEROS_SITEMAP]
    assert hijos_de_producto(_fixture("yoast_productos.xml")) == []


# --- prioridad: nombres compuestos (R2-C14b) ---------------------------------------------------


def _reales(nombre: str) -> Sitemap:
    return leer_sitemap((REALES / nombre).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        # MediaMarkt: el índice general solo tiene nombres compuestos. Las fichas son
        # `productdetailspages`; las páginas de marca, campañas, contenidos, listados e imágenes,
        # secundarias (la coincidencia parcial nunca excluye).
        ("https://t.es/sitemaps/sitemap-productdetailspages-000.xml", PRIORIDAD_PRODUCTO),
        ("https://t.es/sitemaps/sitemap-brandpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-campaignpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-contentpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-marketpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-productlistpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-productlistfacetpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-productimages-000.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-searchlistpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/wpproducts-sitemap.xml", PRIORIDAD_PRODUCTO),
        # Negativos: una palabra corta dentro de otra no dice nada (PcComponentes).
        ("https://t.es/async-feed/sitemap-mundo-videojuegos-es.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/async-feed/sitemap-videoconsolas-ps5-es.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/async-feed/sitemap-dispositivos-postprocesado-3d-es.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/async-feed/sitemap-juguetes-educativos-es.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/async-feed/sitemap-portatiles-es.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/sitemap-vintage.xml", PRIORIDAD_NEUTRO),
        # «product» va dentro de «reproductores», pero no al principio ni al final: no cuenta.
        ("https://t.es/async-feed/sitemap-reproductores-dvd-divx-es.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/async-feed/sitemap-reproductores-mp3-mp4-es.xml", PRIORIDAD_NEUTRO),
        # Las palabras enteras siguen como antes: la exclusión gana.
        ("https://t.es/product_cat-sitemap.xml", None),
        ("https://t.es/sitemap-brand-pages.xml", None),
    ],
)
def test_prioridad_de_los_nombres_compuestos(url: str, esperada: int | None) -> None:
    assert prioridad(url) == esperada


def test_de_producto_en_el_indice_real_de_mediamarkt() -> None:
    # R2-C14b: todos sus hijos salían neutros y se leían primero las páginas de marca, campañas,
    # contenidos y mercados; de las fichas, solo unas pocas.
    indice = _reales("mediamarkt_sitemap_indice.xml")
    ficha = "https://www.mediamarkt.es/sitemaps/sitemap-productdetailspages-{:03d}.xml"
    assert de_producto(indice) == [ficha.format(n) for n in range(MAX_FICHEROS_SITEMAP)]


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        # Causa R4-C (Hawkers): «categories» va pegado dentro de la palabra compuesta. El
        # sitemap de categorías filtradas salía neutro y sus listados entraban en el catálogo.
        (
            "https://www.hawkersco.com/sitemap-customsitemap-filteredcategoriesurl"
            "-HawkersWorldwide-aggregated.xml",
            None,
        ),
        ("https://t.es/sitemap-productcategories.xml", None),
        ("https://t.es/sitemap-subcategorias-es.xml", None),
        ("https://t.es/sitemap-producttaxonomy.xml", None),
        # Solo la familia de categorías excluye dentro de una compuesta: imágenes, páginas y
        # marcas pegadas siguen siendo secundarias (MediaMarkt) y las fichas, de producto.
        ("https://t.es/sitemaps/sitemap-productimages-000.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-brandpages.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-productdetailspages-000.xml", PRIORIDAD_PRODUCTO),
    ],
)
def test_prioridad_excluye_las_categorias_dentro_de_una_compuesta(
    url: str, esperada: int | None
) -> None:
    assert prioridad(url) == esperada


def test_de_producto_en_el_indice_real_de_hawkers() -> None:
    # R4-C: de los tres hijos solo se sigue el de productos; los dos de categorías, no.
    indice = _reales("hawkers_sitemap_indice.xml")
    assert hijos_de_producto(indice) == ["https://www.hawkersco.com/sitemap_1.xml"]


def test_de_producto_de_woocommerce_y_prestashop_no_cambia() -> None:
    # Recambios (caso de referencia): mismos hijos y mismo orden que antes de R2-C14b.
    t = "https://t.es"
    indice = _indice(
        f"{t}/post-sitemap.xml",
        f"{t}/product_cat-sitemap.xml",
        f"{t}/product-sitemap.xml",
        f"{t}/1_index_sitemap.xml",
        f"{t}/product_brand-sitemap.xml",
        f"{t}/product-sitemap2.xml",
        f"{t}/1_es_0_sitemap.xml",
    )
    assert de_producto(indice) == [
        f"{t}/product-sitemap.xml",
        f"{t}/product-sitemap2.xml",
        f"{t}/1_index_sitemap.xml",
        f"{t}/1_es_0_sitemap.xml",
    ]


# --- hijos_de_la_consulta (R2-C14b) ------------------------------------------------------------


def test_hijos_de_la_consulta_en_el_indice_real_de_pccomponentes() -> None:
    # 424 feeds por categoría: el catálogo leía los 10 primeros (placas base, procesadores, RAM…)
    # y «portátil 16gb» solo encontraba módulos de RAM. El feed de portátiles va primero: el que
    # tiene menos palabras ajenas a la consulta; detrás, a igualdad, en el orden del índice.
    hijos = hijos_de_producto(_reales("pccomponentes_sitemap_indice_articulos.xml"))
    feed = "https://www.pccomponentes.com/async-feed/sitemap-{}-es.xml"
    assert feed.format("portatiles") not in de_producto(
        _reales("pccomponentes_sitemap_indice_articulos.xml")
    )
    assert hijos_de_la_consulta(hijos, "Portátil 16GB", maximo=2) == [
        feed.format("portatiles"),
        feed.format("accesorios-portatiles"),
    ]
    assert hijos_de_la_consulta(hijos, "portátiles", maximo=1) == [feed.format("portatiles")]
    assert hijos_de_la_consulta(hijos, "ratón inalámbrico", maximo=3) == [feed.format("ratones")]


def test_hijos_de_la_consulta_sin_ninguno_que_case() -> None:
    hijos = hijos_de_producto(_reales("pccomponentes_sitemap_indice_articulos.xml"))
    assert hijos_de_la_consulta(hijos, "zapatillas running", maximo=2) == []
    # Palabras de menos de 4 letras o solo cifras no cuentan: «pc» y «3d» casan con todo.
    assert hijos_de_la_consulta(hijos, "pc 3d es", maximo=2) == []
    assert hijos_de_la_consulta(hijos, "", maximo=2) == []
    assert hijos_de_la_consulta(hijos, "portátil", maximo=0) == []


def test_hijos_de_la_consulta_no_da_los_que_no_son_de_producto() -> None:
    hijos = ["https://t.es/sitemap-categorias-faros.xml", "https://t.es/sitemap-faros.xml"]
    assert hijos_de_la_consulta(hijos, "faro golf", maximo=2) == ["https://t.es/sitemap-faros.xml"]


def test_hijos_de_la_consulta_con_miles_de_hijos_es_rapido() -> None:
    hijos = [f"https://t.es/async-feed/sitemap-categoria-{n}-es.xml" for n in range(5_000)]
    consulta = "x" * 5_000 + " portatil"
    assert hijos_de_la_consulta(hijos, consulta, maximo=2) == []
    assert segundos_de_cpu(lambda: hijos_de_la_consulta(hijos, consulta, maximo=2), 1) < 1


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
    urls = (
        "https://tienda.es/" + "a-" * 1_000_000,
        "https://tienda.es/" + "%" * 1_000_000,
        "https://tienda.es/" + "1 " * 1_000_000,
        "https://tienda.es/" + "%C3%A1" * 300_000,
    )

    def comprobar() -> None:
        for url in urls:
            assert len(palabras_url(url)) <= MAX_PALABRAS_URL

    assert segundos_de_cpu(comprobar, 2) < 2


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


# --- es_listado ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        # Las páginas de marca del sitemap de MediaMarkt (ronda 2 de la campaña, R2-C14a).
        "https://www.mediamarkt.es/es/brand/jvc/auriculares-bluetooth",
        "https://www.mediamarkt.es/es/brand/cellular-line/auriculares/auriculares-bluetooth",
        "https://tienda.es/marca/valeo/faro-golf",
        "https://tienda.es/categoria/faros/",
        "https://tienda.es/Categor%C3%ADas/faros",
        "https://tienda.es/blog/como-cambiar-un-faro",
        "https://tienda.es/tag/faro",
        "https://tienda.es/brand",
    ],
)
def test_es_listado_con_un_tramo_de_marca_categoria_o_blog(url: str) -> None:
    assert es_listado(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.mediamarkt.es/es/product/_-jvc-ha-a3t-bu-negro-auriculares-de-boton-"
        "bluetooth-jvc-intraurales-106699129.html",
        "https://tienda.es/p/faro-golf",
        # Solo cuenta el tramo entero: «marca» o «blog» dentro del slug no lo hacen listado.
        "https://tienda.es/p/marcador-laser-blog-marca",
        "https://tienda.es/marcas-de-faros-golf",
        # Shopify pone las fichas dentro de la colección: el tramo de producto gana.
        "https://tienda.es/collections/faros/products/faro-golf",
        "https://tienda.es/",
        "no es una url",
    ],
)
def test_no_es_listado(url: str) -> None:
    assert not es_listado(url)


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


def test_con_palabras_salta_el_marcador_corto_del_final() -> None:
    # Campaña multi-sector (causa R4-A): Carrefour pone sus fichas en `/<slug>/<EAN>/p`. El tramo
    # final «p» se tomaba como slug, el sitemap de productos daba 0 % legible y se descartaba.
    assert con_palabras(_urls(_reales("carrefour_sitemap_productos_ean_p.xml"))) == 1.0
    assert con_palabras(["https://t.es/faro-izquierdo-golf/8435437104533/p/"]) == 1.0


@pytest.mark.parametrize(
    "url",
    [
        # El marcador corto solo se salta al final de la ruta: en medio, lo que va antes es una
        # categoría y la ficha sigue identificada solo por un número.
        "https://t.es/p/123456",
        "https://t.es/es/p/123456",
        "https://t.es/categoria-larga/p/123456",
        "https://t.es/ropa-hombre/p/123456",
        "https://t.es/x/frenos-delanteros/id/4521",
        # Quitado el marcador, queda una sola palabra.
        "https://t.es/tienda/p",
    ],
)
def test_con_palabras_sin_slug_legible_aunque_haya_marcador(url: str) -> None:
    assert con_palabras([url]) == 0.0


def test_los_sitemaps_de_landings_seo_van_detras() -> None:
    # Campaña multi-sector (causa R2-C10): Brico Dépôt declara primero `keyword.xml` (landings SEO
    # `/v/…` con un `ItemList`) y detrás sus sitemaps de verdad; el catálogo se quedaba con las
    # landings.
    t = "https://www.bricodepot.es"
    landings = f"{t}/v/nc/sitemap/keyword.xml"
    for url in (landings, f"{t}/keywords-sitemap.xml", f"{t}/sitemap-seo.xml"):
        assert prioridad(url) == PRIORIDAD_SECUNDARIO
    assert prioridad(f"{t}/sitemap-seo-products.xml") == PRIORIDAD_PRODUCTO
    declarados = [
        landings,
        f"{t}/media/sitemap/sitemap_es.xml",
        f"{t}/media/sitemap/sitemap_ib.xml",
    ]
    assert candidatos(f"{t}/", declarados, None)[:3] == [*declarados[1:], landings]


# --- Causa R3-C de la campaña: las carpetas no hacen secundario un sitemap ---------------------


def test_candidatos_de_el_corte_ingles_la_carpeta_contents_no_hunde_el_de_productos() -> None:
    # Robots.txt real de El Corte Inglés (ronda 3, recortado). Su sitemap de productos está en
    # `…/contents/sitemap-variants/sitemap.xml.gz`: la carpeta «contents» (la del CMS) lo hacía
    # secundario y se leía antes el de entradas de teatro, cuyas fichas dan 404.
    t = "https://www.elcorteingles.es"
    declarados = _declarados("elcorteingles_robots_sitemaps.txt")
    assert len(declarados) == 5
    assert candidatos(f"{t}/", declarados, None) == [
        *declarados,
        f"{t}/sitemap.xml",
        f"{t}/sitemap_index.xml",
        f"{t}/wp-sitemap.xml",
    ]


def test_candidatos_de_carrefour_en_el_orden_del_robots() -> None:
    # Robots.txt real de Carrefour (ronda 3, recortado): los cuatro son neutros y manda el orden
    # declarado; que el primero (`_includes`, páginas informativas) falle lo resuelve el
    # configurador probando el siguiente.
    t = "https://www.carrefour.es"
    declarados = _declarados("carrefour_robots_sitemaps.txt")
    assert len(declarados) == 4
    assert all(prioridad(d) == PRIORIDAD_NEUTRO for d in declarados)
    assert candidatos(f"{t}/", declarados, None)[:4] == list(declarados)


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        # Las palabras secundarias de una carpeta no cuentan: es la ruta del CMS o del CDN.
        (
            "https://t.es/sgfm/statics-front/eci/contents/sitemap-variants/sitemap.xml.gz",
            PRIORIDAD_NEUTRO,
        ),
        ("https://t.es/contenidos/sitemap-index.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/seo/sitemap.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/landings/feeds/sitemap-1.xml", PRIORIDAD_NEUTRO),
        ("https://t.es/brandpages/sitemap.xml", PRIORIDAD_NEUTRO),
        # En el nombre del fichero o en los valores de la query, sí.
        ("https://t.es/sitemap_contents.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/contents/sitemap-contents.xml", PRIORIDAD_SECUNDARIO),
        ("https://t.es/xmlsitemap.php?type=landings&page=2", PRIORIDAD_SECUNDARIO),
        ("https://t.es/sitemaps/sitemap-brandpages.xml", PRIORIDAD_SECUNDARIO),
        # Las de producto y las de exclusión siguen contando en toda la ruta.
        ("https://www.tiendanimal.es/articulos/sitemap_index.xml", PRIORIDAD_PRODUCTO),
        ("https://t.es/catalog/sitemap.xml", PRIORIDAD_PRODUCTO),
        ("https://t.es/contents/products/sitemap.xml", PRIORIDAD_PRODUCTO),
        ("https://t.es/blog/sitemap.xml", None),
        ("https://t.es/seo/blog/sitemap.xml", None),
    ],
)
def test_prioridad_las_secundarias_solo_en_el_nombre(url: str, esperada: int | None) -> None:
    assert prioridad(url) == esperada
