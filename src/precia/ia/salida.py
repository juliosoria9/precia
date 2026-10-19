"""Salida estructurada: validador propio del subconjunto de JSON Schema que usamos.

La salida del modelo se trata como no confiable aunque la API garantice el esquema: se vuelve
a validar en el cliente y aquí, además, se comprueban las restricciones que la API no admite
(`minimum`, `maximum`, `minLength`, `maxLength`, `maxItems`...). Los mensajes de error dicen
dónde falla, nunca copian el valor recibido, que puede ser texto de una web.
"""

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from precia.ia.tipos import ErrorIA, SalidaNoValida

MAX_PROFUNDIDAD = 32
MAX_CARACTERES_SALIDA = 200_000

_TIPOS = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})
_ANOTACIONES = frozenset({"description", "title", "$schema"})
_ESTRUCTURA = frozenset(
    {"type", "properties", "required", "additionalProperties", "items", "enum", "anyOf"}
)
# Restricciones que la API de Anthropic no acepta en el esquema: las comprueba solo el cliente.
_SOLO_CLIENTE = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "maxItems",
    }
)
_ADMITIDAS = _ANOTACIONES | _ESTRUCTURA | _SOLO_CLIENTE | {"minItems"}


@dataclass(frozen=True, slots=True)
class Consumo:
    """Lo que costó una respuesta, aunque luego no sirviera."""

    tokens_entrada: int
    tokens_salida: int
    coste_usd: Decimal


class SalidaNoValidaConConsumo(SalidaNoValida):
    """La respuesta llegó (y se pagó) pero no es válida: lleva su consumo para registrarlo."""

    def __init__(self, mensaje: str, consumo: Consumo) -> None:
        super().__init__(mensaje)
        self.consumo = consumo


class RechazoDelModelo(ErrorIA):
    """El modelo se niega a responder (`refusal`). No se reintenta; lleva su consumo."""

    def __init__(self, mensaje: str, consumo: Consumo) -> None:
        super().__init__(mensaje)
        self.consumo = consumo


def _es_del_tipo(valor: Any, tipo: str) -> bool:
    # En Python `bool` hereda de `int`: True no es un número ni un entero en JSON.
    match tipo:
        case "object":
            return isinstance(valor, dict)
        case "array":
            return isinstance(valor, list)
        case "string":
            return isinstance(valor, str)
        case "boolean":
            return isinstance(valor, bool)
        case "null":
            return valor is None
        case "integer":
            return isinstance(valor, int) and not isinstance(valor, bool)
        case "number":
            return isinstance(valor, int | float) and not isinstance(valor, bool)
    raise ValueError(f"Tipo de JSON Schema no admitido: {tipo}")


def _validar(valor: Any, esquema: dict[str, Any], ruta: str, profundidad: int) -> None:
    if profundidad > MAX_PROFUNDIDAD:
        raise SalidaNoValida(f"{ruta}: se supera la profundidad máxima ({MAX_PROFUNDIDAD})")
    _comprobar_palabras_clave(esquema)

    if "anyOf" in esquema:
        for opcion in esquema["anyOf"]:
            try:
                _validar(valor, opcion, ruta, profundidad)
            except SalidaNoValida:
                continue
            break
        else:
            raise SalidaNoValida(f"{ruta}: no cumple ninguna de las opciones")

    if "type" in esquema:
        tipos = esquema["type"] if isinstance(esquema["type"], list) else [esquema["type"]]
        if not any(_es_del_tipo(valor, t) for t in tipos):
            raise SalidaNoValida(f"{ruta}: se esperaba {' o '.join(tipos)}")

    if "enum" in esquema and not any(
        valor == opcion and type(valor) is type(opcion) for opcion in esquema["enum"]
    ):
        raise SalidaNoValida(f"{ruta}: valor fuera de los permitidos")

    if isinstance(valor, str):
        if len(valor) < esquema.get("minLength", 0):
            raise SalidaNoValida(f"{ruta}: texto demasiado corto")
        if "maxLength" in esquema and len(valor) > esquema["maxLength"]:
            raise SalidaNoValida(f"{ruta}: texto demasiado largo")
    elif isinstance(valor, int | float) and not isinstance(valor, bool):
        _validar_numero(valor, esquema, ruta)
    elif isinstance(valor, list):
        _validar_lista(valor, esquema, ruta, profundidad)
    elif isinstance(valor, dict):
        _validar_objeto(valor, esquema, ruta, profundidad)


def _validar_numero(valor: float, esquema: dict[str, Any], ruta: str) -> None:
    if "minimum" in esquema and valor < esquema["minimum"]:
        raise SalidaNoValida(f"{ruta}: menor que el mínimo")
    if "maximum" in esquema and valor > esquema["maximum"]:
        raise SalidaNoValida(f"{ruta}: mayor que el máximo")
    if "exclusiveMinimum" in esquema and valor <= esquema["exclusiveMinimum"]:
        raise SalidaNoValida(f"{ruta}: no supera el mínimo")
    if "exclusiveMaximum" in esquema and valor >= esquema["exclusiveMaximum"]:
        raise SalidaNoValida(f"{ruta}: no queda por debajo del máximo")


