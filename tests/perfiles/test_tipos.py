"""Tipos de perfil (nota 15 §D y nota 16): qué lee cada uno."""

from datetime import datetime
from typing import Any

import pytest

from precia.perfiles.tipos import (
    TIPO_API_SHOPIFY,
    TIPO_API_WOOCOMMERCE,
    TIPO_HTML,
    TIPO_LISTADO,
    TIPO_SITEMAP,
    TIPOS_PERFIL,
    Perfil,
)


def _perfil(tipo: str) -> Perfil:
    datos: dict[str, Any] = {
        "dominio": "tienda.example",
        "version": 1,
        "url_busqueda": "https://tienda.example/",
        "sel_resultado": "",
        "sel_titulo": "",
        "sel_enlace": "",
        "sel_precio_listado": None,
        "sel_campos": {},
        "necesita_js": False,
        "autor": "manual",
        "revisado": False,
        "creado": datetime(2026, 10, 20, 9, 0),
        "tipo": tipo,
    }
    return Perfil(**datos)


def test_los_cinco_tipos() -> None:
    assert TIPO_LISTADO == "listado"
    assert TIPO_SITEMAP == "sitemap"
    assert TIPOS_PERFIL == (
        TIPO_HTML,
        TIPO_API_WOOCOMMERCE,
        TIPO_API_SHOPIFY,
        TIPO_LISTADO,
        TIPO_SITEMAP,
    )


@pytest.mark.parametrize(
    ("tipo", "api", "listado", "sitemap"),
    [
        (TIPO_HTML, False, False, False),
        (TIPO_API_WOOCOMMERCE, True, False, False),
        (TIPO_API_SHOPIFY, True, False, False),
        (TIPO_LISTADO, False, True, False),
        (TIPO_SITEMAP, False, False, True),
    ],
)
def test_propiedades_de_cada_tipo(tipo: str, api: bool, listado: bool, sitemap: bool) -> None:
    perfil = _perfil(tipo)
    assert perfil.es_api is api
    assert perfil.es_listado is listado
    assert perfil.es_sitemap is sitemap
