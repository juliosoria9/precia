"""Foto principal desde las APIs públicas de WooCommerce y Shopify, validada como la del HTML."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from precia.fuentes.apis import ofertas_api
from precia.fuentes.plataformas import Plataforma
from precia.navegador.tipos import Pagina

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fuentes"
WOO = "desguaceslevante.es"
URL_WOO = "https://desguaceslevante.es/wp-json/wc/store/v1/products?search=faro+golf&per_page=10"
SHOP = "recambiosrapidos.es"
URL_SUGGEST = (
    "https://recambiosrapidos.es/search/suggest.json?q=faro+golf"
    "&resources%5Btype%5D=product&resources%5Blimit%5D=10"
)
URL_JS = "https://recambiosrapidos.es/products/faro-delantero-izquierdo-vw-golf-vi.js"


def _pagina(texto: str, url: str) -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=texto,
        tipo_contenido="application/json; charset=utf-8",
        obtenida=datetime(2026, 10, 24, tzinfo=UTC),
    )


def _producto_woo(**cambios: Any) -> dict[str, Any]:
    producto: dict[str, Any] = {
        "id": 1,
        "name": "Faro Golf",
        "permalink": "https://desguaceslevante.es/producto/faro-golf/",
        "prices": {"price": "8990", "currency_code": "EUR", "currency_minor_unit": 2},
        "images": [],
        "is_in_stock": True,
    }
    producto.update(cambios)
    return producto


def _woo(*productos: dict[str, Any]) -> list[Any]:
    pagina = _pagina(json.dumps(list(productos)), URL_WOO)
    return ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, ("precio",))


def _js(**cambios: Any) -> list[Any]:
    datos = json.loads((FIXTURES / "shopify_producto.js").read_text(encoding="utf-8"))
    datos.update(cambios)
    return ofertas_api(Plataforma.SHOPIFY, _pagina(json.dumps(datos), URL_JS), SHOP, ("precio",))


def test_woocommerce_real_primera_imagen() -> None:
    texto = (FIXTURES / "woocommerce_store_api.json").read_text(encoding="utf-8")
    fichas = ofertas_api(Plataforma.WOOCOMMERCE, _pagina(texto, URL_WOO), WOO, ("precio",))
    assert fichas[0].foto == (
        "https://desguaceslevante.es/wp-content/uploads/2026/09/faro-golf-1.jpg"
    )
    # Sin imágenes, sin foto.
    assert fichas[2].foto is None


def test_woocommerce_miniatura_si_no_hay_src() -> None:
    (ficha,) = _woo(_producto_woo(images=[{"src": "", "thumbnail": "/wp-content/up/a.jpg"}]))
    assert ficha.foto == "https://desguaceslevante.es/wp-content/up/a.jpg"


def test_woocommerce_imagen_ajena_o_peligrosa() -> None:
    for src in ("https://otra.example/a.jpg", "javascript:alert(1)", "data:image/png;base64,AA"):
        (ficha,) = _woo(_producto_woo(images=[{"src": src}]))
        assert ficha.foto is None


def test_woocommerce_jetpack_cdn() -> None:
    (ficha,) = _woo(_producto_woo(images=[{"src": "https://i0.wp.com/desguaceslevante.es/a.jpg"}]))
    assert ficha.foto == "https://i0.wp.com/desguaceslevante.es/a.jpg"


def test_shopify_suggest_featured_image_como_objeto() -> None:
    texto = (FIXTURES / "shopify_suggest.json").read_text(encoding="utf-8")
    fichas = ofertas_api(Plataforma.SHOPIFY, _pagina(texto, URL_SUGGEST), SHOP, ("precio",))
    assert fichas[0].foto == (
        "https://cdn.shopify.com/s/files/1/0612/3456/7890/files/faro-golf.jpg?v=1712345678"
    )
    assert fichas[1].foto is None  # `featured_image` con `url: null`


def test_shopify_js_featured_image_con_doble_barra() -> None:
    (ficha,) = _js()
    assert ficha.foto == "https://recambiosrapidos.es/cdn/shop/files/faro-golf.jpg?v=1712345678"


def test_shopify_js_sin_featured_image_usa_images() -> None:
    (ficha,) = _js(featured_image=None, images=[{"src": "//cdn.shopify.com/s/files/b.jpg"}])
    assert ficha.foto == "https://cdn.shopify.com/s/files/b.jpg"


def test_shopify_js_image_src() -> None:
    (ficha,) = _js(featured_image=None, images=[], image={"src": "/cdn/shop/files/c.jpg"})
    assert ficha.foto == "https://recambiosrapidos.es/cdn/shop/files/c.jpg"


def test_shopify_js_foto_peligrosa() -> None:
    (ficha,) = _js(featured_image="javascript:alert(1)", images=["https://evil.example/x.jpg"])
    assert ficha.foto is None
