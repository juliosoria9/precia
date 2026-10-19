"""Contrato del paquete `ia`: petición, respuesta, proveedores, registro y errores.

La IA de PrecIA no tiene herramientas ni ejecuta nada: recibe instrucciones (nuestras, de
confianza) y datos (contenido web, NO confiable) y devuelve JSON que cumple un esquema.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from precia.nucleo.modelos import LlamadaIA


@dataclass(frozen=True, slots=True)
class PeticionIA:
    componente: str
    """`configurador`, `reparador`, `extractor`, `emparejado` o `clasificador`."""
    instrucciones: str
    """Texto nuestro (de confianza). Nunca lleva contenido de webs."""
    datos: str
    """Contenido web (NO confiable). Va delimitado y marcado como datos."""
    esquema: dict[str, Any]
    """JSON Schema de la salida: objetos con `additionalProperties: false` y `required`."""
    max_tokens: int = 2048


@dataclass(frozen=True, slots=True)
class RespuestaIA:
    datos: dict[str, Any]
    tokens_entrada: int
    tokens_salida: int
    coste_usd: Decimal
    duracion_ms: int
    proveedor: str
    modelo: str


class ErrorIA(Exception):
    """Base de los errores de IA. El mensaje se puede enseñar al usuario."""


class IANoDisponible(ErrorIA):
    """Sin clave, sin servidor local o con errores de red persistentes."""


class SalidaNoValida(ErrorIA):
    """La respuesta no es JSON o no cumple el esquema (tras reintentar)."""


class PresupuestoAgotado(ErrorIA):
    """Se ha alcanzado el tope de llamadas por búsqueda o el presupuesto mensual."""


class ProveedorIA(Protocol):
    nombre: str
    """`api` u `ollama`."""
    modelo: str

    async def completar(self, peticion: PeticionIA) -> RespuestaIA: ...


class RegistroLlamadas(Protocol):
    """Lo implementa el almacén. Solo guarda métricas, nunca el contenido de la petición."""

    def registrar_llamada(self, llamada: LlamadaIA) -> None: ...

    def gasto_mes_usd(self) -> Decimal: ...
