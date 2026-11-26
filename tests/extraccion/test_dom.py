"""Árbol DOM propio sobre `html.parser`, tolerante al HTML real (roto)."""

from pathlib import Path

import pytest

from precia.extraccion.dom import MAX_ELEMENTOS, Nodo, analizar, pestana_oculta, tachado
from tests.cronometro import segundos_de_cpu


def _primero(raiz: Nodo, etiqueta: str) -> Nodo:
    for nodo in raiz.descendientes():
        if nodo.etiqueta == etiqueta:
            return nodo
    raise AssertionError(f"no hay <{etiqueta}>")


def _todos(raiz: Nodo, etiqueta: str) -> list[Nodo]:
    return [n for n in raiz.descendientes() if n.etiqueta == etiqueta]


def test_arbol_basico_con_padres_e_hijos() -> None:
    raiz = analizar('<html><body><div id="a"><span>Hola</span></div></body></html>')
    div = _primero(raiz, "div")
    span = div.hijos[0]
    assert span.etiqueta == "span"
    assert span.padre is div
    assert div.atributo("id") == "a"
    assert div.atributo("class") is None
    assert span.texto() == "Hola"


def test_raiz_es_un_documento_sin_padre() -> None:
    raiz = analizar("<p>uno</p>")
    assert raiz.padre is None
    assert raiz.etiqueta == "#documento"
    assert raiz.hijos[0].padre is raiz


def test_atributos_sin_comillas_sin_valor_y_en_mayusculas() -> None:
    raiz = analizar("<DIV CLASS=producto data-id=42 hidden Title='x'></DIV>")
    div = _primero(raiz, "div")
    assert div.atributos == {"class": "producto", "data-id": "42", "hidden": "", "title": "x"}


def test_atributo_duplicado_gana_el_primero_como_en_los_navegadores() -> None:
    div = _primero(analizar('<div class="a" class="b"></div>'), "div")
    assert div.atributo("class") == "a"


def test_clases() -> None:
    div = _primero(analizar('<div class="  precio   precio--oferta\tgrande "></div>'), "div")
    assert div.clases == ("precio", "precio--oferta", "grande")


def test_entidades_en_texto_y_atributos() -> None:
    raiz = analizar('<p title="A &amp; B">Frenos &amp; discos&nbsp;&euro; &#8364; &copy;</p>')
    p = _primero(raiz, "p")
    assert p.atributo("title") == "A & B"
    assert p.texto() == "Frenos & discos € € ©"


def test_cierre_implicito_de_p() -> None:
    raiz = analizar("<div><p>uno<p>dos<div>tres</div></div>")
    parrafos = _todos(raiz, "p")
    assert [p.texto() for p in parrafos] == ["uno", "dos"]
    assert all(p.padre is not None and p.padre.etiqueta == "div" for p in parrafos)
    interior = _todos(raiz, "div")[1]
    assert interior.padre is _todos(raiz, "div")[0]


def test_cierre_implicito_de_li_y_listas_anidadas() -> None:
    raiz = analizar("<ul><li>a<li>b<ul><li>b1<li>b2</ul><li>c</ul>")
    externa = _todos(raiz, "ul")[0]
    assert [li.texto() for li in externa.hijos] == ["a", "b b1 b2", "c"]


def test_cierre_implicito_de_tablas() -> None:
    raiz = analizar("<table><tr><td>1<td>2<tr><th>3<td>4</table><p>fuera")
    filas = _todos(raiz, "tr")
    assert len(filas) == 2
    assert [c.texto() for c in filas[0].hijos] == ["1", "2"]
    assert [c.etiqueta for c in filas[1].hijos] == ["th", "td"]
    p = _primero(raiz, "p")
    assert p.padre is raiz


def test_cierre_implicito_de_dt_dd_y_option() -> None:
    raiz = analizar(
        "<dl><dt>Marca<dd>Bosch<dt>Estado<dd>Usado</dl>"
        "<select><option>uno<option>dos<optgroup><option>tres</select>"
    )
    dl = _primero(raiz, "dl")
    assert [n.etiqueta for n in dl.hijos] == ["dt", "dd", "dt", "dd"]
    assert len(_todos(raiz, "option")) == 3
    assert _primero(raiz, "select").hijos[0].etiqueta == "option"


