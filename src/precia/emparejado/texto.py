"""Agrupado de ofertas por similitud de títulos con `difflib.SequenceMatcher` (modo clásico).

Es la vía sin IA: barata y explicable, pero solo mira las letras. «Pastillas delanteras» y
«Pastillas traseras» se parecen mucho, y por eso el modo IA clasifica además cada oferta.
"""

import re
import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher

UMBRAL_POR_DEFECTO = 0.8
MAX_COMPARACION = 200
"""Caracteres que se comparan: `SequenceMatcher` es cuadrático y los títulos no son confiables."""
MAX_NOMBRE = 120

_NO_ALFANUMERICO = re.compile(r"[^0-9a-z]+")
_ESPACIOS = re.compile(r"\s+")
_PALABRAS_VACIAS = frozenset({"de", "del", "la", "el", "los", "las", "para", "con", "y", "en"})
_BORDES = " |-·,;:/" + chr(0x2013) + chr(0x2014)  # Rayas: hay tiendas que las usan de separador.


def normalizar_titulo(texto: str) -> str:
    """Minúsculas, sin acentos ni signos y con los espacios colapsados."""
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _NO_ALFANUMERICO.sub(" ", sin_acentos).strip()


def limpiar_titulo(texto: str, maximo: int = MAX_NOMBRE) -> str:
    """Texto presentable: sin caracteres de control, espacios colapsados y longitud acotada."""
    texto = _ESPACIOS.sub(" ", texto)
    texto = "".join(c for c in texto if unicodedata.category(c)[0] != "C").strip(_BORDES)
    if len(texto) > maximo:
        texto = texto[: maximo - 1].rstrip() + "…"
    return texto


def _palabras(texto: str) -> list[str]:
    return [
        p for p in normalizar_titulo(texto)[:MAX_COMPARACION].split() if p not in _PALABRAS_VACIAS
    ]


def _formas(texto: str) -> tuple[str, str] | None:
    """Las palabras en su orden y ordenadas, o `None` si no queda ninguna."""
    palabras = _palabras(texto)
    if not palabras:
        return None
    return " ".join(palabras), " ".join(sorted(palabras))


def _ratio(a: str, b: str, umbral: float) -> float:
    # `real_quick_ratio` y `quick_ratio` son cotas superiores baratas de `ratio`: si ya no
    # llegan al umbral, no hace falta la comparación cuadrática.
    comparador = SequenceMatcher(None, a, b)
    if comparador.real_quick_ratio() < umbral or comparador.quick_ratio() < umbral:
        return 0.0
    return comparador.ratio()


def _parecido(a: tuple[str, str] | None, b: tuple[str, str] | None, umbral: float) -> float:
    if a is None or b is None:
        return 0.0
    return max(_ratio(a[0], b[0], umbral), _ratio(a[1], b[1], umbral))


def similitud(a: str, b: str) -> float:
    """Parecido entre dos títulos de 0 a 1.

    Se toma el mejor de comparar las palabras en su orden y ordenadas, porque cada tienda
    coloca la marca y el modelo en un sitio distinto del título.
    """
    return _parecido(_formas(a), _formas(b), 0.0)


def agrupar_por_texto(
    titulos: Sequence[str], *, umbral: float = UMBRAL_POR_DEFECTO
) -> list[list[int]]:
    """Agrupa los índices de los títulos parecidos, en el orden en que aparecen.

    Un título entra en el grupo con el que más se parece solo si alcanza el umbral con
    **todos** sus miembros (enlace completo): así no se encadenan productos distintos a
    través de un título intermedio.
    """
    if not 0.0 < umbral <= 1.0:
        raise ValueError("El umbral de similitud debe estar entre 0 (excluido) y 1")
    formas = [_formas(t) for t in titulos]
    grupos: list[list[int]] = []
    for i, forma in enumerate(formas):
        mejor: list[int] | None = None
        mejor_media = 0.0
        for grupo in grupos:
            parecidos = []
            for j in grupo:
                parecido = _parecido(forma, formas[j], umbral)
                if parecido < umbral:
                    break
                parecidos.append(parecido)
            else:
                media = sum(parecidos) / len(parecidos)
                if media > mejor_media:
                    mejor, mejor_media = grupo, media
        if mejor is None:
            grupos.append([i])
        else:
            mejor.append(i)
    return grupos
