"""Árbol DOM propio sobre `html.parser`, tolerante al HTML real (roto)."""

import time

from precia.extraccion.dom import MAX_ELEMENTOS, Nodo, analizar


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
    inicio = time.perf_counter()
    raiz = analizar(html)
    texto = raiz.texto()
    assert time.perf_counter() - inicio < 10
    assert "Faro delantero 5999" in texto
