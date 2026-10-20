"""Resultados de búsqueda que la página ya trae como datos: JSON-LD `ItemList` y JSON embebido.

Muchas tiendas pintan los resultados en el navegador con JavaScript (Next.js, Vue, React...), así
que el HTML que llega por HTTP no tiene las tarjetas que buscan los selectores del perfil, pero sí
los datos con los que se pintan: `<script id="__NEXT_DATA__" type="application/json">`,
`window.__INITIAL_STATE__ = {...}` o un `ItemList` en JSON-LD para los buscadores. Leerlos es leer
lo que la página entrega a cualquier visitante, sin ejecutar nada.

Todo es dato no confiable:

- El JSON solo se analiza con `json` (nada de `eval` ni de intérpretes de JavaScript); si es
  demasiado grande, está roto o es demasiado profundo para el analizador, se ignora sin error.
- La búsqueda dentro del JSON es iterativa y con topes de profundidad (`MAX_PROFUNDIDAD`) y de
  nodos visitados (`MAX_NODOS`) por documento.
- Los enlaces pasan por `listado.url_de_la_tienda`: solo `http`/`https` del dominio de la tienda.
  Se exige además que sean absolutos o empiecen por `/`: un `slug` suelto («faro-golf») no dice
  respecto a qué ruta va y no se adivina.
- Los precios se validan como en los metadatos (`metadatos.leer_importe`): finitos, positivos y
  por debajo de `MAX_IMPORTE`.

En el JSON embebido la detección es heurística (un objeto con título y enlace): un menú o unas
migas de pan también los tienen. Por eso, si algún candidato trae precio, solo se devuelven los
que lo traen, y en un candidato sin precio se sigue buscando dentro (el bloque de resultados con
su propio título y ruta envuelve los productos). Un objeto que enlaza a otra web no cuenta
aunque traiga además una ruta local: es un anuncio o un afiliado. El `ItemList` de JSON-LD es
explícito y no se filtra.
"""

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import Decimal
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.jsonld import bloques_jsonld
from precia.extraccion.listado import dominio_normalizado, url_de_la_tienda
from precia.extraccion.metadatos import leer_importe, moneda
from precia.extraccion.tipos import Precio, ResultadoListado
from precia.navegador.tipos import Pagina

MAX_JSON = 3_000_000
"""Caracteres de un bloque JSON; un `__NEXT_DATA__` real de búsqueda ocupa unos cientos de KB."""
MAX_SCRIPTS = 30
"""Bloques de JSON embebido (y de asignaciones a `window.*`) que se analizan como mucho."""
MAX_INTENTOS = 5
"""Asignaciones `__INITIAL_STATE__ =` que se prueban por script."""
MAX_PROFUNDIDAD = 40
MAX_NODOS = 50_000
"""Valores JSON que se visitan como mucho en cada documento."""
MAX_CANDIDATOS = 200
MAX_EVALUADOS = 20_000
"""Objetos con título y URL de la tienda que se evalúan a fondo (normalizar la URL, analizar el
título) en toda la página. Un menú real tiene unos cientos; cientos de miles de objetos de relleno
que pasan los filtros baratos harían tardar segundos."""
MAX_TITULO = 300
MAX_UNIDAD_MENOR = 6

_TIPOS_LISTA = frozenset({"ItemList", "OfferCatalog"})
_CLAVES_TITULO = ("name", "title", "productName")
_CLAVES_ENLACE = ("url", "href", "link", "permalink", "path", "slug")
# «value» solo cuenta dentro de un objeto de precio: suelto es demasiado genérico (un filtro con
# su contador, `{"title": "Bosch", "url": "/marca/bosch", "value": 12}`, no tiene precio).
_CLAVES_PRECIO = ("price", "priceAmount", "amount", "prices")
_CLAVES_PRECIO_ANIDADO = ("price", "amount", "value", "priceAmount", "centAmount", "raw")
_CLAVES_MONEDA = ("currency", "currencyCode", "currency_code", "priceCurrency")
_CLAVES_UNIDAD_MENOR = ("currency_minor_unit", "fractionDigits")
_NO_FICHA: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")
_NO_FICHA += (".ico", ".bmp", ".css", ".js", ".json", ".xml", ".woff", ".woff2")
"""Extensiones de recursos (imágenes, estilos...) que no son fichas aunque vayan con título."""
_ASIGNACION = re.compile(r"(?:window\.)?__(?:INITIAL|PRELOADED)_STATE__\s*=\s*")
_ENVOLTORIOS = re.compile(r"^\s*(?://\s*)?(?:<!--|<!\[CDATA\[)|(?://\s*)?(?:-->|\]\]>)\s*$")
_ENTERO = re.compile(r"\d{1,15}")
_ESPACIOS = re.compile(r"\s+")
_DECODIFICADOR = json.JSONDecoder(strict=False)
_UNIDAD_NO_VALIDA = -1


