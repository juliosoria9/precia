"""Lectura de precios en formatos reales de tiendas españolas e internacionales."""

from decimal import Decimal

import pytest

from precia.extraccion import precios
from precia.extraccion.dom import Nodo, analizar, tachado
from precia.extraccion.precios import buscar_precios, leer_precio, texto_de_precio
from precia.extraccion.tipos import SelectorNoValido
from tests.cronometro import segundos_de_cpu


@pytest.mark.parametrize(
    ("texto", "importe", "moneda"),
    [
        # Formato español
        ("1.234,56 €", "1234.56", "EUR"),
        ("38,90€", "38.90", "EUR"),
        ("38,90 €", "38.90", "EUR"),
        ("38 €", "38", "EUR"),
        ("38€", "38", "EUR"),
        ("12,5 €", "12.5", "EUR"),
        ("12.345.678,90 €", "12345678.90", "EUR"),
        ("1.500 €", "1500", "EUR"),
        ("12,00 € IVA incl.", "12.00", "EUR"),
        ("PVP: 45,00€ (IVA incluido)", "45.00", "EUR"),
        ("Precio: 38,90 EUR", "38.90", "EUR"),
        ("38,90 euros", "38.90", "EUR"),
        ("38,90 Euros IVA no incluido", "38.90", "EUR"),
        ("1 234,56 €", "1234.56", "EUR"),
        ("1\u00a0234,56\u00a0€", "1234.56", "EUR"),
        ("1\u202f234,56 €", "1234.56", "EUR"),
        ("€ 12,95", "12.95", "EUR"),
        ("\n\t  89,90\n €  ", "89.90", "EUR"),
        # Formato internacional
        ("€1,234.56", "1234.56", "EUR"),
        ("EUR 12.5", "12.5", "EUR"),
        ("EUR12.50", "12.50", "EUR"),
        ("12.50 EUR", "12.50", "EUR"),
        ("$1,234.56", "1234.56", "USD"),
        ("US$ 19.99", "19.99", "USD"),
        ("19.99 USD", "19.99", "USD"),
        ("£9.99", "9.99", "GBP"),
        ("1,500 €", "1500", "EUR"),
        # Sin moneda: solo si parece un precio (con céntimos o el número solo)
        ("38,90", "38.90", "EUR"),
        ("1.234,56", "1234.56", "EUR"),
        ("Precio: 38,90", "38.90", "EUR"),
        ("38", "38", "EUR"),
        # Rangos y «desde»: el primero
        ("Desde 9,99 €", "9.99", "EUR"),
        ("desde 1.299,00€", "1299.00", "EUR"),
        ("10,00 € - 20,00 €", "10.00", "EUR"),
        ("10,00 € \u2013 20,00 €", "10.00", "EUR"),
        ("De 10 a 20 €", "10", "EUR"),
        ("10-20 €", "10", "EUR"),
        ("$10 - $20", "10", "USD"),
        # Casos trampa con un precio de verdad
        ("3 x 12,00 €", "12.00", "EUR"),
        ("3x12,00€", "12.00", "EUR"),
        ("-20% 29,95 €", "29.95", "EUR"),
        ("Ahorra 20 % · 29,95 €", "29.95", "EUR"),
        ("IVA 21% incluido: 121,00 €", "121.00", "EUR"),
        ("Envío 4,95 € · Tel. 912 345 678", "4.95", "EUR"),
        ("Tel. 912 345 678 · Envío 4,95 €", "4.95", "EUR"),
        ("Ref. 1K0615301AA 89,00 €", "89.00", "EUR"),
        ("2 unidades por 15,50 €", "15.50", "EUR"),
        ("Valoración 4,5 de 5 · 89,90 €", "89.90", "EUR"),
        ("Pack de 4 por 120 €", "120", "EUR"),
    ],
)
def test_lee_precios(texto: str, importe: str, moneda: str) -> None:
    precio = leer_precio(texto)
    assert precio is not None, texto
    assert precio.importe == Decimal(importe)
    assert str(precio.importe) == importe
    assert precio.moneda == moneda


