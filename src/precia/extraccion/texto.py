"""Texto visible de una página y resumen de su estructura para el configurador de IA.

El resumen es lo único que la IA ve de una tienda al generar selectores: HTML simplificado con
la etiqueta, los atributos que sirven para escribir un selector y el texto recortado. Se quitan
scripts, estilos, comentarios, elementos ocultos y manejadores de eventos (por ahorro de tokens
y porque son el sitio típico donde esconder instrucciones para la IA) y todo se escapa.

Si la página entera no cabe en el tope, antes de cortar se hace sitio:

1. **Series comprimidas.** Los hermanos con la misma forma (etiqueta, primera clase y etiquetas
   de todos sus descendientes) son repeticiones: filtros de un buscador, menús, opciones de un
   `select`... De una serie de `MIN_SERIE` o más se dejan los `MAX_EJEMPLOS_SERIE` primeros
   completos y, en lugar del resto, el comentario `<!-- N elementos similares omitidos -->`.
   Basta para escribir un selector y, como el texto de la web se escapa, la web no puede
   escribir ese comentario. Solo se comprime lo necesario para que quepa, empezando por la serie
   que más ahorra, y nunca una que ahorre menos de `MIN_AHORRO_SERIE` del tope: una serie
   pequeña puede ser la tabla de datos de una ficha (la fila de la referencia OEM) y comprimirla
   apenas deja sitio.
2. **Prioridad del listado** (con `listado=True`). La serie que más se parece a una lista de
   resultados (hermanos con enlaces distintos y texto, mejor con precio e imagen) se deja
   completa hasta `MAX_EJEMPLOS_LISTADO` y su contenedor, con la cadena de sus antepasados, tiene
   el sitio reservado: lo que se recorta es el resto de la página, sin romper el anidamiento
   (lo que se abre se cierra) y sin cambiar el orden del documento.

En una búsqueda real (desguacesdli.com) la cabecera, los menús y ~1200 filtros llenaban el tope
antes de llegar a los productos, y la IA se inventaba los selectores. Para una ficha se usa
`listado=False`: los productos relacionados no deben quitarle sitio al producto principal.
"""

import math
import re
from dataclasses import dataclass, field
from html import escape

from precia.extraccion.dom import VACIOS, Nodo, analizar, invisible

MAX_TEXTO_TROZO = 200
MAX_VALOR_ATRIBUTO = 80
MAX_EJEMPLOS_SERIE = 3
"""Hermanos de una serie que se dejan completos al comprimirla."""
MIN_SERIE = 6
"""Con menos hermanos iguales no se comprime: el marcador apenas ahorraría nada."""
MAX_EJEMPLOS_LISTADO = 40
"""Resultados del listado que se dejan completos (una página de búsqueda suele traer menos)."""
MIN_RESULTADOS = 3
"""Hermanos con enlaces distintos que hacen falta para que una serie parezca el listado."""
PROFUNDIDAD_FORMA = 3
"""Niveles de hijos que cuentan para reconocer los resultados de un listado como una serie."""
MIN_AHORRO_SERIE = 0.1
"""Fracción del tope que tiene que ahorrar una serie para que se comprima."""
_ATRIBUTOS_UTILES = frozenset(
    {"id", "class", "href", "src", "alt", "itemprop", "itemtype", "content", "action", "method"}
    | {"name", "type"}
)
_OMITIDOS = frozenset({"iframe", "object", "embed", "link", "meta", "base", "frame", "frameset"})
_TRANSPARENTES = frozenset({"html", "head", "body"})
_ESPACIOS = re.compile(r"\s+")
_CIFRA = re.compile(r"\d")
_MONEDA = re.compile(r"[€$£]|\b(?:EUR|USD|GBP)\b")
_ENLACES_INUTILES = ("#", "?", "javascript:", "mailto:", "tel:")


def texto_visible(html: str) -> str:
    """Texto que ve una persona: sin `script`, `style`, `noscript`, `template`, `svg`,
    comentarios ni elementos ocultos, con los espacios normalizados."""
    return analizar(html).texto()