def test_elementos_vacios_no_abren_ambito() -> None:
    raiz = analizar('<div><img src="a.jpg"><br><input name=q><span>x</span></div>')
    div = _primero(raiz, "div")
    assert [n.etiqueta for n in div.hijos] == ["img", "br", "input", "span"]


def test_autocierre_de_elementos_no_vacios_se_ignora_como_en_html() -> None:
    raiz = analizar("<div/><span>dentro</span>")
    assert _primero(raiz, "span").padre is _primero(raiz, "div")


def test_autocierre_dentro_de_svg_si_cierra() -> None:
    raiz = analizar('<svg><path d="M0"/><circle r=1 /></svg><p>texto</p>')
    svg = _primero(raiz, "svg")
    assert [n.etiqueta for n in svg.hijos] == ["path", "circle"]
    assert _primero(raiz, "p").padre is raiz


def test_cierres_sobrantes_o_desordenados_no_rompen_el_arbol() -> None:
    raiz = analizar("</div></span><div><b><i>x</b>y</i></div><p>z</p>")
    assert _primero(raiz, "p").padre is raiz
    assert _primero(raiz, "div").texto() == "xy"


def test_etiquetas_sin_cerrar_al_final() -> None:
    raiz = analizar("<div><span>precio 12 €")
    assert raiz.texto() == "precio 12 €"


def test_texto_normaliza_espacios_y_separa_bloques() -> None:
    raiz = analizar(
        "<div>  Pastillas\n\t de   freno </div><div>Bosch</div>"
        "<span>38</span><span>,90 €</span><br>IVA incl.<li>uno</li><li>dos</li>"
    )
    assert raiz.texto() == "Pastillas de freno Bosch 38,90 € IVA incl. uno dos"


def test_texto_excluye_scripts_estilos_comentarios_y_ocultos() -> None:
    html = (
        "<p>visible</p><script>var precio = 1;</script><style>.a{}</style>"
        "<noscript>sin js</noscript><template><b>plantilla</b></template>"
        "<svg><text>icono</text></svg><!-- comentario -->"
        '<div hidden>oculto</div><span style="display: none">oculto2</span>'
        '<span style="visibility:hidden">oculto3</span><p>fin</p>'
    )
    assert analizar(html).texto() == "visible fin"


@pytest.mark.parametrize(
    ("html", "esperado"),
    [
        ("<del>59,90 €</del>", True),
        ("<s>59,90 €</s>", True),
        ("<strike>59,90 €</strike>", True),
        ('<span class="old-price">59,90 €</span>', True),
        ('<span class="regular-price">59,90 €</span>', True),
        ('<p class="reference-price">Ancien prix : 17,06 €</p>', True),  # Oscaro
        ('<span class="price--compare">59,90 €</span>', True),
        ('<span class="compare-at-price">59,90 €</span>', True),
        ('<span class="precio-anterior">120,00 €</span>', True),
        ('<span class="price-item price-item--regular">59,90 €</span>', True),  # Shopify
        ('<span id="old-price-123">59,90 €</span>', True),  # Magento
        ("<ins>39,90 €</ins>", False),
        ('<span class="price">39,90 €</span>', False),
        ('<div class="price-box">39,90 €</div>', False),
        ('<span class="precio-actual">39,90 €</span>', False),
        ('<span class="woocommerce-Price-amount amount">39,90 €</span>', False),
        ('<span class="special-price">39,90 €</span>', False),
        ('<span class="price-list">39,90 €</span>', False),
        ('<span class="descuento">-20 %</span>', False),
    ],
)
def test_tachado_por_etiqueta_y_por_clase(html: str, esperado: bool) -> None:
    nodo = next(analizar(html).descendientes())
    assert tachado(nodo) is esperado