class _Contexto:
    """Base y dominio de la tienda, y el cupo de evaluaciones que queda en la página."""

    def __init__(self, base: str, dominio: str) -> None:
        self.base = base
        self.dominio = dominio
        self.restantes = MAX_EVALUADOS

    def evaluar(self) -> bool:
        """Gasta una evaluación; `False` si ya no quedan."""
        if self.restantes <= 0:
            return False
        self.restantes -= 1
        return True


def listado_estructurado(
    pagina: Pagina, dominio: str, *, maximo: int = 10
) -> list[ResultadoListado]:
    """Hasta `maximo` resultados de los datos estructurados de `pagina`, sin repetir URL.

    Primero los del `ItemList` de JSON-LD y después los del JSON embebido. Nunca lanza por datos
    de la web: lo que no se entiende se ignora.
    """
    normalizado = dominio_normalizado(dominio) if dominio else None
    if maximo <= 0 or normalizado is None:
        return []
    raiz = analizar(pagina.html)
    contexto = _Contexto(pagina.url_final or pagina.url, normalizado)
    propia = url_de_la_tienda(contexto.base, contexto.base, normalizado)
    vistas: set[str | None] = {propia}
    resultados: list[ResultadoListado] = []

    embebidos = list(_de_json_embebido(raiz, contexto))
    if any(r.precio is not None for r in embebidos):
        embebidos = [r for r in embebidos if r.precio is not None]
    for resultado in [*_de_itemlist(raiz, contexto), *embebidos]:
        if resultado.url in vistas:
            continue
        vistas.add(resultado.url)
        resultados.append(resultado)
        if len(resultados) >= maximo:
            break
    return resultados


# --- JSON-LD ItemList -----------------------------------------------------------------------


def _de_itemlist(raiz: Nodo, contexto: _Contexto) -> list[ResultadoListado]:
    resultados: list[ResultadoListado] = []
    for texto in bloques_jsonld(raiz):
        dato = _cargar(_ENVOLTORIOS.sub("", texto.strip()))
        for lista in _buscar(dato, lambda o: o if _es_lista(o) else None):
            elementos = lista.get("itemListElement")
            for elemento in (elementos if isinstance(elementos, list) else [elementos])[
                :MAX_CANDIDATOS
            ]:
                if contexto.restantes <= 0:
                    return resultados
                resultado = _elemento_de_lista(elemento, contexto)
                if resultado is not None:
                    resultados.append(resultado)
                    if len(resultados) >= MAX_CANDIDATOS:
                        return resultados
    return resultados


def _es_lista(objeto: dict[str, Any]) -> bool:
    tipos = objeto.get("@type")
    lista = tipos if isinstance(tipos, list) else [tipos]
    return "itemListElement" in objeto and any(
        isinstance(t, str) and t.rsplit("/", 1)[-1].rsplit(":", 1)[-1] in _TIPOS_LISTA
        for t in lista
    )


def _elemento_de_lista(elemento: Any, contexto: _Contexto) -> ResultadoListado | None:
    """Un `ListItem` (con `url` y `name`, o con el producto en `item`) o un `Product` directo."""
    if not isinstance(elemento, dict):
        return None
    item = elemento.get("item")
    producto = item if isinstance(item, dict) else {}
    nombres = (producto.get("name"), elemento.get("name"))
    if not any(isinstance(n, str) for n in nombres) or not contexto.evaluar():
        return None
    enlaces = (producto.get("url"), producto.get("@id"), elemento.get("url"), item)
    if any(_de_otra_web(e, contexto) for e in (producto.get("url"), elemento.get("url"), item)):
        return None  # Producto de otra tienda (anuncio), aunque el `ListItem` pase por la propia.
    url = next((u for e in enlaces if (u := _enlace(e, contexto)) is not None), None)
    if url is None:
        return None
    titulo = _titulo(nombres[0]) or _titulo(nombres[1])
    if titulo is None:
        return None
    precio = _precio_ofertas(producto.get("offers")) or _precio_ofertas(elemento.get("offers"))
    return ResultadoListado(titulo, url, precio)


def _precio_ofertas(valor: Any) -> Precio | None:
    for oferta in (valor if isinstance(valor, list) else [valor])[:MAX_CANDIDATOS]:
        if not isinstance(oferta, dict):
            continue
        divisa = moneda(oferta.get("priceCurrency"))
        for clave in ("price", "lowPrice"):
            precio = leer_importe(oferta.get(clave), divisa)
            if precio is not None:
                return precio
    return None


