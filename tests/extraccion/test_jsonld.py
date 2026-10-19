"""Productos `schema.org` en bloques JSON-LD."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from precia.extraccion.jsonld import MAX_BLOQUE, productos_jsonld
from precia.extraccion.tipos import Precio, ProductoJSONLD

FIXTURES = Path(__file__).parent.parent / "fixtures" / "extraccion"


def _bloque(dato: Any) -> str:
    return f'<script type="application/ld+json">{json.dumps(dato)}</script>'


def _uno(dato: Any) -> ProductoJSONLD:
    productos = productos_jsonld(_bloque(dato))
    assert len(productos) == 1
    return productos[0]


def test_ficha_de_desguace() -> None:
    html = (FIXTURES / "ficha_desguace.html").read_text(encoding="utf-8")
    productos = productos_jsonld(html)
    assert productos == [
        ProductoJSONLD(
            nombre="Faro delantero izquierdo Volkswagen Golf VI (5K1) 2008-2012",
            precio=Precio(Decimal("89.90"), "EUR", "89.90"),
            marca="VALEO",
            mpn="5K1941005E",
            sku="77123",
            estado="Usado",
            disponible=True,
            descripcion=(
                "Faro delantero izquierdo de Volkswagen Golf VI. Pieza usada, probada y en buen "
                "estado. Incluye motor de regulación."
            ),
            imagenes=(
                "https://www.desguaces-ejemplo.es/media/piezas/77123-1.jpg",
                "https://www.desguaces-ejemplo.es/media/piezas/77123-2.jpg",
                "https://www.desguaces-ejemplo.es/media/piezas/77123-3.jpg",
            ),
            url="https://www.desguaces-ejemplo.es/pieza/faro-delantero-izquierdo-golf-vi-77123",
        )
    ]


def test_woocommerce_con_graph() -> None:
    html = (FIXTURES / "ficha_woocommerce_graph.html").read_text(encoding="utf-8")
    [producto] = productos_jsonld(html)
    assert producto.nombre == "Pastillas de freno delanteras Bosch 0 986 494 527"
    assert producto.precio == Precio(Decimal("32.45"), "EUR", "32.45")
    assert producto.marca == "Bosch"
    assert producto.estado == "Nuevo"
    assert producto.gtin == "4047024567890"
    assert producto.sku == "BOS-0986494527"
    assert producto.mpn is None
    assert producto.disponible is True
    assert producto.descripcion == (
        "Juego de pastillas de freno delanteras Bosch para Seat León, VW Golf y Audi A3."
    )
    assert producto.imagenes == (
        "https://recambiosya.es/wp-content/uploads/2026/03/0986494527.jpg",
    )


def test_lista_de_nivel_superior_y_producto_anidado() -> None:
    html = _bloque(
        [
            {"@type": "WebSite", "name": "Tienda"},
            {
                "@type": "ItemPage",
                "mainEntity": {"@type": "Product", "name": "Alternador", "offers": {"price": 120}},
            },
        ]
    )
    [producto] = productos_jsonld(html)
    assert producto.nombre == "Alternador"
    assert producto.precio == Precio(Decimal("120"), "EUR", "120")


def test_varios_productos_en_orden() -> None:
    html = _bloque(
        {
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "item": {"@type": "Product", "name": "Uno"}},
                {"@type": "ListItem", "item": {"@type": ["Product", "Car"], "name": "Dos"}},
            ],
        }
    ) + _bloque({"@type": "http://schema.org/Product", "name": "Tres"})
    assert [p.nombre for p in productos_jsonld(html)] == ["Uno", "Dos", "Tres"]


@pytest.mark.parametrize(
    ("offers", "importe", "moneda", "texto"),
    [
        ({"@type": "Offer", "price": 38.9, "priceCurrency": "EUR"}, "38.9", "EUR", "38.9"),
        ({"@type": "Offer", "price": "38.90"}, "38.90", "EUR", "38.90"),
        ({"@type": "Offer", "price": "38,90"}, "38.90", "EUR", "38,90"),
        (
            {"@type": "Offer", "price": "1.234,56", "priceCurrency": "eur"},
            "1234.56",
            "EUR",
            "1.234,56",
        ),
        ({"@type": "Offer", "price": 25, "priceCurrency": "USD"}, "25", "USD", "25"),
        ([{"@type": "Offer"}, {"@type": "Offer", "price": "10.00"}], "10.00", "EUR", "10.00"),
        (
            {"@type": "AggregateOffer", "lowPrice": "19.99", "highPrice": "45", "offerCount": 3},
            "19.99",
            "EUR",
            "19.99",
        ),
        (
            {"@type": "AggregateOffer", "lowPrice": 5, "offers": [{"price": 7}]},
            "5",
            "EUR",
            "5",
        ),
        (
            {"@type": "Offer", "priceSpecification": {"price": 12.5, "priceCurrency": "GBP"}},
            "12.5",
            "GBP",
            "12.5",
        ),
        (
            {"@type": "Offer", "priceSpecification": [{"price": "8.00", "priceCurrency": "EUR"}]},
            "8.00",
            "EUR",
            "8.00",
        ),
    ],
)
def test_ofertas(offers: Any, importe: str, moneda: str, texto: str) -> None:
    producto = _uno({"@type": "Product", "name": "X", "offers": offers})
    assert producto.precio == Precio(Decimal(importe), moneda, texto)


@pytest.mark.parametrize(
    "offers",
    [
        {"price": "Consultar"},
        {"price": None},
        {"price": True},
        {"price": -5},
        {"price": "NaN"},
        {"price": "Infinity"},
        {"price": 1e30},
        {"price": [1, 2]},
        {"price": {"valor": 3}},
        "38.90",
        [],
        {"priceCurrency": "EUR"},
    ],
)
def test_ofertas_sin_precio_valido(offers: Any) -> None:
    assert _uno({"@type": "Product", "name": "X", "offers": offers}).precio is None


def test_moneda_no_valida_se_ignora() -> None:
    producto = _uno({"@type": "Product", "offers": {"price": 3, "priceCurrency": "<b>€"}})
    assert producto.precio == Precio(Decimal("3"), "EUR", "3")


@pytest.mark.parametrize(
    ("condicion", "estado"),
    [
        ("https://schema.org/NewCondition", "Nuevo"),
        ("http://schema.org/UsedCondition", "Usado"),
        ("RefurbishedCondition", "Reacondicionado"),
        ("schema:RefurbishedCondition", "Reacondicionado"),
        ({"@id": "https://schema.org/UsedCondition"}, "Usado"),
        ("DamagedCondition", None),
        ("Nuevo", None),
        (3, None),
    ],
)
def test_estado(condicion: Any, estado: str | None) -> None:
    assert _uno({"@type": "Product", "itemCondition": condicion}).estado == estado


def test_estado_de_la_oferta() -> None:
    producto = _uno(
        {
            "@type": "Product",
            "offers": {"price": 1, "itemCondition": "https://schema.org/UsedCondition"},
        }
    )
    assert producto.estado == "Usado"


@pytest.mark.parametrize(
    ("disponibilidad", "disponible"),
    [
        ("https://schema.org/InStock", True),
        ("http://schema.org/LimitedAvailability", True),
        ("InStoreOnly", True),
        ("OnlineOnly", True),
        ("PreOrder", True),
        ("BackOrder", True),
        ("https://schema.org/OutOfStock", False),
        ("SoldOut", False),
        ("Discontinued", False),
        ("¿quién sabe?", None),
    ],
)
def test_disponibilidad(disponibilidad: str, disponible: bool | None) -> None:
    producto = _uno({"@type": "Product", "offers": {"price": 1, "availability": disponibilidad}})
    assert producto.disponible is disponible


@pytest.mark.parametrize(
    ("marca", "esperada"),
    [
        ("Bosch", "Bosch"),
        ({"@type": "Brand", "name": "Valeo"}, "Valeo"),
        ({"@type": "Organization", "name": " Hella  KGaA "}, "Hella KGaA"),
        ([{"name": "Febi"}, {"name": "Otra"}], "Febi"),
        ({"@type": "Brand"}, None),
        ("", None),
        (12, None),
    ],
)
def test_marca(marca: Any, esperada: str | None) -> None:
    assert _uno({"@type": "Product", "brand": marca}).marca == esperada


def test_imagenes_en_varias_formas() -> None:
    producto = _uno(
        {
            "@type": "Product",
            "image": [
                "https://t.es/1.jpg",
                {"@type": "ImageObject", "contentUrl": "https://t.es/2.jpg"},
                {"url": "/3.jpg"},
                "javascript:alert(1)",
                "data:image/png;base64,AAAA",
                "https://t.es/1.jpg",
                42,
            ],
        }
    )
    assert producto.imagenes == ("https://t.es/1.jpg", "https://t.es/2.jpg", "/3.jpg")


def test_identificadores_numericos() -> None:
    producto = _uno({"@type": "Product", "sku": 77123, "mpn": "  5K1 941 005 E ", "gtin": 4047024})
    assert (producto.sku, producto.mpn, producto.gtin) == ("77123", "5K1 941 005 E", "4047024")


def test_entidades_y_html_en_textos() -> None:
    producto = _uno(
        {
            "@type": "Product",
            "name": "Faro &amp; piloto",
            "description": "<p>Uno</p><p>dos &lt;3</p>",
        }
    )
    assert producto.nombre == "Faro & piloto"
    assert producto.descripcion == "Uno dos <3"


@pytest.mark.parametrize(
    "bloque",
    [
        "{ esto no es json }",
        '{"@type": "Product", "name": "X",}',
        "",
        "null",
        "42",
        '"Product"',
        pytest.param("[" * 80, id="sin-cerrar"),
        pytest.param("[" * 100_000 + "]" * 100_000, id="anidamiento-extremo"),
    ],
)
def test_bloques_invalidos_se_ignoran(bloque: str) -> None:
    html = f'<script type="application/ld+json">{bloque}</script>' + _bloque(
        {"@type": "Product", "name": "Bueno"}
    )
    assert [p.nombre for p in productos_jsonld(html)] == ["Bueno"]


def test_json_con_saltos_de_linea_en_cadenas_y_comentario_html() -> None:
    html = (
        '<script type="application/ld+json">\n<!--\n{"@type": "Product", "name": "Faro\nizquierdo"}'
        "\n-->\n</script>"
    )
    assert [p.nombre for p in productos_jsonld(html)] == ["Faro izquierdo"]


def test_tipo_de_script_con_mayusculas_y_espacios() -> None:
    html = (
        '<script type=" Application/LD+JSON ; charset=utf-8">'
        '{"@type":"Product","name":"A"}</script>'
    )
    assert [p.nombre for p in productos_jsonld(html)] == ["A"]


def test_otros_scripts_no_cuentan() -> None:
    html = (
        '<script>{"@type":"Product","name":"A"}</script>'
        '<script type="application/json">{"@type":"Product"}</script>'
    )
    assert productos_jsonld(html) == []


def test_bloque_gigante_se_ignora() -> None:
    relleno = "x" * (MAX_BLOQUE + 1)
    html = _bloque({"@type": "Product", "name": "A", "description": relleno})
    assert productos_jsonld(html) == []


def test_referencias_circulares_y_anidamiento_profundo() -> None:
    anidado: dict[str, Any] = {"@type": "Product", "name": "Fondo"}
    for _ in range(300):
        anidado = {"@type": "Thing", "hijo": anidado}
    # Más allá de la profundidad máxima no se busca, pero no se rompe.
    assert productos_jsonld(_bloque(anidado)) == []
    assert (
        productos_jsonld(_bloque({"a": {"b": {"@type": "Product", "name": "Cerca"}}}))[0].nombre
        == "Cerca"
    )


def test_nombres_y_descripciones_recortados() -> None:
    producto = _uno({"@type": "Product", "name": "a" * 5_000, "description": "b " * 10_000})
    assert producto.nombre is not None and len(producto.nombre) <= 300
    assert producto.descripcion is not None and len(producto.descripcion) <= 5_000


def test_urls_que_no_se_pueden_analizar_se_descartan() -> None:
    # Revisión: «http://[abc» pasaba el filtro y luego `urljoin` lanzaba ValueError en la ficha.
    producto = _uno(
        {
            "@type": "Product",
            "url": "http://[abc",
            "image": ["https://[::1/a.jpg", "/b.jpg"],
            "offers": {"price": "10", "url": "https://tienda.es/p/1"},
        }
    )
    assert producto.url == "https://tienda.es/p/1"
    assert producto.imagenes == ("/b.jpg",)