@pytest.mark.parametrize(
    ("html", "esperado"),
    [
        # MediaMarkt (R2-B4): el PVP tachado se marca con `data-test`, sin clase ni id.
        ('<div data-test="mms-strike-price-type-rrp">39,90 €</div>', True),
        ('<span data-testid="old-price">59,90 €</span>', True),
        ('<span data-test-id="price-was">59,90 €</span>', True),
        ('<span data-qa="product_regular_price">59,90 €</span>', True),
        ('<span data-cy="compare-at-price">59,90 €</span>', True),
        ('<span data-e2e="crossed-price">59,90 €</span>', True),
        ('<span data-test="cofr-price strike-price">59,90 €</span>', True),
        ('<div data-test="mms-price">29,99 €</div>', False),
        ('<div data-test="cofr-price product-price">29,99 €</div>', False),
        ('<span data-testid="product-price">29,99 €</span>', False),
        ('<span data-test="mms-strike-price-label"></span>', True),
        # Otros `data-*` llevan JSON o analítica: no se miran.
        ("<span data-gtm='{\"old_price\": 39.9}'>29,99 €</span>", False),
        ('<span data-tracking="old-price">29,99 €</span>', False),
        # `aria-label` es texto libre: «antes de impuestos» no es un precio antiguo.
        ('<span aria-label="Precio antes de impuestos">24,79 €</span>', False),
    ],
)
def test_tachado_por_atributo_de_prueba(html: str, esperado: bool) -> None:
    nodo = next(analizar(html).descendientes())
    assert tachado(nodo) is esperado


def test_texto_sin_tachados() -> None:
    woocommerce = analizar(
        '<p class="price"><del><bdi>59,90&nbsp;€</bdi></del> <ins><bdi>39,90&nbsp;€</bdi></ins></p>'
    )
    assert woocommerce.texto() == "59,90 € 39,90 €"
    assert woocommerce.texto(sin_tachados=True) == "39,90 €"
    oscaro = analizar(
        '<div><p class="reference-price">Ancien prix : 17,06 €</p><p>14,50 €</p></div>'
    )
    assert oscaro.texto(sin_tachados=True) == "14,50 €"


def test_texto_crudo_de_script_conserva_el_contenido() -> None:
    raiz = analizar('<script type="application/ld+json">{"a": "</p>"}</script>')
    script = _primero(raiz, "script")
    assert script.texto_crudo() == '{"a": "</p>"}'
    assert script.texto() == '{"a": "</p>"}'


def test_html_vacio_o_basura() -> None:
    assert analizar("").hijos == []
    assert analizar("<<<>>>&&&").texto() == "<<<>>>&&&"


def test_repr_no_recursivo() -> None:
    raiz = analizar('<div id="x"><p>hola</p></div>')
    assert "div" in repr(_primero(raiz, "div"))


def test_anidamiento_profundo_no_desborda_la_pila() -> None:
    html = "<div>" * 50_000 + "fondo" + "</div>" * 50_000
    raiz = analizar(html)
    assert raiz.texto() == "fondo"
    assert sum(1 for _ in raiz.descendientes()) == 50_000


def test_limite_de_elementos() -> None:
    raiz = analizar("<i>x</i>" * (MAX_ELEMENTOS + 10))
    assert sum(1 for _ in raiz.descendientes()) == MAX_ELEMENTOS


def test_pagina_de_2_mb_en_tiempo_razonable() -> None:
    tarjeta = (
        '<li class="producto"><a href="/p/{i}"><img src="/i/{i}.jpg" alt="Pieza {i}">'
        '<h3 class="titulo">Faro delantero {i}</h3></a>'
        '<span class="precio">{i},90 €</span><p>Descripción larga de la pieza usada '
        "en buen estado, probada y con garantía de tres meses.</p></li>\n"
    )
    html = "<ul>" + "".join(tarjeta.format(i=i) for i in range(6_000)) + "</ul>"
    assert len(html) > 1_500_000
    textos: list[str] = []
    assert segundos_de_cpu(lambda: textos.append(analizar(html).texto()), 10) < 10
    assert "Faro delantero 5999" in textos[-1]