def resumen_para_ia(html: str, max_caracteres: int = 30_000, *, listado: bool = True) -> str:
    """HTML simplificado de `html`, de `max_caracteres` como mucho.

    Si no cabe entero se comprimen las series repetidas y, con `listado`, se reserva el sitio
    del listado de resultados (ver el docstring del módulo); lo que aún sobre se corta.
    """
    raiz = analizar(html)
    entero = _serializar(raiz, _Plan(), max_caracteres)
    if entero is not None:
        return "".join(p.texto for p in entero)
    partes = _serializar(raiz, _planificar(raiz, max_caracteres, listado=listado), None)
    assert partes is not None
    return _ajustar(partes, max_caracteres)


@dataclass(slots=True)
class _Parte:
    texto: str
    prioritaria: bool = False
    """Del listado o de la cadena de sus antepasados: tiene el sitio reservado."""
    cierre: int = 0
    """En una apertura, lo que ocupa su cierre (se reserva al abrir)."""
    apertura: int = -1
    """En un cierre, el índice de la parte que lo abrió."""


@dataclass(frozen=True, slots=True)
class _Cierre:
    etiqueta: str
    prioritaria: bool
    apertura: int
    region: bool
    """Cierra el contenedor del listado."""


@dataclass(slots=True)
class _Plan:
    omitidos: set[int] = field(default_factory=set)
    """`id` de los elementos que no se escriben (con todo su contenido)."""
    marcadores: dict[int, int] = field(default_factory=dict)
    """`id` del primer omitido de una serie → cuántos se omiten (el marcador va en su lugar)."""
    region: Nodo | None = None
    """Contenedor del listado."""
    camino: set[int] = field(default_factory=set)
    """`id` de los antepasados de `region`."""


def _serializar(raiz: Nodo, plan: _Plan, maximo: int | None) -> list[_Parte] | None:
    """Partes del resumen en orden; `None` si pasan de `maximo` (sin tope si es `None`)."""
    partes: list[_Parte] = []
    longitud = 0
    dentro = False
    pila: list[Nodo | str | _Cierre] = list(reversed(raiz.contenido))
    while pila:
        actual = pila.pop()
        if isinstance(actual, _Cierre):
            dentro = dentro and not actual.region
            parte = _Parte(f"</{actual.etiqueta}>", actual.prioritaria, apertura=actual.apertura)
        elif isinstance(actual, str):
            parte = _Parte(_texto(actual), dentro)
        elif _omitir(actual):
            continue
        elif id(actual) in plan.omitidos:
            if (omitidos := plan.marcadores.get(id(actual))) is None:
                continue
            parte = _Parte(_marcador(omitidos), dentro)
        elif actual.etiqueta in _TRANSPARENTES:
            pila.extend(reversed(actual.contenido))
            continue
        else:
            es_region = actual is plan.region
            parte = _Parte(_apertura(actual), dentro or es_region or id(actual) in plan.camino)
            if actual.etiqueta not in VACIOS:
                parte.cierre = len(actual.etiqueta) + 3
                pila.append(_Cierre(actual.etiqueta, parte.prioritaria, len(partes), es_region))
                pila.extend(reversed(actual.contenido))
                dentro = dentro or es_region
        if not parte.texto:
            continue
        longitud += len(parte.texto)
        if maximo is not None and longitud > maximo:
            return None
        partes.append(parte)
    return partes


def _ajustar(partes: list[_Parte], maximo: int) -> str:
    """Las partes que caben en `maximo`, con el sitio de las prioritarias reservado.

    Una apertura no prioritaria solo entra si caben ella y su cierre; desde la primera parte no
    prioritaria que no cabe ya no entra ninguna más (salvo los cierres pendientes), para que el
    resumen no salte de un trozo de la página a otro. Si las prioritarias no caben todas, se
    corta por el final.
    """
    reservado = sum(len(p.texto) for p in partes if p.prioritaria)
    libre = max(0, maximo - reservado)
    salida: list[str] = []
    abiertas: set[int] = set()
    longitud = 0
    agotado = False
    for indice, parte in enumerate(partes):
        if parte.prioritaria:
            if longitud + len(parte.texto) > maximo:
                break
        elif parte.apertura >= 0:
            if parte.apertura not in abiertas:
                continue
        elif agotado or len(parte.texto) + parte.cierre > libre:
            agotado = True
            continue
        else:
            libre -= len(parte.texto) + parte.cierre
            if parte.cierre:
                abiertas.add(indice)
        salida.append(parte.texto)
        longitud += len(parte.texto)
    return "".join(salida)


def _marcador(omitidos: int) -> str:
    if omitidos == 1:
        return "<!-- 1 elemento similar omitido -->"
    return f"<!-- {omitidos} elementos similares omitidos -->"


