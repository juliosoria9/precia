"""Producto estructurado desde OpenGraph (`og:*`, `product:*`) y microdatos de schema.org.

Es la segunda vía de la cascada de extracción, tras el JSON-LD: muchas tiendas pequeñas no
publican JSON-LD pero sí microdatos (PrestaShop los trae en su tema por defecto) u OpenGraph para
las redes sociales (Shopify pone `og:price:amount`). Son datos que la página entrega a cualquier
visitante en su propio marcado, así que se leen con las mismas cautelas que el JSON-LD:

- Solo cuenta como producto un ámbito `itemscope` cuyo `itemtype` es un `Product`, una obra que
  se vende (`Book`, `Audiobook`...: `tipos.OBRAS_VENDIBLES`) o un `Offer` de `schema.org`, o un
  OpenGraph con `og:type` de producto o con precio. Un `og:title` y un `og:image` sueltos los
  lleva cualquier portada o artículo de blog.
- Las propiedades de un ámbito anidado (el accesorio relacionado, la marca como `Organization`)
  no se mezclan con las del producto, y un `Product` anidado en otro nunca es el de la ficha.
- Un ámbito que declara la URL de otra página (`itemprop="url"`) es otro producto: las
  miniaturas de «Productos relacionados» la llevan. Qué página es la ficha lo dicen su URL, el
  `canonical` y `og:url`. Si no, el precio de una bombilla del carrusel pasaría por el del faro.
- Todo se recorta y se valida: importes finitos, positivos y por debajo de `MAX_IMPORTE`, URL sin
  `javascript:` ni `data:`, y topes de ámbitos, nodos visitados, metas e imágenes.

El resultado reutiliza `ProductoJSONLD` como «producto estructurado»: la cascada de `ficha` lo
trata igual venga de donde venga.
"""

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import fields, replace
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.precios import MAX_IMPORTE, leer_precio
from precia.extraccion.tipos import OBRAS_VENDIBLES, Precio, ProductoJSONLD

MAX_AMBITOS = 50
"""Ámbitos `Product` (y `Offer`) que se miran como mucho; una ficha real tiene unos pocos."""
MAX_VISITAS = 300_000
"""Nodos que se recorren en total buscando propiedades (varios ámbitos anidados que envuelven la
página entera harían que cada búsqueda la recorriese de nuevo)."""
MAX_METAS = 500
MAX_IMAGENES = 50
MAX_NOMBRE = 300
MAX_DESCRIPCION = 5_000
MAX_IDENTIFICADOR = 100
MAX_URL = 2_000
MAX_TEXTO_PRECIO = 50
MIN_IMPORTE = Decimal("0.01")

_TIPOS_PRODUCTO = (
    frozenset({"Product", "IndividualProduct", "ProductModel", "SomeProducts"}) | OBRAS_VENDIBLES
)
_TIPOS_OFERTA = frozenset({"Offer", "AggregateOffer"})
_ESTADOS = {
    "newcondition": "Nuevo",
    "new": "Nuevo",
    "usedcondition": "Usado",
    "used": "Usado",
    "refurbishedcondition": "Reacondicionado",
    "refurbished": "Reacondicionado",
}
_DISPONIBLE = frozenset(
    {"instock", "limitedavailability", "instoreonly", "onlineonly", "preorder", "presale"}
    | {"backorder", "madetoorder", "availablefororder", "available", "pending"}
)
_NO_DISPONIBLE = frozenset({"outofstock", "oos", "soldout", "discontinued"})
_GTIN = ("gtin", "gtin13", "gtin14", "gtin12", "gtin8")
_ATRIBUTO_VALOR = {
    "meta": "content",
    "audio": "src",
    "embed": "src",
    "iframe": "src",
    "img": "src",
    "source": "src",
    "track": "src",
    "video": "src",
    "a": "href",
    "area": "href",
    "link": "href",
    "object": "data",
    "data": "value",
    "meter": "value",
    "time": "datetime",
}
_ATRIBUTO_URL = frozenset(e for e, a in _ATRIBUTO_VALOR.items() if a in ("src", "href", "data"))
"""Elementos cuyo valor de microdatos es una URL (`<a>` → `href`, `<img>` → `src`...)."""
_IMPORTE_MAQUINA = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_IMPORTE_AMBIGUO = re.compile(r"\d{1,3}\.\d{3}")
"""«1.500»: 1,5 en formato de máquina o 1500 con el punto de miles (Shopify sin decimales)."""
MAX_PAGINAS = 10
"""URL de la propia página que se recogen (`canonical` y `og:url`)."""
_MONEDA = re.compile(r"[A-Z]{3}")
_ESQUEMAS_PROHIBIDOS = ("javascript:", "data:", "vbscript:", "file:")
_ESPACIOS = re.compile(r"\s+")


