"""Conectores de solo lectura a las APIs públicas de WooCommerce y Shopify."""

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pytest

from precia.emparejado.referencias import normalizar_referencia
from precia.fuentes.apis import (
    AVISO_AGOTADO,
    ofertas_api,
    plantilla_api,
    url_api,
    url_detalle,
)
from precia.fuentes.plataformas import Plataforma
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import OrigenDato, ValorCampo

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fuentes"
TODOS = ("precio", "referencia_oem", "marca", "estado", "descripcion", "fotos")
WOO = "desguaceslevante.es"
URL_WOO = "https://desguaceslevante.es/wp-json/wc/store/v1/products?search=faro+golf&per_page=10"
SHOP = "recambiosrapidos.es"
URL_SUGGEST = (
    "https://recambiosrapidos.es/search/suggest.json?q=faro+golf"
    "&resources%5Btype%5D=product&resources%5Blimit%5D=10"
)
URL_JS = "https://recambiosrapidos.es/products/faro-delantero-izquierdo-vw-golf-vi.js"


def _pagina(texto: str, url: str, tipo: str = "application/json; charset=utf-8") -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=texto,
        tipo_contenido=tipo,
        obtenida=datetime(2026, 10, 20, tzinfo=UTC),
    )


def _fixture(nombre: str, url: str) -> Pagina:
    return _pagina((FIXTURES / nombre).read_text(encoding="utf-8"), url)


def _api(valor: str) -> ValorCampo:
    return ValorCampo(valor, OrigenDato.API, anclado=True, confianza=1.0)


def _producto_woo(**cambios: Any) -> dict[str, Any]:
    """Producto mínimo de la Store API con los campos que se quieran cambiar."""
    producto: dict[str, Any] = {
        "id": 1,
        "name": "Faro Golf",
        "permalink": "https://desguaceslevante.es/producto/faro-golf/",
        "sku": "",
        "short_description": "",
        "description": "",
        "prices": {
            "price": "8990",
            "regular_price": "8990",
            "sale_price": "8990",
            "currency_code": "EUR",
            "currency_minor_unit": 2,
        },
        "images": [],
        "tags": [],
        "attributes": [],
        "is_in_stock": True,
    }
    producto.update(cambios)
    return producto


def _woo(*productos: dict[str, Any], campos: tuple[str, ...] = TODOS) -> list[Any]:
    pagina = _pagina(json.dumps(list(productos)), URL_WOO)
    return ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, campos)


# --- URL de las APIs --------------------------------------------------------------------------


def test_url_api_woocommerce() -> None:
    assert url_api(Plataforma.WOOCOMMERCE, "https://desguaceslevante.es/", "faro golf") == URL_WOO


def test_url_api_shopify() -> None:
    assert url_api(Plataforma.SHOPIFY, "https://recambiosrapidos.es", "faro golf") == URL_SUGGEST


def test_url_api_codifica_la_consulta_entera() -> None:
    """Una consulta no puede añadir parámetros, fragmentos ni cambiar la ruta."""
    url = url_api(Plataforma.WOOCOMMERCE, "https://tienda.es", "a&per_page=100#x/../y ñ")
    assert url == (
        "https://tienda.es/wp-json/wc/store/v1/products"
        "?search=a%26per_page%3D100%23x%2F..%2Fy+%C3%B1&per_page=10"
    )


@pytest.mark.parametrize("plataforma", [Plataforma.SHOPIFY, Plataforma.WOOCOMMERCE])
def test_plantilla_api_con_marcador_de_consulta(plataforma: Plataforma) -> None:
    plantilla = plantilla_api(plataforma, "https://tienda.es/cualquier/ruta?x=1")
    assert plantilla is not None
    assert plantilla.startswith("https://tienda.es/")
    assert plantilla.count("{consulta}") == 1
    url = url_api(plataforma, "https://tienda.es", "faro golf")
    assert url == plantilla.replace("{consulta}", quote_plus("faro golf"))


@pytest.mark.parametrize(
    "plataforma", [Plataforma.PRESTASHOP, Plataforma.MAGENTO, Plataforma.WORDPRESS]
)
def test_sin_api_publica(plataforma: Plataforma) -> None:
    assert plantilla_api(plataforma, "https://tienda.es") is None
    assert url_api(plataforma, "https://tienda.es", "faro") is None


@pytest.mark.parametrize("base", ["", "ftp://tienda.es", "https://u:c@tienda.es", "tienda.es"])
def test_url_api_con_base_no_valida(base: str) -> None:
    assert url_api(Plataforma.SHOPIFY, base, "faro") is None


# --- Detalle de Shopify -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        ("https://recambiosrapidos.es/products/faro-golf?variant=1&_pos=1", "faro-golf"),
        ("https://www.recambiosrapidos.es/collections/faros/products/faro-golf/", "faro-golf"),
        ("/products/faro-golf", "faro-golf"),
    ],
)
def test_url_detalle_shopify(url: str, esperada: str) -> None:
    detalle = url_detalle(Plataforma.SHOPIFY, url, SHOP)
    assert detalle is not None
    assert detalle.endswith(f"/products/{esperada}.js")
    assert "recambiosrapidos.es/" in detalle


