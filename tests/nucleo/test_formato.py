from datetime import date, datetime
from decimal import Decimal

import pytest

from precia.nucleo.formato import decimal, dolares, euros, fecha_corta, fecha_hora, miles


@pytest.mark.parametrize(
    ("importe", "esperado"),
    [
        ("0", "0,00 €"),
        ("18", "18,00 €"),
        ("38.905", "38,91 €"),
        ("1234.5", "1.234,50 €"),
        ("-3.2", "-3,20 €"),
    ],
)
def test_euros(importe: str, esperado: str) -> None:
    assert euros(Decimal(importe)) == esperado


@pytest.mark.parametrize(
    ("importe", "esperado"),
    [
        ("0", "0 $"),
        ("0.0148", "0,0148 $"),
        ("0.00003", "0,0000 $"),
        ("10", "10,00 $"),
        ("12.5", "12,50 $"),
    ],
)
def test_dolares_usa_cuatro_decimales_solo_por_debajo_de_un_dolar(
    importe: str, esperado: str
) -> None:
    assert dolares(Decimal(importe)) == esperado


def test_miles() -> None:
    assert miles(59500) == "59.500"
    assert miles(40) == "40"


def test_decimal_con_coma() -> None:
    assert decimal(4.8) == "4,8"
    assert decimal(10.12, 2) == "10,12"
    assert decimal(3) == "3,0"


def test_fechas() -> None:
    assert fecha_corta(date(2026, 10, 19)) == "19 oct"
    assert fecha_hora(datetime(2026, 1, 5, 9, 7)) == "5 ene 09:07"
