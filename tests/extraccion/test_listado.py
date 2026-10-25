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
REALES = Path(__file__).parent.parent / "fixtures" / "reales"
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
            # La foto de la tarjeta; el logo de la cabecera está fuera de los resultados.
            f"{BASE}/media/piezas/77123-1.jpg",
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


@pytest.mark.parametrize("selector", [".price", ".price bdi"])
def test_oferta_woocommerce_lee_el_precio_vigente(selector: str) -> None:
    html = (
        '<li><a href="/p/1">Faro Golf</a><p class="price"><del><bdi>59,90&nbsp;€</bdi></del> '
        "<ins><bdi>39,90&nbsp;€</bdi></ins></p></li>"
        '<li><a href="/p/2">Piloto Golf</a><p class="price"><bdi>48,40&nbsp;€</bdi> '
        "<small>40,00 € sin IVA</small></p></li>"
    )
    perfil = replace(
        PERFIL,
        sel_resultado="li",
        sel_titulo="a",
        sel_enlace="a @href",
        sel_precio_listado=selector,
    )
    resultados = extraer_listado(_pagina(html), perfil)
    assert [r.precio.importe if r.precio else None for r in resultados] == [
        Decimal("39.90"),
        Decimal("48.40"),
    ]


def test_oscaro_real_lee_el_precio_actual() -> None:
    # Tarjetas reales de Oscaro: «Ancien prix : 17,06 €» va antes que «Prix actuel : 14,50 €»
    # en `div.price`, y el listado se quedaba con el tachado.
    url = "https://www.oscaro.es/es/search?q=faro"
    html = (REALES / "oscaro_busqueda_faro_ofertas.html").read_text(encoding="utf-8")
    perfil = replace(
        PERFIL,
        dominio="oscaro.es",
        url_busqueda="https://www.oscaro.es/es/search?q={consulta}",
        sel_resultado="article.productbox",
        sel_titulo="h2",
        sel_enlace="a @href",
        sel_precio_listado="div.price",
    )
    resultados = extraer_listado(_pagina(html, url), perfil)
    assert [r.precio.importe if r.precio else None for r in resultados] == [
        Decimal("14.50"),
        Decimal("126.62"),
        Decimal("47.35"),
    ]
    assert resultados[2].url.endswith("-2423844-259-p")


def test_mediamarkt_real_lee_el_precio_vigente_y_no_el_pvp_tachado() -> None:
    # R2-B4: el PVP tachado («39,90 €») va en `data-test="mms-strike-price-type-rrp"`, sin clase
    # ni id que lo delaten, delante del vigente («29,99 €») en el mismo bloque de precio.
    url = "https://www.mediamarkt.es/es/search.html?query=filtro"
    html = (REALES / "mediamarkt_busqueda_filtro_tarjetas.html").read_bytes().decode("utf-8")
    perfil = replace(
        PERFIL,
        dominio="mediamarkt.es",
        url_busqueda="https://www.mediamarkt.es/es/search.html?query={consulta}",
        sel_resultado="[data-test=mms-product-card]",
        sel_titulo="h3",
        sel_enlace="a[data-test=mms-router-link-product-list-item-link] @href",
        sel_precio_listado="[data-test=mms-price]",
    )
    resultados = extraer_listado(_pagina(html, url), perfil)
    assert [r.precio.importe if r.precio else None for r in resultados] == [
        Decimal("29.99"),
        Decimal("30.99"),
        Decimal("26.99"),
    ]


# --- R2-C13: la tarjeta es el propio enlace (`<a href>` que envuelve el producto) -------------

PCCOMPONENTES = replace(
    PERFIL,
    dominio="pccomponentes.com",
    url_busqueda="https://www.pccomponentes.com/buscar/?query={consulta}",
    sel_titulo="h3.product-card__title",
    sel_precio_listado="[data-e2e=price-card]",
)


