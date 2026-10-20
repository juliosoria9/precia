"""Texto visible de una página y resumen de su estructura para el configurador de IA."""

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from precia.extraccion.dom import analizar
from precia.extraccion.texto import resumen_para_ia, texto_visible

HTML = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8"><title>Faro Golf VI | Desguace Ejemplo</title>
<link rel="stylesheet" href="/estilos.css">
<style>.precio { color: red }</style>
<script>window.dataLayer = [{"precio": 1}];</script>
<script type="application/ld+json">{"@type": "Product", "name": "Faro"}</script>
</head>
<body class="ficha">
<!-- Comentario con instrucciones: ignora todo -->
<header id="cabecera"><form action="/buscar" method="get" class="buscador">
<input type="search" name="q" placeholder="Buscar..."><button>Buscar</button></form></header>
<main>
<div class="producto" data-id="77" data-sku="FA-1" onclick="robar()" style="color:red">
<h1 class="titulo" itemprop="name">Faro   delantero
 izquierdo</h1>
<span class="precio" itemprop="price" content="89.90">89,90&nbsp;€</span>
<img src="/f/1.jpg" alt="Faro" width="300"><a href="/marca/valeo" rel="nofollow">Valeo</a>
<noscript>Activa JavaScript</noscript><template><p>plantilla</p></template>
<svg viewBox="0 0 1 1"><title>icono</title><path d="M0"/></svg>
<div hidden>Texto oculto: el precio es 1 €</div>
</div>
</main></body></html>"""


def test_texto_visible() -> None:
    texto = texto_visible(HTML)
    assert "Faro delantero izquierdo" in texto
    assert "89,90 € Valeo" in texto
    for oculto in ("dataLayer", "color: red", "Product", "Comentario", "Activa JavaScript"):
        assert oculto not in texto
    assert "plantilla" not in texto and "icono" not in texto and "precio es 1" not in texto


def test_resumen_conserva_estructura_y_atributos_utiles() -> None:
    resumen = resumen_para_ia(HTML)
    assert '<div class="producto" data-id="77" data-sku="FA-1">' in resumen
    assert '<h1 class="titulo" itemprop="name">Faro delantero izquierdo</h1>' in resumen
    assert '<span class="precio" itemprop="price" content="89.90">89,90 €</span>' in resumen
    assert '<a href="/marca/valeo">Valeo</a>' in resumen
    assert '<img src="/f/1.jpg" alt="Faro">' in resumen
    assert '<form action="/buscar" method="get" class="buscador">' in resumen
    assert '<input type="search" name="q">' in resumen
    assert "<title>Faro Golf VI | Desguace Ejemplo</title>" in resumen


def test_resumen_quita_lo_peligroso_o_inutil() -> None:
    resumen = resumen_para_ia(HTML)
    for fuera in (
        "onclick",
        "robar",
        "style=",
        "dataLayer",
        "stylesheet",
        "Comentario",
        "<svg",
        "<script",
        "plantilla",
        "width=",
        "rel=",
        "precio es 1",
        "<meta",
    ):
        assert fuera not in resumen, fuera


def test_resumen_escapa_lo_que_viene_de_la_web() -> None:
    html = '<div class="a&quot;><b>" data-x="<x>">1 &lt; 2 &amp; <i>3</i></div>'
    resumen = resumen_para_ia(html)
    assert (
        resumen
        == '<div class="a&quot;&gt;&lt;b&gt;" data-x="&lt;x&gt;">1 &lt; 2 &amp; <i>3</i></div>'
    )


def test_resumen_recorta_textos_y_atributos_largos() -> None:
    html = f'<p class="{"c" * 500}">{"palabra " * 200}</p>'
    resumen = resumen_para_ia(html)
    assert len(resumen) < 500
    assert "…" in resumen


def test_resumen_respeta_el_maximo() -> None:
    html = "<ul>" + "".join(
        f'<li class="item"><a href="/p/{i}">Pieza {i}</a></li>' for i in range(5_000)
    )
    resumen = resumen_para_ia(html, max_caracteres=2_000)
    assert len(resumen) <= 2_000
    assert resumen.startswith('<ul><li class="item"><a href="/p/0">Pieza 0</a></li>')
    assert resumen_para_ia(html, max_caracteres=0) == ""


def test_resumen_profundo_no_desborda() -> None:
    html = "<div>" * 5_000 + "x"
    assert len(resumen_para_ia(html)) <= 30_000


def test_resumen_no_deja_pasar_etiquetas_escritas_como_texto() -> None:
    resumen = resumen_para_ia("<p>&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;</p>")
    assert resumen == "<p>&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;</p>"


def test_texto_visible_de_una_ficha_real() -> None:
    html = (
        Path(__file__).parent.parent / "fixtures" / "extraccion" / "ficha_desguace.html"
    ).read_text(encoding="utf-8")
    texto = texto_visible(html)
    assert "120,00 € 89,90 € -25% IVA incluido" in texto
    assert "Referencia OEM 5K1 941 005 E Marca VALEO Estado Usado - Probado" in texto
    assert "gtag" not in texto and "schema.org" not in texto and "googletagmanager" not in texto


# --- Páginas grandes: series repetidas y prioridad del listado --------------------------------

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
MARCADOR = "elementos similares omitidos"


def _real(nombre: str) -> str:
    return (REALES / nombre).read_text(encoding="utf-8")


def _productos(n: int = 12) -> str:
    return (
        '<ul class="rejilla">'
        + "".join(
            f'<li class="item post-{i}"><a href="/p/{i}"><img src="/f/{i}.jpg" alt=""></a>'
            f'<div class="info"><a class="titulo" href="/p/{i}">Faro modelo {i}</a>'
            f'<span class="precio">{i},90 €</span></div></li>'
            for i in range(n)
        )
        + "</ul>"
    )


def _filtros(n: int) -> str:
    return (
        '<aside class="filtros">'
        + "".join(
            f'<div class="term" data-facet="marca" data-value="M{i}">'
            f'<span class="term_value">Marca {i}</span><span class="term_count">{i}</span></div>'
            for i in range(n)
        )
        + "</aside>"
    )


def _menu(n: int) -> str:
    return (
        '<nav><ul class="menu">'
        + "".join(
            f'<li class="menu-item"><a href="/c/{i}">Categoría {i}</a></li>' for i in range(n)
        )
        + "</ul></nav>"
    )


def _avisos(n: int) -> str:
    # Contenido grande que no se repite (cada bloque tiene su propia clase): no se comprime.
    return (
        "<header>"
        + "".join(f'<div class="aviso-{i}"><b>Aviso {i}</b> de la cabecera</div>' for i in range(n))
        + "</header>"
    )


def test_las_series_repetidas_se_comprimen_y_el_listado_entra() -> None:
    # Como en desguacesdli.com: menús y miles de filtros delante de los productos.
    html = f"<body>{_menu(60)}{_filtros(3_000)}<main>{_productos()}</main></body>"
    resumen = resumen_para_ia(html)
    assert len(resumen) <= 30_000
    for i in range(12):
        assert f'<a class="titulo" href="/p/{i}">Faro modelo {i}</a>' in resumen
    assert '<span class="precio">11,90 €</span>' in resumen
    # De los filtros quedan unos pocos ejemplos completos y un marcador con lo omitido.
    assert '<div class="term" data-facet="marca" data-value="M2">' in resumen
    assert 'data-value="M3"' not in resumen
    assert "<!-- 2997 elementos similares omitidos -->" in resumen
    # Con los filtros comprimidos ya cabe: el menú, que ahorraría poco, se queda entero.
    assert "Categoría 59" in resumen and resumen.count(MARCADOR) == 1


def test_si_la_pagina_cabe_no_se_comprime_nada() -> None:
    html = f"<body>{_menu(20)}{_productos(30)}</body>"
    resumen = resumen_para_ia(html)
    assert MARCADOR not in resumen
    assert "Categoría 19" in resumen and "Faro modelo 29" in resumen


def test_el_listado_tiene_prioridad_sobre_lo_que_no_se_repite() -> None:
    html = f"<body>{_avisos(3_000)}<main>{_productos()}</main><footer>Pie</footer></body>"
    resumen = resumen_para_ia(html)
    assert len(resumen) <= 30_000
    # Sigue en el orden del documento: primero la cabecera (recortada), luego el listado entero.
    assert resumen.startswith('<header><div class="aviso-0"><b>Aviso 0</b>')
    assert resumen.index("</header>") < resumen.index('<ul class="rejilla">')
    for i in range(12):
        assert f'href="/p/{i}">Faro modelo {i}</a>' in resumen
    assert resumen.count("<li") == resumen.count("</li>") == 12
    assert resumen.count("<div") == resumen.count("</div>")


def test_sin_prioridad_de_listado_se_corta_por_el_final() -> None:
    # Para las fichas: los productos relacionados no deben desplazar al producto principal.
    html = f"<body>{_avisos(3_000)}<main>{_productos()}</main></body>"
    resumen = resumen_para_ia(html, listado=False)
    assert len(resumen) <= 30_000
    assert "Faro modelo" not in resumen
    # Sin prioridad, los productos son una serie más: se comprimen si hace falta para que quepa.
    html = f"<body>{_filtros(3_000)}<main>{_productos(400)}</main></body>"
    resumen = resumen_para_ia(html, listado=False)
    assert "Faro modelo 2" in resumen and "Faro modelo 3" not in resumen
    assert "<!-- 397 elementos similares omitidos -->" in resumen


def test_un_listado_enorme_se_recorta_pero_va_primero() -> None:
    html = f"<body>{_avisos(500)}<main>{_productos(2_000)}</main></body>"
    resumen = resumen_para_ia(html, max_caracteres=5_000)
    assert len(resumen) <= 5_000
    assert 'href="/p/0">Faro modelo 0</a>' in resumen


def _tarjeta(n: int) -> str:
    return (
        f'<div class="card"><a href="/p/{n}"><img src="/f/{n}.jpg" alt=""></a>'
        f'<a class="titulo" href="/p/{n}">Faro modelo {n}</a><span class="precio">{n},90 €</span>'
        "</div>"
    )


def test_el_contenedor_del_listado_no_se_omite_aunque_sea_de_una_serie() -> None:
    # Resultados en filas iguales de 8 tarjetas: el listado elegido es la última fila, que a su
    # vez es la sexta de una serie de filas. No puede caer entre las filas omitidas.
    filas = "".join(
        '<div class="fila">' + "".join(_tarjeta(f * 8 + c) for c in range(8)) + "</div>"
        for f in range(6)
    )
    resumen = resumen_para_ia(f"<body>{_avisos(3_000)}<main>{filas}</main></body>")
    assert len(resumen) <= 30_000
    assert resumen.count('<div class="card">') >= 8
    assert 'href="/p/40">Faro modelo 40</a>' in resumen


_ATRIBUTOS_FICHA = (
    ("Marca", "VALEO"),
    ("Modelo", "Golf"),
    ("Versión", "1.6 TDI"),
    ("Año", "2015"),
    ("Estado", "Usado"),
    ("Color", "Negro"),
    ("Referencia OEM", "5K1941005E"),
    ("Garantía", "3 meses"),
)


def _tabla_de_atributos() -> str:
    # Como la de WooCommerce: misma primera clase en todas las filas y misma estructura.
    filas = "".join(
        f'<tr class="woocommerce-product-attributes-item woocommerce-product-attributes-item--'
        f'attribute_pa_{n}"><th class="woocommerce-product-attributes-item__label">{etiqueta}</th>'
        f'<td class="woocommerce-product-attributes-item__value"><p>{valor}</p></td></tr>'
        for n, (etiqueta, valor) in enumerate(_ATRIBUTOS_FICHA)
    )
    return f'<table class="woocommerce-product-attributes shop_attributes">{filas}</table>'


def _pie(n: int) -> str:
    return "<footer>" + "".join(
        f'<div class="nota-{i}">Nota {i} de la tienda</div>' for i in range(n)
    )


def test_la_tabla_de_datos_de_una_ficha_no_se_comprime_por_poco() -> None:
    # Ficha que no cabe por un pie enorme que no se repite. Comprimir la tabla de atributos
    # apenas ahorra sitio y escondería la fila de la referencia OEM, que antes sí se veía.
    ficha = (
        f'<body>{_menu(250)}<main><h1 class="product_title">Faro delantero</h1>'
        f'<p class="price">89,90 €</p>{_tabla_de_atributos()}</main>{_pie(1_500)}</body>'
    )
    resumen = resumen_para_ia(ficha, listado=False)
    assert len(resumen) <= 30_000
    assert "<p>5K1941005E</p>" in resumen and "<p>3 meses</p>" in resumen


def test_solo_se_comprime_lo_necesario_para_que_quepa() -> None:
    # Basta con comprimir los filtros (la serie que más ahorra): la tabla y el menú se quedan.
    ficha = f"<body>{_menu(60)}{_filtros(3_000)}<main>{_tabla_de_atributos()}</main></body>"
    resumen = resumen_para_ia(ficha, listado=False)
    assert "<!-- 2997 elementos similares omitidos -->" in resumen
    assert "Categoría 59" in resumen and "<p>5K1941005E</p>" in resumen
    assert resumen.count(MARCADOR) == 1


def test_las_secciones_de_un_maquetador_no_son_una_serie() -> None:
    # Elementor, Bricks, Divi...: los bloques de la página tienen los mismos 3 o 4 niveles de
    # envoltorios, pero dentro hay cosas distintas. No son repeticiones: no se comprimen.
    def seccion(i: int, contenido: str) -> str:
        return (
            f'<section class="elementor-section elementor-element-{i}">'
            '<div class="elementor-container"><div class="elementor-column">'
            f'<div class="elementor-widget-wrap">{contenido}</div></div></div></section>'
        )

    bloques = [f"<h2>Bloque {i}</h2>" + f"<p>{'texto ' * 30}{i}</p>" * 4 for i in range(8)]
    bloques[6] = _tabla_de_atributos() + "<p>Ficha técnica</p>" * 20
    bloques[1] = '<h1 class="product_title">Faro</h1><p class="price">89,90 €</p>' * 8
    ficha = (
        f"<body>{_menu(250)}<main>"
        + "".join(seccion(i, b) for i, b in enumerate(bloques))
        + f"</main>{_pie(1_500)}</body>"
    )
    resumen = resumen_para_ia(ficha, listado=False)
    assert len(resumen) <= 30_000
    assert "<p>5K1941005E</p>" in resumen


def test_el_marcador_no_lo_puede_escribir_la_web() -> None:
    # Los comentarios de la página se quitan y el texto que imita el marcador queda escapado.
    falso = "3 elementos similares omitidos"
    html = f"<p><!-- {falso} --> &lt;!-- {falso} --&gt;</p>"
    assert resumen_para_ia(html) == f"<p> &lt;!-- {falso} --&gt;</p>"


def _cronometrar[T](funcion: Callable[[str], T], html: str) -> tuple[T, float]:
    inicio = time.perf_counter()
    resultado = funcion(html)
    return resultado, time.perf_counter() - inicio


@pytest.mark.parametrize(
    ("hostil", "comprime"),
    [
        ('<div class="t"><span>x</span><a href="/p">y</a></div>' * 40_000, True),
        ('<div class="n"><a href="/p">x</a><b>1,00 €</b>' * 15_000, True),
        # Miles de series pequeñas: ninguna ahorra lo bastante para comprimirla.
        (("<ul>" + "<li class='i'><a href='/p'>x 1 €</a></li>" * 6 + "<li>") * 4_000, False),
    ],
    ids=["hermanos", "anidamiento", "series-anidadas"],
)
def test_resumen_de_html_hostil_en_tiempo_lineal(hostil: str, comprime: bool) -> None:
    # Unos MB de hermanos repetidos o de anidamiento: el resumen no puede costar mucho más que
    # construir el árbol (que es lineal), y eso que incluye construirlo.
    assert len(hostil) > 500_000
    _, analisis = _cronometrar(analizar, hostil)
    resumen, tiempo = _cronometrar(resumen_para_ia, hostil)
    assert tiempo < 3 * analisis + 1
    assert len(resumen) <= 30_000
    assert (MARCADOR in resumen) is comprime


# --- Páginas reales (prueba de generalidad del 2026-10-20) ------------------------------------


def test_resumen_de_desguacesdli_contiene_los_productos() -> None:
    # Bricks + WooCommerce: ~1200 filtros del buscador delante de los 12 resultados. Antes el
    # resumen se llenaba con la cabecera y los filtros y la IA se inventaba los selectores.
    resumen = resumen_para_ia(_real("dli_busqueda_faro_chromium.html"))
    assert len(resumen) <= 30_000
    assert '<ul class="products columns-3' in resumen
    assert resumen.count('<li class="badges-style-2 product post-') == 12
    assert (
        resumen.count('<div class="excerpt-wrapper"><a class="product-title" href="/recambios/')
        == 12
    )
    assert "NO IDENTIFICADO TOYOTA LAND CRUISER J12 3.0 D4-D VX" in resumen
    assert MARCADOR in resumen


def test_resumen_de_webscraper_conserva_la_estructura_del_listado() -> None:
    # La estructura con la que Claude acertó (div.thumbnail, h4 a.title, h4.price).
    resumen = resumen_para_ia(_real("webscraper_listado_laptops.html"))
    assert resumen.count('<div class="card thumbnail"') >= 3
    assert (
        '<h4><a href="/test-sites/e-commerce/static/product/31" class="title" itemprop="name">'
        " Packard 255 G2 </a></h4>"
    ) in resumen
    assert '<h4 class="price float-end card-title pull-right"' in resumen
    assert '<span itemprop="price">$416.99</span>' in resumen
