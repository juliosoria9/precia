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
MAX_SEGMENTOS = 1_000
"""Pares hueco-segmento del *streaming* de React que se cuelgan como mucho (Zalando: unos 45)."""
MAX_SUBIDA = 4 * MAX_PROFUNDIDAD
"""Antepasados que se miran para no colgar un segmento dentro de sí mismo; más, no se cuelga."""


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
_FRONTERA = "\x00"
"""Marca interna de `Nodo.texto` en cada frontera de un elemento en línea; nunca sale del texto
(el carácter nulo que traiga la página se quita: no se ve)."""
_CENTIMOS_APARTE = re.compile(
    r"\x00(\d{2})\x00([ \u00a0\u202f]?)(?=[€$£]|(?i:euros?|eur)(?![a-z]))"
)
"""Exactamente dos cifras entre fronteras de elementos en línea, seguidas de la moneda: los
céntimos de `228<sup>96</sup>€` (causa R3-A2). Empieza por la marca, que se busca rápido; el
entero de delante lo mira `_ENTERO_DELANTE`."""
_SUPERINDICE = "\x01"
"""Marca interna de `Nodo.texto` al abrir un `<sup>`; como `_FRONTERA`, nunca sale del texto."""
_CENTIMOS_EN_SUP = re.compile(r"\x01[\s\x00]*(\d{2})([\s\x00]*)(?=[€$£]|(?i:euros?|eur)(?![a-z]))")
"""Dos cifras al principio de un `<sup>`, con espacios de formato alrededor, y la moneda detrás:
los céntimos de `<span>\\n 1\\n</span>\\n<sup>\\n 95\\n</sup>€` (Coolmod)."""
_HUECO = re.compile(r"[\s\x00]*\Z")
_VENTANA_HUECO = 200
"""Espacios de formato que se miran como mucho alrededor del entero (la sangría del HTML)."""
_ENTERO_DELANTE = re.compile(r"(?:\d{1,3}(?:\.\d{3}){1,2}|\d{1,3}(?:,\d{3}){1,2}|\d{1,7})\Z")
_VENTANA_ENTERO = 12
"""Caracteres que se miran delante de los céntimos (el entero más largo, `123.456.789`, son 11)."""
_OCULTO_EN_ESTILO = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
TACHADOS = frozenset({"del", "s", "strike"})
"""Etiquetas de texto tachado: en una tienda, el precio antiguo de una oferta."""
_PALABRAS_PRECIO = frozenset({"price", "precio", "prix", "pvp"})
_PALABRAS_ANTIGUO = frozenset(
    {"old", "was", "before", "regular", "reference", "compare", "crossed", "strike"}
    | {"striked", "original", "antes", "anterior", "antiguo", "tachado", "ancien", "rrp"}
)
_TROZOS_CLASE = re.compile(r"[-_]+")
_ID_REACT = r"([A-Za-z]:[0-9a-fA-F]+)"
_LLAMADA_REACT = re.compile(rf'\$R([CRS])\(\s*"{_ID_REACT}"\s*,\s*"{_ID_REACT}"')
"""`$RC("B:N","S:N")` y `$RR(...)` (React 19, con un tercer argumento) llevan primero el hueco;
`$RS("S:N","P:N")`, primero el segmento. Solo con ids literales: la definición
`$RC=function(b,c,e){...}` no casa."""
ATRIBUTOS_DE_PRUEBA = ("data-test", "data-testid", "data-test-id", "data-qa", "data-cy", "data-e2e")
"""Atributos con el nombre de un componente para los tests de la tienda (React, Vue...). Como las
clases, pueden marcar el precio antiguo (MediaMarkt: `data-test="mms-strike-price-type-rrp"`).
Los demás `data-*` no se miran: suelen llevar JSON, URL o importes de analítica."""


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

    def texto(self, *, sin_tachados: bool = False, pestanas: bool = False) -> str:
        """Texto visible normalizado: sin scripts, estilos ni elementos ocultos.

        Los bloques (`div`, `p`, `li`, `br`...) separan con un espacio; los elementos en línea
        no, para que `<span>38</span><span>,90 €</span>` siga siendo `38,90 €`. Los céntimos en
        un elemento aparte y seguidos de la moneda (`228<sup>96</sup>€`, en superíndice) se
        leen con coma decimal, `228,96€`, sea cual sea la etiqueta o la clase (causa R3-A2):
        es lo que la persona ve. Sin moneda detrás, o con el entero pegado a una letra, no
        (una referencia partida en varios `<b>` no cambia). Con
        `sin_tachados`, tampoco el de los descendientes `tachado` (el precio antiguo); el propio
        nodo no se mira: eso lo decide quien llama.

        Con `pestanas`, entra también el de las pestañas ocultas (`pestana_oculta`), que la
        persona ve con un clic. Ese texto solo sirve para ver si la ficha cita una referencia
        (B5); nunca para anclar valores ni precios, que se anclan contra el texto por defecto.
        """
        trozos: list[str] = []
        pila: list[Nodo | str] = list(reversed(self.contenido))
        while pila:
            actual = pila.pop()
            if isinstance(actual, str):
                if actual == _FRONTERA:
                    if trozos and trozos[-1] != _FRONTERA:
                        trozos.append(_FRONTERA)
                elif limpio := actual.replace(_FRONTERA, "").replace(_SUPERINDICE, ""):
                    trozos.append(limpio)
                continue
            if invisible(actual) and not (pestanas and pestana_oculta(actual)):
                continue
            if sin_tachados and tachado(actual):
                continue
            # Una marca al abrir y otra al cerrar (cuando la pila llega a la que se deja aquí).
            if actual.etiqueta in _BLOQUES:
                trozos.append(" ")
                pila.append(" ")
            else:
                if trozos and trozos[-1] != _FRONTERA:
                    trozos.append(_FRONTERA)
                if actual.etiqueta == "sup":
                    trozos.append(_SUPERINDICE)
                pila.append(_FRONTERA)
            pila.extend(reversed(actual.contenido))
        texto = "".join(trozos)
        if _SUPERINDICE in texto:
            texto = _centimos_en_superindice(texto).replace(_SUPERINDICE, "")
        if _FRONTERA in texto:
            texto = _centimos_con_coma(texto).replace(_FRONTERA, "")
        return _ESPACIOS.sub(" ", texto).strip()

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