# --- Texto con pestañas ocultas (B5) ------------------------------------------------------------

FIXTURES = Path(__file__).parent.parent / "fixtures" / "extraccion"


def test_texto_con_pestanas_incluye_paneles_ocultos() -> None:
    # La pestaña «Referencias OEM» de Oscaro: la persona la ve con un clic.
    raiz = analizar('<p>Pastillas</p><div role="tabpanel" hidden><p>1K0698151</p></div>')
    assert raiz.texto() == "Pastillas"
    assert raiz.texto(pestanas=True) == "Pastillas 1K0698151"
    assert pestana_oculta(_primero(raiz, "div"))


@pytest.mark.parametrize(
    "html",
    [
        "<div hidden><p>1K0698151</p></div>",
        '<div role="tabpanel" style="display:none"><p>1K0698151</p></div>',
        '<div role="tabpanel" hidden style="visibility: hidden">1K0698151</div>',
        '<div role="tabpanel" hidden><script>var r = "1K0698151";</script></div>',
        '<div role="tabpanel" hidden><template><p>1K0698151</p></template></div>',
        '<div role="tabpanel" hidden><div hidden>1K0698151</div></div>',
    ],
)
def test_texto_con_pestanas_no_incluye_otros_ocultos(html: str) -> None:
    assert "1K0698151" not in analizar(html).texto(pestanas=True)


def test_texto_por_defecto_no_cambia() -> None:
    # Prueba de no regresión del anclaje: sin `pestanas`, el texto es el de siempre.
    raiz = analizar((FIXTURES / "ficha_desguace.html").read_text(encoding="utf-8"))
    assert raiz.texto() == raiz.texto(pestanas=False)
    assert raiz.texto(pestanas=True) == raiz.texto()


# --- Streaming SSR de React (causa R2-C9) ------------------------------------------------------
# React 18 manda el contenido en segmentos `<div hidden id="S:N">` y un `<script>` en línea los
# cuelga en el sitio de su hueco: `$RC("B:N","S:N")` (en el de `<template id="B:N">`) o
# `$RS("S:N","P:N")` (en el de `<template id="P:N">`). Los ids van en hexadecimal.

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
_RC = (
    "<script>$RC=function(b,c,e){c=document.getElementById(c);c.parentNode.removeChild(c);};"
    '$RC("B:0","S:0")</script>'
)


def test_streaming_de_react_cuelga_el_segmento_en_el_sitio_de_su_hueco() -> None:
    raiz = analizar(
        '<main><div class="rejilla"><template id="B:0"></template></div></main>'
        '<div hidden id="S:0"><article><a href="/p/1">Faro LED</a> <span>38,90 €</span>'
        f"</article></div>{_RC}"
    )
    assert raiz.texto() == "Faro LED 38,90 €"
    rejilla = _primero(raiz, "div")
    articulo = _primero(raiz, "article")
    assert articulo.padre is rejilla
    assert rejilla.hijos == [articulo] and rejilla.contenido == [articulo]
    assert not _todos(raiz, "template")
    assert [n.atributo("id") for n in _todos(raiz, "div")] == [None]


def test_streaming_de_react_conserva_el_orden_y_admite_ids_hexadecimales() -> None:
    raiz = analizar(
        '<p>Antes</p><template id="B:a"></template><p>Después</p>'
        '<div hidden id="S:a">uno <b>dos</b></div><script>$RC("B:a","S:a")</script>'
    )
    assert raiz.texto() == "Antes uno dos Después"
    assert [n.etiqueta for n in raiz.hijos] == ["p", "b", "p", "script"]


def test_streaming_de_react_rs_y_segmentos_anidados() -> None:
    # Zalando: B:0 ← S:0 ⊃ P:1f ← S:1f. `$RS` lleva primero el segmento y luego el hueco.
    raiz = analizar(
        '<div id="raiz"><template id="B:0"></template></div>'
        '<div hidden id="S:0"><section><template id="P:1f"></template></section></div>'
        f"{_RC}"
        '<div hidden id="S:1f"><article>Zapatilla 59,95 €</article></div>'
        '<script>$RS("S:1f","P:1f")</script>'
    )
    assert raiz.texto() == "Zapatilla 59,95 €"
    articulo = _primero(raiz, "article")
    assert articulo.padre is not None and articulo.padre.etiqueta == "section"
    assert articulo.padre.padre is _primero(raiz, "div")


