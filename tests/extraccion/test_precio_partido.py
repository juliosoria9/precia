"""Selector de precio que solo da un trozo del precio (causa R9-P de la campaña real).

Caso real (ronda 9, sesión S03): la IA del configurador propuso para la ficha de amazon.es
`#corePriceDisplay_desktop_feature_div span.priceToPay span.a-price-whole`, que solo da la parte
entera con la coma colgando («22,»): los céntimos y la moneda van en elementos hermanos. El
anclaje lo descartaba (bien) y ninguna ficha validaba. El antepasado cercano (`span.priceToPay`)
da el precio completo, «22,95€». Ahora, si lo seleccionado es un trozo así y un antepasado a
pocos niveles se lee como un único precio que empieza por ese entero, se lee el antepasado. Si
el antepasado tiene dos precios distintos (el tachado y el vigente), no se sube.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.extraccion.dom import analizar
from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.listado import extraer_listado
from precia.extraccion.precios import leer_precio, texto_de_precio
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import OrigenDato
from precia.perfiles.tipos import Perfil

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
AMAZON = "amazon_ficha_titulo_lejos_del_precio_chromium.html"
MEDIAMARKT = "mediamarkt_ficha_precio_partido_recorte.html"
ENTERO = "#corePriceDisplay_desktop_feature_div span.priceToPay span.a-price-whole"

PARTIDO = (
    '<div class="bloque"><span class="precio"><span class="oculto"></span>'
    '<span><span class="entero">22<span class="sep">,</span></span>'
    '<span class="fraccion">95</span><span class="moneda">€</span></span></span></div>'
)
CON_TACHADO = (
    '<div class="bloque"><span class="precio">'
    '<span class="entero">22<span class="sep">,</span></span>'
    '<span class="fraccion">95</span><span class="moneda">€</span>'
    "<del>26,95 €</del></span></div>"
)


def _pagina(html: str, url: str = "https://tienda.es/p/1") -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html",
        obtenida=datetime(2026, 10, 24, tzinfo=UTC),
    )


def _perfil(sel_precio_listado: str | None = None, **selectores: str) -> Perfil:
    return Perfil(
        dominio="tienda.es",
        version=1,
        url_busqueda="https://tienda.es/buscar?q={consulta}",
        sel_resultado=".resultado",
        sel_titulo="a",
        sel_enlace="a @href",
        sel_precio_listado=sel_precio_listado,
        sel_campos=selectores,
        necesita_js=False,
        autor="manual",
        revisado=True,
        creado=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_el_entero_con_la_coma_colgando_se_lee_en_el_antepasado() -> None:
    assert texto_de_precio(analizar(PARTIDO), ".entero") == "22,95€"


@pytest.mark.parametrize("selector", [".precio", ".fraccion", ".entero .sep"])
def test_lo_que_no_es_un_entero_colgando_no_cambia(selector: str) -> None:
    esperado = {".precio": "22,95€", ".fraccion": "95", ".entero .sep": ","}[selector]
    assert texto_de_precio(analizar(PARTIDO), selector) == esperado


def test_no_sube_a_un_antepasado_con_dos_precios_distintos() -> None:
    assert texto_de_precio(analizar(CON_TACHADO), ".entero") == "22,"


@pytest.mark.parametrize(
    "html",
    [
        '<div><span class="e">22,</span> <span>Envío 4,99 €</span></div>',
        # Otro importe que empieza igual: el del trozo no está en el HTML (lo pinta el JS).
        '<div><span class="e">22,</span> <span>Envío 22,50 €</span></div>',
        '<div class="c"><span class="e">22,</span><span></span></div><div>Envío 22,50 €</div>',
        '<span class="p">Unidades: <span class="e">3,</span> desde 3,50 €</span>',
    ],
)
def test_no_sube_si_el_antepasado_es_otro_importe(html: str) -> None:
    texto = texto_de_precio(analizar(html), ".e") or ""
    assert texto.endswith(",")
    assert leer_precio(texto) is None


@pytest.mark.parametrize(
    ("moneda", "separador", "esperado"),
    [("£", ".", "£22.95"), ("€ ", ",", "€ 22,95"), ("$", ".", "$22.95")],
)
def test_sube_con_la_moneda_delante(moneda: str, separador: str, esperado: str) -> None:
    html = (
        f'<span class="p"><span>{moneda}</span><span class="e">22<span>{separador}</span></span>'
        "<span>95</span></span>"
    )
    assert texto_de_precio(analizar(html), ".e") == esperado


@pytest.mark.parametrize(
    "html",
    [
        '<div><span class="o">Antes: <span class="e">39<span>,</span></span><span>90</span>'
        '<span>€</span></span><span class="n">29,90 €</span></div>',
        '<div>Antes: <span class="o"><span class="e">39<span>,</span></span><span>90</span>'
        '<span>€</span></span> <span class="n">29,90 €</span></div>',
        '<div><span class="o"><span class="e">39<span>,</span></span><span>90</span>'
        '<span>€</span></span> PVP recomendado <span class="n">29,90 €</span></div>',
    ],
)
def test_no_sube_al_precio_anunciado_como_antiguo(html: str) -> None:
    assert texto_de_precio(analizar(html), ".e") == "39,"


def test_devuelve_solo_el_precio_y_no_el_resto_del_antepasado() -> None:
    html = '<span class="p">Precio: <span class="e">22<span>,</span></span><span>95 €</span></span>'
    assert texto_de_precio(analizar(html), ".e") == "22,95 €"


def test_los_centimos_en_un_bloque_se_juntan_con_el_entero() -> None:
    html = (
        '<div class="a"><span class="w">49,</span><div><span>99</span></div><span> €</span></div>'
    )
    assert texto_de_precio(analizar(html), ".w") == "49,99 €"


@pytest.mark.parametrize("texto", ["29, 99 €", "29, 99 € 29,99€", "Ahora 29, 99 €"])
def test_los_centimos_sueltos_tras_un_entero_colgando_no_son_el_precio(texto: str) -> None:
    precio = leer_precio(texto)
    assert precio is None or precio.importe == Decimal("29.99")


def test_ficha_real_de_mediamarkt_con_el_selector_de_la_parte_entera() -> None:
    raiz = analizar((REALES / MEDIAMARKT).read_text(encoding="utf-8"))
    entero = texto_de_precio(raiz, '[data-test="branded-price-whole-value"]')
    precio = leer_precio(entero or "")
    assert precio is not None
    assert precio.importe == Decimal("49.99")
    # El contenedor del precio partido se lee «49, 99 €»: mejor sin precio que 99 €.
    assert leer_precio(texto_de_precio(raiz, "div.notranslate") or "") is None
    # Un nivel más arriba está también el texto accesible, «49,99€».
    precio = leer_precio(texto_de_precio(raiz, '[data-test~="mms-branded-price"]') or "")
    assert precio is not None
    assert precio.importe == Decimal("49.99")


def test_ficha_real_de_amazon_con_el_selector_de_la_parte_entera() -> None:
    html = (REALES / AMAZON).read_text(encoding="utf-8")
    pagina = _pagina(html, "https://www.amazon.es/dp/B07YG63BQJ")
    ficha = asyncio.run(extraer_ficha(pagina, ("precio",), perfil=_perfil(precio=ENTERO)))
    assert ficha.precio is not None
    assert ficha.precio.importe == Decimal("22.95")
    assert ficha.campos["precio"].origen is OrigenDato.PERFIL


def test_listado_con_el_selector_de_la_parte_entera() -> None:
    tarjeta = (
        '<div class="resultado"><a href="/p/1">Filtro de agua</a>'
        '<span class="a-price"><span class="ent">30<span>,</span></span>'
        "<span>46</span><span>€</span></span></div>"
    )
    pagina = _pagina(f"<html><body>{tarjeta}</body></html>", "https://tienda.es/buscar?q=x")
    resultados = extraer_listado(pagina, _perfil(sel_precio_listado=".ent"))
    assert len(resultados) == 1
    assert resultados[0].precio is not None
    assert resultados[0].precio.importe == Decimal("30.46")


@pytest.mark.parametrize("separador", [".", "\xa0"])
def test_el_entero_con_miles_tambien(separador: str) -> None:
    html = (
        f'<div><span class="p"><span class="e">1{separador}234,</span>'
        "<span>56</span><span>€</span></span></div>"
    )
    texto = texto_de_precio(analizar(html), ".e") or ""
    assert texto.replace(" ", separador) == f"1{separador}234,56€"
