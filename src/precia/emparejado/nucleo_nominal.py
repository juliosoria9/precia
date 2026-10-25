"""Regla sin IA del núcleo nominal: «cerradura de la puerta…» no es «puerta…».

En español el sintagma nominal lleva el núcleo delante: «puerta delantera derecha» es una
puerta y «cerradura de la puerta delantera derecha» es una cerradura, aunque las dos nombren
las mismas palabras. La regla busca el núcleo de la consulta (su primera palabra de contenido)
en el título y, si va detrás de otro sustantivo y de una preposición de pertenencia («de»,
«para», «compatible con»), la oferta parece una pieza o un accesorio de lo buscado.

«Kit/juego/set/pack de X» cuenta como X: en recambios es el producto normal («Juego de pastillas
de freno», «Kit de embrague»), no una pieza de X. Solo «kit para X» o un kit de otra cosa de X
(«Kit de reparación de la puerta») se objetan.

Es conservadora y barata: solo mira el principio del título y no resuelve títulos en inglés
(«door lock»), sinónimos ni variantes de lado. Por eso solo descarta en modo clásico o si falla
la IA, y en los demás casos solo sirve para ordenar (nota 05). Todo es lineal y acotado: los
títulos de las tiendas son datos no confiables.
"""

import re

from precia.emparejado.referencias import referencias_de_consulta
from precia.emparejado.texto import normalizar_titulo

MAX_TEXTO = 300
"""Del título y de la consulta solo se mira el principio."""
MAX_DISTANCIA = 8
"""Palabras del título, contadas desde la primera de contenido, en las que se busca el núcleo."""
MIN_LETRAS = 3

_PALABRA = re.compile(r"\w+")
_VACIAS = frozenset(
    [
        "a",
        "al",
        "con",
        "de",
        "del",
        "el",
        "en",
        "la",
        "las",
        "lo",
        "los",
        "para",
        "por",
        "sin",
        "su",
        "sus",
        "un",
        "una",
        "unos",
        "unas",
        "y",
        "o",
        "e",
        "the",
        "of",
        "for",
        "with",
        "and",
    ]
)
_ARTICULOS = frozenset(["el", "la", "los", "las", "lo", "un", "una", "unos", "unas", "the"])
_PERTENENCIA = frozenset({"de", "del", "para", "for", "of"})
_ENVOLTORIOS = frozenset(
    [
        "recambio",
        "recambios",
        "repuesto",
        "repuestos",
        "pieza",
        "piezas",
        "nuevo",
        "nueva",
        "nuevos",
        "nuevas",
        "original",
        "originales",
        "oferta",
        "ofertas",
    ]
)
_KITS = frozenset({"kit", "kits", "juego", "juegos", "set", "sets", "pack", "packs", "conjunto"})


def nucleo(consulta: str) -> str | None:
    """Primera palabra de contenido de la consulta, normalizada y en singular.

    Sin cifras, sin palabras vacías y sin las referencias OEM; `None` si no queda ninguna.
    """
    consulta = consulta[:MAX_TEXTO]
    referencias = {r.lower() for r in referencias_de_consulta(consulta)}
    for palabra in normalizar_titulo(consulta).split():
        if (
            len(palabra) >= MIN_LETRAS
            and palabra.isalpha()
            and palabra not in _VACIAS
            and palabra not in referencias
        ):
            return _singular(palabra)
    return None


def pieza_de(titulo: str, consulta: str) -> str | None:
    """Motivo si el título parece una pieza o un kit de lo buscado; `None` si no objeta."""
    buscado = nucleo(consulta)
    if buscado is None:
        return None
    palabras = _contenido_del_titulo(titulo, buscado)
    if not palabras or _igual(palabras[0], buscado):
        return None
    cabeza = palabras[0]
    de_la_consulta = normalizar_titulo(consulta[:MAX_TEXTO]).split()
    if any(_igual(cabeza, p) for p in de_la_consulta):
        return None
    posicion = _detras_de_pertenencia(palabras, buscado)
    if posicion is None:
        return None
    if cabeza in _KITS:
        return f"Parece un kit que incluye «{buscado}» (empieza por «{cabeza}»)"
    return f"Parece una pieza o accesorio de «{buscado}» (empieza por «{cabeza}»)"


