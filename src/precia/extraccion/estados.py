"""Estado del producto con un vocabulario común: `Nuevo`, `Usado` o `Reacondicionado`.

Es el mismo que da `jsonld` a partir de `schema.org/itemCondition`. Las tiendas, sus API y la
IA escriben el estado con sus palabras («Material de segunda mano», «Pieza usada», «Ocasión»):
aquí se pasa al vocabulario común para poder compararlo y filtrar por él. Si el texto no dice
un estado claro (o dice dos), no se decide.
"""

import re
import unicodedata

USADO, NUEVO, REACONDICIONADO = "Usado", "Nuevo", "Reacondicionado"
MAX_VALOR_ESTADO = 100
"""Más largo ya no es el valor de un estado sino una descripción."""

ESTADOS: dict[str, str] = {
    "usado": USADO,
    "usada": USADO,
    "usados": USADO,
    "usadas": USADO,
    "segunda mano": USADO,
    "de segunda mano": USADO,
    "ocasion": USADO,
    "de ocasion": USADO,
    "como nuevo": USADO,
    "como nueva": USADO,
    "seminuevo": USADO,
    "seminueva": USADO,
    "used": USADO,
    "nuevo": NUEVO,
    "nueva": NUEVO,
    "nuevos": NUEVO,
    "nuevas": NUEVO,
    "new": NUEVO,
    "reacondicionado": REACONDICIONADO,
    "reacondicionada": REACONDICIONADO,
    "reconstruido": REACONDICIONADO,
    "reconstruida": REACONDICIONADO,
    "remanufacturado": REACONDICIONADO,
    "remanufacturada": REACONDICIONADO,
    "refurbished": REACONDICIONADO,
}
"""Frases normalizadas (minúsculas, sin acentos) y su estado."""
FRASE_ESTADO = re.compile(r"\b(?:" + "|".join(sorted(ESTADOS, key=len, reverse=True)) + r")\b")
"""Frases literales, de la más larga a la más corta («como nuevo» antes que «nuevo»): sin
cuantificadores anidados, el coste es lineal."""
_NO_ALFANUMERICO = re.compile(r"[^a-z0-9]+")


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y solo letras y cifras separadas por un espacio."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c)
    )
    return _NO_ALFANUMERICO.sub(" ", sin_acentos).strip()


def estado_canonico(texto: str) -> str | None:
    """`Nuevo`, `Usado` o `Reacondicionado` si `texto` lo dice sin contradecirse, o `None`."""
    if not texto or len(texto) > MAX_VALOR_ESTADO:
        return None
    encontrados = {ESTADOS[m.group()] for m in FRASE_ESTADO.finditer(normalizar(texto))}
    return encontrados.pop() if len(encontrados) == 1 else None
