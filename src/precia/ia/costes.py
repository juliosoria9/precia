"""Tabla de precios por modelo (USD por millón de tokens) y cálculo del coste de una llamada."""

from dataclasses import dataclass
from decimal import Decimal

from precia.ia.tipos import ErrorIA

MILLON = 1_000_000


@dataclass(frozen=True, slots=True)
class Tarifa:
    entrada_usd_millon: Decimal
    salida_usd_millon: Decimal
    umbral_contexto_largo: int | None = None
    """Tokens de entrada a partir de los cuales toda la llamada se cobra a la tarifa larga."""
    entrada_largo_usd_millon: Decimal = Decimal(0)
    salida_largo_usd_millon: Decimal = Decimal(0)


GRATIS = Tarifa(Decimal(0), Decimal(0))

TARIFAS: dict[str, Tarifa] = {
    "claude-haiku-5-5": Tarifa(
        entrada_usd_millon=Decimal("0.10"),
        salida_usd_millon=Decimal("0.50"),
        umbral_contexto_largo=100_000,
        entrada_largo_usd_millon=Decimal("0.50"),
        salida_largo_usd_millon=Decimal("2.50"),
    ),
}


def tarifa(proveedor: str, modelo: str) -> Tarifa:
    """Tarifa del modelo. Los modelos locales (Ollama) no cuestan nada."""
    if proveedor == "ollama":
        return GRATIS
    try:
        return TARIFAS[modelo]
    except KeyError:
        # Sin tarifa no se puede respetar el presupuesto: mejor no llamar.
        raise ErrorIA(
            f"No hay tarifa para el modelo «{modelo}»; no se puede calcular el coste"
        ) from None


def calcular_coste(proveedor: str, modelo: str, tokens_entrada: int, tokens_salida: int) -> Decimal:
    """Coste en USD de una llamada con esos tokens."""
    if tokens_entrada < 0 or tokens_salida < 0:
        raise ValueError("Los tokens no pueden ser negativos")
    t = tarifa(proveedor, modelo)
    entrada, salida = t.entrada_usd_millon, t.salida_usd_millon
    if t.umbral_contexto_largo is not None and tokens_entrada > t.umbral_contexto_largo:
        entrada, salida = t.entrada_largo_usd_millon, t.salida_largo_usd_millon
    return (entrada * tokens_entrada + salida * tokens_salida) / MILLON