@pytest.mark.parametrize(
    "url",
    [
        "https://otra-tienda.example/products/faro-golf",
        "https://recambiosrapidos.es/collections/faros",
        "https://recambiosrapidos.es/products/",
        "https://recambiosrapidos.es/products/a/b",
        "javascript:alert(1)",
    ],
)
def test_url_detalle_shopify_no_valida(url: str) -> None:
    assert url_detalle(Plataforma.SHOPIFY, url, SHOP) is None


def test_url_detalle_solo_shopify() -> None:
    url = "https://desguaceslevante.es/producto/faro-golf/"
    assert url_detalle(Plataforma.WOOCOMMERCE, url, WOO) is None


# --- WooCommerce Store API --------------------------------------------------------------------


@pytest.fixture(scope="module")
def fichas_woo() -> list[Any]:
    pagina = _fixture("woocommerce_store_api.json", URL_WOO)
    return ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, TODOS)


def test_woocommerce_solo_productos_de_la_tienda(fichas_woo: list[Any]) -> None:
    assert [f.url for f in fichas_woo] == [
        "https://desguaceslevante.es/producto/faro-delantero-izquierdo-golf-vi/",
        "https://desguaceslevante.es/producto/pastillas-freno-delanteras-bosch/",
        "https://desguaceslevante.es/producto/tapacubos-15/",
    ]


def test_woocommerce_producto_completo(fichas_woo: list[Any]) -> None:
    faro = fichas_woo[0]
    assert faro.titulo == "Faro delantero izquierdo VW Golf VI \N{EN DASH} Hella"
    assert faro.precio is not None
    assert (faro.precio.importe, faro.precio.moneda) == (Decimal("89.90"), "EUR")
    assert faro.campos == {
        "precio": _api("89,90 €"),
        "referencia_oem": _api("5K1941005"),
        "marca": _api("Hella"),
        "estado": _api("Usado"),
        "descripcion": _api(
            "Faro original desmontado de un Golf VI de 2011. Probado y con garantía de 3 meses."
        ),
        "fotos": _api("3 fotos"),
    }
    assert faro.avisos == ()


def test_woocommerce_atributo_oem_marca_y_etiqueta_de_estado(fichas_woo: list[Any]) -> None:
    pastillas = fichas_woo[1]
    assert pastillas.precio is not None
    assert pastillas.precio.importe == Decimal("32.45")
    assert pastillas.campos["referencia_oem"] == _api("0 986 494 527")
    assert pastillas.campos["marca"] == _api("Bosch")
    assert pastillas.campos["estado"] == _api("Nuevo")
    assert pastillas.campos["descripcion"] == _api(
        "Juego de 4 pastillas Bosch para eje delantero. Producto nuevo en caja."
    )
    assert pastillas.campos["fotos"] == _api("1 foto")
    assert AVISO_AGOTADO in pastillas.avisos


def test_woocommerce_sin_precio_y_sku_interno(fichas_woo: list[Any]) -> None:
    tapacubos = fichas_woo[2]
    assert tapacubos.titulo == 'Tapacubos 15" (consultar precio)'
    assert tapacubos.precio is None
    assert set(tapacubos.campos) == {"descripcion"}
    assert tapacubos.campos["descripcion"] == _api("Juego de 4.")


def test_woocommerce_solo_los_campos_pedidos() -> None:
    producto = _producto_woo(name="Faro Golf 5K1941005", sku="5K1941005")
    (ficha,) = _woo(producto, campos=("marca", "referencia_oem", "color"))
    assert set(ficha.campos) == {"referencia_oem"}
    assert ficha.precio is not None  # El precio de la oferta se da siempre.
    assert any("color" in aviso for aviso in ficha.avisos)


@pytest.mark.parametrize(
    ("moneda", "unidad", "precio", "importe", "texto"),
    [
        ("USD", 2, "1999", Decimal("19.99"), "19,99 USD"),
        ("JPY", 0, "1500", Decimal("1500"), "1.500,00 JPY"),
        ("KWD", 3, "12345", Decimal("12.345"), "12,35 KWD"),
    ],
)
def test_woocommerce_otras_monedas(
    moneda: str, unidad: int, precio: str, importe: Decimal, texto: str
) -> None:
    precios = {"price": precio, "currency_code": moneda, "currency_minor_unit": unidad}
    (ficha,) = _woo(_producto_woo(prices=precios))
    assert ficha.precio is not None
    assert (ficha.precio.importe, ficha.precio.moneda) == (importe, moneda)
    assert ficha.campos["precio"] == _api(texto)


@pytest.mark.parametrize(
    "precios",
    [
        {"price": "", "currency_code": "EUR", "currency_minor_unit": 2},
        {"price": "0", "currency_code": "EUR", "currency_minor_unit": 2},
        {"price": "-100", "currency_code": "EUR", "currency_minor_unit": 2},
        {"price": "89.90", "currency_code": "EUR", "currency_minor_unit": 2},
        {"price": "1e9", "currency_code": "EUR", "currency_minor_unit": 2},
        {"price": "9" * 40, "currency_code": "EUR", "currency_minor_unit": 2},
        {"price": "8990", "currency_code": "EUR", "currency_minor_unit": "2"},
        {"price": "8990", "currency_code": "EUR", "currency_minor_unit": True},
        {"price": "8990", "currency_code": "EUR", "currency_minor_unit": 99},
        {"price": "8990", "currency_code": "<b>€</b>", "currency_minor_unit": 2},
        {"price": "8990", "currency_minor_unit": 2},
        {"price": ["8990"], "currency_code": "EUR", "currency_minor_unit": 2},
        "8990",
        None,
    ],
)
def test_woocommerce_precio_raro_no_da_precio(precios: object) -> None:
    (ficha,) = _woo(_producto_woo(prices=precios))
    assert ficha.precio is None
    assert "precio" not in ficha.campos