def producto_metadatos(raiz: Nodo, *, url: str | None = None) -> ProductoJSONLD | None:
    """Producto de los microdatos y del OpenGraph de la página, o `None` si no hay ninguno.

    Si hay los dos, mandan los microdatos (describen el producto con más detalle) y OpenGraph
    completa lo que falte; las imágenes se juntan sin repetir. `url` es la de la página: junto
    con el `canonical` y `og:url` sirve para descartar los ámbitos de otros productos.
    """
    recogida = _Recogida()
    recogida.recorrer(raiz)
    paginas = _paginas([url, *recogida.canonicas, *recogida.metas.get("og:url", ())])
    microdatos = _microdatos(recogida, paginas)
    opengraph = _opengraph(recogida.metas)
    if microdatos is None:
        return opengraph
    if opengraph is None:
        return microdatos
    return _completar(microdatos, opengraph)


def leer_importe(valor: object, moneda: str | None = None) -> Precio | None:
    """Importe de un dato estructurado (atributo `content`, valor JSON...) o `None` si es absurdo.

    Acepta números, `Decimal`, cadenas en formato de máquina (`"45.9"`) y precios escritos
    («12,90 €»). Se descartan los no finitos (`NaN`, `1e309`), los negativos, el cero, los de
    menos de un céntimo y los que pasan de `MAX_IMPORTE`: una web no confiable puede publicar
    cualquier cosa. También «1.500»: es 1,5 o 1500 según quién lo escriba (Shopify sin decimales
    pone el punto de miles) y no se adivina.
    """
    importe: Decimal | None = None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        if not -MAX_IMPORTE < valor < MAX_IMPORTE:
            return None  # `str()` de un entero de miles de cifras lanza ValueError.
        texto = str(valor)
        importe = Decimal(valor)
    elif isinstance(valor, float):
        if not math.isfinite(valor):
            return None
        texto = str(valor)
        try:
            importe = Decimal(texto)
        except InvalidOperation:
            return None
    elif isinstance(valor, Decimal):
        if not valor.is_finite() or not MIN_IMPORTE <= valor < MAX_IMPORTE:
            return None
        texto = format(valor, "f")
        importe = valor
    elif isinstance(valor, str) and len(valor) <= MAX_TEXTO_PRECIO:
        texto = valor.strip()
        if _IMPORTE_AMBIGUO.fullmatch(texto):
            return None
        if _IMPORTE_MAQUINA.fullmatch(texto):
            importe = Decimal(texto)
        elif (leido := leer_precio(texto)) is not None:
            importe = leido.importe
            moneda = moneda or leido.moneda
    else:
        return None
    if importe is None or not importe.is_finite() or not MIN_IMPORTE <= importe < MAX_IMPORTE:
        return None
    return Precio(importe, moneda or "EUR", texto)


def moneda(valor: object) -> str | None:
    """Código ISO 4217 de tres letras (`eur` → `EUR`) o `None`."""
    if isinstance(valor, str) and _MONEDA.fullmatch(limpio := valor.strip().upper()):
        return limpio
    return None


# --- Microdatos -----------------------------------------------------------------------------