@pytest.mark.parametrize(
    ("texto", "importe", "moneda"),
    [
        ("Antes 49,99 € Ahora 39,99 €", "39.99", "EUR"),
        ("Ancien prix : 17,06 € Prix actuel : 14,50 €", "14.50", "EUR"),  # Oscaro real
        ("Antes: 59,90 € 39,90 €", "39.90", "EUR"),
        ("PVP recomendado 59,90 € Precio 39,90 €", "39.90", "EUR"),
        ("Was £20.00 Now £15.00", "15.00", "GBP"),
        ("Ahorra 20,00 € 39,90 €", "39.90", "EUR"),
        ("31,93 € precio recomendado* 14,50 €", "14.50", "EUR"),
        ("14,50 € 31,93 € precio recomendado*", "14.50", "EUR"),
        # Revisión: la marca de antiguo vale para los dos extremos de un rango...
        ("Antes 30 - 40 € Ahora 20 - 25 €", "20", "EUR"),
        ("Antes 30 - 40 €  20 - 25 €", "20", "EUR"),
        # ... y, si todos quedan marcados, mejor el que solo lo está por detrás.
        ("Antes: 59,90 € 49,90 € PVP recomendado: 69,90 €", "49.90", "EUR"),
    ],
)
def test_salta_el_precio_antiguo_anunciado(texto: str, importe: str, moneda: str) -> None:
    precio = leer_precio(texto)
    assert precio is not None, texto
    assert str(precio.importe) == importe
    assert precio.moneda == moneda


@pytest.mark.parametrize(
    ("texto", "importe"),
    [
        ("Antes 59,90 €", "59.90"),
        ("31,93 € precio recomendado*", "31.93"),
        ("48,40 € 40,00 € sin IVA", "48.40"),
        ("Antes 30 - 40 €", "30"),
    ],
)
def test_el_precio_antiguo_vale_si_no_hay_otro(texto: str, importe: str) -> None:
    precio = leer_precio(texto)
    assert precio is not None, texto
    assert str(precio.importe) == importe


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        "Consultar precio",
        "Gratis",
        "Agotado",
        "-20%",
        "- 20 %",
        "IVA 21%",
        "Tel. 912 345 678",
        "Teléfono: 912345678",
        "Llámanos al 91 123 45 67",
        "Ref. 12345",
        "Referencia: 8200 123 456",
        "Ref: 1K0615301AA",
        "OEM 7701208265",
        "SKU 4421",
        "CP 28001",
        "2 unidades",
        "3 x 2",
        "2x1",
        "Valoración 4,5 de 5",
        "4,5",
        "-5,00 €",
        "Ahorras -5,00 €",
        "Modelo 2015-2019",
        "Golf VI 1.6 TDI 105 CV",
        "Talla 38",
        "€",
        "1.234.567.890.123,00 €",
        "12,345,67 €",
    ],
)
def test_sin_importe_o_trampa(texto: str) -> None:
    assert leer_precio(texto) is None, texto


def test_texto_original_del_precio() -> None:
    precio = leer_precio("PVP:  1.234,56 € IVA incl.")
    assert precio is not None
    assert precio.texto == "1.234,56 €"
    precio = leer_precio("Desde €1,234.56")
    assert precio is not None
    assert precio.texto == "€1,234.56"


def test_texto_enorme_no_se_procesa_entero() -> None:
    assert leer_precio("a" * 100_000 + " 12,00 €") is None
    precio = leer_precio("12,00 € " + "a" * 100_000)
    assert precio is not None


def test_no_texto() -> None:
    assert leer_precio(None) is None  # type: ignore[arg-type]


