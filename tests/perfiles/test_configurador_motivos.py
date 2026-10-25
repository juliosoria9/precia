"""El configurador deja escrito por qué descartó cada paso antes del perfil que sale (R2-OBS).

Causa R2-OBS de la campaña multi-sector (ronda 2): en MediaMarkt y PcComponentes el buscador
HTML se descubría, la IA proponía selectores que no validaban y el perfil salía del catálogo por
sitemap con una nota que no decía nada del buscador, ni quedaba en el registro qué selectores
había propuesto la IA. La búsqueda es un recorte de la real de MediaMarkt
(`tests/fixtures/reales/mediamarkt_busqueda_filtro_tarjetas.html`); la portada, el sitemap y las
fichas son sintéticos y mínimos. Sin red.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from precia.ia.simulado import Guion
from precia.perfiles.configurador import COMPONENTE, MAX_DESCARTE, con_descartes, configurar
from precia.perfiles.tipos import TIPO_HTML, TIPO_SITEMAP, Perfil
from precia.perfiles.validador import MAX_RESUMEN
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    CAMPOS_OK,
    CONSULTA,
    INSTANTE,
    LISTADO_OK,
    CatalogoFalso,
    NavegadorFalso,
    paginas_sitio,
    sesion,
    xml_indice,
    xml_urlset,
)

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
TIENDA = "https://www.mediamarkt.es"
BUSQUEDA = f"{TIENDA}/es/search.html?query=filtro"
PLANTILLA = f"{TIENDA}/es/search.html?query={{consulta}}"
INDICE = f"{TIENDA}/sitemaps/sitemap-index.xml"
DE_FICHAS = f"{TIENDA}/sitemaps/sitemap-productdetailspages-000.xml"
PORTADA = (
    "<html><head><title>MediaMarkt</title></head><body><header><a href='/es/'>MediaMarkt</a>"
    '<form action="/es/search.html" method="get" role="search">'
    '<input id="search-form" name="query" type="search"></form></header></body></html>'
)
SELECTOR_INVENTADO = "div.tarjeta-producto-inventada"
"""Lo que propone la IA y no existe en la página (como en la ronda 2)."""
LISTADO_MALO: dict[str, Any] = {
    "sel_resultado": SELECTOR_INVENTADO,
    "sel_titulo": "h2.titulo",
    "sel_enlace": "a @href",
    "sel_precio_listado": None,
}


def _busqueda() -> str:
    return (REALES / "mediamarkt_busqueda_filtro_tarjetas.html").read_bytes().decode("utf-8")


def _fichas() -> list[str]:
    return [TIENDA + ruta for ruta in re.findall(r'href="(/es/product/[^"]+)"', _busqueda())]


def _ficha(url: str, n: int) -> str:
    """Ficha sintética con el precio en el JSON-LD y a la vista: el sitemap vale sin IA."""
    importe = f"{20 + n}.99"
    producto = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": f"Filtro de agua {n}",
        "offers": {"@type": "Offer", "url": url, "priceCurrency": "EUR", "price": importe},
    }
    return (
        f"<html><head><title>Filtro de agua {n}</title>"
        f'<script type="application/ld+json">{json.dumps(producto)}</script></head>'
        f"<body><h1>Filtro de agua {n}</h1><span class='precio'>{importe.replace('.', ',')} €"
        "</span></body></html>"
    )


def _mediamarkt() -> NavegadorFalso:
    fichas = _fichas()
    sitio = {f"{TIENDA}/": PORTADA, BUSQUEDA: _busqueda()}
    sitio.update({url: _ficha(url, n) for n, url in enumerate(fichas)})
    sitio[INDICE] = xml_indice([DE_FICHAS])
    sitio[DE_FICHAS] = xml_urlset(fichas)
    return NavegadorFalso(sitio, declarados=(INDICE,))


def _configurar(
    navegador: NavegadorFalso, guion: list[Guion], dominio: str, consulta: str, **opciones: Any
) -> tuple[Perfil, list[Any]]:
    sesion_ia, proveedor = sesion({COMPONENTE: guion})
    perfil = asyncio.run(
        configurar(
            dominio,
            navegador,
            sesion_ia,
            consulta,
            validador=validar_con_dns_publico,
            reloj=lambda: INSTANTE,
            **opciones,
        )
    )
    return perfil, proveedor.peticiones


def test_la_busqueda_real_de_mediamarkt_se_descubre_y_la_ia_no_la_valida() -> None:
    """Comprobación del escenario: el buscador se descubre y la IA gasta 2 llamadas en él."""
    navegador = _mediamarkt()
    perfil, peticiones = _configurar(
        navegador,
        [LISTADO_MALO, LISTADO_MALO],
        "www.mediamarkt.es",
        "filtro",
        catalogo=CatalogoFalso(),
    )
    assert BUSQUEDA in navegador.urls
    assert len(peticiones) == 2
    assert perfil.tipo == TIPO_SITEMAP
    assert perfil.url_busqueda == INDICE


def test_la_nota_del_perfil_sitemap_dice_por_que_se_descarto_el_buscador() -> None:
    perfil, _ = _configurar(
        _mediamarkt(),
        [LISTADO_MALO, LISTADO_MALO],
        "www.mediamarkt.es",
        "filtro",
        catalogo=CatalogoFalso(),
    )
    # El origen y la validación del perfil que sale, primero; lo descartado, al final.
    assert perfil.nota.startswith("Catálogo por sitemap (3 URL), sin IA. Válido:")
    assert "descartado" in perfil.nota
    assert PLANTILLA in perfil.nota
    assert "La IA no ha conseguido un perfil válido" in perfil.nota
    assert len(perfil.nota) <= MAX_RESUMEN


def test_el_registro_guarda_el_motivo_y_los_selectores_que_propuso_la_ia(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="precia.perfiles.configurador"):
        _configurar(
            _mediamarkt(),
            [LISTADO_MALO, LISTADO_MALO],
            "www.mediamarkt.es",
            "filtro",
            catalogo=CatalogoFalso(),
        )
    mensajes = [r.getMessage() for r in caplog.records if r.name == "precia.perfiles.configurador"]
    propuestos = [m for m in mensajes if "la IA propone" in m and SELECTOR_INVENTADO in m]
    assert len(propuestos) == 2  # Un registro por intento, con lo que propuso la IA.
    assert all("mediamarkt.es" in m for m in propuestos)
    descartes = [m for m in mensajes if "descartado" in m]
    assert len(descartes) == 1
    assert PLANTILLA in descartes[0]
    # Una sola línea por registro: lo que viene de la IA o de la página no la parte.
    assert all("\n" not in m for m in mensajes)


def test_un_selector_con_saltos_de_linea_no_parte_el_registro(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raro = {**LISTADO_MALO, "sel_titulo": "h2\nINFO falso: perfil válido"}
    with caplog.at_level(logging.INFO, logger="precia.perfiles.configurador"):
        _configurar(
            _mediamarkt(), [raro, raro], "www.mediamarkt.es", "filtro", catalogo=CatalogoFalso()
        )
    mensajes = [r.getMessage() for r in caplog.records if r.name == "precia.perfiles.configurador"]
    assert mensajes
    assert all("\n" not in m and "\r" not in m for m in mensajes)


def test_si_el_buscador_vale_a_la_primera_la_nota_no_cambia() -> None:
    """No regresión (recambios): sin pasos descartados, la nota es la de siempre."""
    navegador = NavegadorFalso(paginas_sitio())
    perfil, _ = _configurar(navegador, [LISTADO_OK, CAMPOS_OK], "www.recambios-prueba.es", CONSULTA)
    assert perfil.tipo == TIPO_HTML
    assert perfil.nota.startswith("Válido:")
    assert "descartado" not in perfil.nota
    assert "Antes" not in perfil.nota


def test_los_descartes_caben_en_la_nota_aunque_haya_que_recortar() -> None:
    larga = "Catálogo por sitemap (9 URL), sin IA. Válido: " + "x" * (MAX_RESUMEN - 60)
    motivos = ["Buscador https://t.es/b?q={consulta}: " + "y" * 900, "Otro paso sin punto"]
    nota = con_descartes(larga, motivos)
    assert len(nota) == MAX_RESUMEN
    assert nota.startswith("Catálogo por sitemap (9 URL), sin IA. Válido: xxx")
    assert "Antes, descartado: Buscador https://t.es/b?q={consulta}: yyy" in nota
    corta = con_descartes("Válido: 3 resultados.", motivos)
    assert corta.startswith("Válido: 3 resultados. Antes, descartado: Buscador ")
    assert corta.endswith("Otro paso sin punto.")
    assert "y" * (MAX_DESCARTE - 1) not in corta  # Cada motivo, recortado a MAX_DESCARTE.
    assert con_descartes("Válido: 3 resultados.", []) == "Válido: 3 resultados."