# --- JSON embebido --------------------------------------------------------------------------


def _de_json_embebido(raiz: Nodo, contexto: _Contexto) -> Iterator[ResultadoListado]:
    """Candidatos del JSON embebido; se para con `MAX_CANDIDATOS` que traigan precio.

    Los que no traen precio se cuentan aparte: el árbol de categorías del menú, con cientos de
    entradas, suele ir antes que los resultados y no debe agotar el cupo.
    """
    con_precio = sin_precio = 0
    for dato in _documentos_embebidos(raiz):
        if contexto.restantes <= 0:
            return
        for resultado in _buscar(
            dato, lambda o: _candidato(o, contexto), entrar=lambda r: r.precio is None
        ):
            if resultado.precio is None:
                sin_precio += 1
                if sin_precio > MAX_CANDIDATOS:
                    continue
            else:
                con_precio += 1
            yield resultado
            if con_precio >= MAX_CANDIDATOS:
                return


def _documentos_embebidos(raiz: Nodo) -> Iterator[Any]:
    """JSON de `<script type="application/json">` y de asignaciones `__INITIAL_STATE__ = {...}`."""
    analizados = 0
    for nodo in raiz.descendientes():
        if analizados >= MAX_SCRIPTS:
            return
        if nodo.etiqueta != "script":
            continue
        tipo = nodo.atributos.get("type", "").split(";")[0].strip().lower()
        if tipo == "application/json":
            analizados += 1
            dato = _cargar(nodo.texto_crudo())
            if dato is not None:
                yield dato
        elif tipo in ("", "text/javascript", "application/javascript", "module"):
            texto = nodo.texto_crudo()
            if "_STATE__" not in texto:
                continue
            analizados += 1
            yield from _asignaciones(texto)


def _asignaciones(texto: str) -> Iterator[Any]:
    """Valores JSON asignados a `window.__INITIAL_STATE__` o `__PRELOADED_STATE__`.

    `raw_decode` lee un único valor JSON desde la posición y deja lo que venga detrás (`;`, otras
    asignaciones). Si no es JSON válido (un objeto literal de JavaScript, `JSON.parse(...)`), se
    ignora: no se interpreta JavaScript.
    """
    if len(texto) > MAX_JSON:
        return
    for intento, coincidencia in enumerate(_ASIGNACION.finditer(texto)):
        if intento >= MAX_INTENTOS:
            return
        inicio = coincidencia.end()
        if texto[inicio : inicio + 1] not in ("{", "["):
            continue
        try:
            dato, _ = _DECODIFICADOR.raw_decode(texto, inicio)
        except (ValueError, RecursionError):
            continue
        yield dato


def _candidato(objeto: dict[str, Any], contexto: _Contexto) -> ResultadoListado | None:
    # Primero lo barato: el título puede traer HTML y analizarlo cuesta más que el enlace.
    if not any(isinstance(objeto.get(c), str) for c in _CLAVES_TITULO):
        return None
    # Un `slug` suelto («golf») no gasta cupo: se descarta sin normalizar nada. Así un selector de
    # vehículos con miles de modelos no agota el cupo antes de llegar a los resultados.
    enlaces = [e for c in _CLAVES_ENLACE if _parece_url(e := objeto.get(c))]
    if not enlaces or any(_de_otra_web(e, contexto) for e in enlaces) or not contexto.evaluar():
        return None
    url = next((u for e in enlaces if (u := _enlace(e, contexto)) is not None), None)
    if url is None:
        return None
    titulo = next((t for c in _CLAVES_TITULO if (t := _titulo(objeto.get(c)))), None)
    if titulo is None:
        return None
    return ResultadoListado(titulo, url, _precio_objeto(objeto))


def _precio_objeto(
    objeto: dict[str, Any], nivel: int = 0, heredada: str | None = None
) -> Precio | None:
    """`price: 12.9`, `price: "12,90 €"`, `price: {"amount": "8.75"}` o en unidades menores.

    Las unidades menores (`currency_minor_unit`, `fractionDigits`, `centAmount`) van en el mismo
    objeto que el importe, como en la Store API de WooCommerce o en commercetools.
    """
    divisa = next((d for c in _CLAVES_MONEDA if (d := moneda(objeto.get(c)))), None) or heredada
    unidad = _unidad_menor(objeto)
    for clave in _CLAVES_PRECIO if nivel == 0 else _CLAVES_PRECIO_ANIDADO:
        if clave not in objeto:
            continue
        valor = objeto[clave]
        if isinstance(valor, dict):
            precio = _precio_objeto(valor, nivel + 1, divisa) if nivel < 2 else None
        elif unidad is not None:
            precio = _en_unidades_menores(valor, unidad, divisa)
        else:
            precio = leer_importe(valor, divisa)
        if precio is not None:
            return precio
    return None