def _tipos(itemtype: str) -> frozenset[str]:
    """`https://schema.org/Product http://schema.org/Thing` → `{"Product", "Thing"}`.

    Solo se admiten tipos de schema.org: un `itemtype` de otro vocabulario no dice nada.
    """
    tipos: set[str] = set()
    for token in itemtype[:MAX_URL].split()[:10]:
        try:
            partes = urlsplit(token)
        except ValueError:
            continue
        host = (partes.hostname or "").removeprefix("www.")
        if host == "schema.org" and partes.scheme in ("http", "https"):
            tipos.add(partes.path.strip("/").rsplit("/", 1)[-1])
    return frozenset(tipos)


class _Presupuesto:
    """Nodos que aún se pueden visitar entre todos los ámbitos."""

    def __init__(self) -> None:
        self.restante = MAX_VISITAS


def _propiedades(ambito: Nodo, presupuesto: _Presupuesto) -> dict[str, list[Nodo]]:
    """Nodos con `itemprop` del ámbito, sin entrar en los ámbitos anidados (son de otro objeto).

    Recorrido iterativo: un anidamiento de miles de `div` no desborda la pila.
    """
    propiedades: dict[str, list[Nodo]] = {}
    pila = list(reversed(ambito.hijos))
    while pila and presupuesto.restante > 0:
        presupuesto.restante -= 1
        nodo = pila.pop()
        nombres = nodo.atributos.get("itemprop")
        if nombres:
            for nombre in nombres.split()[:10]:
                lista = propiedades.setdefault(nombre, [])
                if len(lista) < MAX_IMAGENES:
                    lista.append(nodo)
        if "itemscope" not in nodo.atributos:
            pila.extend(reversed(nodo.hijos))
    return propiedades


def _valor(nodo: Nodo) -> str | None:
    """Valor de una propiedad según el elemento (regla de microdatos de HTML).

    Un atributo `content` manda en cualquier elemento: es lo que hacen PrestaShop y muchos temas
    (`<span itemprop="price" content="45.9">45,90 €</span>`).
    """
    if "content" in nodo.atributos:
        return nodo.atributos["content"]
    atributo = _ATRIBUTO_VALOR.get(nodo.etiqueta)
    if atributo is not None and (atributo in nodo.atributos or nodo.etiqueta != "time"):
        return nodo.atributos.get(atributo)
    return nodo.texto()


def _valor_texto(nodo: Nodo) -> str | None:
    """Valor de una propiedad que es texto (`name`, `brand`): de un enlace, su texto.

    Por la regla de los microdatos el valor de `<a itemprop="name" href="/p/31">Faro</a>` es
    `/p/31`, pero muchas tiendas lo marcan así (las tarjetas de webscraper.io) y lo que quieren
    decir es «Faro». Un `content` sigue mandando; una imagen sin él no tiene texto.
    """
    if "content" in nodo.atributos or nodo.etiqueta not in _ATRIBUTO_URL:
        return _valor(nodo)
    return nodo.texto()


def _primero(
    nodos: Iterable[Nodo], maximo: int, valor: Callable[[Nodo], str | None] = _valor
) -> str | None:
    for nodo in nodos:
        if "itemscope" in nodo.atributos:
            continue
        texto = _texto(valor(nodo), maximo)
        if texto:
            return texto
    return None