def encabeza(titulo: str, consulta: str) -> bool:
    """El núcleo de la consulta es la primera palabra de contenido del título."""
    buscado = nucleo(consulta)
    if buscado is None:
        return False
    palabras = _contenido_del_titulo(titulo, buscado)
    return bool(palabras) and _igual(palabras[0], buscado)


def _contenido_del_titulo(titulo: str, buscado: str) -> list[str]:
    """Las palabras del título desde la primera de contenido, normalizadas.

    Al principio se saltan las cifras y referencias, las palabras en mayúsculas (suelen ser la
    marca o el modelo, si el resto del título no va en mayúsculas), las palabras vacías y los
    envoltorios que no dicen qué es la pieza («recambio», «original», «nuevo»...). Después, un
    «kit de» (o «juego de las»...) delante, salvo que lo buscado sea el kit («kit embrague»).
    """
    originales = _PALABRA.findall(titulo[:MAX_TEXTO])
    todo_mayusculas = all(not p.isalpha() or p.isupper() for p in originales)
    inicio = 0
    for original in originales:
        normal = normalizar_titulo(original)
        marca = not todo_mayusculas and original.isupper() and len(original) >= 2
        if (
            any(c.isdigit() for c in original)
            or marca
            or not normal
            or normal in _VACIAS
            or normal in _ENVOLTORIOS
        ):
            inicio += 1
            continue
        break
    resultado: list[str] = []
    for original in originales[inicio : inicio + MAX_DISTANCIA + 2]:
        resultado.extend(normalizar_titulo(original).split())
    return _sin_kit(resultado, buscado)


def _sin_kit(palabras: list[str], buscado: str) -> list[str]:
    """Sin el «kit de» (con o sin artículo) del principio: «kit de embrague» es un embrague."""
    if len(palabras) < 3 or palabras[0] not in _KITS or palabras[1] not in ("de", "del"):
        return palabras
    if _igual(palabras[0], buscado):
        return palabras
    resto = palabras[2:]
    if resto[0] in _ARTICULOS and len(resto) > 1:
        resto = resto[1:]
    return resto


def _detras_de_pertenencia(palabras: list[str], buscado: str) -> int | None:
    """Posición del núcleo si va detrás de «de», «para» o «compatible con» (con o sin artículo)."""
    for i in range(1, min(len(palabras), MAX_DISTANCIA + 1)):
        if not _igual(palabras[i], buscado):
            continue
        j = i - 1
        if palabras[j] in _ARTICULOS and j > 0:
            j -= 1
        if palabras[j] in _PERTENENCIA and j > 0:
            return i
        if palabras[j] == "con" and j > 0 and palabras[j - 1] == "compatible":
            return i
        return None
    return None


def _igual(palabra: str, buscado: str) -> bool:
    """Misma palabra salvo el plural, mirado por los dos lados («cables» y «cable»)."""
    return not _formas(palabra).isdisjoint(_formas(buscado))


def _formas(palabra: str) -> set[str]:
    formas = {palabra}
    if palabra.endswith("ces"):
        formas.add(palabra[:-3] + "z")
    if palabra.endswith("es"):
        formas.add(palabra[:-2])
    if palabra.endswith("s"):
        formas.add(palabra[:-1])
    return {f for f in formas if len(f) >= MIN_LETRAS} or {palabra}


def _singular(palabra: str) -> str:
    """Sin la marca de plural (como `nucleo.orquestador._singular`): «faros» → «faro»."""
    if palabra.endswith("ces"):
        raiz = palabra[:-3] + "z"
    elif palabra.endswith("es") and palabra[-3:-2] not in "aeiou":
        raiz = palabra[:-2]
    elif palabra.endswith("s"):
        raiz = palabra[:-1]
    else:
        return palabra
    return raiz if len(raiz) >= MIN_LETRAS else palabra
