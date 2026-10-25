"""R2-C13: tarjeta que es el propio enlace (`<a href>` que envuelve el producto).

En la búsqueda real de PcComponentes cada resultado es `<a class="wrapper-…" href>` y dentro va
`div.product-card`. El validador decía «aparece en la página, pero no dentro de ningún elemento
de sel_resultado» aunque el enlace fuera el propio resultado o lo envolviera, y la IA no sabía
que «@href» a secas lee el atributo del resultado.
"""

from pathlib import Path

import pytest

from precia.extraccion.dom import Nodo, analizar
from precia.perfiles.configurador import INSTRUCCIONES_CATEGORIA, INSTRUCCIONES_LISTADO
from precia.perfiles.reparador import INSTRUCCIONES as INSTRUCCIONES_REPARADOR
from precia.perfiles.tipos import Perfil
from precia.perfiles.validador import selectores_ausentes
from tests.perfiles.sitio import perfil

REALES = Path(__file__).parent.parent / "fixtures" / "reales"


def _raiz() -> Nodo:
    html = (REALES / "pccomponentes_busqueda_tarjeta_enlace.html").read_bytes().decode("utf-8")
    return analizar(html)


def _pccomponentes(sel_resultado: str, sel_enlace: str) -> Perfil:
    return perfil(
        dominio="pccomponentes.com",
        sel_resultado=sel_resultado,
        sel_titulo="h3.product-card__title",
        sel_enlace=sel_enlace,
        sel_precio_listado="[data-e2e=price-card]",
    )


@pytest.mark.parametrize(
    ("sel_resultado", "sel_enlace"),
    [
        ("a.wrapper-s5NDOZ", "@href"),
        ("a.wrapper-s5NDOZ", "a.wrapper-s5NDOZ @href"),
        ("div.product-card", "a @href"),
        ("div.product-card", "a.wrapper-s5NDOZ @href"),
    ],
)
def test_el_enlace_que_es_o_envuelve_el_resultado_no_es_un_problema(
    sel_resultado: str, sel_enlace: str
) -> None:
    problemas = selectores_ausentes(_raiz(), _pccomponentes(sel_resultado, sel_enlace))
    assert problemas == []


@pytest.mark.parametrize("sel_enlace", ["h3.product-card__title @href", "a.no-existe @href"])
def test_si_el_enlace_no_sale_y_el_resultado_esta_dentro_de_un_enlace_se_sugiere(
    sel_enlace: str,
) -> None:
    problemas = selectores_ausentes(_raiz(), _pccomponentes("div.product-card", sel_enlace))
    (problema,) = [p for p in problemas if p.startswith("sel_enlace")]
    assert "Cada resultado está dentro de un enlace <a href>" in problema
    assert "usa ese <a> como sel_resultado y «@href» a secas como sel_enlace" in problema


def test_si_el_resultado_ya_es_el_enlace_se_sugiere_href_a_secas() -> None:
    problemas = selectores_ausentes(
        _raiz(), _pccomponentes("a.wrapper-s5NDOZ", "span.no-existe @href")
    )
    (problema,) = [p for p in problemas if p.startswith("sel_enlace")]
    assert "Cada resultado ya es un enlace <a href>: usa «@href» a secas como sel_enlace." in (
        problema
    )


def test_sin_enlace_alrededor_no_se_sugiere_nada() -> None:
    raiz = analizar(
        '<div class="card"><h3>Faro</h3><a class="ver" href="/p1">ver</a></div><h1>Resultados</h1>'
    )
    problemas = selectores_ausentes(
        raiz, perfil(sel_resultado="div.card", sel_titulo="h1", sel_enlace="a.no @href")
    )
    assert problemas
    assert not any("<a href>" in p for p in problemas), problemas


@pytest.mark.parametrize(
    "instrucciones",
    [INSTRUCCIONES_LISTADO, INSTRUCCIONES_CATEGORIA, INSTRUCCIONES_REPARADOR],
    ids=["listado", "categoria", "reparador"],
)
def test_las_instrucciones_explican_el_resultado_que_es_el_enlace(instrucciones: str) -> None:
    assert (
        "si cada resultado es en sí mismo el enlace (un <a href> que envuelve la tarjeta), "
        "sel_resultado es ese <a> y sel_enlace es «@href» a secas, sin selector"
    ) in instrucciones
