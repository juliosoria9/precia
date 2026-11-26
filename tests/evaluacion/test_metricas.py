"""Métricas del banco de evaluación (H8): comparar lo extraído con la etiqueta y contar."""

from decimal import Decimal

import pytest

from precia.evaluacion.metricas import Juicio, Metricas, coincide, juzgar


@pytest.mark.parametrize(
    ("campo", "esperado", "obtenido"),
    [
        ("precio", "38.90", "38.9"),
        ("precio", "38.90", "38.90"),
        ("precio", "1234.50", "1234.5000"),
        ("marca", "Brembo", "BREMBO"),
        ("marca", "Citroën", "citroen"),
        ("marca", "  Bosch  ", "Bosch"),
        ("referencia_oem", "P 85 075", "P85075"),
        ("referencia_oem", "1K0-698-151", "1k0698151"),
        ("estado", "Nuevo", "nuevo"),
    ],
)
def test_valores_que_coinciden(campo: str, esperado: str, obtenido: str) -> None:
    assert coincide(campo, esperado, obtenido)


@pytest.mark.parametrize(
    ("campo", "esperado", "obtenido"),
    [
        ("precio", "38.90", "38.91"),
        ("precio", "38.90", "3890"),
        ("precio", "38.90", "gratis"),
        ("precio", "38.90", "NaN"),
        ("marca", "Brembo", "Bosch"),
        ("marca", "Brembo", "Brembo Racing"),
        ("referencia_oem", "P 85 075", "P 85 076"),
        ("referencia_oem", "P 85 075", ""),
        ("estado", "Nuevo", "Usado"),
    ],
)
def test_valores_que_no_coinciden(campo: str, esperado: str, obtenido: str) -> None:
    assert not coincide(campo, esperado, obtenido)


def test_juicio_de_cada_combinacion() -> None:
    assert juzgar("marca", "Brembo", "brembo") is Juicio.CORRECTO
    assert juzgar("marca", "Brembo", "Bosch") is Juicio.INCORRECTO
    assert juzgar("marca", "Brembo", None) is Juicio.AUSENTE
    # La etiqueta dice que la página no da el dato: sacarlo es inventárselo.
    assert juzgar("marca", None, "Bosch") is Juicio.INVENTADO
    assert juzgar("marca", None, None) is Juicio.BIEN_AUSENTE
    assert juzgar("marca", "Brembo", "   ") is Juicio.AUSENTE


def test_cobertura_y_exactitud() -> None:
    metricas = Metricas()
    for juicio in (
        Juicio.CORRECTO,
        Juicio.CORRECTO,
        Juicio.CORRECTO,
        Juicio.INCORRECTO,
        Juicio.AUSENTE,
        Juicio.INVENTADO,
        Juicio.BIEN_AUSENTE,
    ):
        metricas.anotar(juicio)
    # 5 campos con valor en la etiqueta; se obtuvieron 4 (3 bien y 1 mal).
    assert metricas.esperados == 5
    assert metricas.cobertura == pytest.approx(4 / 5)
    # Exactitud sobre lo que se dio por obtenido: 3 bien de 5 (4 obtenidos y 1 inventado).
    assert metricas.exactitud == pytest.approx(3 / 5)
    assert metricas.inventados == 1


def test_sin_datos_las_tasas_son_cero_y_no_dividen_por_cero() -> None:
    metricas = Metricas()
    assert metricas.cobertura == 0.0 and metricas.exactitud == 0.0


def test_las_metricas_se_suman() -> None:
    a, b = Metricas(), Metricas()
    a.anotar(Juicio.CORRECTO)
    b.anotar(Juicio.AUSENTE)
    total = a + b
    assert (total.correctos, total.ausentes, total.esperados) == (1, 1, 2)
    assert total.cobertura == pytest.approx(0.5)


def test_el_precio_se_compara_como_importe_exacto() -> None:
    assert coincide("precio", "0.10", str(Decimal("0.1")))
    assert not coincide("precio", "0.10", "0.1000001")