# --- Plan: series repetidas y listado ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Rasgos:
    """Lo que se sabe de un elemento visible, calculado de los hijos hacia arriba."""

    formas: tuple[int, ...]
    """Forma con 0..PROFUNDIDAD_FORMA niveles de hijos (solo etiquetas), como número."""
    forma: int
    """Forma con todos los descendientes: la de dos elementos es igual si son repeticiones."""
    tamano: int
    """Lo que ocupa el elemento, con todo su contenido, en el resumen sin comprimir."""
    enlace: str | None
    """`href` del primer enlace útil (no `#`, `?...`, `javascript:`...)."""
    imagen: bool
    precio: bool
    """Tiene una clase o `itemprop` de precio, o texto con cifras y un símbolo de moneda."""
    cifra: bool
    moneda: bool
    texto: int
    """Caracteres de texto visible (aproximado)."""


def _planificar(raiz: Nodo, maximo: int, *, listado: bool) -> _Plan:
    """Qué series se comprimen y, con `listado`, qué contenedor tiene el sitio reservado."""
    visibles = _visibles(raiz)
    rasgos = _rasgos(visibles)
    # Para reconocer el listado basta la forma de unos pocos niveles (los resultados pueden
    # variar en lo hondo); para comprimir, la forma entera: las secciones de un maquetador
    # (Elementor, Bricks...) comparten los envoltorios, pero no son repeticiones.
    series: list[tuple[Nodo, list[Nodo]]] = []
    repetidos: list[list[Nodo]] = []
    for padre in visibles:
        grupos: dict[tuple[int, str], list[Nodo]] = {}
        iguales: dict[tuple[int, str], list[Nodo]] = {}
        for hijo in padre.hijos:
            if (r := rasgos.get(id(hijo))) is not None:
                primera = hijo.clases[0] if hijo.clases else ""
                grupos.setdefault((r.formas[-1], primera), []).append(hijo)
                iguales.setdefault((r.forma, primera), []).append(hijo)
        series.extend((padre, g) for g in grupos.values() if len(g) >= MIN_RESULTADOS)
        repetidos.extend(g for g in iguales.values() if len(g) >= MIN_SERIE)

    plan = _Plan()
    del_listado: set[int] = set()
    candidatas: list[tuple[list[Nodo], int]] = []
    if listado:
        puntos, mejor = max(
            ((_puntuacion(g, rasgos), i) for i, (_, g) in enumerate(series)), default=(0.0, -1)
        )
        if puntos > 0:
            plan.region, grupo = series[mejor]
            del_listado = {id(n) for n in grupo}
            candidatas.append((grupo, MAX_EJEMPLOS_LISTADO))
            # El contenedor y sus antepasados nunca se omiten, aunque sean de una serie.
            antepasado: Nodo | None = plan.region
            while antepasado is not None:
                plan.camino.add(id(antepasado))
                antepasado = antepasado.padre
    candidatas.extend(
        (g, MAX_EJEMPLOS_SERIE) for g in repetidos if not del_listado.intersection(map(id, g))
    )

    # Primero la serie que más ahorra, y solo hasta que quepa. Las series anidadas en una ya
    # omitida cuentan dos veces su ahorro: entonces la estimación se queda corta y lo que sobre
    # lo recorta `_ajustar`.
    total = rasgos[id(raiz)].tamano
    ahorros: list[tuple[int, list[Nodo]]] = []
    for grupo, conservar in candidatas:
        if omitidos := [n for n in grupo[conservar:] if id(n) not in plan.camino]:
            ahorro = sum(rasgos[id(n)].tamano for n in omitidos) - len(_marcador(len(omitidos)))
            ahorros.append((ahorro, omitidos))
    ahorros.sort(key=lambda a: a[0], reverse=True)
    for ahorro, omitidos in ahorros:
        if total <= maximo or ahorro < MIN_AHORRO_SERIE * maximo:
            break
        plan.omitidos.update(id(n) for n in omitidos)
        plan.marcadores[id(omitidos[0])] = len(omitidos)
        total -= ahorro
    return plan