@pytest.mark.parametrize(
    ("sku", "esperada"),
    [
        ("5K1941005", "5K1941005"),
        ("0986494527", "0986494527"),
        ("1K0-698-151", "1K0-698-151"),
        ("1234", None),
        ("000451", None),
        ("2015", None),
        ("FARO-GOLF", None),
        ("100MM", None),
        ("", None),
        (12345678, None),
        ("x" * 5_000, None),
        ("1" * 9_000 + "a", None),
    ],
)
def test_woocommerce_sku_solo_si_parece_referencia(sku: object, esperada: str | None) -> None:
    """Aunque el título corrobore el SKU, tiene que tener forma de referencia OEM."""
    titulo = f"Faro Golf {sku}"[:300] if isinstance(sku, str) else "Faro Golf"
    (ficha,) = _woo(_producto_woo(name=titulo, sku=sku))
    assert ficha.campos.get("referencia_oem") == (_api(esperada) if esperada else None)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Usado", "Usado"),
        ("usada", "Usado"),
        ("Segunda mano", "Usado"),
        ("Como nuevo", "Usado"),
        ("Seminuevo", "Usado"),
        ("Nuevo", "Nuevo"),
        ("NUEVA", "Nuevo"),
        ("Reacondicionado", "Reacondicionado"),
        ("Reconstruido", "Reacondicionado"),
        ("Nuevo modelo 2024", None),
        ("Novedades", None),
        ("Usado / Nuevo", None),
    ],
)
def test_woocommerce_estado_desde_etiquetas(texto: str, esperado: str | None) -> None:
    (ficha,) = _woo(_producto_woo(tags=[{"id": 1, "name": texto, "slug": "x"}]))
    assert ficha.campos.get("estado") == (_api(esperado) if esperado else None)


def test_woocommerce_estado_desde_atributo_condicion() -> None:
    atributos = [{"id": 0, "name": "Condición", "terms": [{"id": 0, "name": "Pieza usada"}]}]
    (ficha,) = _woo(_producto_woo(attributes=atributos))
    assert ficha.campos["estado"] == _api("Usado")


def test_woocommerce_marca_desde_atributo_fabricante() -> None:
    atributos = [{"id": 3, "name": "Fabricante", "terms": [{"id": 9, "name": "Valeo"}]}]
    (ficha,) = _woo(_producto_woo(attributes=atributos))
    assert ficha.campos["marca"] == _api("Valeo")


def test_woocommerce_url_relativa_se_resuelve_en_la_tienda() -> None:
    (ficha,) = _woo(_producto_woo(permalink="/producto/faro-golf/?utm_source=x"))
    assert ficha.url == "https://desguaceslevante.es/producto/faro-golf/"


@pytest.mark.parametrize(
    "permalink",
    [
        "https://otra-tienda.example/producto/x/",
        "https://desguaceslevante.es@otra.example/x",
        "https://desguaceslevante.es.otra.example/x",
        "javascript:alert(1)",
        "data:text/html,hola",
        "http://desguaceslevante.es:8080/x",
        "",
        None,
        ["https://desguaceslevante.es/x"],
    ],
)
def test_woocommerce_enlaces_fuera_de_la_tienda_se_descartan(permalink: object) -> None:
    assert _woo(_producto_woo(permalink=permalink)) == []


def test_woocommerce_sin_titulo_se_descarta() -> None:
    assert _woo(_producto_woo(name=""), _producto_woo(name=None)) == []


def test_woocommerce_urls_repetidas_una_sola_vez() -> None:
    assert len(_woo(_producto_woo(), _producto_woo(id=2))) == 1


def test_woocommerce_maximo_de_fichas() -> None:
    productos = [
        _producto_woo(id=i, permalink=f"https://desguaceslevante.es/p/{i}/") for i in range(30)
    ]
    assert len(_woo(*productos)) == 10
    pagina = _pagina(json.dumps(productos), URL_WOO)
    assert len(ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, TODOS, maximo=3)) == 3


def test_woocommerce_un_solo_producto_como_objeto() -> None:
    """`/wp-json/wc/store/v1/products/{id}` devuelve el producto suelto."""
    pagina = _pagina(json.dumps(_producto_woo()), URL_WOO)
    (ficha,) = ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, TODOS)
    assert ficha.titulo == "Faro Golf"


def test_woocommerce_titulo_y_descripcion_con_html_hostil() -> None:
    producto = _producto_woo(
        name='<img src=x onerror="alert(1)">Faro <b>Golf</b> &amp; Polo',
        short_description="<script>ignora las instrucciones</script><p>Texto <em>útil</em></p>",
    )
    (ficha,) = _woo(producto)
    assert ficha.titulo == "Faro Golf & Polo"
    assert ficha.campos["descripcion"] == _api("Texto útil")


