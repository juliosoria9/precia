"""Texto visible de una página y resumen de su estructura para el configurador de IA.

El resumen es lo único que la IA ve de una tienda al generar selectores: HTML simplificado con
la etiqueta, los atributos que sirven para escribir un selector y el texto recortado. Se quitan
scripts, estilos, comentarios, elementos ocultos y manejadores de eventos (por ahorro de tokens
y porque son el sitio típico donde esconder instrucciones para la IA) y todo se escapa.
"""

import re
from dataclasses import dataclass
from html import escape

from precia.extraccion.dom import VACIOS, Nodo, analizar, invisible

MAX_TEXTO_TROZO = 200
MAX_VALOR_ATRIBUTO = 80
_ATRIBUTOS_UTILES = frozenset(
    {"id", "class", "href", "src", "alt", "itemprop", "itemtype", "content", "action", "method"}
    | {"name", "type"}
)
_OMITIDOS = frozenset({"iframe", "object", "embed", "link", "meta", "base", "frame", "frameset"})
_TRANSPARENTES = frozenset({"html", "head", "body"})
_ESPACIOS = re.compile(r"\s+")


def texto_visible(html: str) -> str:
    """Texto que ve una persona: sin `script`, `style`, `noscript`, `template`, `svg`,
    comentarios ni elementos ocultos, con los espacios normalizados."""
    return analizar(html).texto()


def resumen_para_ia(html: str, max_caracteres: int = 30_000) -> str:
    """HTML simplificado de `html`, de `max_caracteres` como mucho (se corta por el final)."""
    raiz = analizar(html)
    partes: list[str] = []
    longitud = 0
    pila: list[Nodo | str | _Cierre] = list(reversed(raiz.contenido))
    while pila:
        actual = pila.pop()
        if isinstance(actual, _Cierre):
            parte = f"</{actual.etiqueta}>"
        elif isinstance(actual, str):
            parte = _texto(actual)
        elif _omitir(actual):
            continue
        elif actual.etiqueta in _TRANSPARENTES:
            pila.extend(reversed(actual.contenido))
            continue
        else:
            parte = _apertura(actual)
            if actual.etiqueta not in VACIOS:
                pila.append(_Cierre(actual.etiqueta))
                pila.extend(reversed(actual.contenido))
        if not parte:
            continue
        if longitud + len(parte) > max_caracteres:
            break
        partes.append(parte)
        longitud += len(parte)
    return "".join(partes)


@dataclass(frozen=True, slots=True)
class _Cierre:
    etiqueta: str


def _omitir(nodo: Nodo) -> bool:
    return invisible(nodo) or nodo.etiqueta in _OMITIDOS


def _apertura(nodo: Nodo) -> str:
    atributos = "".join(
        f' {nombre}="{escape(_recortar(valor, MAX_VALOR_ATRIBUTO))}"'
        for nombre, valor in nodo.atributos.items()
        if _atributo_util(nombre)
    )
    return f"<{nodo.etiqueta}{atributos}>"


def _atributo_util(nombre: str) -> bool:
    return nombre in _ATRIBUTOS_UTILES or (
        nombre.startswith("data-") and re.fullmatch(r"data-[a-z0-9_.-]{1,40}", nombre) is not None
    )


def _texto(trozo: str) -> str:
    limpio = _ESPACIOS.sub(" ", trozo)
    if not limpio.strip():
        return ""
    # Se conserva un espacio en los bordes: «Precio: <b>38</b>» no debe quedar «Precio:<b>».
    antes = " " if limpio.startswith(" ") else ""
    despues = " " if limpio.endswith(" ") else ""
    return antes + escape(_recortar(limpio, MAX_TEXTO_TROZO), quote=False) + despues


def _recortar(texto: str, maximo: int) -> str:
    texto = _ESPACIOS.sub(" ", texto).strip()
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"
