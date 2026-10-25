"""Lectura y validación de formularios y parámetros de búsqueda.

FastAPI necesita `python-multipart` para `Form()`. Como los formularios de PrecIA son
`application/x-www-form-urlencoded`, se leen con `urllib.parse` de la biblioteca estándar.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from urllib.parse import parse_qs, urlencode

from precia.nucleo.modelos import CLAVES_CAMPOS, ConsultaBusqueda, Modo
from precia.perfiles.entrada import MAX_ENTRADA

MAX_TEXTO = 200
MAX_FILTRO = 300
MAX_TIENDAS = 50
MAX_CAMPOS_FORMULARIO = 100
MAX_DOMINIO = 253
MAX_NOMBRE_TIENDA = 100
IMPORTE_MAXIMO = Decimal(1_000_000)
# [0-9] y no \d: \d acepta dígitos de otros alfabetos («٣٠»).
_IMPORTE = re.compile(r"[0-9]{1,6}([.,][0-9]{1,2})?")
_IMPORTE_MILES = re.compile(r"[0-9]{1,3}(\.[0-9]{3})+(,[0-9]{1,2})?")
_VERSION = re.compile(r"[0-9]{1,9}")
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


def parametros_de_consulta(consulta: ConsultaBusqueda) -> str:
    """La consulta como parámetros de URL: la inversa de `consulta_desde_parametros` (C1).

    Va en la URL de una búsqueda en segundo plano (`/busquedas/{id}?...`) y en «Actualizar
    precios», para que el formulario salga relleno aunque la tarea ya no esté.
    """
    pares: list[tuple[str, str]] = [("texto", consulta.texto), ("modo", consulta.modo.value)]
    pares.extend(("campos", c) for c in consulta.campos)
    pares.extend(("tiendas", t) for t in consulta.tiendas)
    if consulta.descubrir:
        pares.append(("descubrir", "on"))
    if consulta.filtro:
        pares.append(("filtro", consulta.filtro))
    return urlencode(pares)


def datos_de_alta(formulario: Parametros) -> tuple[str, str]:
    """Dominio y nombre del formulario «Añadir tienda». El dominio lo valida después la fuente."""
    dominio = primero(formulario, "dominio").strip()
    if not dominio:
        raise ErrorFormulario("Escribe el dominio de la tienda, por ejemplo «tienda.es».")
    if len(dominio) > MAX_DOMINIO:
        raise ErrorFormulario(f"El dominio no puede pasar de {MAX_DOMINIO} caracteres.")
    nombre = primero(formulario, "nombre").strip()
    if len(nombre) > MAX_NOMBRE_TIENDA or not nombre.isprintable():
        raise ErrorFormulario(
            f"El nombre de la tienda puede tener hasta {MAX_NOMBRE_TIENDA} caracteres visibles."
        )
    return dominio, nombre


def leer_entrada(formulario: Parametros) -> str:
    """URL de entrada del formulario, sin espacios alrededor; vacía si no se escribe ninguna.

    Aquí solo se mira el tamaño: las reglas (mismo host, marcadores...) las aplica la fuente con
    `perfiles.entrada.normalizar_entrada`, que conoce el dominio de la tienda.
    """
    entrada = primero(formulario, "entrada").strip()
    if len(entrada) > MAX_ENTRADA:
        raise ErrorFormulario(f"La URL de entrada no puede pasar de {MAX_ENTRADA} caracteres.")
    return entrada


def leer_version(texto: str) -> int:
    """Número de versión de un perfil («1», «12»...). Solo dígitos ASCII y un tamaño razonable."""
    limpio = texto.strip()
    if not _VERSION.fullmatch(limpio) or int(limpio) < 1:
        raise ErrorFormulario("La versión del perfil no es válida.")
    return int(limpio)