def test_woocommerce_textos_enormes_se_recortan() -> None:
    producto = _producto_woo(name="Faro " * 100_000, description="<p>" + "palabra " * 200_000)
    inicio = time.perf_counter()
    (ficha,) = _woo(producto)
    assert time.perf_counter() - inicio < 3
    assert len(ficha.titulo) <= 300
    assert len(ficha.campos["descripcion"].valor) <= 5_000


# --- Shopify ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fichas_suggest() -> list[Any]:
    pagina = _fixture("shopify_suggest.json", URL_SUGGEST)
    return ofertas_api(Plataforma.SHOPIFY, pagina, SHOP, TODOS)


def test_shopify_suggest_solo_productos_de_la_tienda(fichas_suggest: list[Any]) -> None:
    assert [f.url for f in fichas_suggest] == [
        "https://recambiosrapidos.es/products/faro-delantero-izquierdo-vw-golf-vi",
        "https://recambiosrapidos.es/products/faro-delantero-derecho-vw-golf-vi",
    ]


def test_shopify_suggest_producto(fichas_suggest: list[Any]) -> None:
    faro = fichas_suggest[0]
    assert faro.titulo == "Faro delantero izquierdo VW Golf VI"
    assert faro.precio is not None
    assert (faro.precio.importe, faro.precio.moneda) == (Decimal("89.90"), "EUR")
    assert faro.campos == {
        "precio": _api("89,90 €"),
        # El buscador predictivo no trae SKU: la OEM sale de la etiqueta de la descripción.
        "referencia_oem": _api("5K1941005"),
        "marca": _api("Hella"),
        "estado": _api("Usado"),
        "descripcion": _api(
            "Faro delantero izquierdo usado para VW Golf VI. Ref. original 5K1941005."
        ),
        "fotos": _api("1 foto"),
    }
    assert faro.avisos == ()


def test_shopify_suggest_agotado_y_sin_imagen(fichas_suggest: list[Any]) -> None:
    derecho = fichas_suggest[1]
    assert derecho.precio is not None
    assert derecho.precio.importe == Decimal("95.00")
    assert AVISO_AGOTADO in derecho.avisos
    assert "fotos" not in derecho.campos
    assert "descripcion" not in derecho.campos


def test_shopify_moneda_de_la_tienda() -> None:
    pagina = _fixture("shopify_suggest.json", URL_SUGGEST)
    faro = ofertas_api(Plataforma.SHOPIFY, pagina, SHOP, TODOS, moneda="GBP")[0]
    assert faro.precio is not None
    assert faro.precio.moneda == "GBP"
    assert faro.campos["precio"] == _api("89,90 GBP")


def test_shopify_moneda_no_valida_no_da_precio() -> None:
    pagina = _fixture("shopify_suggest.json", URL_SUGGEST)
    faro = ofertas_api(Plataforma.SHOPIFY, pagina, SHOP, TODOS, moneda="euros")[0]
    assert faro.precio is None


def test_shopify_producto_js() -> None:
    (ficha,) = ofertas_api(Plataforma.SHOPIFY, _fixture("shopify_producto.js", URL_JS), SHOP, TODOS)
    assert ficha.url == "https://recambiosrapidos.es/products/faro-delantero-izquierdo-vw-golf-vi"
    assert ficha.titulo == "Faro delantero izquierdo VW Golf VI"
    assert ficha.precio is not None
    assert (ficha.precio.importe, ficha.precio.moneda) == (Decimal("89.90"), "EUR")
    assert ficha.campos == {
        "precio": _api("89,90 €"),
        "referencia_oem": _api("5K1941005"),
        "marca": _api("Hella"),
        "estado": _api("Usado"),
        "descripcion": _api(
            "Faro delantero izquierdo usado para VW Golf VI (2008-2012). "
            "Ref. original 5K1941005. Probado."
        ),
        "fotos": _api("2 fotos"),
    }


def _producto_js(**cambios: Any) -> dict[str, Any]:
    producto: dict[str, Any] = {
        "id": 1,
        "title": "Retrovisor Seat León",
        "handle": "retrovisor-seat-leon",
        "description": "",
        "vendor": "Seat",
        "tags": [],
        "price": 4500,
        "available": True,
        "variants": [{"id": 1, "sku": "", "price": 4500, "available": True}],
        "images": [],
        "options": [],
        "url": "/products/retrovisor-seat-leon",
    }
    producto.update(cambios)
    return producto


def _js(producto: dict[str, Any]) -> list[Any]:
    pagina = _pagina(json.dumps(producto), URL_JS)
    return ofertas_api(Plataforma.SHOPIFY, pagina, SHOP, TODOS)


