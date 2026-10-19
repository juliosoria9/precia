"""Lectura de precios en formatos reales de tiendas españolas e internacionales."""

import time
from decimal import Decimal

import pytest

from precia.extraccion.precios import buscar_precios, leer_precio


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
        ("Antes 49,99 € Ahora 39,99 €", "49.99", "EUR"),
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
    inicio = time.perf_counter()
    assert list(buscar_precios("123 " * 40_000)) == []
    assert time.perf_counter() - inicio < 2
    precio = leer_precio("Total 123 456 789,10 €")
    assert precio is not None
    assert precio.importe == Decimal("123456789.10")
