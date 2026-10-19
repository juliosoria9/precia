"""Contrato del paquete `extraccion`: precios, producto JSON-LD, listados y fichas."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from precia.nucleo.modelos import ValorCampo


@dataclass(frozen=True, slots=True)
class Precio:
    importe: Decimal
    moneda: str = "EUR"
    texto: str = ""
    """Texto original del que se leyó (para el anclaje)."""


@dataclass(frozen=True, slots=True)
class ProductoJSONLD:
    """Lo que interesa de un `schema.org/Product` (con su `Offer` o `AggregateOffer`)."""

    nombre: str | None = None
    precio: Precio | None = None
    marca: str | None = None
    mpn: str | None = None
    sku: str | None = None
    gtin: str | None = None
    estado: str | None = None
    """`Nuevo`, `Usado` o `Reacondicionado` (de `itemCondition`)."""
    disponible: bool | None = None
    descripcion: str | None = None
    imagenes: tuple[str, ...] = ()
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ResultadoListado:
    """Un resultado de la página de búsqueda de una tienda."""

    titulo: str
    url: str
    """Absoluta y del mismo dominio que la tienda."""
    precio: Precio | None = None


@dataclass(frozen=True, slots=True)
class FichaExtraida:
    """Campos de una ficha tras la cascada JSON-LD → perfil → IA y la verificación."""

    url: str
    titulo: str
    precio: Precio | None
    campos: dict[str, ValorCampo]
    """Solo los campos pedidos que se han obtenido; cada uno con origen, anclaje y confianza."""
    avisos: tuple[str, ...] = field(default_factory=tuple)


class SelectorNoValido(ValueError):
    """El selector no pertenece al subconjunto de CSS admitido."""


class ExtractorIA(Protocol):
    """Extractor de respaldo con IA. Devuelve valores en bruto; el anclaje se comprueba fuera."""

    async def extraer(self, texto_pagina: str, campos: tuple[str, ...]) -> dict[str, str]: ...