def test_shopify_js_variante_disponible_y_opcion_de_estado() -> None:
    variantes = [
        {"id": 1, "sku": "1T0857508", "price": 5000, "available": False},
        {"id": 2, "sku": "1T0857508A", "price": 4200, "available": True},
    ]
    opciones = [{"name": "Estado", "position": 1, "values": ["Reacondicionado"]}]
    descripcion = "<p>Ref. original 1T0857508 (con tapa) o 1T0857508A (sin tapa).</p>"
    producto = _producto_js(variants=variantes, options=opciones, description=descripcion)
    producto["title"] = "Retrovisor Seat León 1T0857508A"
    (ficha,) = _js(producto)
    assert ficha.precio is not None
    assert ficha.precio.importe == Decimal("42.00")
    assert ficha.campos["referencia_oem"] == _api("1T0857508A")
    assert ficha.campos["estado"] == _api("Reacondicionado")
    assert AVISO_AGOTADO not in ficha.avisos


def test_shopify_js_sin_variantes_disponibles() -> None:
    (ficha,) = _js(_producto_js(available=False, variants=[{"id": 1, "price": 4500}]))
    assert AVISO_AGOTADO in ficha.avisos
    assert ficha.precio is not None


@pytest.mark.parametrize("precio", [-1, 0, 4500.5, "4500", True, None, 10**20])
def test_shopify_js_precio_raro_no_da_precio(precio: object) -> None:
    (ficha,) = _js(_producto_js(price=precio, variants=[{"id": 1, "price": precio}]))
    assert ficha.precio is None


@pytest.mark.parametrize("url", ["https://otra-tienda.example/products/x", "javascript:x", 5])
def test_shopify_js_url_de_otra_tienda(url: object) -> None:
    assert _js(_producto_js(url=url)) == []


# --- JSON hostil o inesperado -----------------------------------------------------------------


HOSTILES = [
    "",
    "no es json",
    "<!doctype html><html><body>Página de la tienda</body></html>",
    "null",
    "true",
    "42",
    '"texto"',
    "[]",
    "{}",
    '{"code":"rest_no_route","message":"No se ha encontrado ninguna ruta","data":{"status":404}}',
    '[1, "a", null, true, []]',
    '[{"name": 5, "permalink": []}]',
    '{"resources": []}',
    '{"resources": {"results": []}}',
    '{"resources": {"results": {"products": "x"}}}',
    '{"resources": {"results": {"products": [null, 1, {"title": []}]}}}',
    '{"variants": "x", "title": "Faro"}',
    '[{"name": "Faro", "permalink": "https://otra.example/x", "prices": NaN}]',
    "[" * 100_000,
    "[" * 100_000 + "]" * 100_000,
    '{"a":' * 50_000 + "1" + "}" * 50_000,
    "1" * 10_000,
    '{"resources": {"results": {"products": [' + "{}," * 200_000 + "{}]}}}",
]


@pytest.mark.parametrize("plataforma", list(Plataforma))
@pytest.mark.parametrize("texto", HOSTILES, ids=[f"hostil{i}" for i in range(len(HOSTILES))])
def test_json_hostil_da_lista_vacia(plataforma: Plataforma, texto: str) -> None:
    pagina = _pagina(texto, URL_WOO if plataforma is Plataforma.WOOCOMMERCE else URL_SUGGEST)
    inicio = time.perf_counter()
    assert ofertas_api(plataforma, pagina, WOO, TODOS) == []
    assert ofertas_api(plataforma, pagina, SHOP, TODOS) == []
    assert time.perf_counter() - inicio < 3


def test_json_gigante_se_ignora() -> None:
    texto = json.dumps([_producto_woo(description="x" * 6_000_000)])
    pagina = _pagina(texto, URL_WOO)
    assert ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, TODOS) == []


def test_dominio_no_valido_da_lista_vacia() -> None:
    pagina = _fixture("woocommerce_store_api.json", URL_WOO)
    assert ofertas_api(Plataforma.WOOCOMMERCE, pagina, "", TODOS) == []


def test_respuesta_de_una_tienda_no_vale_para_otro_dominio() -> None:
    """El JSON de `desguaceslevante.es` no da fichas de `otra-tienda.example`, aunque traiga
    un `permalink` suyo (antes sí las daba: lo vio la revisión adversarial)."""
    pagina = _fixture("woocommerce_store_api.json", URL_WOO)
    assert ofertas_api(Plataforma.WOOCOMMERCE, pagina, "otra-tienda.example", TODOS) == []


def test_muchos_productos_hostiles_en_tiempo_acotado() -> None:
    producto = _producto_woo(
        sku="1" * 9_000 + "a",
        tags=[{"name": "nuevo " * 100}] * 200,
        attributes=[{"name": "Estado", "terms": [{"name": "usado " * 100}] * 20}] * 20,
        images=[{"src": "https://desguaceslevante.es/i.jpg"}] * 40_000,
    )
    texto = json.dumps([producto] * 2)
    assert len(texto) < 5_000_000
    pagina = _pagina(texto, URL_WOO)
    inicio = time.perf_counter()
    fichas = ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, TODOS)
    assert time.perf_counter() - inicio < 3
    assert len(fichas) == 1
    assert "referencia_oem" not in fichas[0].campos
    assert "estado" not in fichas[0].campos
    assert fichas[0].campos["fotos"] == _api("50 fotos")