def test_streaming_de_react_con_plantillas_data_rci_y_data_rsi() -> None:
    # El runtime externo de React (sin scripts en línea) usa plantillas con los ids.
    raiz = analizar(
        '<template id="B:0"></template><div hidden id="S:0"><p>uno</p></div>'
        '<template data-rci="" data-bid="B:0" data-sid="S:0"></template>'
        '<template id="P:1"></template><div hidden id="S:1"><p>dos</p></div>'
        '<template data-rsi="" data-sid="S:1" data-pid="P:1"></template>'
    )
    assert raiz.texto() == "uno dos"


@pytest.mark.parametrize(
    "html",
    [
        # Sin llamada, un `hidden` sigue oculto: el anclaje no se relaja.
        '<template id="B:0"></template><div hidden id="S:0">38,90 €</div>',
        '<template id="B:0"></template><div hidden id="S:0">38,90 €</div>'
        '<script>$RC("B:1","S:1")</script>',
        # La llamada solo cuenta en un `<script>` en línea.
        '<template id="B:0"></template><div hidden id="S:0">38,90 €</div><p>$RC("B:0","S:0")</p>',
        '<template id="B:0"></template><div hidden id="S:0">38,90 €</div>'
        '<script src="/r.js">$RC("B:0","S:0")</script>',
        # El hueco tiene que ser un <template>, y el segmento, un <div hidden>.
        '<div id="B:0"></div><div hidden id="S:0">38,90 €</div><script>$RC("B:0","S:0")</script>',
        '<template id="B:0"></template><section hidden id="S:0">38,90 €</section>'
        '<script>$RC("B:0","S:0")</script>',
        # Un segmento que contiene su propio hueco no se mueve (sería un ciclo).
        '<div hidden id="S:0"><template id="B:0"></template>38,90 €</div>'
        '<script>$RC("B:0","S:0")</script>',
        # Ids que no son de React.
        '<template id="B:0g"></template><div hidden id="S:0g">38,90 €</div>'
        '<script>$RC("B:0g","S:0g")</script>',
    ],
)
def test_streaming_de_react_sin_par_valido_no_cambia_nada(html: str) -> None:
    assert "38,90" not in analizar(html).texto()


def test_streaming_de_react_hostil_no_se_cuelga() -> None:
    # Muchos pares bajo el mismo padre, repetidos y cruzados: termina pronto y sin ciclos.
    n = 20_000
    huecos = "".join(f'<template id="B:{i:x}"></template>' for i in range(n))
    segmentos = "".join(f'<div hidden id="S:{i:x}">s{i} </div>' for i in range(n))
    llamadas = "".join(f'$RC("B:{i:x}","S:{i:x}");$RC("B:{i:x}","S:0");' for i in range(n))
    html = f"<div>{huecos}</div>{segmentos}<script>{llamadas}</script>"
    raices: list[Nodo] = []
    textos: list[str] = []

    def analizar_y_leer() -> None:
        raices.append(analizar(html))
        textos.append(raices[-1].texto())

    assert segundos_de_cpu(analizar_y_leer, 5) < 5
    assert textos[-1].startswith("s0 s1 s2")
    assert sum(1 for _ in raices[-1].descendientes()) <= 3 * n


def test_streaming_de_react_zalando_real() -> None:
    # Búsqueda «filtro» de Zalando (ronda 2): sin colgar los segmentos, el texto visible era
    # solo el título (29 caracteres) y la IA no veía ningún producto.
    html = (REALES / "zalando_busqueda_streaming_react.html").read_text(encoding="utf-8")
    texto = analizar(html).texto()
    assert "Gafas con filtro de luz azul - gold-coloured/transparent 350,00 €" in texto
    assert "14,39 €" in texto and "45,45 €" in texto
    assert texto.index("350,00 €") < texto.index("14,39 €") < texto.index("45,45 €")


