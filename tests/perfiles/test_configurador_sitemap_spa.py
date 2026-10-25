"""Causa R5-E de la campaña: un perfil `sitemap` con fichas SPA se valida con Chromium.

En la ronda 5, Xtralife servía por HTTP el mismo armazón Angular de 5.548 bytes en cualquier
ruta (`tests/fixtures/reales/xtralife_armazon_angular_sin_producto.html`), también en sus fichas;
con Chromium la tienda sí pinta. El perfil `sitemap` se creaba con `necesita_js=False` fijo y la
ficha para la IA se abría siempre por HTTP: no podía validar ninguna. Ahora, si las fichas por
HTTP se abren bien pero son armazones (sin producto y casi sin texto), se repite la validación
con Chromium antes de gastar la IA, y la ficha de la IA se abre igual que se valida. Una ficha
normal sin precio nunca dispara Chromium. Sin red.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from precia.perfiles.configurador import COMPONENTE, _Configuracion, parece_armazon
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    PRODUCTOS,
    TIENDA,
    CatalogoFalso,
    NavegadorFalso,
    html_ficha,
    pagina,
    paginas_sitio,
    sesion,
    xml_urlset,
)
from tests.perfiles.test_configurador_sin_buscador import (
    FICHAS,
    SIN_PROPUESTA,
    ejecutar,
    fallo,
)

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
ARMAZON = (REALES / "xtralife_armazon_angular_sin_producto.html").read_bytes().decode("utf-8")
DE_PRODUCTOS = f"{TIENDA}/sitemapproducts-1.xml"
SIN_PRECIO_CON_TEXTO = (
    "<html><head><title>Guía de tallas</title></head><body><h1>Cómo elegir tu talla</h1>"
    + "<p>Mide el contorno con una cinta métrica y compara con la tabla de cada marca.</p>" * 12
    + "</body></html>"
)


def _tienda(fichas_http: dict[str, str], **opciones: Any) -> dict[str, str]:
    sitio = paginas_sitio(formulario=False, busqueda=None, **opciones)
    sitio.update(fichas_http)
    sitio[DE_PRODUCTOS] = xml_urlset(FICHAS)
    return sitio


def _fichas_con_js(navegador: NavegadorFalso) -> list[str]:
    return [url for url, js in navegador.pedidas if js and url in FICHAS]


# --- parece_armazon -----------------------------------------------------------------------------


def test_el_armazon_spa_real_parece_un_armazon() -> None:
    assert parece_armazon(pagina(FICHAS[0], ARMAZON))


def test_lo_que_ya_vino_por_chromium_no_es_un_armazon_que_rescatar() -> None:
    # Renderizar otra vez lo que ya pintó Chromium (rescate de ADR-023) no aporta nada.
    assert not parece_armazon(replace(pagina(FICHAS[0], ARMAZON), por_chromium=True))


def test_una_pagina_sin_precio_pero_con_texto_no_es_un_armazon() -> None:
    assert not parece_armazon(pagina(FICHAS[0], SIN_PRECIO_CON_TEXTO))


def test_una_ficha_con_precio_no_es_un_armazon() -> None:
    assert not parece_armazon(pagina(FICHAS[0], html_ficha(PRODUCTOS[0])))


# --- De extremo a extremo ------------------------------------------------------------------------


def test_fichas_spa_validan_con_chromium_sin_gastar_la_ia() -> None:
    sitio = _tienda(dict.fromkeys(FICHAS, ARMAZON), jsonld=())
    con_js = {p.url: html_ficha(p, jsonld=True) for p in PRODUCTOS}
    navegador = NavegadorFalso(sitio, paginas_js=con_js, declarados=(DE_PRODUCTOS,))
    perfil, peticiones = ejecutar(navegador, [SIN_PROPUESTA], catalogo=CatalogoFalso())
    assert perfil.url_busqueda == DE_PRODUCTOS
    assert perfil.necesita_js
    assert perfil.sel_campos == {}
    assert "con Chromium" in perfil.nota
    assert "sin IA" in perfil.nota
    assert len(peticiones) == 1
    assert _fichas_con_js(navegador)


def test_si_con_chromium_hace_falta_la_ia_ve_la_ficha_pintada() -> None:
    sitio = _tienda(dict.fromkeys(FICHAS, ARMAZON), jsonld=())
    con_js = {p.url: html_ficha(p) for p in PRODUCTOS}
    navegador = NavegadorFalso(sitio, paginas_js=con_js, declarados=(DE_PRODUCTOS,))
    perfil, peticiones = ejecutar(navegador, [SIN_PROPUESTA, CAMPOS_OK], catalogo=CatalogoFalso())
    assert perfil.necesita_js
    assert perfil.sel_campos == CAMPOS_OK
    assert "con Chromium" in perfil.nota
    assert len(peticiones) == 2
    assert PRODUCTOS[0].referencia in peticiones[1].datos


def test_fichas_con_precio_por_http_nunca_piden_chromium() -> None:
    # El caso de recambios: la ficha trae el precio a la vista, pero sin JSON-LD hace falta la IA.
    sitio = _tienda({}, jsonld=())
    navegador = NavegadorFalso(sitio, paginas_js={}, declarados=(DE_PRODUCTOS,))
    perfil, peticiones = ejecutar(navegador, [SIN_PROPUESTA, CAMPOS_OK], catalogo=CatalogoFalso())
    assert not perfil.necesita_js
    assert "con Chromium" not in perfil.nota
    assert len(peticiones) == 2
    assert _fichas_con_js(navegador) == []


def test_fichas_sin_precio_pero_con_texto_no_piden_chromium() -> None:
    sitio = _tienda(dict.fromkeys(FICHAS, SIN_PRECIO_CON_TEXTO), jsonld=())
    navegador = NavegadorFalso(sitio, paginas_js={}, declarados=(DE_PRODUCTOS,))
    motivo, peticiones = fallo(navegador, [SIN_PROPUESTA, CAMPOS_OK], catalogo=CatalogoFalso())
    assert "sitemap" in motivo
    assert len(peticiones) == 1
    assert _fichas_con_js(navegador) == []


# --- La caché de fichas distingue HTTP de Chromium ---------------------------------------------


def test_la_cache_de_fichas_distingue_http_de_chromium() -> None:
    url = FICHAS[0]
    con_js = html_ficha(PRODUCTOS[0], jsonld=True)
    navegador = NavegadorFalso({url: ARMAZON}, paginas_js={url: con_js})
    sesion_ia, _ = sesion({COMPONENTE: []})
    configuracion = _Configuracion(
        dominio=DOMINIO,
        navegador=navegador,
        sesion=sesion_ia,
        consulta=CONSULTA,
        claves=("precio",),
        validador=validar_con_dns_publico,
        busqueda=None,
        creado=datetime(2026, 10, 24, tzinfo=UTC),
    )
    por_http = asyncio.run(configuracion.abrir_ficha([url], js=False))
    por_chromium = asyncio.run(configuracion.abrir_ficha([url], js=True))
    assert por_http is not None and por_http.html == ARMAZON
    assert por_chromium is not None and por_chromium.html == con_js