def _unidad_menor(objeto: dict[str, Any]) -> int | None:
    for clave in _CLAVES_UNIDAD_MENOR:
        if clave in objeto:
            valor = objeto[clave]
            valida = isinstance(valor, int) and not isinstance(valor, bool)
            return valor if valida and 0 <= valor <= MAX_UNIDAD_MENOR else _UNIDAD_NO_VALIDA
    return 2 if "centAmount" in objeto else None


def _en_unidades_menores(valor: Any, unidad: int, divisa: str | None) -> Precio | None:
    """`"1095"` con dos decimales → 10,95. Si la unidad no es válida, el importe no es de fiar."""
    if unidad == _UNIDAD_NO_VALIDA or isinstance(valor, bool):
        return None
    if isinstance(valor, int) and 0 < valor < 10**15:
        texto = str(valor)
    elif isinstance(valor, str) and _ENTERO.fullmatch(valor.strip()):
        texto = valor.strip()
    else:
        return None
    # Como `Decimal` y no como texto: «12.900» (tres decimales) sería ambiguo para `leer_importe`.
    precio = leer_importe(Decimal(texto).scaleb(-unidad), divisa)
    return replace(precio, texto=texto) if precio is not None else None


# --- Comunes --------------------------------------------------------------------------------


def _cargar(texto: str) -> Any:
    if not texto or len(texto) > MAX_JSON:
        return None
    try:
        return json.loads(texto, strict=False)
    except (ValueError, RecursionError):
        # ValueError incluye los enteros de miles de cifras (límite de `int` de Python 3.11+).
        return None


def _buscar[T](
    dato: Any,
    convertir: Callable[[dict[str, Any]], T | None],
    *,
    entrar: Callable[[T], bool] = lambda _: False,
) -> Iterator[T]:
    """Lo que `convertir` saca de los objetos de `dato`, en preorden.

    En un objeto convertido solo se entra si `entrar` lo pide. Iterativo y con topes de
    profundidad y de nodos: el JSON viene de la web.
    """
    pila: list[tuple[Any, int]] = [(dato, 0)]
    visitados = 0
    while pila and visitados < MAX_NODOS:
        actual, profundidad = pila.pop()
        visitados += 1
        if profundidad > MAX_PROFUNDIDAD:
            continue
        if isinstance(actual, dict):
            convertido = convertir(actual)
            if convertido is not None:
                yield convertido
                if not entrar(convertido):
                    continue
            hijos = list(actual.values())
        elif isinstance(actual, list):
            hijos = actual
        else:
            continue
        pila.extend((hijo, profundidad + 1) for hijo in reversed(hijos))


def _enlace(valor: Any, contexto: _Contexto) -> str | None:
    """URL absoluta de la tienda, o `None` si es de otra web, no es una ficha o es ambigua."""
    if not isinstance(valor, str):
        return None
    enlace = valor.strip()
    if not enlace.lower().startswith(("http://", "https://", "/")):
        return None
    try:
        if urlsplit(enlace).path.lower().endswith(_NO_FICHA):
            return None  # Antes de normalizar, que es lo caro.
    except ValueError:
        return None
    url = url_de_la_tienda(enlace, contexto.base, contexto.dominio)
    if url is None:
        return None
    ruta = urlsplit(url).path.lower()
    return None if ruta.endswith(_NO_FICHA) else url


def _parece_url(valor: Any) -> bool:
    return isinstance(valor, str) and valor.strip().lower().startswith(("http://", "https://", "/"))


def _de_otra_web(valor: Any, contexto: _Contexto) -> bool:
    """`valor` es una URL absoluta (o `//host`) que no es de la tienda."""
    if not isinstance(valor, str):
        return False
    enlace = valor.strip()
    if not enlace.lower().startswith(("http://", "https://", "//")):
        return False
    # Atajo para el caso normal (host ASCII): `url_de_la_tienda` normaliza, recodifica y
    # reconstruye la URL, y miles de enlaces de otra web en un JSON hostil se notan.
    try:
        host = (urlsplit(enlace).hostname or "").rstrip(".")
    except ValueError:
        return True
    if host.isascii() and host.removeprefix("www.") != contexto.dominio:
        return True
    return url_de_la_tienda(enlace, contexto.base, contexto.dominio) is None


def _titulo(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = unescape(valor[: MAX_TITULO * 4])
    if "<" in texto:
        texto = analizar(texto).texto()
    texto = _ESPACIOS.sub(" ", texto).strip()
    if not texto:
        return None
    return texto if len(texto) <= MAX_TITULO else texto[: MAX_TITULO - 1].rstrip() + "…"