# --- Céntimos en un elemento aparte (causa R3-A2) -----------------------------------------------
# Campaña multi-sector, ronda 3: Brico Dépôt pinta el precio como
# `<span>228</span><span><span class="price-top">96</span><span class="price-bottom">€</span>`
# (los céntimos en superíndice) y el texto quedaba «22896€»: todos los importes salían por 100.
# Un entero y exactamente dos cifras en elementos en línea distintos, seguidos de la moneda, se
# leen con coma decimal, sea cual sea la etiqueta o la clase.

BRICO_228_96 = (
    '<p class="price-wrapper"><span class="price-style"><span>228</span><span>'
    '<span class="price-top">96</span><span class="price-bottom">€</span></span></span></p>'
)


@pytest.mark.parametrize(
    ("html", "esperado"),
    [
        (BRICO_228_96, "228,96€"),
        ("<span>12<sup>99</sup>€</span>", "12,99€"),
        ("<b>12</b><sup>99</sup> €", "12,99 €"),
        ("<span>7</span><sup>05</sup>&nbsp;EUR", "7,05 EUR"),
        ("<span>1.228</span><sup>96</sup>€", "1.228,96€"),
        ("<span>1,228</span><sup>96</sup>$", "1,228.96$"),
        ("<p>Precio: <span>19</span><span><i>99</i></span><span>£</span></p>", "Precio: 19,99£"),
    ],
)
def test_centimos_en_un_elemento_aparte_y_seguidos_de_moneda_llevan_coma(
    html: str, esperado: str
) -> None:
    assert analizar(html).texto() == esperado


@pytest.mark.parametrize(
    ("html", "esperado"),
    [
        # Ya trae el separador.
        ("<span>38</span><span>,90 €</span>", "38,90 €"),
        # Brico Dépôt sin céntimos: el hueco de los céntimos es un &nbsp;.
        (
            '<span>139</span><span><span class="price-top">&nbsp;</span>'
            '<span class="price-bottom">€</span></span>',
            "139 €",
        ),
        # Referencias partidas en varios elementos: sin moneda detrás, o pegadas a una letra.
        ("<b>1K0</b><b>615</b><b>301</b>", "1K0615301"),
        ("Ref <strong>038</strong><strong>25</strong>3014G", "Ref 038253014G"),
        ("<b>1K0</b><b>61</b>€", "1K061€"),
        ("Ref <span>AB</span><span>12</span><span>34</span>€", "Ref AB1234€"),
        ("<span>12</span><b>99</b>", "1299"),
        # Ni una cifra suelta (m²), ni tres, ni lo que va detrás de un precio entero.
        ("15<sup>2</sup>", "152"),
        ("15<sup>2</sup> €", "152 €"),
        ("<span>12</span><span>345</span>€", "12345€"),
        ("19,99 €<sup>12</sup>", "19,99 €12"),
        ("<span>12345678</span><sup>90</sup>€", "1234567890€"),
        # Con un espacio o un bloque en medio, no van juntos.
        ("<span>12</span> <span>99</span>€", "12 99€"),
        ("<li><span>3</span></li><li><span>99</span><span>€</span></li>", "3 99€"),
        # El mismo número en un solo elemento.
        ("<span>2299€</span>", "2299€"),
    ],
)
def test_sin_moneda_detras_o_con_separador_el_texto_no_cambia(html: str, esperado: str) -> None:
    assert analizar(html).texto() == esperado


# --- Céntimos en superíndice con el HTML indentado (banco de evaluación, S06) ---------------------
# Coolmod pinta `<span class="int_price">\n 1\n</span>\n<sup>\n<span>\n 95\n</span><span>€</span>
# </sup>`: los espacios de formato impedían juntar los céntimos y el texto quedaba «1 95 €», así
# que la IA y el anclaje daban por bueno un precio de 95 €. En un `<sup>` los céntimos se juntan
# aunque haya espacios alrededor, siempre que el entero vaya en su propio elemento.