@pytest.mark.parametrize(
    ("sel_resultado", "sel_enlace"),
    [
        ("a.wrapper-s5NDOZ", "@href"),
        ("a.wrapper-s5NDOZ", "a.wrapper-s5NDOZ @href"),
        ("a.wrapper-s5NDOZ", "a @href"),
        ("div.product-card", "a @href"),
        ("div.product-card", "a.wrapper-s5NDOZ @href"),
    ],
)
def test_pccomponentes_real_tarjeta_que_es_el_propio_enlace(
    sel_resultado: str, sel_enlace: str
) -> None:
    # En la rejilla real cada resultado es `<a class="wrapper-…" href>` que envuelve
    # `div.product-card`: el enlace no es descendiente del resultado, sino el resultado mismo o
    # su ancestro. El menú tiene otro `<a class="wrapper-s5NDOZ">` fuera de la rejilla.
    url = "https://www.pccomponentes.com/search/?query=filtro"
    html = (REALES / "pccomponentes_busqueda_tarjeta_enlace.html").read_bytes().decode("utf-8")
    perfil = replace(PCCOMPONENTES, sel_resultado=sel_resultado, sel_enlace=sel_enlace)
    resultados = extraer_listado(_pagina(html, url), perfil)
    assert [r.url for r in resultados] == [
        "https://www.pccomponentes.com/aspiradora-sin-cable-dreame-r20-slim-190aw-motor-sin-"
        "escobillas-autonomia-90min-filtro-multietapa",
        "https://www.pccomponentes.com/aspiradora-en-seco-y-humedo-dyson-v16-piston-animal-"
        "submarine-sin-cable-900-w-filtro-hepa-autonomia-70-min",
        "https://www.pccomponentes.com/aspiradora-sin-cable-rowenta-x-force-flex-9-60-rh2048wo-"
        "100aw-filtro-lavable-autonomia-45min",
    ]
    assert resultados[0].titulo.startswith("Aspiradora sin cable Dreame R20 Slim")
    assert [r.precio.importe if r.precio else None for r in resultados] == [
        Decimal("199"),
        Decimal("809.01"),
        Decimal("167.92"),
    ]


def _tarjetas(cuerpo: str) -> Pagina:
    return _pagina(f'<html><body><div id="resultados">{cuerpo}</div></body></html>')


def test_el_enlace_que_envuelve_a_varios_resultados_no_se_usa() -> None:
    # Una envoltura común a toda la rejilla no es el enlace de ningún resultado.
    pagina = _tarjetas(
        '<a class="rejilla" href="/ofertas">'
        '<div class="card"><h3>Faro izquierdo</h3></div>'
        '<div class="card"><h3>Faro derecho</h3></div>'
        "</a>"
    )
    perfil = replace(PERFIL, sel_resultado="div.card", sel_titulo="h3", sel_enlace="a @href")
    assert extraer_listado(pagina, perfil) == []


def test_si_un_descendiente_encaja_gana_al_enlace_que_envuelve_el_resultado() -> None:
    pagina = _tarjetas(
        '<div class="x" data-url="/fuera">'
        '<div class="card"><h3>Faro izquierdo</h3><span class="x" data-url="/dentro"></span></div>'
        "</div>"
    )
    perfil = replace(PERFIL, sel_resultado="div.card", sel_titulo="h3", sel_enlace=".x @data-url")
    assert [r.url for r in extraer_listado(pagina, perfil)] == [f"{BASE}/dentro"]


def test_el_enlace_lejano_no_se_toma_por_el_del_resultado() -> None:
    # Solo cuentan el propio resultado y sus ancestros cercanos, no un `<a>` cualquiera de
    # más arriba (aquí, cinco niveles por encima y con un solo resultado dentro).
    pagina = _tarjetas(
        '<a href="/lejos"><div><div><div><div><div class="card"><h3>Faro</h3></div>'
        "</div></div></div></div></a>"
    )
    perfil = replace(PERFIL, sel_resultado="div.card", sel_titulo="h3", sel_enlace="a @href")
    assert extraer_listado(pagina, perfil) == []


def test_el_titulo_no_se_busca_fuera_del_resultado() -> None:
    # El respaldo es solo para sel_enlace: el título sigue siendo relativo al resultado.
    pagina = _tarjetas('<a href="/p1" title="Faro"><div class="card"><span>x</span></div></a>')
    perfil = replace(PERFIL, sel_resultado="div.card", sel_titulo="a @title", sel_enlace="a @href")
    assert extraer_listado(pagina, perfil) == []