class _Recogida:
    """Ámbitos de microdatos, metas de OpenGraph y `canonical` de una pasada por el árbol."""

    def __init__(self) -> None:
        self.productos: list[Nodo] = []
        """Ámbitos `Product` que no están dentro de otro `Product`."""
        self.ofertas: list[tuple[Nodo, Nodo | None]] = []
        """Ámbitos `Offer` con el `Product` más cercano que los contiene (o `None`)."""
        self.metas: dict[str, list[str]] = {}
        self.canonicas: list[str] = []
        self._numero_metas = 0

    def recorrer(self, raiz: Nodo) -> None:
        # Iterativo y arrastrando el `Product` más cercano: saber si un ámbito está dentro de
        # otro no obliga a subir por los padres (con miles de ámbitos anidados sería cuadrático).
        pila: list[tuple[Nodo, Nodo | None]] = [(hijo, None) for hijo in reversed(raiz.hijos)]
        while pila:
            nodo, producto = pila.pop()
            self._meta(nodo)
            dentro = producto
            if "itemscope" in nodo.atributos:
                tipos = _tipos(nodo.atributo("itemtype") or "")
                if tipos & _TIPOS_PRODUCTO:
                    if producto is None and len(self.productos) < MAX_AMBITOS:
                        self.productos.append(nodo)
                    dentro = nodo
                elif tipos & _TIPOS_OFERTA and len(self.ofertas) < MAX_AMBITOS:
                    self.ofertas.append((nodo, producto))
            pila.extend((hijo, dentro) for hijo in reversed(nodo.hijos))

    def _meta(self, nodo: Nodo) -> None:
        if nodo.etiqueta == "link":
            rel = (nodo.atributo("rel") or "").lower().split()
            if "canonical" in rel and len(self.canonicas) < MAX_PAGINAS:
                self.canonicas.append(nodo.atributo("href") or "")
        elif nodo.etiqueta == "meta" and self._numero_metas < MAX_METAS:
            clave = (nodo.atributo("property") or nodo.atributo("name") or "").strip().lower()
            if clave.startswith(("og:", "product:")):
                self._numero_metas += 1
                self.metas.setdefault(clave, []).append(nodo.atributo("content") or "")


_Paginas = tuple[str | None, frozenset[tuple[str, str]]]


def _paginas(urls: Iterable[str | None]) -> _Paginas:
    """Base para resolver URL relativas y claves (host, ruta) de la propia página.

    Se ignoran la consulta y el fragmento (`?search_query=`, `#/2-color` de PrestaShop) y la
    portada: hay temas que ponen la de la portada en el `og:url` de todas las páginas.
    """
    base: str | None = None
    claves: set[tuple[str, str]] = set()
    for valor in list(urls)[: MAX_PAGINAS * 2]:
        url = _url(valor)
        if url is None or not url.lower().startswith(("http://", "https://")):
            continue
        base = base or url
        clave = _clave_pagina(url, None)
        if clave is not None and clave[1]:
            claves.add(clave)
    return base, frozenset(claves)


def _clave_pagina(url: str, base: str | None) -> tuple[str, str] | None:
    """`https://www.t.es/p/faro/?x=1#y` → `("t.es", "/p/faro")`; `None` si no es http(s)."""
    try:
        partes = urlsplit(urljoin(base, url) if base else url)
    except ValueError:
        return None
    host = (partes.hostname or "").removeprefix("www.")
    if not host or partes.scheme.lower() not in ("http", "https"):
        return None
    return host, partes.path.rstrip("/")


def _microdatos(recogida: _Recogida, paginas: _Paginas) -> ProductoJSONLD | None:
    """El ámbito de producto de la ficha o, si no hay, una oferta con precio que no sea de otro.

    Primero el que declara la URL de la página y después el primero con nombre o precio que no
    declare la de otra. Una oferta vale suelta o dentro de un producto admitido; la de un
    relacionado (anidado o con la URL de otra página) no.
    """
    base, claves = paginas
    presupuesto = _Presupuesto()
    propios: list[ProductoJSONLD] = []
    otros: list[ProductoJSONLD] = []
    admitidos: set[int] = set()
    for ambito in recogida.productos:
        producto = _producto_microdatos(_propiedades(ambito, presupuesto), presupuesto)
        suya = _clave_pagina(producto.url, base) if producto.url else None
        if claves and suya is not None and suya not in claves:
            continue  # Declara la URL de otra página: es otro producto.
        admitidos.add(id(ambito))
        if producto.nombre or producto.precio:
            (propios if suya is not None and suya in claves else otros).append(producto)
    if propios or otros:
        return (propios or otros)[0]
    for ambito, contenedor in recogida.ofertas:
        if contenedor is not None and id(contenedor) not in admitidos:
            continue
        propiedades = _propiedades(ambito, presupuesto)
        precio = _precio_microdatos([propiedades])
        if precio is not None:
            return ProductoJSONLD(
                precio=precio,
                estado=_estado(_primero(propiedades.get("itemCondition", ()), MAX_URL)),
                disponible=_disponible(_primero(propiedades.get("availability", ()), MAX_URL)),
            )
    return None


