"""Lectura y validación de formularios y parámetros de búsqueda.

FastAPI necesita `python-multipart` para `Form()`. Como los formularios de PrecIA son
`application/x-www-form-urlencoded`, se leen con `urllib.parse` de la biblioteca estándar.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from urllib.parse import parse_qs

from precia.nucleo.modelos import CLAVES_CAMPOS, ConsultaBusqueda, Modo

MAX_TEXTO = 200
MAX_FILTRO = 300
MAX_TIENDAS = 50
MAX_CAMPOS_FORMULARIO = 100
IMPORTE_MAXIMO = Decimal(1_000_000)
# [0-9] y no \d: \d acepta dígitos de otros alfabetos («٣٠»).
_IMPORTE = re.compile(r"[0-9]{1,6}([.,][0-9]{1,2})?")
_IMPORTE_MILES = re.compile(r"[0-9]{1,3}(\.[0-9]{3})+(,[0-9]{1,2})?")
_VERDADERO = frozenset({"on", "true", "1", "si", "sí"})

Parametros = Mapping[str, Sequence[str]]


class ErrorFormulario(ValueError):
    """Dato de formulario no válido; el mensaje se muestra al usuario."""


def leer_formulario(cuerpo: bytes) -> dict[str, list[str]]:
    try:
        return parse_qs(
            cuerpo.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=False,
            encoding="utf-8",
            errors="strict",
            max_num_fields=MAX_CAMPOS_FORMULARIO,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise ErrorFormulario("El formulario no tiene un formato válido.") from error


def primero(parametros: Parametros, clave: str, defecto: str = "") -> str:
    valores = parametros.get(clave)
    return valores[0] if valores else defecto


def leer_importe(texto: str) -> Decimal:
    """Convierte «30», «30,50», «30.5», «1.234» o «1.234,56» en un importe positivo.

    El punto se toma como separador de miles solo si va seguido de grupos de tres cifras
    (formato español); si no, es el separador decimal. Solo se admiten dígitos ASCII.
    """
    limpio = texto.strip().replace(" ", "")
    if _IMPORTE_MILES.fullmatch(limpio):
        limpio = limpio.replace(".", "").replace(",", ".")
    elif _IMPORTE.fullmatch(limpio):
        limpio = limpio.replace(",", ".")
    else:
        raise ErrorFormulario("Escribe un importe en euros, por ejemplo 30,50.")
    importe = Decimal(limpio)
    if not 0 < importe < IMPORTE_MAXIMO:
        raise ErrorFormulario("El importe tiene que ser mayor que cero y menor de un millón.")
    return importe


def consulta_desde_parametros(parametros: Parametros) -> ConsultaBusqueda:
    texto = primero(parametros, "texto").strip()
    if not texto:
        raise ErrorFormulario("Escribe un producto o una referencia OEM.")
    if len(texto) > MAX_TEXTO:
        raise ErrorFormulario(f"La búsqueda no puede pasar de {MAX_TEXTO} caracteres.")

    filtro = primero(parametros, "filtro").strip()
    if len(filtro) > MAX_FILTRO:
        raise ErrorFormulario(f"El filtro no puede pasar de {MAX_FILTRO} caracteres.")

    tiendas = tuple(dict.fromkeys(t.strip() for t in parametros.get("tiendas", ()) if t.strip()))
    campos = tuple(dict.fromkeys(c for c in parametros.get("campos", ()) if c in CLAVES_CAMPOS))

    try:
        modo = Modo(primero(parametros, "modo", Modo.AUTO))
    except ValueError:
        modo = Modo.AUTO

    consulta = ConsultaBusqueda(
        texto=texto,
        tiendas=tiendas[:MAX_TIENDAS],
        modo=modo,
        descubrir=primero(parametros, "descubrir").lower() in _VERDADERO,
        filtro=filtro,
    )
    return replace(consulta, campos=campos) if campos else consulta
