"""Árbol DOM propio sobre `html.parser`, tolerante al HTML real.

Las tiendas sirven HTML roto: párrafos y celdas sin cerrar, cierres desordenados, atributos sin
comillas, `<div/>`... Aquí se reproduce lo esencial de lo que hace un navegador (cierres
implícitos y elementos vacíos), sin aspirar al algoritmo completo de HTML5.

El contenido viene de webs no confiables: todos los recorridos son iterativos (un anidamiento
de 100 000 `div` no desborda la pila) y hay topes de tamaño, de profundidad y de elementos.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

MAX_CARACTERES = 8_000_000
"""Por encima de esto se analiza solo el principio (el navegador ya corta en 5 MiB)."""
MAX_PROFUNDIDAD = 512
"""Los elementos más profundos se cuelgan del último admitido (Chromium hace algo parecido)."""
MAX_ELEMENTOS = 250_000
"""Una página real de 2 MB tiene unas decenas de miles; el resto se descarta."""


def _etiquetas(texto: str) -> frozenset[str]:
    return frozenset(texto.split())


VACIOS = _etiquetas("area base br col embed hr img input keygen link meta param source track wbr")
INVISIBLES = frozenset({"script", "style", "noscript", "template", "svg"})
"""Su contenido no forma parte del texto visible."""
_EXTRANJEROS = frozenset({"svg", "math"})
_BLOQUES = _etiquetas(
    """
    address article aside blockquote body br dd details dialog div dl dt fieldset figcaption
    figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr html li main nav ol option p pre
    section summary table tbody td tfoot th thead title tr ul
    """
)
_CIERRAN_P = _etiquetas(
    """
    address article aside blockquote dd details dialog div dl dt fieldset figcaption figure
    footer form h1 h2 h3 h4 h5 h6 header hgroup hr li main menu nav ol p pre section table
    ul
    """
)
_Regla = tuple[frozenset[str], frozenset[str]]
_TABLA = frozenset({"table", "tbody", "thead", "tfoot"})
# Etiqueta que se abre → reglas (elemento abierto que cierra, fronteras que no se cruzan).
# Al cerrar un elemento se cierra también todo lo abierto dentro (un `tr` cierra sus `td`).
_IMPLICITOS: dict[str, tuple[_Regla, ...]] = {
    "li": ((frozenset({"li"}), frozenset({"ul", "ol", "menu"})),),
    "dt": ((frozenset({"dt", "dd"}), frozenset({"dl"})),),
    "dd": ((frozenset({"dt", "dd"}), frozenset({"dl"})),),
    "tr": ((frozenset({"tr"}), _TABLA),),
    "td": ((frozenset({"td", "th"}), frozenset({"tr"}) | _TABLA),),
    "th": ((frozenset({"td", "th"}), frozenset({"tr"}) | _TABLA),),
    "tbody": ((frozenset({"tbody", "thead", "tfoot"}), frozenset({"table"})),),
    "thead": ((frozenset({"tbody", "thead", "tfoot"}), frozenset({"table"})),),
    "tfoot": ((frozenset({"tbody", "thead", "tfoot"}), frozenset({"table"})),),
    "option": ((frozenset({"option"}), frozenset({"select", "datalist", "optgroup"})),),
    "optgroup": (
        (frozenset({"option"}), frozenset({"select", "datalist", "optgroup"})),
        (frozenset({"optgroup"}), frozenset({"select"})),
    ),
    "a": ((frozenset({"a"}), frozenset()),),
}
_FRONTERA_P = frozenset({"button", "table", "td", "th", "li", "dd", "dt"})
_ESPACIOS = re.compile(r"\s+")
_OCULTO_EN_ESTILO = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


@dataclass(eq=False, slots=True)
class Nodo:
    """Elemento del árbol. La raíz es un nodo `#documento` sin atributos ni padre."""

    etiqueta: str
    atributos: dict[str, str] = field(default_factory=dict)
    hijos: list["Nodo"] = field(default_factory=list)
    """Solo los elementos hijos, en orden."""
    padre: "Nodo | None" = None
    contenido: list["Nodo | str"] = field(default_factory=list)
    """Elementos hijos y trozos de texto intercalados, en el orden del documento."""

    def __repr__(self) -> str:
        return f"Nodo({self.etiqueta!r}, {self.atributos!r}, hijos={len(self.hijos)})"

    def atributo(self, nombre: str) -> str | None:
        return self.atributos.get(nombre.lower())

    @property
    def clases(self) -> tuple[str, ...]:
        return tuple(self.atributos.get("class", "").split())

    def descendientes(self) -> Iterator["Nodo"]:
        """Elementos descendientes en orden del documento (sin incluir este nodo)."""
        pila = list(reversed(self.hijos))
        while pila:
            nodo = pila.pop()
            yield nodo
            pila.extend(reversed(nodo.hijos))

    def texto(self) -> str:
        """Texto visible normalizado: sin scripts, estilos ni elementos ocultos.

        Los bloques (`div`, `p`, `li`, `br`...) separan con un espacio; los elementos en línea
        no, para que `<span>38</span><span>,90 €</span>` siga siendo `38,90 €`.
        """
        trozos: list[str] = []
        pila: list[Nodo | str] = list(reversed(self.contenido))
        while pila:
            actual = pila.pop()
            if isinstance(actual, str):
                trozos.append(actual)
                continue
            if invisible(actual):
                continue
            if actual.etiqueta in _BLOQUES:
                trozos.append(" ")
                pila.append(" ")
            pila.extend(reversed(actual.contenido))
        return _ESPACIOS.sub(" ", "".join(trozos)).strip()

    def texto_crudo(self) -> str:
        """Todo el texto sin filtrar ni normalizar (para leer el JSON de un `<script>`)."""
        trozos: list[str] = []
        pila: list[Nodo | str] = list(reversed(self.contenido))
        while pila:
            actual = pila.pop()
            if isinstance(actual, str):
                trozos.append(actual)
            else:
                pila.extend(reversed(actual.contenido))
        return "".join(trozos)


def invisible(nodo: Nodo) -> bool:
    """El elemento no se ve: script, estilo, plantilla, SVG, `hidden` o CSS en línea que oculta."""
    if nodo.etiqueta in INVISIBLES:
        return True
    if "hidden" in nodo.atributos:
        return True
    estilo = nodo.atributos.get("style")
    # Texto oculto con CSS: lo que el usuario no ve no debe servir para anclar un valor.
    return estilo is not None and _OCULTO_EN_ESTILO.search(estilo) is not None


def analizar(html: str) -> Nodo:
    """Construye el árbol de `html`, por roto que esté. Nunca lanza por HTML mal formado."""
    constructor = _Constructor()
    constructor.feed(html[:MAX_CARACTERES])
    constructor.close()
    return constructor.raiz


class _Constructor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raiz = Nodo("#documento")
        self._pila: list[Nodo] = [self.raiz]
        self._abiertos: dict[str, int] = {}
        self._elementos = 0
        self._extranjero = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._abrir(tag, attrs, autocerrado=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._abrir(tag, attrs, autocerrado=True)

    def handle_endtag(self, tag: str) -> None:
        if not self._abiertos.get(tag):
            return
        while len(self._pila) > 1:
            nodo = self._sacar()
            if nodo.etiqueta == tag:
                return

    def handle_data(self, data: str) -> None:
        self._pila[-1].contenido.append(data)

    def _abrir(self, tag: str, attrs: list[tuple[str, str | None]], *, autocerrado: bool) -> None:
        if self._elementos >= MAX_ELEMENTOS:
            return
        self._elementos += 1
        if not self._extranjero:
            self._cerrar_implicitos(tag)
        atributos: dict[str, str] = {}
        for nombre, valor in attrs:
            atributos.setdefault(nombre, valor or "")
        padre = self._pila[-1]
        nodo = Nodo(tag, atributos, padre=padre)
        padre.hijos.append(nodo)
        padre.contenido.append(nodo)
        # En HTML la barra de `<div/>` no cierra nada; dentro de SVG o MathML, sí.
        cerrado = tag in VACIOS or (autocerrado and self._extranjero > 0)
        if not cerrado and len(self._pila) < MAX_PROFUNDIDAD:
            self._pila.append(nodo)
            self._abiertos[tag] = self._abiertos.get(tag, 0) + 1
            if tag in _EXTRANJEROS:
                self._extranjero += 1

    def _cerrar_implicitos(self, tag: str) -> None:
        if tag in _CIERRAN_P and self._abiertos.get("p"):
            self._cerrar_hasta({"p"}, _FRONTERA_P)
        for cierra, frontera in _IMPLICITOS.get(tag, ()):
            self._cerrar_hasta(cierra, frontera)

    def _cerrar_hasta(self, cierra: frozenset[str] | set[str], frontera: frozenset[str]) -> None:
        """Cierra el elemento abierto más cercano de `cierra` si no hay una frontera antes."""
        for i in range(len(self._pila) - 1, 0, -1):
            etiqueta = self._pila[i].etiqueta
            if etiqueta in cierra:
                while len(self._pila) > i:
                    self._sacar()
                return
            if etiqueta in frontera:
                return

    def _sacar(self) -> Nodo:
        nodo = self._pila.pop()
        self._abiertos[nodo.etiqueta] -= 1
        if nodo.etiqueta in _EXTRANJEROS:
            self._extranjero -= 1
        return nodo