def _puntuacion(grupo: list[Nodo], rasgos: dict[int, _Rasgos]) -> float:
    """Cuánto parece `grupo` la lista de resultados; 0 si no lo parece.

    Cuentan los hermanos con enlace y algo de texto; sin `MIN_RESULTADOS` enlaces distintos no
    es un listado (los filtros de un buscador no enlazan a fichas). Un menú tiene enlaces y
    texto, pero rara vez precio e imagen: por eso pesan, y el número de resultados solo en
    escala logarítmica (un pie con 60 enlaces no gana a 12 productos).
    """
    utiles = [r for n in grupo if (r := rasgos[id(n)]).enlace is not None and r.texto >= 3]
    distintos = len({r.enlace for r in utiles})
    if distintos < MIN_RESULTADOS:
        return 0.0
    con_precio = sum(r.precio for r in utiles) / len(utiles)
    con_imagen = sum(r.imagen for r in utiles) / len(utiles)
    return (1 + 2 * con_precio + con_imagen) * math.log2(1 + distintos)


def _visibles(raiz: Nodo) -> list[Nodo]:
    """`raiz` y los descendientes que se escriben en el resumen, en orden del documento."""
    nodos: list[Nodo] = []
    pila = [raiz]
    while pila:
        nodo = pila.pop()
        nodos.append(nodo)
        pila.extend(h for h in reversed(nodo.hijos) if not _omitir(h))
    return nodos


def _rasgos(visibles: list[Nodo]) -> dict[int, _Rasgos]:
    """`_Rasgos` de cada elemento de `visibles`, de las hojas hacia arriba (lineal)."""
    formas: dict[tuple[str | int, ...], int] = {}
    rasgos: dict[int, _Rasgos] = {}
    # En orden inverso del documento cada elemento llega después de todos sus descendientes.
    for nodo in reversed(visibles):
        etiqueta = nodo.etiqueta
        hijos = [r for h in nodo.hijos if (r := rasgos.get(id(h))) is not None]
        enlace = _enlace_util(nodo)
        imagen = etiqueta == "img"
        precio = _clase_de_precio(nodo)
        cifra = moneda = False
        texto = 0
        # Como en `_serializar`: la raíz y las transparentes no escriben su etiqueta.
        if nodo.padre is None or etiqueta in _TRANSPARENTES:
            tamano = 0
        else:
            tamano = len(_apertura(nodo)) + (0 if etiqueta in VACIOS else len(etiqueta) + 3)
        for r in hijos:
            texto += r.texto
            tamano += r.tamano
            imagen = imagen or r.imagen
            precio = precio or r.precio
            cifra = cifra or r.cifra
            moneda = moneda or r.moneda
            enlace = enlace or r.enlace
        for trozo in nodo.contenido:
            if isinstance(trozo, str):
                texto += len(trozo.strip())
                tamano += len(_texto(trozo))
                cifra = cifra or _CIFRA.search(trozo) is not None
                moneda = moneda or _MONEDA.search(trozo) is not None
        hoja = formas.setdefault((etiqueta,), len(formas))
        if hijos:
            niveles = [hoja]
            for nivel in range(PROFUNDIDAD_FORMA):
                forma: tuple[str | int, ...] = (
                    etiqueta,
                    *_sin_repetidos([r.formas[nivel] for r in hijos]),
                )
                niveles.append(formas.setdefault(forma, len(formas)))
            completa = formas.setdefault(
                (etiqueta, *_sin_repetidos([r.forma for r in hijos])), len(formas)
            )
        else:
            niveles = [hoja] * (PROFUNDIDAD_FORMA + 1)
            completa = hoja
        rasgos[id(nodo)] = _Rasgos(
            tuple(niveles),
            completa,
            tamano,
            enlace,
            imagen,
            precio or (cifra and moneda),
            cifra,
            moneda,
            texto,
        )
    return rasgos


def _sin_repetidos(formas: list[int]) -> list[int]:
    """Sin repeticiones seguidas: con tres `<p>` o con cuatro, la forma es la misma."""
    return [f for i, f in enumerate(formas) if i == 0 or formas[i - 1] != f]


def _enlace_util(nodo: Nodo) -> str | None:
    if nodo.etiqueta != "a":
        return None
    href = nodo.atributos.get("href", "").strip()
    if not href or href.lower().startswith(_ENLACES_INUTILES):
        return None
    return href


def _clase_de_precio(nodo: Nodo) -> bool:
    for nombre in ("class", "itemprop"):
        valor = nodo.atributos.get(nombre, "").lower()
        if "price" in valor or "precio" in valor:
            return True
    return False


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
