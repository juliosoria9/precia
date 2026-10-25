"""Causa R5-F de la campaña: la única llamada a la IA del modo sitemap no se gasta en vano.

En la ronda 5, Carrefour la gastó en las páginas del localizador de tiendas de su primer sitemap
(sin precio ni producto) y Xtralife en el armazón Angular de 5.548 bytes que sirve en cualquier
ruta (`tests/fixtures/reales/xtralife_armazon_angular_sin_producto.html`) y con un sitemap cuya
URL no valía para el perfil. La IA solo se pide si la URL del sitemap vale y alguna de las fichas
abiertas parece un producto (`parece_producto`); si no, el sitemap se descarta sin gastarla y el
siguiente aún puede usarla. Sin red.
"""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from precia.perfiles.catalogo import Catalogo, UrlCatalogo
from precia.perfiles.configurador import COMPONENTE, _Configuracion, parece_producto
from precia.perfiles.tipos import ConfiguracionFallida
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
    COMIDA,
    FICHAS,
    HTML_INFORMATIVA,
    INFORMATIVAS,
    SIN_PROPUESTA,
    _informativas,
    ejecutar,
    fallo,
)

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
ARMAZON = (REALES / "xtralife_armazon_angular_sin_producto.html").read_bytes().decode("utf-8")
DE_PRODUCTOS = f"{TIENDA}/sitemapproducts-1.xml"


def _sin_buscador(**opciones: Any) -> dict[str, str]:
    return paginas_sitio(formulario=False, busqueda=None, **opciones)


# --- parece_producto ----------------------------------------------------------------------------


def test_el_armazon_spa_real_no_parece_un_producto() -> None:
    assert not parece_producto(pagina(FICHAS[0], ARMAZON))


def test_una_pagina_informativa_sin_importes_no_parece_un_producto() -> None:
    assert not parece_producto(pagina(FICHAS[0], HTML_INFORMATIVA))


def test_una_ficha_con_un_importe_a_la_vista_parece_un_producto() -> None:
    assert parece_producto(pagina(FICHAS[0], html_ficha(PRODUCTOS[0])))


def test_una_ficha_con_el_precio_solo_en_microdatos_parece_un_producto() -> None:
    # Un selector de atributo (`meta[itemprop=price] @content`) no necesita el precio a la vista.
    html = (
        '<html><body><div itemscope itemtype="https://schema.org/Product">'
        '<h1 itemprop="name">Faro izquierdo</h1><div itemprop="offers" itemscope '
        'itemtype="https://schema.org/Offer"><meta itemprop="price" content="45.90">'
        '<meta itemprop="priceCurrency" content="EUR"></div></div></body></html>'
    )
    assert parece_producto(pagina(FICHAS[0], html))


# --- De extremo a extremo ------------------------------------------------------------------------


def test_un_sitemap_de_armazones_spa_no_gasta_la_ia() -> None:
    sitio = _sin_buscador(jsonld=())
    for url in FICHAS:
        sitio[url] = ARMAZON
    sitio[DE_PRODUCTOS] = xml_urlset(FICHAS)
    navegador = NavegadorFalso(sitio, declarados=(DE_PRODUCTOS,))
    catalogo = CatalogoFalso()
    motivo, peticiones = fallo(navegador, [SIN_PROPUESTA, CAMPOS_OK], catalogo=catalogo)
    assert catalogo.guardados == []
    assert "sitemap" in motivo
    # Solo la propuesta de plantillas del buscador: la ficha no se le enseña a la IA.
    assert len(peticiones) == 1


def test_un_sitemap_sin_producto_deja_la_ia_para_el_siguiente() -> None:
    # Carrefour: el primer sitemap declarado da páginas sin precio; el bueno va detrás y sus
    # fichas, sin JSON-LD, necesitan la IA.
    sitio = _sin_buscador(jsonld=())
    informativas = _informativas(sitio)
    sitio[COMIDA] = xml_urlset(FICHAS)
    navegador = NavegadorFalso(sitio, declarados=(INFORMATIVAS, COMIDA))
    catalogo = CatalogoFalso()
    perfil, peticiones = ejecutar(navegador, [SIN_PROPUESTA, CAMPOS_OK], catalogo=catalogo)
    assert perfil.url_busqueda == COMIDA
    assert perfil.sel_campos == CAMPOS_OK
    assert perfil.nota.startswith("Catálogo por sitemap (4 URL), selectores de la ficha por IA. ")
    assert set(informativas[:2]) <= set(navegador.urls)
    assert len(peticiones) == 2
    assert PRODUCTOS[0].referencia in peticiones[1].datos


def test_un_sitemap_con_url_que_no_vale_no_abre_fichas_ni_gasta_la_ia() -> None:
    # Xtralife: el sitemap estaba en otro host y el perfil no podía validar por mucho que la IA
    # acertara. Se descarta antes de abrir ninguna ficha.
    navegador = NavegadorFalso(_sin_buscador(jsonld=()))
    sesion_ia, proveedor = sesion({COMPONENTE: [CAMPOS_OK]})
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
    leido = Catalogo(
        sitemap="https://otra-tienda.es/sitemap.xml",
        entradas=tuple(UrlCatalogo(u, " ".join(re.findall(r"[a-z]+", u))) for u in FICHAS),
        parcial=False,
        ficheros=1,
    )
    with pytest.raises(ConfiguracionFallida, match="sitemap"):
        asyncio.run(configuracion.probar_sitemap(leido))
    assert proveedor.peticiones == []
    assert navegador.urls == []
    assert not configuracion._ia_en_sitemap
