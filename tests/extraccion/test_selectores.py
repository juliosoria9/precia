"""Motor de selectores CSS restringido (ADR-015): lo que no está en el subconjunto, se rechaza."""

import time

import pytest

from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.selectores import (
    MAX_LONGITUD,
    dividir_selector,
    extraer_valor,
    extraer_valores,
    seleccionar,
)
from precia.extraccion.tipos import SelectorNoValido

HTML = """
<div id="listado" class="productos rejilla">
  <article class="producto destacado" data-id="101" data-sku="FR-1K0">
    <h2 class="titulo"><a href="/pieza/101" title="Faro">Faro delantero izquierdo</a></h2>
    <span class="precio precio--oferta" itemprop="price" content="89.90">89,90 €</span>
    <span class="precio precio--antes">120,00 €</span>
    <img src="/f/101-1.jpg"><img src="/f/101-2.jpg">
  </article>
  <article class="producto" data-id="102" lang="es-ES">
    <h2 class="titulo"><a href="https://tienda.es/pieza/102">Piloto trasero</a></h2>
    <div class="caja"><span class="precio">45,00 €</span></div>
  </article>
  <section><p>uno</p><p>dos</p><span>s</span><p>tres</p></section>
</div>
"""


@pytest.fixture(scope="module")
def raiz() -> Nodo:
    return analizar(HTML)


def _textos(raiz: Nodo, selector: str) -> list[str]:
    return [n.texto() for n in seleccionar(raiz, selector)]


@pytest.mark.parametrize(
    ("selector", "esperado"),
    [
        ("h2", ["Faro delantero izquierdo", "Piloto trasero"]),
        ("#listado > article > h2", ["Faro delantero izquierdo", "Piloto trasero"]),
        (".precio", ["89,90 €", "120,00 €", "45,00 €"]),
        (".precio.precio--oferta", ["89,90 €"]),
        ("span.precio.precio--antes", ["120,00 €"]),
        ("article.destacado .precio--oferta", ["89,90 €"]),
        ("article > .precio", ["89,90 €", "120,00 €"]),
        ("article .precio", ["89,90 €", "120,00 €", "45,00 €"]),
        ("article>div>span", ["45,00 €"]),
        ("[itemprop]", ["89,90 €"]),
        ("[itemprop=price]", ["89,90 €"]),
        ('[itemprop="price"]', ["89,90 €"]),
        ("[class~=precio--antes]", ["120,00 €"]),
        ("[data-sku^=FR]", ["89,90"]),
        ("a[href$='/102']", ["Piloto trasero"]),
        ('a[href*="pieza/1"]', ["Faro delantero izquierdo", "Piloto trasero"]),
        ("[lang|=es]", None),
        ("section p:nth-of-type(2)", ["dos"]),
        ("section p:first-of-type", ["uno"]),
        ("section > *:nth-of-type(1)", ["uno", "s"]),
        ("h2 a, .precio--antes", ["Faro delantero izquierdo", "120,00 €", "Piloto trasero"]),
        ("#no-existe", []),
        ("*", None),
    ],
)
def test_subconjunto_admitido(raiz: Nodo, selector: str, esperado: list[str] | None) -> None:
    if selector == "[lang|=es]":
        with pytest.raises(SelectorNoValido):
            seleccionar(raiz, selector)
        return
    if selector == "*":
        assert len(seleccionar(raiz, selector)) == sum(1 for _ in raiz.descendientes())
        return
    if selector == "[data-sku^=FR]":
        nodos = seleccionar(raiz, selector)
        assert [n.atributo("data-id") for n in nodos] == ["101"]
        return
    assert _textos(raiz, selector) == esperado


def test_grupo_sin_duplicados_y_en_orden_del_documento(raiz: Nodo) -> None:
    nodos = seleccionar(raiz, ".precio--antes, .precio, article .precio")
    assert [n.texto() for n in nodos] == ["89,90 €", "120,00 €", "45,00 €"]


def test_seleccion_relativa_a_un_nodo(raiz: Nodo) -> None:
    segundo = seleccionar(raiz, "article")[1]
    assert [n.texto() for n in seleccionar(segundo, ".precio")] == ["45,00 €"]
    # Como `querySelectorAll`: el propio nodo y sus ancestros cuentan para encajar.
    assert [n.texto() for n in seleccionar(segundo, "article h2 a")] == ["Piloto trasero"]


def test_descendiente_con_retroceso() -> None:
    raiz = analizar("<div class=a><div class=b><p><span>x</span></p></div></div>")
    assert len(seleccionar(raiz, ".a > .b span")) == 1
    assert len(seleccionar(raiz, ".a > div p > span")) == 1
    assert seleccionar(raiz, ".b > span") == []


def test_mayusculas_en_tipo_y_atributo() -> None:
    raiz = analizar('<DIV DATA-X="1"><SPAN>a</SPAN></DIV>')
    assert len(seleccionar(raiz, "DIV[DATA-X='1'] > Span")) == 1


def test_valor_de_atributo_distingue_mayusculas() -> None:
    raiz = analizar('<a class="Precio">a</a>')
    assert seleccionar(raiz, ".precio") == []
    assert len(seleccionar(raiz, ".Precio")) == 1


