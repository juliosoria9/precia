"""Tabla de precios de los modelos y cálculo del coste de una llamada."""

from decimal import Decimal

import pytest

from precia.ia.costes import calcular_coste, tarifa
from precia.ia.tipos import ErrorIA


def test_haiku_por_debajo_del_umbral() -> None:
    # 0,10 $ por millón de entrada y 0,50 $ por millón de salida.
    assert calcular_coste("api", "claude-haiku-5-5", 50_000, 0) == Decimal("0.005")
    assert calcular_coste("api", "claude-haiku-5-5", 0, 1_000_000) == Decimal("0.50")
    assert calcular_coste("api", "claude-haiku-5-5", 10_000, 500) == Decimal("0.00125")


def test_haiku_justo_en_el_umbral_usa_la_tarifa_normal() -> None:
    assert calcular_coste("api", "claude-haiku-5-5", 100_000, 0) == Decimal("0.01")


def test_haiku_por_encima_del_umbral_encarece_entrada_y_salida() -> None:
    coste = calcular_coste("api", "claude-haiku-5-5", 200_000, 1_000)
    assert coste == Decimal("0.50") * 200_000 / 1_000_000 + Decimal("2.50") * 1_000 / 1_000_000
    assert coste == Decimal("0.1025")


def test_ollama_es_gratis() -> None:
    assert calcular_coste("ollama", "qwen2.5:7b-instruct-q4_K_M", 50_000, 9_000) == 0
    assert tarifa("ollama", "cualquiera").entrada_usd_millon == 0


def test_modelo_de_la_api_sin_tarifa() -> None:
    with pytest.raises(ErrorIA, match="tarifa"):
        calcular_coste("api", "modelo-inventado", 1, 1)


def test_tokens_negativos() -> None:
    with pytest.raises(ValueError):
        calcular_coste("api", "claude-haiku-5-5", -1, 0)
