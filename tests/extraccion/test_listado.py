"""Resultados de la página de búsqueda de una tienda según su perfil."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.extraccion.listado import extraer_listado
from precia.extraccion.tipos import Precio, ResultadoListado, SelectorNoValido
from precia.navegador.tipos import Pagina
from precia.perfiles.tipos import Perfil

FIXTURES = Path(__file__).parent.parent / "fixtures" / "extraccion"
URL_BUSQUEDA = "https://www.desguaces-ejemplo.es/buscar?q=faro+golf"
BASE = "https://www.desguaces-ejemplo.es"

PERFIL = Perfil(
    dominio="desguaces-ejemplo.es",
    version=1,
    url_busqueda="https://www.desguaces-ejemplo.es/buscar?q={consulta}",
    sel_resultado="#resultados > div.resultado",
    sel_titulo="h3.nombre",
    sel_enlace="a.enlace @href",
    sel_precio_listado=".precio",
    sel_campos={},
    necesita_js=False,
    autor="manual",
    revisado=True,
    creado=datetime(2026, 10, 1, tzinfo=UTC),
)


def _pagina(html: str, url: str = URL_BUSQUEDA) -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html; charset=utf-8",
        obtenida=datetime(2026, 10, 19, tzinfo=UTC),
    )


@pytest.fixture(scope="module")
def pagina() -> Pagina:
    return _pagina((FIXTURES / "listado_tienda.html").read_text(encoding="utf-8"))


def test_listado_real(pagina: Pagina) -> None:
    assert extraer_listado(pagina, PERFIL) == [
        ResultadoListado(
            "Faro delantero izquierdo Golf VI",
            f"{BASE}/pieza/faro-delantero-izquierdo-golf-vi-77123",
            Precio(Decimal("89.90"), "EUR", "89,90 €"),
        ),
        ResultadoListado(
            "Faro delantero derecho Golf VI & regulador",
            f"{BASE}/pieza/faro-delantero-derecho-golf-vi-77124",
            Precio(Decimal("95.00"), "EUR", "95,00 €"),
        ),
        ResultadoListado("Piloto trasero Golf VI", f"{BASE}/pieza/piloto-trasero-golf-vi-77300"),
        ResultadoListado(
            "Mayúsculas", f"{BASE}/pieza/77303", Precio(Decimal("12.50"), "EUR", "12,50 €")
        ),
    ]


def test_maximo(pagina: Pagina) -> None:
    assert len(extraer_listado(pagina, PERFIL, maximo=2)) == 2
    assert extraer_listado(pagina, PERFIL, maximo=0) == []


def test_sin_selector_de_precio(pagina: Pagina) -> None:
    perfil = replace(PERFIL, sel_precio_listado=None)
    assert all(r.precio is None for r in extraer_listado(pagina, perfil))


def test_dominio_del_perfil_con_www(pagina: Pagina) -> None:
    perfil = replace(PERFIL, dominio="www.desguaces-ejemplo.es")
    assert len(extraer_listado(pagina, perfil)) == 4


def test_enlace_en_el_propio_resultado() -> None:
    html = (
        '<a class="p" href="/a">Uno</a><a class="p" href="https://tienda.es/b#x">Dos</a>'
        '<a class="p" href="https://tienda.es/c?utm_source=x&id=3&gclid=1">Tres</a>'
    )
    perfil = replace(PERFIL, dominio="tienda.es", sel_resultado="a.p", sel_titulo="@title")
    perfil = replace(perfil, sel_enlace="@href")
    # Sin título no hay resultado.
    assert extraer_listado(_pagina(html, "https://tienda.es/buscar?q=x"), perfil) == []
    perfil = replace(perfil, sel_titulo="*, b, i")
    html = html.replace(">Uno<", "><b>Uno</b><").replace(">Dos<", "><i>Dos</i><")
    html = html.replace(">Tres<", "><b>Tres</b><")
    resultados = extraer_listado(_pagina(html, "https://tienda.es/buscar?q=x"), perfil)
    assert [r.url for r in resultados] == [
        "https://tienda.es/a",
        "https://tienda.es/b",
        "https://tienda.es/c?id=3",
    ]


def test_dominios_internacionalizados() -> None:
    html = '<li><a href="https://xn--espaa-rta.es/p/1">Pieza</a></li><li><a href="https://españa.es/p/2">Otra</a></li>'
    perfil = replace(
        PERFIL, dominio="españa.es", sel_resultado="li", sel_titulo="a", sel_enlace="a @href"
    )
    resultados = extraer_listado(_pagina(html, "https://xn--espaa-rta.es/buscar"), perfil)
    assert [r.url for r in resultados] == [
        "https://xn--espaa-rta.es/p/1",
        "https://xn--espaa-rta.es/p/2",
    ]


def test_titulos_largos_se_recortan() -> None:
    html = f'<li><a href="/p">{"Faro " * 200}</a></li>'
    perfil = replace(PERFIL, sel_resultado="li", sel_titulo="a", sel_enlace="a @href")
    [resultado] = extraer_listado(_pagina(html), perfil)
    assert len(resultado.titulo) <= 300


def test_selector_no_valido_del_perfil() -> None:
    perfil = replace(PERFIL, sel_resultado="div:has(a)")
    with pytest.raises(SelectorNoValido):
        extraer_listado(_pagina("<div></div>"), perfil)


def test_pagina_vacia() -> None:
    assert extraer_listado(_pagina(""), PERFIL) == []


def test_enlaces_a_la_propia_pagina_no_son_resultados() -> None:
    # Revisión: `href="#"` o `href=""` (vista rápida, botones) se resolvían a la propia página
    # de búsqueda, que acababa abierta como si fuera una ficha.
    html = (
        '<li><a href="#">Vista rápida</a></li><li><a href="">Vacío</a></li>'
        '<li><a href="?q=faro+golf#top">Misma</a></li><li><a href="/p/1">Faro</a></li>'
    )
    perfil = replace(PERFIL, sel_resultado="li", sel_titulo="a", sel_enlace="a @href")
    resultados = extraer_listado(_pagina(html), perfil)
    assert [r.url for r in resultados] == [f"{BASE}/p/1"]


def test_la_consulta_de_la_url_se_codifica() -> None:
    # Revisión: la consulta se copiaba tal cual; un espacio o unas comillas acababan en la URL
    # que luego se descarga (y `http.client` rechaza las URL con espacios).
    html = '<li><a href="/p?ref=faro golf&amp;n=&quot;1&quot;">Faro</a></li>'
    perfil = replace(PERFIL, sel_resultado="li", sel_titulo="a", sel_enlace="a @href")
    [resultado] = extraer_listado(_pagina(html), perfil)
    assert resultado.url == f"{BASE}/p?ref=faro%20golf&n=%221%22"
    assert not any(c.isspace() or c == '"' for c in resultado.url)
