"""Referencias OEM: normalización y detección de candidatas en títulos y campos.

Una referencia candidata es alfanumérica, tiene al menos 5 caracteres y al menos un dígito
una vez quitados espacios y separadores. Las tiendas la escriben de muchas formas
(«1K0 698 151», «1K0-698-151», «REF.1K0698151»), así que se normaliza antes de comparar.
"""

import re

MAX_TEXTO = 10_000
"""Solo se analiza este prefijo del texto: los datos vienen de webs no confiables."""

LONGITUD_MINIMA = 5

_SEPARADORES = re.compile(r"[\s\-./_]+")
_TOKEN = re.compile(r"[0-9A-Za-z]+(?:[-./_][0-9A-Za-z]+)*")
# Los precios contienen números largos («12.345,67 €») que no son referencias. El número solo
# puede empezar al principio de una tira de cifras: sin el lookbehind, un título hostil hecho
# de miles de cifras volvía cuadrática la búsqueda (segundos por llamada).
_PRECIO = re.compile(
    r"(?:€|\$|\bEUR\b|\bUSD\b)\s*\d[\d.,]*"
    r"|(?<![\d.,])\d[\d.,]*\s*(?:€|\$|\bEUR\b|\bUSD\b|\beuros?\b)",
    re.IGNORECASE,
)
# Cilindrada pegada a la familia del motor («1.9TDI», «1,6HDI», «2.0TFSI») y normas de
# emisiones («Euro5»): salen en piezas muy distintas del mismo coche y no identifican ninguna.
_MOTOR_O_NORMA = re.compile(r"\b\d[.,]\d[A-Za-z]{2,6}\b|\beuro\s?\d\b", re.IGNORECASE)
_UNIDADES = frozenset(
    {"KM", "KMS", "MM", "CM", "CV", "KW", "HP", "W", "V", "AH", "ML", "L", "KG", "G", "NM", "CC"}
)
_CON_UNIDAD = re.compile(r"^\d+(?:" + "|".join(sorted(_UNIDADES, key=len, reverse=True)) + r")$")
_RANGO_AÑOS = re.compile(r"^(?:19|20)\d{2}(?:19|20)\d{2}$")
_AÑO = re.compile(r"^(?:19|20)\d{2}$")
_MILES = re.compile(r"^\d{1,3}(?:\.\d{3})+$")


def normalizar_referencia(texto: str) -> str:
    """Quita espacios y separadores (`-`, `.`, `/`, `_`) y pasa a mayúsculas."""
    return _SEPARADORES.sub("", texto).upper()


def es_referencia(texto: str) -> bool:
    """Indica si un texto ya normalizado tiene forma de referencia OEM."""
    return (
        len(texto) >= LONGITUD_MINIMA
        and texto.isascii()
        and texto.isalnum()
        and any(c.isdigit() for c in texto)
    )


def _es_numero_o_medida(normalizado: str, crudo: str, siguiente: str | None) -> bool:
    if _CON_UNIDAD.match(normalizado) or _RANGO_AÑOS.match(normalizado) or _MILES.match(crudo):
        return True
    return normalizado.isdigit() and siguiente is not None and siguiente.upper() in _UNIDADES


def _es_trozo(crudo: str) -> bool:
    """Trozo corto de una referencia escrita por grupos («1K0 698 151», «0 986 494 104»)."""
    return (
        crudo.isalnum()
        and len(crudo) <= 4
        and any(c.isdigit() for c in crudo)
        and not _AÑO.match(crudo)
    )


def referencias_en(texto: str) -> set[str]:
    """Devuelve las referencias OEM candidatas (normalizadas) que aparecen en el texto."""
    texto = _MOTOR_O_NORMA.sub(" ", _PRECIO.sub(" ", texto[:MAX_TEXTO]))
    tokens = list(_TOKEN.finditer(texto))
    encontradas: set[str] = set()

    for i, token in enumerate(tokens):
        crudo = token.group()
        siguiente = tokens[i + 1].group() if i + 1 < len(tokens) else None
        normalizado = normalizar_referencia(crudo)
        if _es_numero_o_medida(normalizado, crudo, siguiente):
            continue
        if es_referencia(normalizado):
            encontradas.add(normalizado)
        # «REF.1K0698151»: la referencia va pegada a un prefijo con un separador.
        for parte in _SEPARADORES.split(crudo):
            parte = parte.upper()
            if parte != normalizado and es_referencia(parte):
                encontradas.add(parte)

    encontradas.update(_referencias_por_grupos(texto, tokens))
    return encontradas


def _referencias_por_grupos(texto: str, tokens: list[re.Match[str]]) -> set[str]:
    # Un primer trozo con algún dígito seguido de al menos dos grupos solo de dígitos,
    # separados únicamente por espacios. Exigirlo así evita unir «Audi A4 B8» o «Golf 6 2010».
    encontradas: set[str] = set()
    i = 0
    while i < len(tokens):
        if not _es_trozo(tokens[i].group()):
            i += 1
            continue
        fin = i + 1
        while (
            fin < len(tokens)
            and _es_trozo(tokens[fin].group())
            and tokens[fin].group().isdigit()
            and texto[tokens[fin - 1].end() : tokens[fin].start()].isspace()
        ):
            fin += 1
        if fin - i >= 3:
            unida = "".join(t.group() for t in tokens[i:fin]).upper()
            siguiente = tokens[fin].group() if fin < len(tokens) else None
            if es_referencia(unida) and not (siguiente and siguiente.upper() in _UNIDADES):
                encontradas.add(unida)
            i = fin
        else:
            i += 1
    return encontradas
