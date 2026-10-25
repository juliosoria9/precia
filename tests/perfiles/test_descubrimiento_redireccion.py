"""Causa R3-G de la campaña multi-sector: una ruta de búsqueda que redirige y pierde la consulta.

El Corte Inglés no tiene formulario ni enlace de búsqueda en la portada (su `input#main_search`
está deshabilitado y fuera de todo `<form>`) y su `/search?q=filtro` responde 200 tras redirigir a
`/search-nwx/` sin la consulta. `parece_listado` rechaza esa página con razón, pero nadie probaba
la ruta final con la consulta: ahora se prueba con los `PARAMETROS_ENLACE`, detrás de las
candidatas de siempre y antes de Chromium.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from precia.perfiles.descubrimiento import (
    ORIGEN_PATRON,
    ORIGEN_REDIRECCION,
    PARAMETROS_ENLACE,
    abrir_portada,
    descubrir,
)
from precia.perfiles.tipos import ConfiguracionFallida
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import NavegadorFalso

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
NWX_HTML = (REALES / "elcorteingles_search_nwx_sin_consulta.html").read_text(encoding="utf-8")
PORTADA_HTML = (REALES / "elcorteingles_portada_sin_buscador_chromium.html").read_text(
    encoding="utf-8"
)

ECI = "elcorteingles.es"
TIENDA = f"https://www.{ECI}"
PORTADA = f"{TIENDA}/"
CONSULTA = "filtro"
SEARCH = f"{TIENDA}/search?q=filtro"
NWX = f"{TIENDA}/search-nwx/"
BUENA = f"{NWX}?s=filtro"


def listado() -> str:
    tarjetas = "".join(
        f'<div class="tarjeta"><a href="/electrodomesticos/filtro-{n}/">Filtro de agua {n}</a> '
        f"<span>{n},99 €</span></div>"
        for n in range(1, 5)
    )
    return (
        f"<html><head><title>filtro · El Corte Inglés</title></head><body>{tarjetas}</body></html>"
    )


def descubrir_con(navegador: NavegadorFalso) -> Any:
    async def probar() -> Any:
        host = f"www.{ECI}"
        portada = await abrir_portada(host, ECI, navegador, validador=validar_con_dns_publico)
        return await descubrir(
            portada, host, ECI, navegador, CONSULTA, validador=validar_con_dns_publico
        )

    return asyncio.run(probar())


def sitio(**paginas: str) -> dict[str, str]:
    return {PORTADA: PORTADA_HTML, NWX: NWX_HTML, **paginas}


def test_el_corte_ingles_prueba_la_ruta_final_con_la_consulta() -> None:
    # Como en la tienda: `/search?q=` y `/search-nwx/?q=` acaban en `/search-nwx/` vacía.
    navegador = NavegadorFalso(
        sitio(**{BUENA: listado()}),
        redirecciones={SEARCH: NWX, f"{NWX}?q=filtro": NWX},
    )
    busqueda = descubrir_con(navegador)
    assert busqueda.plantilla == f"{NWX}?s={{consulta}}"
    assert busqueda.origen == ORIGEN_REDIRECCION
    assert not busqueda.necesita_js
    # Detrás de las candidatas de siempre (por HTTP) y antes de pedir nada más a Chromium.
    antes = navegador.pedidas[: navegador.pedidas.index((BUENA, False))]
    assert (SEARCH, False) in antes
    assert [url for url, js in antes if js] == [PORTADA]


def test_las_derivadas_van_detras_de_las_candidatas_de_siempre() -> None:
    # Una tienda que ya se configuraba con un patrón posterior no hace ninguna descarga de más.
    magento = f"{TIENDA}/catalogsearch/result/?q=filtro"
    navegador = NavegadorFalso(sitio(**{magento: listado()}), redirecciones={SEARCH: NWX})
    busqueda = descubrir_con(navegador)
    assert busqueda.plantilla == f"{TIENDA}/catalogsearch/result/?q={{consulta}}"
    assert busqueda.origen == ORIGEN_PATRON
    assert not any(url.startswith(f"{NWX}?") for url in navegador.urls)


def test_las_derivadas_tambien_se_prueban_con_chromium() -> None:
    # Por HTTP la ruta final con la consulta es el cascarón vacío; los resultados los pinta el JS.
    navegador = NavegadorFalso(
        sitio(**{BUENA: NWX_HTML}),
        paginas_js={BUENA: listado()},
        redirecciones={SEARCH: NWX},
    )
    busqueda = descubrir_con(navegador)
    assert busqueda.plantilla == f"{NWX}?s={{consulta}}"
    assert busqueda.necesita_js
    derivadas = [(u, js) for u, js in navegador.pedidas if u.startswith(f"{NWX}?")]
    assert len([d for d in derivadas if not d[1]]) == len(PARAMETROS_ENLACE)
    primera_js = next(i for i, (u, js) in enumerate(navegador.pedidas) if js and u != PORTADA)
    assert navegador.pedidas.index((BUENA, False)) < primera_js


@pytest.mark.parametrize(
    "final",
    [
        PORTADA,  # La portada: una ruta que no existe.
        f"{TIENDA}/ayuda/",  # Sin palabra de búsqueda (login, ayuda...).
        f"{TIENDA}/es/",  # Solo un prefijo de idioma.
        f"{NWX}?s=filtro",  # La consulta sigue en la URL final.
        f"{TIENDA}/search/filtro/",  # La consulta como tramo de la ruta.
    ],
    ids=["portada", "sin-busqueda", "idioma", "consulta-en-query", "consulta-en-ruta"],
)
def test_redirecciones_que_no_dan_plantillas(final: str) -> None:
    paginas = sitio() if final == PORTADA else sitio(**{final: NWX_HTML})
    navegador = NavegadorFalso(paginas, redirecciones={SEARCH: final})
    with pytest.raises(ConfiguracionFallida):
        descubrir_con(navegador)
    pedidas = {url for url, _ in navegador.pedidas}
    base = final.split("?", 1)[0]
    derivadas = {f"{base}?{p}=filtro" for p in PARAMETROS_ENLACE} - {final}
    assert not derivadas & pedidas


def test_solo_se_deriva_de_una_ruta() -> None:
    # Dos rutas habituales que pierden la consulta: se deriva solo de la primera que se prueba
    # (`/buscar?q=` va antes que `/search?q=` en `PATRONES`), como tope de descargas.
    otra = f"{TIENDA}/search-results/"
    navegador = NavegadorFalso(
        sitio(**{otra: NWX_HTML}),
        redirecciones={SEARCH: NWX, f"{TIENDA}/buscar?q=filtro": otra},
    )
    with pytest.raises(ConfiguracionFallida):
        descubrir_con(navegador)
    assert any(url.startswith(f"{otra}?") for url in navegador.urls)
    assert not any(url.startswith(f"{NWX}?") for url in navegador.urls)