@pytest.mark.parametrize(
    "selector",
    [
        "",
        "   ",
        ",",
        "a,",
        ",a",
        "a,,b",
        "> a",
        "a >",
        "a > > b",
        "a ~ b",
        "a + b",
        "a:has(b)",
        "a:not(.x)",
        "li:first-child",
        "li:last-of-type",
        "li:nth-child(2)",
        "p:nth-of-type(2n+1)",
        "p:nth-of-type(odd)",
        "p:nth-of-type(0)",
        "p:nth-of-type()",
        "a::before",
        "a:hover",
        "[href",
        "[href=]",
        "[href='x]",
        "[href=a b]",
        "[href='x' i]",
        "[href!=x]",
        "[lang|=es]",
        "a|b",
        "ns|a",
        "#",
        ".",
        ".1abc",
        "#a#b",
        "a b c d e f g h i j k l m n o p q r s t u v w x y z",
        "a*",
        "a\\:b",
        "a{}",
        "a; b",
        "a\u0000",
        "a @href",
        "*" * (MAX_LONGITUD + 1),
        "div " * 200,
    ],
)
def test_lo_demas_se_rechaza(raiz: Nodo, selector: str) -> None:
    with pytest.raises(SelectorNoValido):
        seleccionar(raiz, selector)


def test_selector_no_texto_se_rechaza(raiz: Nodo) -> None:
    with pytest.raises(SelectorNoValido):
        seleccionar(raiz, 42)  # type: ignore[arg-type]


def test_extraer_valor_texto_o_atributo(raiz: Nodo) -> None:
    assert extraer_valor(raiz, "h2 a") == "Faro delantero izquierdo"
    assert extraer_valor(raiz, "h2 a @href") == "/pieza/101"
    assert extraer_valor(raiz, "[itemprop=price] @content") == "89.90"
    assert extraer_valor(raiz, "h2 a   @title") == "Faro"
    assert extraer_valor(raiz, "h2 a @no-existe") is None
    assert extraer_valor(raiz, ".nada") is None


def test_extraer_valor_salta_coincidencias_vacias() -> None:
    raiz = analizar('<a class="t"> </a><a class="t" href="">x</a><a class="t" href="/b">y</a>')
    assert extraer_valor(raiz, ".t") == "x"
    assert extraer_valor(raiz, ".t @href") == "/b"


def test_extraer_valor_del_propio_nodo(raiz: Nodo) -> None:
    enlace = seleccionar(raiz, "h2 a")[0]
    assert extraer_valor(enlace, "@href") == "/pieza/101"


@pytest.mark.parametrize("selector", ["", "a @", "a @href @title", "a @hr ef", "@", "@1x"])
def test_extraer_valor_rechaza_sufijos_raros(raiz: Nodo, selector: str) -> None:
    with pytest.raises(SelectorNoValido):
        extraer_valor(raiz, selector)


def test_extraer_valores_y_dividir_selector(raiz: Nodo) -> None:
    assert extraer_valores(raiz, "img @src") == ["/f/101-1.jpg", "/f/101-2.jpg"]
    assert extraer_valores(raiz, "h2 a") == ["Faro delantero izquierdo", "Piloto trasero"]
    assert dividir_selector("  img.foto   @SRC ") == ("img.foto", "src")
    assert dividir_selector("img.foto") == ("img.foto", None)
    assert dividir_selector("@href") == ("", "href")


def test_selector_no_valido_falla_aunque_no_haya_coincidencias() -> None:
    with pytest.raises(SelectorNoValido):
        extraer_valores(analizar(""), "a:has(b)")


def test_rendimiento_en_un_listado_grande() -> None:
    html = (
        "<ul>"
        + "".join(
            f'<li class="p"><div><a href="/p/{i}"><span>{i}</span></a></div></li>'
            for i in range(20_000)
        )
        + "</ul>"
    )
    raiz = analizar(html)
    inicio = time.perf_counter()
    assert len(seleccionar(raiz, "ul li.p div > a span")) == 20_000
    assert len(seleccionar(raiz, "li:nth-of-type(3) a")) == 1
    assert len(seleccionar(raiz, "div div div div span")) == 0
    assert time.perf_counter() - inicio < 5


def test_rendimiento_con_anidamiento_profundo() -> None:
    raiz = analizar("<div>" * 500 + "<span>x</span>")
    inicio = time.perf_counter()
    assert len(seleccionar(raiz, "div div div div div div div div span")) == 1
    assert len(seleccionar(raiz, "div > div div > div p")) == 0
    assert time.perf_counter() - inicio < 5


def test_selector_gigante_con_sufijo_se_rechaza_rapido() -> None:
    # Revisión: el sufijo « @atributo» se buscaba con una expresión cuadrática antes de
    # comprobar la longitud; un selector de 100 000 caracteres tardaba decenas de segundos.
    gigante = "a" + " " * 100_000 + "b"
    inicio = time.perf_counter()
    with pytest.raises(SelectorNoValido):
        dividir_selector(gigante)
    with pytest.raises(SelectorNoValido):
        extraer_valor(analizar("<a>x</a>"), gigante)
    assert time.perf_counter() - inicio < 1
