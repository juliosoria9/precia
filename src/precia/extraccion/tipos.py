"""Contrato del paquete `extraccion`: precios, producto JSON-LD, listados y fichas."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from precia.nucleo.modelos import ValorCampo

OBRAS_VENDIBLES = frozenset(
    {"Book", "Audiobook", "Movie", "MusicAlbum", "MusicRecording", "VideoGame"}
    | {"SoftwareApplication"}
)
"""Tipos de `schema.org` que no son `Product` pero que las tiendas venden con su oferta.

Las librerías publican `Book` o `Audiobook` con `offers` y sin `Product`. La lista es cerrada a
propósito: `Event`, `Service`, `Organization` u `OfferCatalog` también llevan ofertas y no son el
producto de una ficha.
"""


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
    en_lista: bool = False
    """Va dentro de los `itemListElement` de una lista (`ItemList`, `OfferCatalog`): es una
    tarjeta de un listado o un producto relacionado, no el producto de la página por sí solo."""


@dataclass(frozen=True, slots=True)
class ResultadoListado:
    """Un resultado de la página de búsqueda de una tienda."""

    titulo: str
    url: str
    """Absoluta y del mismo dominio que la tienda."""
    precio: Precio | None = None
    foto: str | None = None
    """La foto de la tarjeta (`imagenes.foto_de_tarjeta`), ya validada; `None` si no hay."""


@dataclass(frozen=True, slots=True)
class FichaExtraida:
    """Campos de una ficha tras la cascada JSON-LD → perfil → IA y la verificación."""

    url: str
    titulo: str
    precio: Precio | None
    campos: dict[str, ValorCampo]
    """Solo los campos pedidos que se han obtenido; cada uno con origen, anclaje y confianza."""
    avisos: tuple[str, ...] = field(default_factory=tuple)
    referencias_citadas: frozenset[str] = frozenset()
    """Las referencias buscadas que la página cita, ancladas en su texto visible o en una pestaña
    oculta (B5). Solo sirven para el emparejado como «equivalente», nunca para anclar valores."""
    obtenida: datetime | None = None
    """Cuándo se descargó la página (o la respuesta de la API): si salió de la caché, es la
    fecha de la descarga, no la de ahora. Es la que va al histórico (A3)."""
    listado: int = 0
    """Si la página es un listado y no una ficha (R2-C10: landing de marca o SEO), cuántos
    productos trae su JSON-LD `ItemList`. Entonces no hay precio ni campos; si no, 0."""
    repetidos: dict[str, str] = field(default_factory=dict)
    """Campos cuyo selector del perfil leyó lo mismo que el de otro campo de más prioridad, y se
    descartaron (R2-CAMPOS): campo descartado → campo que se queda con el valor."""
    sin_forma: dict[str, str] = field(default_factory=dict)
    """Campos cuyo selector del perfil leyó un valor sin forma de ese campo (R3-E: «100% algodón»
    como referencia) y se descartaron: campo → valor leído, recortado."""
    foto: str | None = None
    """URL `https` de la foto principal, validada por `imagenes.foto_valida` y sacada del marcado
    de la página o de la API de la tienda, nunca de la IA. Se enlaza, no se descarga."""


class SelectorNoValido(ValueError):
    """El selector no pertenece al subconjunto de CSS admitido."""


class ExtractorIA(Protocol):
    """Extractor de respaldo con IA. Devuelve valores en bruto; el anclaje se comprueba fuera."""

    async def extraer(self, texto_pagina: str, campos: tuple[str, ...]) -> dict[str, str]: ...