# --- Revisión adversarial ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plataforma", "fichero", "dominio", "url_final"),
    [
        (
            Plataforma.WOOCOMMERCE,
            "woocommerce_store_api.json",
            WOO,
            "https://espejo-de-precios.example/wp-json/wc/store/v1/products?search=faro",
        ),
        (Plataforma.SHOPIFY, "shopify_suggest.json", SHOP, "https://otra.example/search/x.json"),
        (Plataforma.SHOPIFY, "shopify_producto.js", SHOP, "https://otra.example/products/x.js"),
    ],
)
def test_respuesta_servida_por_otra_web_se_descarta(
    plataforma: Plataforma, fichero: str, dominio: str, url_final: str
) -> None:
    """Si la API redirige a otra web, su JSON no es de la tienda aunque sus enlaces lo parezcan.

    El descargador sigue redirecciones a cualquier host público; con `confianza` 1 y anclado,
    un JSON de un tercero con `permalink` de la tienda colaría precios falsos.
    """
    texto = (FIXTURES / fichero).read_text(encoding="utf-8")
    pagina = Pagina(
        url=URL_WOO if plataforma is Plataforma.WOOCOMMERCE else URL_SUGGEST,
        url_final=url_final,
        estado=200,
        html=texto,
        tipo_contenido="application/json",
        obtenida=datetime(2026, 10, 20, tzinfo=UTC),
    )
    assert ofertas_api(plataforma, pagina, dominio, TODOS) == []


def test_respuesta_de_www_de_la_tienda_si_vale() -> None:
    url = URL_WOO.replace("https://", "https://www.")
    pagina = _fixture("woocommerce_store_api.json", url)
    assert len(ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, TODOS)) == 3


@pytest.mark.parametrize("estado", [201, 204, 301, 400, 403, 404, 429, 500, 503])
def test_respuesta_con_estado_de_error_no_da_fichas(estado: int) -> None:
    """Un 403 o un 500 con JSON (de un WAF o de una caché vieja) no es el catálogo."""
    texto = (FIXTURES / "woocommerce_store_api.json").read_text(encoding="utf-8")
    pagina = Pagina(
        url=URL_WOO,
        url_final=URL_WOO,
        estado=estado,
        html=texto,
        tipo_contenido="application/json",
        obtenida=datetime(2026, 10, 20, tzinfo=UTC),
    )
    esperadas = 3 if 200 <= estado < 300 else 0
    assert len(ofertas_api(Plataforma.WOOCOMMERCE, pagina, WOO, TODOS)) == esperadas


def test_shopify_js_referencia_solo_de_la_variante_elegida() -> None:
    """La OEM de otra variante (el faro del otro lado) no se pega al precio de esta."""
    variantes = [
        {"id": 1, "title": "Izquierdo", "sku": "", "price": 8990, "available": True},
        {"id": 2, "title": "Derecho", "sku": "5K1941006", "price": 9500, "available": True},
    ]
    (ficha,) = _js(_producto_js(variants=variantes))
    assert ficha.precio is not None
    assert ficha.precio.importe == Decimal("89.90")
    assert "referencia_oem" not in ficha.campos


def test_shopify_js_sin_variantes_con_precio_usa_la_unica_variante() -> None:
    producto = _producto_js(title="Faro Golf 5K1941005", price=8990)
    producto["variants"] = [{"id": 1, "sku": "5K1941005"}]
    (ficha,) = _js(producto)
    assert ficha.campos["referencia_oem"] == _api("5K1941005")


@pytest.mark.parametrize("plataforma", [Plataforma.WOOCOMMERCE, Plataforma.SHOPIFY])
def test_productos_repetidos_con_html_pesado_no_se_reprocesan(plataforma: Plataforma) -> None:
    """100 copias de un producto con 20 000 etiquetas en la descripción: solo se lee una.

    Antes se pasaban a texto las 100 descripciones y luego se descartaban por URL repetida
    (unos 6 s de CPU para una sola ficha).
    """
    titulo = "Faro" + "<b>" * 400
    if plataforma is Plataforma.WOOCOMMERCE:
        woo = _producto_woo(name=titulo, short_description="<i>" * 6_600)
        woo["description"] = "<a>" * 6_600
        pagina = _pagina(json.dumps([woo] * 100), URL_WOO)
        dominio = WOO
    else:
        shop = {"title": titulo, "url": "/products/faro?_pos=1", "price": "1.00"}
        shop |= {"body": "<i>" * 6_600, "description": "<a>" * 6_600}
        respuesta = {"resources": {"results": {"products": [shop] * 100}}}
        pagina = _pagina(json.dumps(respuesta), URL_SUGGEST)
        dominio = SHOP
    assert len(pagina.html) < 5_000_000
    inicio = time.perf_counter()
    fichas = ofertas_api(plataforma, pagina, dominio, TODOS)
    assert time.perf_counter() - inicio < 1.5
    assert len(fichas) == 1


# --- SKU interno frente a referencia OEM (prueba real de generalidad) -------------------------

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
VELAZQUEZ = "desguacesvelazquez.com"
URL_VELAZQUEZ = (
    "https://www.desguacesvelazquez.com/wp-json/wc/store/v1/products?search=faro&per_page=10"
)


@pytest.fixture(scope="module")
def fichas_velazquez() -> list[Any]:
    texto = (REALES / "velazquez_api_woocommerce_faro.json").read_text(encoding="utf-8")
    return ofertas_api(Plataforma.WOOCOMMERCE, _pagina(texto, URL_VELAZQUEZ), VELAZQUEZ, TODOS)