def _centimos_en_superindice(texto: str) -> str:
    """`1` + `<sup>95</sup>` + `€` con espacios de formato en medio → `1,95 €`.

    Si hay espacios entre el entero y el `<sup>`, el entero tiene que ir en su propio elemento
    (con una frontera delante): «Talla 1 <sup>95</sup> €» no es un precio. Sin espacios, decide
    después `_centimos_con_coma`, como siempre. Lineal: cada coincidencia mira una ventana fija.
    """
    partes: list[str] = []
    hecho = 0
    for centimos in _CENTIMOS_EN_SUP.finditer(texto):
        hueco = _HUECO.search(
            texto, max(hecho, centimos.start() - _VENTANA_HUECO), centimos.start()
        )
        fin = hueco.start() if hueco is not None else centimos.start()
        if not texto[fin : centimos.start()].strip(_FRONTERA):
            continue  # Sin espacios en medio: lo resuelve `_centimos_con_coma`.
        entero = _ENTERO_DELANTE.search(texto, max(hecho, fin - _VENTANA_ENTERO), fin)
        if entero is None:
            continue
        desde = max(hecho, entero.start() - _VENTANA_HUECO)
        delante = texto[desde : entero.start()].rstrip()
        if not (delante.endswith(_FRONTERA) or (not delante and desde == 0)):
            continue
        decimal = "." if "," in entero.group() else ","
        espacio = " " if centimos.group(2).strip(_FRONTERA) else ""
        partes += (texto[hecho : entero.end()], decimal, centimos.group(1), espacio)
        hecho = centimos.end()
    if not partes:
        return texto
    partes.append(texto[hecho:])
    return "".join(partes)