def test_buscar_precios_en_una_pagina() -> None:
    texto = (
        "Faro delantero 89,90 € IVA incl. · Envío 4,95 € · Tel. 912 345 678 · Talla 38 · "
        "Antes 120 € · -25% · 3 x 12,00 € · Valoración 4,5 · precio 15.50"
    )
    importes = [p.importe for p in buscar_precios(texto)]
    assert importes == [
        Decimal("89.90"),
        Decimal("4.95"),
        Decimal("120"),
        Decimal("12.00"),
        Decimal("15.50"),
    ]


def test_miles_con_espacio_no_es_cuadratico() -> None:
    # Revisión: una tabla de números separados por espacios («123 123 123...») hacía que la
    # expresión de miles con espacio retrocediera desde cada grupo: 160 KB tardaban más de un
    # minuto. Una página hostil podía bloquear la extracción.
    hostil = "123 " * 40_000
    assert list(buscar_precios(hostil)) == []
    assert segundos_de_cpu(lambda: list(buscar_precios(hostil)), 2) < 2
    precio = leer_precio("Total 123 456 789,10 €")
    assert precio is not None
    assert precio.importe == Decimal("123456789.10")


WOOCOMMERCE = (
    '<p class="price"><del aria-hidden="true"><span class="woocommerce-Price-amount amount">'
    '<bdi>59,90&nbsp;€</bdi></span></del> <ins><span class="woocommerce-Price-amount amount">'
    "<bdi>39,90&nbsp;€</bdi></span></ins></p>"
)


def test_texto_de_precio_sin_lo_tachado() -> None:
    assert texto_de_precio(analizar(WOOCOMMERCE), ".price") == "39,90 €"


def test_texto_de_precio_salta_coincidencias_tachadas() -> None:
    # El primer `bdi` está dentro del `<del>`: es el precio antiguo.
    assert texto_de_precio(analizar(WOOCOMMERCE), ".price bdi") == "39,90 €"


def test_texto_de_precio_si_todo_esta_tachado_da_el_texto_de_siempre() -> None:
    oscaro = analizar(
        '<div class="price"><p class="reference-price">Ancien prix : 17,06 €</p>'
        "<p>Prix actuel : 14,50 €</p></div>"
    )
    assert texto_de_precio(oscaro, "p.reference-price") == "Ancien prix : 17,06 €"
    assert texto_de_precio(oscaro, "div.price") == "Prix actuel : 14,50 €"
    # Shopify (Dawn) marca así también el precio normal de un producto sin oferta.
    shopify = analizar('<span class="price-item price-item--regular">10,00 €</span>')
    assert texto_de_precio(shopify, "span.price-item") == "10,00 €"


MAGENTO_1 = (
    '<div class="product-view"><h1>Faro delantero izquierdo</h1><div class="price-box">'
    '<span class="regular-price" id="product-price-12"><span class="price">49,90 €</span></span>'
    '</div><button>Añadir al carrito</button></div><div class="block-related"><ol><li>'
    '<a href="/bombilla">Bombilla H7</a><div class="price-box"><p class="old-price">'
    '<span class="price">30,00 €</span></p><p class="special-price"><span class="price">'
    "19,90 €</span></p></div></li></ol></div>"
)


@pytest.mark.parametrize("selector", [".price", ".price-box .price"])
def test_texto_de_precio_no_salta_a_otro_bloque_de_la_pagina(selector: str) -> None:
    # Revisión: en Magento 1, `regular-price` es el precio vigente si no hay oferta. Saltarlo
    # daba el precio de un producto relacionado (19,90 €). Sin un precio vigente al lado del
    # tachado, el de siempre (`extraer_valor`), como antes de B4.
    assert texto_de_precio(analizar(MAGENTO_1), selector) == "49,90 €"


def test_texto_de_precio_salta_el_tachado_si_el_vigente_esta_al_lado() -> None:
    prestashop = analizar(
        '<div class="product-prices"><div class="product-discount">'
        '<span class="regular-price">59,90 €</span></div><div class="product-price">'
        '<div class="current-price"><span itemprop="price">39,90 €</span></div></div></div>'
    )
    assert texto_de_precio(prestashop, ".product-prices span") == "39,90 €"