COOLMOD_1_95 = (
    '<div class="block">\n    <span class="product_price int_price">\n        1\n    </span>\n'
    '    <sup class="text-sm">\n        <span class="dec_price">\n            95\n        </span>\n'
    '        <span class="currency_symbol">&euro;</span>\n    </sup>\n</div>'
)


@pytest.mark.parametrize(
    ("html", "esperado"),
    [
        (COOLMOD_1_95, "1,95 €"),
        ("<span>12</span> <sup>99</sup>€", "12,99€"),
        ("<span> 1.228 </span>\n<sup> 96 </sup> €", "1.228,96 €"),
        ("<p>Precio <b>\n 7 \n</b><sup>\n05\n</sup>\nEUR</p>", "Precio 7,05 EUR"),
        # Sangría larga, como en la página real.
        (
            "<div>"
            + " " * 40
            + "<span>"
            + " " * 40
            + "3"
            + " " * 40
            + "</span>"
            + " " * 40
            + "<sup>"
            + " " * 40
            + "49"
            + " " * 40
            + "</sup>€</div>",
            "3,49 €",
        ),
    ],
)
def test_centimos_en_superindice_se_juntan_aunque_haya_espacios_de_formato(
    html: str, esperado: str
) -> None:
    assert analizar(html).texto() == esperado


@pytest.mark.parametrize(
    ("html", "esperado"),
    [
        # El entero no va en su propio elemento: puede ser una talla o una cantidad.
        ("Talla 1 <sup>95</sup> €", "Talla 1 95 €"),
        ("Pack de 2 <sup> 12 </sup> €", "Pack de 2 12 €"),
        # Sin moneda detrás, tres cifras o una sola: no son céntimos.
        ("<span>12</span> <sup>99</sup>", "12 99"),
        ("<span>12</span> <sup>995</sup> €", "12 995 €"),
        ("<span>12</span> <sup>9</sup> €", "12 9 €"),
        # Entero pegado a una letra: una referencia.
        ("<span>1K0</span> <sup>61</sup> €", "1K0 61 €"),
        # Sin superíndice, un espacio en medio sigue separando.
        ("<span>12</span> <span>99</span>€", "12 99€"),
    ],
)
def test_superindice_que_no_son_centimos_no_cambia(html: str, esperado: str) -> None:
    assert analizar(html).texto() == esperado


def test_el_texto_nunca_lleva_la_marca_de_superindice() -> None:
    texto = analizar("<p>a\x01b<span>1</span> <sup>99</sup>€</p>").texto()
    assert "\x01" not in texto and "\x00" not in texto


def test_centimos_en_un_elemento_aparte_tambien_sin_tachados() -> None:
    raiz = analizar("<p><s>316,54 €</s>" + BRICO_228_96 + "</p>")
    assert raiz.texto() == "316,54 € 228,96€"
    assert raiz.texto(sin_tachados=True) == "228,96€"
    assert raiz.texto(pestanas=True) == "316,54 € 228,96€"


def test_el_texto_nunca_lleva_caracteres_nulos() -> None:
    assert "\x00" not in analizar("<p>a\x00b<span>1</span><sup>99</sup>€</p>").texto()


def test_centimos_aparte_con_miles_de_elementos_vacios_no_se_cuelga() -> None:
    html = "<p>1" + "<i></i>" * 100_000 + "<sup>99</sup>€ " + "<b>7</b>" * 50_000 + "</p>"
    textos: list[str] = []
    assert segundos_de_cpu(lambda: textos.append(analizar(html).texto()), 5) < 5
    assert textos[-1].startswith("1,99€ 777")


def test_centimos_aparte_en_la_ficha_real_de_brico_depot() -> None:
    # Recorte de la ficha real (ronda 3): «… 5 3 1495€» con el precio vigente a 14,95 €.
    html = (REALES / "bricodepot_ficha_precio_centimos_en_span.html").read_text(encoding="utf-8")
    texto = analizar(html).texto()
    assert texto.endswith("Filtro de carbono ø 13,2 cm 5 3 14,95€")
    assert "1495" not in texto
