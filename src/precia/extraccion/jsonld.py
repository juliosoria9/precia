"""Productos `schema.org` en los bloques `<script type="application/ld+json">` de una página.

Es la primera vía de la cascada de extracción: muchas tiendas (WooCommerce, PrestaShop,
Shopify...) publican el producto con su oferta para los buscadores. El JSON viene de la web y
no es de fiar: un bloque mal formado, demasiado grande o demasiado anidado se ignora sin error.
"""

import json
import re
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.precios import MAX_IMPORTE, leer_precio
from precia.extraccion.tipos import Precio, ProductoJSONLD

MAX_BLOQUE = 1_000_000
MAX_BLOQUES = 50
MAX_PRODUCTOS = 100
MAX_PROFUNDIDAD = 64
MAX_IMAGENES = 50
MAX_NOMBRE = 300
MAX_DESCRIPCION = 5_000
MAX_IDENTIFICADOR = 100
MAX_URL = 2_000

_TIPOS_PRODUCTO = frozenset({"Product", "IndividualProduct", "ProductModel"})
_ESTADOS = {
    "NewCondition": "Nuevo",
    "UsedCondition": "Usado",
    "RefurbishedCondition": "Reacondicionado",
}
_DISPONIBLE = frozenset(
    {"InStock", "LimitedAvailability", "InStoreOnly", "OnlineOnly", "PreOrder", "PreSale"}
    | {"BackOrder", "MadeToOrder"}
)
_NO_DISPONIBLE = frozenset({"OutOfStock", "SoldOut", "Discontinued"})
_ENVOLTORIOS = re.compile(r"^\s*(?://\s*)?(?:<!--|<!\[CDATA\[)|(?://\s*)?(?:-->|\]\]>)\s*$")
_IMPORTE_JSON = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_MONEDA = re.compile(r"[A-Z]{3}")
_ESQUEMAS_PROHIBIDOS = ("javascript:", "data:", "vbscript:", "file:")
_ESPACIOS = re.compile(r"\s+")


def productos_jsonld(html: str) -> list[ProductoJSONLD]:
    """Productos de los bloques JSON-LD de `html`, en orden del documento."""
    return productos_de_arbol(analizar(html))


def productos_de_arbol(raiz: Nodo) -> list[ProductoJSONLD]:
    """Igual que `productos_jsonld`, pero sobre un árbol ya analizado."""
    productos: list[ProductoJSONLD] = []
    for texto in bloques_jsonld(raiz):
        dato = _cargar(texto)
        if dato is None:
            continue
        for objeto in _productos_en(dato):
            productos.append(_producto(objeto))
            if len(productos) >= MAX_PRODUCTOS:
                return productos
    return productos


def bloques_jsonld(raiz: Nodo) -> list[str]:
    """Texto en bruto de cada bloque JSON-LD (hasta `MAX_BLOQUES`)."""
    bloques: list[str] = []
    for nodo in raiz.descendientes():
        if nodo.etiqueta != "script":
            continue
        tipo = nodo.atributos.get("type", "").split(";")[0].strip().lower()
        if tipo == "application/ld+json":
            bloques.append(nodo.texto_crudo())
            if len(bloques) >= MAX_BLOQUES:
                break
    return bloques


def _cargar(texto: str) -> Any:
    if len(texto) > MAX_BLOQUE:
        return None
    limpio = _ENVOLTORIOS.sub("", texto.strip())
    try:
        # strict=False: hay tiendas que meten saltos de línea sin escapar dentro de las cadenas.
        return json.loads(limpio, strict=False)
    except (ValueError, RecursionError):
        return None


def _productos_en(dato: Any) -> Iterator[dict[str, Any]]:
    """Objetos de tipo producto a cualquier profundidad (en preorden), sin pasar del tope."""
    pila: list[tuple[Any, int]] = [(dato, 0)]
    while pila:
        actual, profundidad = pila.pop()
        if profundidad > MAX_PROFUNDIDAD:
            continue
        if isinstance(actual, dict):
            if _es_producto(actual):
                yield actual
            hijos = list(actual.values())
        elif isinstance(actual, list):
            hijos = actual
        else:
            continue
        pila.extend((hijo, profundidad + 1) for hijo in reversed(hijos))


def _es_producto(objeto: dict[str, Any]) -> bool:
    tipos = objeto.get("@type")
    lista = tipos if isinstance(tipos, list) else [tipos]
    return any(isinstance(t, str) and _nombre_esquema(t) in _TIPOS_PRODUCTO for t in lista)