def _producto_microdatos(
    propiedades: dict[str, list[Nodo]], presupuesto: _Presupuesto
) -> ProductoJSONLD:
    ofertas = [
        _propiedades(nodo, presupuesto)
        for nodo in propiedades.get("offers", ())
        if "itemscope" in nodo.atributos
    ]
    # Primero las ofertas y, si no traen nada, el propio producto (hay temas que ponen el precio
    # directamente en el `Product`).
    fuentes = [*ofertas, propiedades]

    def propiedad(nombre: str, maximo: int) -> str | None:
        return next(
            (v for fuente in fuentes if (v := _primero(fuente.get(nombre, ()), maximo))), None
        )

    estado = _estado(_primero(propiedades.get("itemCondition", ()), MAX_URL))
    return ProductoJSONLD(
        nombre=_primero(propiedades.get("name", ()), MAX_NOMBRE, _valor_texto),
        precio=_precio_microdatos(fuentes),
        marca=_marca_microdatos(propiedades.get("brand", ()), presupuesto),
        mpn=_primero(propiedades.get("mpn", ()), MAX_IDENTIFICADOR),
        sku=_primero(propiedades.get("sku", ()), MAX_IDENTIFICADOR),
        gtin=next(
            (g for c in _GTIN if (g := _primero(propiedades.get(c, ()), MAX_IDENTIFICADOR))),
            None,
        ),
        estado=estado or _estado(propiedad("itemCondition", MAX_URL)),
        disponible=_disponible(propiedad("availability", MAX_URL)),
        descripcion=_descripcion(
            next(
                (
                    _valor(n)
                    for n in propiedades.get("description", ())
                    if "itemscope" not in n.atributos
                ),
                None,
            )
        ),
        imagenes=_imagenes(_valor(n) for n in propiedades.get("image", ())),
        url=next(
            (u for n in propiedades.get("url", ()) if (u := _url(_valor(n))) is not None), None
        ),
    )


def _precio_microdatos(fuentes: list[dict[str, list[Nodo]]]) -> Precio | None:
    for fuente in fuentes:
        divisa = moneda(_primero(fuente.get("priceCurrency", ()), 10))
        for clave in ("price", "lowPrice"):
            for nodo in fuente.get(clave, ()):
                precio = leer_importe(_valor(nodo), divisa)
                if precio is not None:
                    return precio
    return None


def _marca_microdatos(nodos: Iterable[Nodo], presupuesto: _Presupuesto) -> str | None:
    for nodo in nodos:
        if "itemscope" in nodo.atributos:
            nombres = _propiedades(nodo, presupuesto).get("name", ())
            marca = _primero(nombres, MAX_NOMBRE, _valor_texto)
        else:
            marca = _texto(_valor_texto(nodo), MAX_NOMBRE)
        if marca:
            return marca
    return None


# --- OpenGraph ------------------------------------------------------------------------------