def _centimos_con_coma(texto: str) -> str:
    """`228` + `96` + `€` en elementos distintos → `228,96€` (con los miles en comas, `1,228`,
    con punto decimal). El entero tiene que acabar justo en la frontera y no ir pegado a una
    letra, una cifra o un separador, aunque haya otra frontera en medio: `1K0<b>61</b>€` y
    `AB<b>12</b><b>34</b>€` son referencias. Lineal: cada coincidencia mira una ventana fija."""
    partes: list[str] = []
    hecho = 0
    for centimos in _CENTIMOS_APARTE.finditer(texto):
        frontera = centimos.start()
        entero = _ENTERO_DELANTE.search(texto, max(hecho, frontera - _VENTANA_ENTERO), frontera)
        if entero is None:
            continue
        inicio = entero.start()
        previo = texto[inicio - 1] if inicio else ""
        if previo == _FRONTERA:
            previo = texto[inicio - 2] if inicio > 1 else ""
        if previo and (previo.isalnum() or previo in "_.,"):
            continue
        decimal = "." if "," in entero.group() else ","
        partes += (texto[hecho:frontera], decimal, centimos.group(1), centimos.group(2))
        hecho = centimos.end()
    if not partes:
        return texto
    partes.append(texto[hecho:])
    return "".join(partes)


def oculto(nodo: Nodo) -> bool:
    """El elemento o alguno de sus antepasados es `invisible`: en la página no se ve."""
    actual: Nodo | None = nodo
    while actual is not None:
        if invisible(actual):
            return True
        actual = actual.padre
    return False


def invisible(nodo: Nodo) -> bool:
    """El elemento no se ve: script, estilo, plantilla, SVG, `hidden` o CSS en línea que oculta."""
    if nodo.etiqueta in INVISIBLES:
        return True
    if "hidden" in nodo.atributos:
        return True
    estilo = nodo.atributos.get("style")
    # Texto oculto con CSS: lo que el usuario no ve no debe servir para anclar un valor.
    return estilo is not None and _OCULTO_EN_ESTILO.search(estilo) is not None


def pestana_oculta(nodo: Nodo) -> bool:
    """Panel de pestaña (`role="tabpanel"`) oculto solo con `hidden`, a la espera de un clic.

    Oscaro guarda así la pestaña «Referencias del fabricante (OEM)». Si además lo oculta el CSS
    en línea, o es un script o una plantilla, no cuenta: eso no lo ve nadie.
    """
    if nodo.etiqueta in INVISIBLES or "hidden" not in nodo.atributos:
        return False
    if nodo.atributos.get("role", "").strip().lower() != "tabpanel":
        return False
    estilo = nodo.atributos.get("style")
    return estilo is None or _OCULTO_EN_ESTILO.search(estilo) is None


def tachado(nodo: Nodo) -> bool:
    """`del`, `s`, `strike` o una clase, `id` o atributo de prueba (`ATRIBUTOS_DE_PRUEBA`) de
    precio antiguo (`old-price`, `price--compare`, `data-test="mms-strike-price-type-rrp"`).

    Cada nombre (un valor puede llevar varios separados por espacios) cuenta si, partido por
    guiones y guiones bajos, tiene una palabra de precio y otra de antiguo: `regular-price` y
    `reference-price` sí; `price` o `price-box`, no. `aria-label` no se mira: es texto libre
    («Precio antes de impuestos» no es un precio antiguo).
    """
    if nodo.etiqueta in TACHADOS:
        return True
    nombres = [*nodo.clases, *nodo.atributos.get("id", "").split()]
    for atributo in ATRIBUTOS_DE_PRUEBA:
        nombres.extend(nodo.atributos.get(atributo, "").split())
    for nombre in nombres:
        trozos = set(_TROZOS_CLASE.split(nombre.lower()))
        if trozos & _PALABRAS_PRECIO and trozos & _PALABRAS_ANTIGUO:
            return True
    return False