def test_velazquez_real_el_sku_del_desguace_no_es_la_oem(fichas_velazquez: list[Any]) -> None:
    """La Store API real de un desguace: `sku` es su código de stock («DESP220618»).

    La OEM del fabricante viene en el atributo «Ref.Fabricante»; «Ref.Local» (220618) es el
    mismo código de stock sin prefijo y «Ref.Equivalente», una referencia de recambio
    alternativo. Ninguna de esas dos, ni el SKU, puede salir como OEM.
    """
    texto = (REALES / "velazquez_api_woocommerce_faro.json").read_text(encoding="utf-8")
    skus = {p["sku"] for p in json.loads(texto)}
    assert len(fichas_velazquez) == 10
    referencias = [f.campos["referencia_oem"].valor for f in fichas_velazquez]
    assert not skus & set(referencias)
    assert referencias == [
        "260107244r",
        "8200002469",
        "260606152R",
        "8200002470",
        "9682828280",
        "8K0941004",
        "260107249",
        "260605720r",
        "90114430",
        "90114431",
    ]
    assert all(f.campos["referencia_oem"].origen is OrigenDato.API for f in fichas_velazquez)


def test_woocommerce_sku_sin_corroborar_no_es_oem() -> None:
    """Un SKU con forma de referencia pero que no aparece en ningún otro sitio es interno."""
    (ficha,) = _woo(_producto_woo(sku="DESP220618", description="<p>Faro usado.</p>"))
    assert "referencia_oem" not in ficha.campos


@pytest.mark.parametrize(
    "descripcion",
    [
        "<p>[getpid sku=&#8221;5K1941005&#8243;]</p>",
        "<p>SKU: 5K1941005</p>",
        "<p>Ref. interna 5K1941005</p>",
        "<p>Código de almacén 5K1941005</p>",
        "<p>No original 5K1941005</p>",
    ],
)
def test_woocommerce_sku_en_el_texto_sin_etiqueta_oem_no_cuenta(descripcion: str) -> None:
    (ficha,) = _woo(_producto_woo(sku="5K1941005", description=descripcion))
    assert "referencia_oem" not in ficha.campos


@pytest.mark.parametrize(
    "descripcion",
    [
        "<p>Referencia OEM 5K1941005.</p>",
        "<p>Ref. original: 5K1-941-005</p>",
        "<p>OEM: 5K1941005</p>",
        "<p>Ref. fabricante 5K1941005, probado.</p>",
        "<p>Part number #5K1941005</p>",
        "<p>Nº de pieza 5K1941005</p>",
        "<p>Código del fabricante: 5K1941005</p>",
    ],
)
def test_woocommerce_sku_corroborado_en_el_texto_como_oem(descripcion: str) -> None:
    (ficha,) = _woo(_producto_woo(sku="5K1941005", description=descripcion))
    assert normalizar_referencia(ficha.campos["referencia_oem"].valor) == "5K1941005"


def test_woocommerce_sku_corroborado_en_el_titulo() -> None:
    (ficha,) = _woo(_producto_woo(name="Faro Golf VI 5K1 941 005", sku="5K1941005"))
    assert ficha.campos["referencia_oem"] == _api("5K1941005")


@pytest.mark.parametrize(
    "nombre",
    [
        "Ref.Fabricante",
        "Referencia del fabricante",
        "Código de fabricante",
        "Cód. fabricante",
        "Nº OEM",
        "OEM",
        "Referencia OE",
        "Part Number",
        "MPN",
        "Ref. original",
        "Número de pieza",
    ],
)
def test_woocommerce_atributo_oem_gana_al_sku_interno(nombre: str) -> None:
    atributos = [{"id": 0, "name": nombre, "terms": [{"id": 0, "name": "260107244R"}]}]
    (ficha,) = _woo(_producto_woo(sku="DESP220618", attributes=atributos))
    assert ficha.campos["referencia_oem"] == _api("260107244R")


@pytest.mark.parametrize(
    "nombre",
    [
        "Ref.Local",
        "Ref.Equivalente",
        "Ref. interna",
        "Referencia OEM equivalente",
        "Referencias compatibles",
        "Bastidor",
        "Código Motor",
        "Marca fabricante",
        "Fabricante",
    ],
)
def test_woocommerce_atributos_que_no_son_la_oem(nombre: str) -> None:
    atributos = [{"id": 0, "name": nombre, "terms": [{"id": 0, "name": "260107244R"}]}]
    (ficha,) = _woo(_producto_woo(sku="DESP220618", attributes=atributos))
    assert "referencia_oem" not in ficha.campos


@pytest.mark.parametrize(
    ("descripcion", "esperada"),
    [
        ("<p>Faro usado. Ref. original 1K0 698 151.</p>", "1K0 698 151"),
        ("<p>Referencia OEM: 0 986 494 527</p>", "0 986 494 527"),
        ("<p>OEM 5K1941005 / 5K1941006</p>", "5K1941005"),
        ("<p>Ref. original 2015</p>", None),
        ("<p>Referencia OEM: consultar</p>", None),
        ("<p>Faro para Golf VI 5K1941005</p>", None),
    ],
)
def test_woocommerce_oem_etiquetada_en_la_descripcion_sin_sku(
    descripcion: str, esperada: str | None
) -> None:
    (ficha,) = _woo(_producto_woo(sku="", short_description=descripcion))
    assert ficha.campos.get("referencia_oem") == (_api(esperada) if esperada else None)


