"""Tiendas falsas de WooCommerce y Shopify con su API pública, para los tests de perfiles `api_*`.

Las páginas son las fixtures de `tests/fixtures/fuentes/` (copias fieles de una portada y de
las respuestas reales de cada plataforma), servidas por el `NavegadorFalso` de `sitio`.
"""

from pathlib import Path
from typing import Any

from precia.perfiles.tipos import TIPO_API_SHOPIFY, TIPO_API_WOOCOMMERCE, Perfil
from tests.perfiles.sitio import CONSULTA, perfil

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "fuentes"

WOO = "desguaceslevante.es"
WOO_PORTADA = f"https://{WOO}/"
WOO_PLANTILLA = f"https://{WOO}/wp-json/wc/store/v1/products?search={{consulta}}&per_page=10"
WOO_API = WOO_PLANTILLA.replace("{consulta}", CONSULTA.replace(" ", "+"))
WOO_TITULOS = (
    "Faro delantero izquierdo VW Golf VI – Hella",  # noqa: RUF001 (el guion del JSON real)
    "Pastillas de freno delanteras Bosch",
    'Tapacubos 15" (consultar precio)',
)

SHOPIFY = "recambiosrapidos.es"
SHOPIFY_PORTADA = f"https://{SHOPIFY}/"
SHOPIFY_PLANTILLA = (
    f"https://{SHOPIFY}/search/suggest.json?q={{consulta}}"
    "&resources%5Btype%5D=product&resources%5Blimit%5D=10"
)
SHOPIFY_API = SHOPIFY_PLANTILLA.replace("{consulta}", CONSULTA.replace(" ", "+"))


def fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def paginas_woo() -> dict[str, str]:
    return {
        WOO_PORTADA: fixture("portada_woocommerce.html"),
        WOO_API: fixture("woocommerce_store_api.json"),
    }


def paginas_shopify() -> dict[str, str]:
    return {
        SHOPIFY_PORTADA: fixture("portada_shopify.html"),
        SHOPIFY_API: fixture("shopify_suggest.json"),
    }


def perfil_api(tipo: str = TIPO_API_WOOCOMMERCE, **cambios: Any) -> Perfil:
    dominio, plantilla = (WOO, WOO_PLANTILLA)
    if tipo == TIPO_API_SHOPIFY:
        dominio, plantilla = (SHOPIFY, SHOPIFY_PLANTILLA)
    datos: dict[str, Any] = {
        "dominio": dominio,
        "url_busqueda": plantilla,
        "sel_resultado": "",
        "sel_titulo": "",
        "sel_enlace": "",
        "sel_precio_listado": None,
        "sel_campos": {},
        "autor": "🤖 configurador",
        "revisado": False,
        "tipo": tipo,
    }
    datos.update(cambios)
    return perfil(**datos)