def test_texto_de_precio_mira_cada_nodo_una_sola_vez(monkeypatch: pytest.MonkeyPatch) -> None:
    # Revisión: con miles de coincidencias dentro de un tachado a cientos de niveles, se subía
    # por todos los ancestros de cada una. Una ficha hostil de 3,6 MB paraba el bucle de
    # asyncio 40 s, y el plazo de la tienda no podía cortarlo.
    niveles, coincidencias = 300, 2_000
    html = (
        "<del>"
        + "<b>" * niveles
        + '<i class="p"></i>' * coincidencias
        + "</b>" * niveles
        + '</del><span class="p">12,90 €</span>'
    )
    raiz = analizar(html)
    miradas: list[Nodo] = []

    def contar(nodo: Nodo) -> bool:
        miradas.append(nodo)
        return tachado(nodo)

    monkeypatch.setattr(precios, "tachado", contar)
    assert texto_de_precio(raiz, ".p") == "12,90 €"
    assert len(miradas) <= niveles + coincidencias + 10


def test_texto_de_precio_salta_el_tachado_marcado_con_data_test() -> None:
    # MediaMarkt (R2-B4): el PVP tachado va en `data-test="…strike-price…"`, antes del vigente.
    tarjeta = analizar(
        '<div data-test="mms-price"><div><span>-24%</span>'
        '<div data-test="x-strike-price-type-rrp"><span>39,90 €</span></div></div>'
        "<div><span>29,99 €</span></div></div>"
    )
    texto = texto_de_precio(tarjeta, "[data-test=mms-price]")
    # El descuento («-24%») no es un importe y se queda; el tachado no.
    assert texto == "-24% 29,99 €"
    precio = leer_precio(texto)
    assert precio is not None and precio.importe == Decimal("29.99")


def test_texto_de_precio_con_atributo_como_extraer_valor() -> None:
    raiz = analizar('<meta itemprop="price" content="14.50"><del>17,06 €</del>')
    assert texto_de_precio(raiz, "meta[itemprop=price] @content") == "14.50"


def test_texto_de_precio_sin_coincidencias_o_con_selector_no_valido() -> None:
    assert texto_de_precio(analizar(WOOCOMMERCE), ".precio") is None
    with pytest.raises(SelectorNoValido):
        texto_de_precio(analizar(WOOCOMMERCE), "p:has(del)")


# --- Céntimos en un elemento aparte (causa R3-A2) -----------------------------------------------
# Brico Dépôt: `<span>228</span><span><span class="price-top">96</span>...€`. El selector del
# perfil y el anclaje leían 22896 € en vez de 228,96 €.

BRICO = (
    '<div class="price-box"><p class="price-was"><s>316,54\xa0€</s></p>'
    '<p class="price-wrapper"><span class="price-style"><span>228</span><span>'
    '<span class="price-top">96</span><span class="price-bottom">€</span></span></span></p>'
    '<p class="price-save">Ahorra 87,58\xa0€</p></div>'
)


def test_texto_de_precio_con_los_centimos_en_un_span_aparte() -> None:
    raiz = analizar(BRICO)
    texto = texto_de_precio(raiz, ".price-wrapper")
    assert texto == "228,96€"
    precio = leer_precio(texto)
    assert precio is not None and precio.importe == Decimal("228.96")
    vigente = leer_precio(texto_de_precio(raiz, ".price-box") or "")
    assert vigente is not None and vigente.importe == Decimal("228.96")


def test_buscar_precios_en_una_pagina_con_los_centimos_aparte() -> None:
    importes = [p.importe for p in buscar_precios(analizar(BRICO).texto(sin_tachados=True))]
    assert importes == [Decimal("228.96"), Decimal("87.58")]