def analizar(html: str) -> Nodo:
    """Construye el árbol de `html`, por roto que esté. Nunca lanza por HTML mal formado."""
    html = html[:MAX_CARACTERES]
    constructor = _Constructor()
    constructor.feed(html)
    constructor.close()
    if "$R" in html or "data-rci" in html or "data-rsi" in html:
        _colgar_segmentos(constructor.raiz)
    return constructor.raiz


def _colgar_segmentos(raiz: Nodo) -> None:
    """*Streaming* SSR de React 18 (y Next.js): cuelga cada segmento en el sitio de su hueco.

    React manda el contenido en segmentos `<div hidden id="S:N">` y un `<script>` en línea los
    cuelga, como se hace aquí, en el sitio de su hueco: `$RC("B:N","S:N")` en el de
    `<template id="B:N">` y `$RS("S:N","P:N")` en el de `<template id="P:N">` (el runtime
    externo usa `<template data-rci data-bid data-sid>` y `<template data-rsi data-sid
    data-pid>`). Sin esto todo el contenido colgaba de un `[hidden]` y no se veía (causa R2-C9,
    Zalando). Solo se cuelga un `<div hidden>` con un par explícito y su hueco `<template>`: un
    `hidden` sin par sigue oculto, así que el anclaje no se relaja. Los pares van en el orden del
    documento (un segmento puede traer los huecos de otros). Como los comentarios no entran en el
    árbol, el contenido provisional que sigue al hueco (un esqueleto de carga) se queda.
    """
    huecos: dict[str, Nodo] = {}
    segmentos: dict[str, Nodo] = {}
    pares: list[tuple[str, str]] = []
    for nodo in raiz.descendientes():
        atributos = nodo.atributos
        if nodo.etiqueta == "script" and "src" not in atributos:
            codigo = nodo.texto_crudo()
            if "$R" in codigo:
                for tipo, primero, segundo in _LLAMADA_REACT.findall(codigo):
                    pares.append((segundo, primero) if tipo == "S" else (primero, segundo))
        elif nodo.etiqueta == "template":
            huecos.setdefault(atributos.get("id", ""), nodo)
            if "data-rci" in atributos:
                pares.append((atributos.get("data-bid", ""), atributos.get("data-sid", "")))
            elif "data-rsi" in atributos:
                pares.append((atributos.get("data-pid", ""), atributos.get("data-sid", "")))
        elif nodo.etiqueta == "div" and "hidden" in atributos:
            segmentos.setdefault(atributos.get("id", ""), nodo)
    for id_hueco, id_segmento in pares[:MAX_SEGMENTOS]:
        hueco = huecos.get(id_hueco) if id_hueco else None
        segmento = segmentos.get(id_segmento) if id_segmento else None
        if hueco is not None and segmento is not None:
            _colgar(hueco, segmento)


def _colgar(hueco: Nodo, segmento: Nodo) -> None:
    """Pone los hijos de `segmento` en el sitio de `hueco` y quita los dos del árbol."""
    padre, origen = hueco.padre, segmento.padre
    if padre is None or origen is None:
        return  # Ya colgado (un par repetido) o fuera del árbol.
    antepasado: Nodo | None = padre
    for _ in range(MAX_SUBIDA):
        if antepasado is None:
            break
        if antepasado is segmento:
            return  # El hueco está dentro del segmento: sería un ciclo.
        antepasado = antepasado.padre
    else:
        return
    origen.hijos.remove(segmento)
    origen.contenido.remove(segmento)
    for hijo in segmento.hijos:
        hijo.padre = padre
    i = padre.hijos.index(hueco)
    padre.hijos[i : i + 1] = segmento.hijos
    i = padre.contenido.index(hueco)
    padre.contenido[i : i + 1] = segmento.contenido
    segmento.padre = hueco.padre = None
    segmento.hijos, segmento.contenido = [], []


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
