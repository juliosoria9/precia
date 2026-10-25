"""Contrato del paquete `ia`: petición, respuesta, proveedores, registro y errores.

La IA de PrecIA no tiene herramientas ni ejecuta nada: recibe instrucciones (nuestras, de
confianza) y datos (contenido web, NO confiable) y devuelve JSON que cumple un esquema.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from precia.nucleo.modelos import LlamadaIA

CARACTERES_POR_TOKEN = 2
"""Caracteres por ficha de salida, a la baja, para `max_tokens_para` (R2-TOK).

El texto corriente da 3 o 4, pero las clases generadas (`mms-ui-dlUvRw`, `sc-59b6826e-0`), los
id y las URL que devuelven los perfiles se trocean en fichas mucho más cortas.
"""
MARGEN_TOKENS = 64
MINIMO_TOKENS = 512
"""`max_tokens_para` nunca da menos: era el tope fijo de antes y basta para respuestas cortas."""
_SALTO = 8
"""Salto de línea y sangría de cada valor, por si el modelo devuelve el JSON con formato."""
_NUMERO = 24


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
    """Tope de fichas de salida. Para un esquema acotado, al menos `max_tokens_para(esquema)`."""


def max_tokens_para(esquema: dict[str, Any]) -> int:
    """Tope de fichas de salida en el que cabe la respuesta válida más larga de `esquema` (R2-TOK).

    Cuenta los caracteres del JSON más largo que cumple las cotas del esquema (`maxLength`,
    `maxItems`, `enum`), con sangría, a `CARACTERES_POR_TOKEN`, más `MARGEN_TOKENS`, y nunca
    menos de `MINIMO_TOKENS`. Con un tope menor, una respuesta válida puede cortarse; lo que no
    quepa en este ya incumpliría las cotas. `ValueError` si algún texto o lista no tiene cota.
    """
    calculado = math.ceil(_caracteres(esquema, "") / CARACTERES_POR_TOKEN) + MARGEN_TOKENS
    return max(calculado, MINIMO_TOKENS)


def _caracteres(esquema: dict[str, Any], ruta: str) -> int:
    donde = ruta or "raíz"
    if "anyOf" in esquema:
        return max(_caracteres(opcion, ruta) for opcion in esquema["anyOf"])
    if "enum" in esquema:
        return max(len(json.dumps(valor, ensure_ascii=False)) for valor in esquema["enum"])
    tipo = esquema.get("type")
    if isinstance(tipo, list):
        return max(_caracteres({**esquema, "type": t}, ruta) for t in tipo)
    match tipo:
        case "string":
            cota = esquema.get("maxLength")
            if not isinstance(cota, int):
                raise ValueError(f"{donde}: texto sin cota (maxLength)")
            return cota + 2
        case "number" | "integer":
            return _NUMERO
        case "boolean" | "null":
            return 5
        case "array":
            cota = esquema.get("maxItems")
            if not isinstance(cota, int):
                raise ValueError(f"{donde}: lista sin cota (maxItems)")
            elemento = _caracteres(esquema.get("items", {}), f"{ruta}[]")
            return 2 + cota * (elemento + 1 + _SALTO)
        case "object":
            propiedades = esquema.get("properties", {})
            if esquema.get("additionalProperties", True) is not False:
                raise ValueError(f"{donde}: objeto sin cota (additionalProperties)")
            return 2 + sum(
                len(json.dumps(clave, ensure_ascii=False))
                + 2
                + _caracteres(sub, f"{ruta}.{clave}" if ruta else clave)
                + 1
                + _SALTO
                for clave, sub in propiedades.items()
            )
    raise ValueError(f"{donde}: tipo sin cota ({tipo!r})")


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


class CacheExtracciones(Protocol):
    """Respuestas ya limpias del extractor de respaldo, por texto, campos y modelo (A3).

    Nunca lanza: un fallo al leer o al guardar cuenta como fallo de caché. Solo guarda la huella
    del texto y los valores extraídos, nunca la página ni el prompt.
    """

    def leer(
        self, texto_sha256: str, campos: tuple[str, ...], modelo: str
    ) -> dict[str, str] | None: ...

    def guardar(
        self, texto_sha256: str, campos: tuple[str, ...], modelo: str, valores: Mapping[str, str]
    ) -> None: ...