def _validar_lista(valor: list[Any], esquema: dict[str, Any], ruta: str, profundidad: int) -> None:
    if len(valor) < esquema.get("minItems", 0):
        raise SalidaNoValida(f"{ruta}: faltan elementos")
    if "maxItems" in esquema and len(valor) > esquema["maxItems"]:
        raise SalidaNoValida(f"{ruta}: sobran elementos")
    if "items" in esquema:
        for indice, elemento in enumerate(valor):
            _validar(elemento, esquema["items"], f"{ruta}[{indice}]", profundidad + 1)


def _validar_objeto(
    valor: dict[str, Any], esquema: dict[str, Any], ruta: str, profundidad: int
) -> None:
    propiedades: dict[str, Any] = esquema.get("properties", {})
    for clave in esquema.get("required", []):
        if clave not in valor:
            raise SalidaNoValida(f"{ruta}: falta el campo obligatorio «{clave}»")
    # La clave sobrante viene de la respuesta: no se copia en el mensaje.
    if esquema.get("additionalProperties", True) is False and set(valor) - set(propiedades):
        raise SalidaNoValida(f"{ruta}: contiene campos no permitidos")
    for clave, subesquema in propiedades.items():
        if clave in valor:
            _validar(valor[clave], subesquema, f"{ruta}.{clave}", profundidad + 1)


def validar(datos: Any, esquema: dict[str, Any]) -> dict[str, Any]:
    """Comprueba que `datos` cumple `esquema` y lo devuelve. La raíz tiene que ser un objeto.

    Lanza `SalidaNoValida` si no lo cumple y `ValueError` si el esquema usa algo no admitido
    (eso es un fallo nuestro, no del modelo).
    """
    if not isinstance(datos, dict):
        raise SalidaNoValida("$: la salida tiene que ser un objeto JSON")
    _validar(datos, esquema, "$", 0)
    return datos


def _rechazar_constante(nombre: str) -> Any:
    raise SalidaNoValida(f"La salida contiene un número no válido en JSON ({nombre})")


def _decimal_finito(texto: str) -> float:
    # `parse_constant` no ve los números que desbordan: «1e999» llega como inf.
    valor = float(texto)
    if not math.isfinite(valor):
        raise SalidaNoValida("La salida contiene un número no válido en JSON (desbordamiento)")
    return valor


def interpretar(texto: str, esquema: dict[str, Any]) -> dict[str, Any]:
    """Lee el texto del modelo como JSON y lo valida contra el esquema."""
    if len(texto) > MAX_CARACTERES_SALIDA:
        raise SalidaNoValida("La salida del modelo es demasiado grande")
    try:
        datos = json.loads(texto, parse_constant=_rechazar_constante, parse_float=_decimal_finito)
    except (ValueError, RecursionError) as error:
        raise SalidaNoValida("La salida del modelo no es JSON válido") from error
    return validar(datos, esquema)


def leer_json(datos: bytes | str) -> Any:
    """`json.loads` que convierte el anidamiento excesivo en `ValueError`, como los demás fallos."""
    try:
        return json.loads(datos)
    except RecursionError:
        raise ValueError("JSON demasiado anidado") from None


def _comprobar_palabras_clave(esquema: dict[str, Any]) -> None:
    desconocidas = set(esquema) - _ADMITIDAS
    if desconocidas:
        raise ValueError(f"Palabras clave de JSON Schema no admitidas: {sorted(desconocidas)}")
    tipos = esquema.get("type", [])
    for tipo in tipos if isinstance(tipos, list) else [tipos]:
        if tipo not in _TIPOS:
            raise ValueError(f"Tipo de JSON Schema no admitido: {tipo}")


def esquema_para_api(esquema: dict[str, Any]) -> dict[str, Any]:
    """Copia del esquema sin las restricciones que la API no admite (el cliente las valida).

    Recorre el esquema entero y lanza `ValueError` si usa algo que `validar` no sabe
    comprobar: así el fallo salta antes de enviar (y pagar) la petición, no al validar.
    """
    _comprobar_palabras_clave(esquema)
    limpio: dict[str, Any] = {}
    for clave, valor in esquema.items():
        if clave in _SOLO_CLIENTE or (clave == "minItems" and valor > 1):
            continue
        if clave == "properties":
            limpio[clave] = {nombre: esquema_para_api(sub) for nombre, sub in valor.items()}
        elif clave == "items":
            limpio[clave] = esquema_para_api(valor)
        elif clave == "anyOf":
            limpio[clave] = [esquema_para_api(sub) for sub in valor]
        elif isinstance(valor, list):
            limpio[clave] = list(valor)
        else:
            limpio[clave] = valor
    return limpio