def _nombre_esquema(valor: str) -> str:
    """`https://schema.org/InStock`, `schema:InStock` o `InStock` → `InStock`."""
    return valor.strip().rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _producto(objeto: dict[str, Any]) -> ProductoJSONLD:
    ofertas = _ofertas(objeto.get("offers"))
    oferta, precio = next(
        ((o, p) for o in ofertas if (p := _precio_oferta(o)) is not None),
        (ofertas[0] if ofertas else {}, None),
    )
    estado = _estado(objeto.get("itemCondition"))
    if estado is None:
        estado = _estado(oferta.get("itemCondition"))
    disponible = _disponible(oferta.get("availability"))
    if disponible is None:
        disponible = _disponible(objeto.get("availability"))
    return ProductoJSONLD(
        nombre=_texto_simple(objeto.get("name"), MAX_NOMBRE),
        precio=precio,
        marca=_marca(objeto.get("brand")),
        mpn=_identificador(objeto.get("mpn")),
        sku=_identificador(objeto.get("sku")),
        gtin=next(
            (
                g
                for clave in ("gtin", "gtin13", "gtin14", "gtin12", "gtin8")
                if (g := _identificador(objeto.get(clave))) is not None
            ),
            None,
        ),
        estado=estado,
        disponible=disponible,
        descripcion=_descripcion(objeto.get("description")),
        imagenes=_imagenes(objeto.get("image")),
        url=_url(objeto.get("url")) or _url(oferta.get("url")),
    )


def _ofertas(valor: Any) -> list[dict[str, Any]]:
    lista = valor if isinstance(valor, list) else [valor]
    return [o for o in lista if isinstance(o, dict)]


def _precio_oferta(oferta: dict[str, Any], profundidad: int = 0) -> Precio | None:
    moneda = _moneda(oferta.get("priceCurrency"))
    claves = ("lowPrice", "price") if _es_agregada(oferta) else ("price",)
    for clave in claves:
        precio = _leer_importe(oferta.get(clave), moneda)
        if precio is not None:
            return precio
    for especificacion in _ofertas(oferta.get("priceSpecification")):
        precio = _leer_importe(
            especificacion.get("price"), _moneda(especificacion.get("priceCurrency")) or moneda
        )
        if precio is not None:
            return precio
    if profundidad < 2:
        for interna in _ofertas(oferta.get("offers")):
            precio = _precio_oferta(interna, profundidad + 1)
            if precio is not None:
                return precio
    return None


def _es_agregada(oferta: dict[str, Any]) -> bool:
    tipo = oferta.get("@type")
    return (isinstance(tipo, str) and _nombre_esquema(tipo) == "AggregateOffer") or (
        "lowPrice" in oferta
    )


def _leer_importe(valor: Any, moneda: str | None) -> Precio | None:
    importe: Decimal | None = None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        texto = str(valor)
        try:
            importe = Decimal(texto)
        except InvalidOperation:
            return None
    elif isinstance(valor, str) and len(valor) <= 50:
        texto = valor.strip()
        if _IMPORTE_JSON.fullmatch(texto):
            importe = Decimal(texto)
        elif (leido := leer_precio(texto)) is not None:
            importe = leido.importe
            moneda = moneda or leido.moneda
    else:
        return None
    if importe is None or not importe.is_finite() or importe <= 0 or importe >= MAX_IMPORTE:
        return None
    return Precio(importe, moneda or "EUR", texto)


def _moneda(valor: Any) -> str | None:
    if isinstance(valor, str) and _MONEDA.fullmatch(valor.strip().upper()):
        return valor.strip().upper()
    return None


def _estado(valor: Any) -> str | None:
    if isinstance(valor, dict):
        valor = valor.get("@id") or valor.get("name")
    if isinstance(valor, str):
        return _ESTADOS.get(_nombre_esquema(valor))
    return None


def _disponible(valor: Any) -> bool | None:
    if isinstance(valor, dict):
        valor = valor.get("@id") or valor.get("name")
    if not isinstance(valor, str):
        return None
    nombre = _nombre_esquema(valor)
    if nombre in _DISPONIBLE:
        return True
    if nombre in _NO_DISPONIBLE:
        return False
    return None


def _marca(valor: Any) -> str | None:
    for candidata in valor if isinstance(valor, list) else [valor]:
        if isinstance(candidata, dict):
            candidata = candidata.get("name")
        texto = _texto_simple(candidata, MAX_NOMBRE)
        if texto:
            return texto
    return None


def _identificador(valor: Any) -> str | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        valor = str(valor)
    return _texto_simple(valor, MAX_IDENTIFICADOR)


def _imagenes(valor: Any) -> tuple[str, ...]:
    imagenes: list[str] = []
    for candidata in valor if isinstance(valor, list) else [valor]:
        if isinstance(candidata, dict):
            candidata = candidata.get("url") or candidata.get("contentUrl")
        url = _url(candidata)
        if url is not None and url not in imagenes:
            imagenes.append(url)
            if len(imagenes) >= MAX_IMAGENES:
                break
    return tuple(imagenes)


def _url(valor: Any) -> str | None:
    if not isinstance(valor, str):
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


def _texto_simple(valor: Any, maximo: int) -> str | None:
    if not isinstance(valor, str):
        return None
    texto = _ESPACIOS.sub(" ", unescape(valor[: maximo * 4])).strip()
    return texto[:maximo].rstrip() or None


def _descripcion(valor: Any) -> str | None:
    """Las descripciones suelen traer HTML y entidades: se queda solo su texto visible."""
    if not isinstance(valor, str):
        return None
    recortado = valor[: MAX_DESCRIPCION * 4]
    if "<" in recortado or "&" in recortado:
        limpio = analizar(recortado).texto()
    else:
        limpio = _ESPACIOS.sub(" ", recortado).strip()
    return limpio[:MAX_DESCRIPCION].rstrip() or None