def test_shopify_js_sku_interno_sin_corroborar_no_es_oem() -> None:
    variantes = [{"id": 1, "sku": "RR000123X", "price": 4500, "available": True}]
    (ficha,) = _js(_producto_js(variants=variantes, description="<p>Retrovisor usado.</p>"))
    assert "referencia_oem" not in ficha.campos


def test_shopify_js_sku_corroborado_en_el_titulo() -> None:
    variantes = [{"id": 1, "sku": "6J1857507", "price": 4500, "available": True}]
    (ficha,) = _js(_producto_js(title="Retrovisor Seat Ibiza 6J1857507", variants=variantes))
    assert ficha.campos["referencia_oem"] == _api("6J1857507")


def test_shopify_js_oem_del_texto_no_se_pega_a_otra_variante() -> None:
    """Con varias variantes, la OEM de la descripción puede ser la de la otra pieza."""
    variantes = [
        {"id": 1, "title": "Izquierdo", "sku": "RR0001A", "price": 8990, "available": True},
        {"id": 2, "title": "Derecho", "sku": "RR0001B", "price": 9500, "available": True},
    ]
    descripcion = "<p>Derecho: ref. original 5K1941006.</p>"
    (ficha,) = _js(_producto_js(variants=variantes, description=descripcion))
    assert "referencia_oem" not in ficha.campos


def test_oem_etiquetada_con_texto_hostil_en_tiempo_acotado() -> None:
    hostiles = [
        "ref. original " * 2_000,
        "Referencia OEM: " + "5K1-" * 3_000,
        "oem " * 3_000 + "5K1941005",
        "nº " * 3_000,
        ":" * 20_000,
        "Referencia OEM 1 2 3 4 5 6 7 8 9 2015 " * 500,
        "N.º de pieza " + " 698" * 5_000,
    ]
    inicio = time.perf_counter()
    for texto in hostiles:
        _woo(_producto_woo(sku="5K1941005", description=texto, short_description=texto))
    assert time.perf_counter() - inicio < 2


@pytest.mark.parametrize(
    ("descripcion", "esperada"),
    [
        ("<p>Ref. original 0 986 494 527 2015</p>", "0 986 494 527"),
        ("<p>N.º de pieza: 5K1941005</p>", "5K1941005"),
        ("<p>OEM number: 5K1941005</p>", "5K1941005"),
    ],
)
def test_woocommerce_oem_etiquetada_casos_de_borde(descripcion: str, esperada: str) -> None:
    (ficha,) = _woo(_producto_woo(description=descripcion))
    assert ficha.campos["referencia_oem"] == _api(esperada)


def test_woocommerce_producto_variable_no_toma_la_oem_del_texto() -> None:
    """Un producto variable (izquierdo y derecho) da el precio de una variación cualquiera."""
    producto = _producto_woo(
        type="variable",
        variations=[{"id": 2, "attributes": []}, {"id": 3, "attributes": []}],
        description="<p>Derecho: ref. original 5K1941006.</p>",
    )
    (ficha,) = _woo(producto)
    assert "referencia_oem" not in ficha.campos


# --- Revisión adversarial del fallo 3 ---------------------------------------------------------


@pytest.mark.parametrize(
    "nombre",
    [
        "Código color fabricante",
        "Código de pintura del fabricante",
        "Número de serie del fabricante",
        "Código motor fabricante",
        "Código EAN fabricante",
        "Número de bastidor del fabricante",
    ],
)
def test_woocommerce_otros_codigos_del_fabricante_no_son_la_oem(nombre: str) -> None:
    """Color, motor, serie, EAN o bastidor «del fabricante» no son la referencia de la pieza."""
    atributos = [{"id": 0, "name": nombre, "terms": [{"id": 0, "name": "NV676X12"}]}]
    (ficha,) = _woo(_producto_woo(sku="DESP220618", attributes=atributos))
    assert "referencia_oem" not in ficha.campos


def test_woocommerce_oem_etiquetada_no_pega_dos_referencias_con_barra() -> None:
    """«5K1941005/5K1941006» son dos referencias, no una de 18 caracteres."""
    (ficha,) = _woo(_producto_woo(description="<p>Ref. original 5K1941005/5K1941006</p>"))
    assert ficha.campos["referencia_oem"] == _api("5K1941005")


def test_shopify_js_sku_de_la_variante_corroborado_en_la_descripcion() -> None:
    """Con varias variantes, el SKU elegido vale si la descripción lo da como OEM."""
    variantes = [
        {"id": 1, "sku": "5K1941005", "price": 8990, "available": True},
        {"id": 2, "sku": "5K1941006", "price": 9500, "available": True},
    ]
    descripcion = "<p>Izquierdo: ref. original 5K1941005. Derecho: ref. original 5K1941006.</p>"
    (ficha,) = _js(_producto_js(title="Faro Golf VI", variants=variantes, description=descripcion))
    assert ficha.campos["referencia_oem"] == _api("5K1941005")
