"""Causa R5-C de la campaña: el prefijo de idioma de la portada (R4-C) llega al catálogo.

En la ronda 5, Decántalo (portada final `https://www.decantalo.com/es/es/`) guardó 42 449 URL de
`/es/es/`, `/es/ca/`, `/au/en/`, `/bg/en/`… El filtro de R4-C estaba en `catalogos()`, pero el
configurador le pasaba solo el origen de la portada (sin la ruta) y al rehacer el catálogo cada
7 días solo se conocía el sitemap: en los dos casos el prefijo era `()` y no se filtraba nada.
Ahora el configurador pasa la URL final entera, el catálogo lleva su `idioma` y el almacén lo
guarda para rehacerlo con el mismo filtro. Sitemaps reales de Decántalo
(`tests/fixtures/reales/decantalo_*.xml`), sin red.
"""

import asyncio
from pathlib import Path

from precia.perfiles.catalogo import Catalogo, descubrir_catalogo, leer_catalogo, leer_hijos
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    PORTADA,
    PRODUCTOS,
    TIENDA,
    CatalogoFalso,
    NavegadorFalso,
    html_ficha,
    html_portada,
    paginas_sitio,
    xml_indice,
    xml_urlset,
)
from tests.perfiles.test_configurador_sin_buscador import SIN_PROPUESTA, ejecutar

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
DECANTALO = "https://www.decantalo.com"
RAIZ = f"{DECANTALO}/sitemap.xml"
LG = DECANTALO + "/{}/module/lgsitemaps/sitemap?{}"
ES_INDICE = LG.format("es/es", "name=sitemap_1")
ES_FICHAS = LG.format("es/es", "fc=module&name=product_1_1")
AU_INDICE = LG.format("au/en", "name=sitemap_6")
AU_FICHAS = LG.format("au/en", "fc=module&name=product_6_1")
VINOS = [
    f"{DECANTALO}/es/es/edetaria-seleccio-vi-de-finca-qualifica-el-mas.html",
    f"{DECANTALO}/es/es/vina-mayor-reserva.html",
    f"{DECANTALO}/es/es/david-duband-cote-de-nuits-village-rouge.html",
]
WINES = [f"{DECANTALO}/au/en/vina-mayor-reserva-red.html", f"{DECANTALO}/au/en/lacima-wine.html"]


def _real(nombre: str) -> str:
    return (REALES / nombre).read_text(encoding="utf-8")


def _decantalo() -> NavegadorFalso:
    return NavegadorFalso(
        {
            RAIZ: _real("decantalo_sitemap_indice_paises.xml"),
            ES_INDICE: _real("decantalo_sitemap_es_es.xml"),
            ES_FICHAS: _real("decantalo_sitemap_productos_es_es.xml"),
            AU_INDICE: _real("decantalo_sitemap_au_en.xml"),
            AU_FICHAS: xml_urlset(WINES),
        },
        declarados=(RAIZ,),
    )


def _leer(idioma: tuple[str, ...]) -> Catalogo:
    catalogo = asyncio.run(
        leer_catalogo(
            RAIZ, "decantalo.com", _decantalo(), validador=validar_con_dns_publico, idioma=idioma
        )
    )
    assert catalogo is not None
    return catalogo


# --- perfiles.catalogo ---------------------------------------------------------------------------


def test_el_catalogo_lleva_el_prefijo_de_idioma_de_la_portada() -> None:
    catalogo = asyncio.run(
        descubrir_catalogo(
            f"{DECANTALO}/es/es/",
            "decantalo.com",
            _decantalo(),
            None,
            validador=validar_con_dns_publico,
        )
    )
    assert catalogo is not None
    assert catalogo.idioma == ("es", "es")
    assert [e.url for e in catalogo.entradas] == VINOS


def test_rehacer_con_el_prefijo_guardado_no_mete_otros_paises() -> None:
    catalogo = _leer(("es", "es"))
    assert [e.url for e in catalogo.entradas] == VINOS
    assert catalogo.idioma == ("es", "es")
    assert AU_FICHAS in catalogo.pendientes


def test_rehacer_sin_prefijo_queda_como_antes() -> None:
    # Una tienda sin prefijo (los recambios): ninguna URL sale por idioma.
    catalogo = _leer(())
    assert catalogo.idioma == ()
    assert set(VINOS) <= {e.url for e in catalogo.entradas}


def test_los_hijos_pendientes_se_leen_con_el_prefijo_guardado() -> None:
    catalogo = asyncio.run(
        leer_hijos(
            RAIZ,
            [AU_FICHAS, ES_FICHAS],
            "decantalo.com",
            _decantalo(),
            validador=validar_con_dns_publico,
            idioma=("es", "es"),
        )
    )
    assert catalogo is not None
    assert [e.url for e in catalogo.entradas] == VINOS


# --- De extremo a extremo: el configurador -------------------------------------------------------

EN_ESPANOL = f"{TIENDA}/es/es/"
INDICE = f"{TIENDA}/sitemap.xml"
DE_AUSTRALIA = f"{TIENDA}/au/en/sitemap.xml"
DE_ESPANA = f"{TIENDA}/es/es/sitemap.xml"
FICHAS_ES = [f"{TIENDA}/es/es{p.ruta}" for p in PRODUCTOS]
FICHAS_AU = [f"{TIENDA}/au/en/p/front-headlamp-golf-{n}" for n in range(1, 4)]


def test_configurar_con_la_portada_en_un_prefijo_solo_guarda_su_pais() -> None:
    # Como en Decántalo: la portada redirige a /es/es/ y el índice trae antes otro país.
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[EN_ESPANOL] = html_portada(formulario=False)
    for p, url in zip(PRODUCTOS, FICHAS_ES, strict=True):
        sitio[url] = html_ficha(p, jsonld=True)
    sitio[INDICE] = xml_indice([DE_AUSTRALIA, DE_ESPANA])
    sitio[DE_AUSTRALIA] = xml_urlset(FICHAS_AU)
    sitio[DE_ESPANA] = xml_urlset(FICHAS_ES)
    navegador = NavegadorFalso(sitio, declarados=(INDICE,), redirecciones={PORTADA: EN_ESPANOL})
    catalogo = CatalogoFalso()
    perfil, _ = ejecutar(navegador, [SIN_PROPUESTA], catalogo=catalogo)
    assert perfil.es_sitemap
    ((_, sitemap, entradas, _),) = catalogo.guardados
    assert sitemap == INDICE
    assert [url for url, _, _ in entradas] == FICHAS_ES
    assert catalogo.idiomas == [("es", "es")]


def test_configurar_con_la_portada_en_la_raiz_no_cambia() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None, jsonld=(0, 1, 2, 3))
    sitio[INDICE] = xml_urlset([p.url for p in PRODUCTOS])
    navegador = NavegadorFalso(sitio, declarados=(INDICE,))
    catalogo = CatalogoFalso()
    perfil, _ = ejecutar(navegador, [SIN_PROPUESTA], catalogo=catalogo)
    assert perfil.es_sitemap
    assert [url for url, _, _ in catalogo.guardados[0][2]] == [p.url for p in PRODUCTOS]
    assert catalogo.idiomas == [()]