def _opengraph(metas: dict[str, list[str]]) -> ProductoJSONLD | None:
    def meta(*claves: str, maximo: int = MAX_NOMBRE) -> str | None:
        return next(
            (t for c in claves for valor in metas.get(c, ()) if (t := _texto(valor, maximo))),
            None,
        )

    tipo = (meta("og:type") or "").lower()
    precios = ("product:sale_price:amount", "product:price:amount", "og:price:amount")
    if not (tipo.startswith("product") or tipo == "og:product" or any(p in metas for p in precios)):
        return None
    divisa = moneda(meta("product:price:currency", "og:price:currency"))
    precio = next(
        (
            p
            for clave in precios
            for valor in metas.get(clave, ())
            if (
                p := leer_importe(
                    valor,
                    moneda(meta("product:sale_price:currency")) or divisa
                    if clave.startswith("product:sale")
                    else divisa,
                )
            )
            is not None
        ),
        None,
    )
    return ProductoJSONLD(
        nombre=meta("og:title"),
        precio=precio,
        marca=meta("product:brand", "og:brand"),
        mpn=meta("product:mfr_part_no", maximo=MAX_IDENTIFICADOR),
        sku=meta("product:retailer_item_id", "product:sku", maximo=MAX_IDENTIFICADOR),
        gtin=meta("product:gtin", "product:ean", "product:upc", maximo=MAX_IDENTIFICADOR),
        estado=_estado(meta("product:condition", "og:condition")),
        disponible=_disponible(meta("product:availability", "og:availability")),
        descripcion=_descripcion(meta("og:description", maximo=MAX_DESCRIPCION)),
        imagenes=_imagenes([*metas.get("og:image", ()), *metas.get("og:image:url", ())]),
        url=_url(meta("og:url", maximo=MAX_URL)),
    )


# --- Comunes --------------------------------------------------------------------------------


def _completar(principal: ProductoJSONLD, extra: ProductoJSONLD) -> ProductoJSONLD:
    """`principal` con los campos vacíos rellenos desde `extra` e imágenes de los dos."""
    cambios: dict[str, object] = {
        campo.name: getattr(extra, campo.name)
        for campo in fields(principal)
        if getattr(principal, campo.name) is None
    }
    cambios["imagenes"] = _imagenes([*principal.imagenes, *extra.imagenes])
    return replace(principal, **cambios)  # type: ignore[arg-type]


def _nombre_esquema(valor: str) -> str:
    """`https://schema.org/InStock`, `schema:InStock` o `in stock` → `instock`."""
    nombre = valor.strip().rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return _ESPACIOS.sub("", nombre).lower()


def _estado(valor: str | None) -> str | None:
    return _ESTADOS.get(_nombre_esquema(valor)) if valor else None


def _disponible(valor: str | None) -> bool | None:
    if not valor:
        return None
    nombre = _nombre_esquema(valor)
    if nombre in _DISPONIBLE:
        return True
    if nombre in _NO_DISPONIBLE:
        return False
    return None


def _imagenes(valores: Iterable[str | None]) -> tuple[str, ...]:
    imagenes: list[str] = []
    for valor in valores:
        url = _url(valor)
        if url is not None and url not in imagenes:
            imagenes.append(url)
            if len(imagenes) >= MAX_IMAGENES:
                break
    return tuple(imagenes)


def _url(valor: str | None) -> str | None:
    if valor is None:
        return None
    url = valor.strip()
    if not url or len(url) > MAX_URL or any(c.isspace() for c in url):
        return None
    if url.lower().startswith(_ESQUEMAS_PROHIBIDOS):
        return None
    try:
        urlsplit(url)  # «http://[abc» haría fallar después a `urljoin`.
    except ValueError:
        return None
    return url


def _texto(valor: str | None, maximo: int) -> str | None:
    """Espacios normalizados y recortado (el analizador de HTML ya resolvió las entidades)."""
    if not valor:
        return None
    texto = _ESPACIOS.sub(" ", valor[: maximo * 4]).strip()
    return texto[:maximo].rstrip() or None


def _descripcion(valor: str | None) -> str | None:
    """Una descripción en un `content` puede traer HTML: se queda solo su texto visible."""
    if not valor:
        return None
    recortado = valor[: MAX_DESCRIPCION * 4]
    if "<" in recortado:
        recortado = analizar(recortado).texto()
    return _texto(recortado, MAX_DESCRIPCION)
