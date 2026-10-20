"""Conectores de solo lectura a las APIs públicas que la propia tienda usa en su buscador.

- **WooCommerce Store API** (`/wp-json/wc/store/v1/products`): la usan los bloques de la tienda
  para pintar el catálogo; es pública, sin clave ni sesión.
- **Shopify**: `/search/suggest.json` (el buscador predictivo del tema) y, para el detalle,
  `/products/{handle}.js` (lo que usa el botón «Añadir al carrito»).

Solo se construyen URL de GET y se interpreta la respuesta; la descarga va por `navegador`, con
sus límites, su validación anti-SSRF y su User-Agent honesto. No se reutiliza ninguna clave ni
cookie: si una tienda pide credenciales para su API, no se usa.

Los valores salen de los datos de la propia tienda, así que van con `OrigenDato.API`, anclados
por construcción y con confianza 1. Aun así la respuesta es **no confiable**: tamaño máximo,
profundidad limitada por el propio `json` (un anidamiento excesivo se descarta), número de
productos, imágenes, etiquetas y atributos acotado, textos HTML pasados a texto plano y
recortados, y solo URL de la tienda (`extraccion.listado.url_de_la_tienda`). Un JSON roto,
gigante o con otra forma da una lista vacía, nunca una excepción.

**Referencia OEM.** El `sku` es el código que la tienda usa en su almacén: en un desguace suele
ser su número de stock («DESP220618»), no la referencia del fabricante. Por eso la OEM sale, por
este orden, de (1) un atributo cuyo nombre la identifica («Ref.Fabricante», «OEM», «Part
number»...), (2) el SKU **solo si está corroborado**: aparece como referencia en el título o
justo después de una etiqueta OEM en la descripción («Ref. original 5K1941005»), y (3) la
primera referencia que sigue a una etiqueta OEM en la descripción, si el producto es una sola
pieza (con varias variantes, esa referencia puede ser la de otra). Un SKU sin corroborar no es
nunca la OEM.
"""

import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from precia.emparejado.referencias import es_referencia, normalizar_referencia, referencias_en
from precia.extraccion.dom import analizar
from precia.extraccion.ficha import formatear_precio
from precia.extraccion.listado import dominio_normalizado, url_de_la_tienda
from precia.extraccion.precios import MAX_IMPORTE
from precia.extraccion.tipos import FichaExtraida, Precio
from precia.fuentes.plataformas import Plataforma, origen_de
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import CLAVES_CAMPOS, OrigenDato, ValorCampo

MARCADOR = "{consulta}"
MAX_JSON = 5_000_000
"""Caracteres de respuesta que se aceptan (el navegador ya corta en 5 MiB)."""
MAX_ENTRADAS = 100
"""Elementos de la lista de productos que se miran, aunque la API devuelva más."""
MAX_FICHAS = 10
MAX_TITULO = 300
MAX_DESCRIPCION = 5_000
MAX_MARCA = 100
MAX_REFERENCIA = 40
MAX_VALOR_ESTADO = 100
MAX_IMAGENES = 50
MAX_ETIQUETAS = 100
MAX_ATRIBUTOS = 50
MAX_TERMINOS = 50
MAX_VARIANTES = 100
MAX_UNIDAD_MENOR = 6
DIGITOS_MINIMOS_SKU_NUMERICO = 7
"""Un SKU solo de cifras más corto suele ser un número interno («1234», «000451»), no una OEM."""
MAX_OEM_ETIQUETADAS = 10
"""Referencias tras una etiqueta OEM que se leen de cada texto (las siguientes se ignoran)."""
MAX_TRAS_ETIQUETA = 60
"""Caracteres tras la etiqueta en los que se busca la referencia."""
AVISO_AGOTADO = "La tienda lo marca como agotado."
"""Va en `FichaExtraida.avisos`; el orquestador lo traduce a `disponible=False`."""

_RUTAS_API: dict[Plataforma, str] = {
    Plataforma.WOOCOMMERCE: "/wp-json/wc/store/v1/products?search={consulta}&per_page=10",
    # Corchetes ya codificados: la plantilla pasa tal cual por la validación de URL.
    Plataforma.SHOPIFY: (
        "/search/suggest.json?q={consulta}&resources%5Btype%5D=product&resources%5Blimit%5D=10"
    ),
}
_LectorPrecio = Callable[[object, str | None], Precio | None]
_ENTERO = re.compile(r"\d{1,15}")
_DECIMAL = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_MONEDA = re.compile(r"[A-Z]{3}")
_HANDLE = re.compile(r"[A-Za-z0-9_%-]{1,255}")
_ESPACIOS = re.compile(r"\s+")
_NO_ALFANUMERICO = re.compile(r"[^a-z0-9]+")
# Etiqueta OEM en un texto ya limpio (`_texto_html` deja un solo espacio entre palabras):
# «Referencia OEM», «Ref. original», «Ref.Fabricante», «Código del fabricante», «Nº de pieza»,
# «OEM», «MPN», «Part number». Sin «Ref.» ni «Referencia» a secas: las tiendas los usan también
# para su código interno. Alternativas literales y cuantificadores acotados: coste lineal.
_ETIQUETA_OEM = re.compile(
    r"\b(?:"
    r"(?:ref(?:erencias?|s)?\.?|c[oó]d(?:igo)?\.?|n[uú]m(?:ero)?\.?|n\.?[º°]\.?) ?"
    r"(?:(?:de |del )?fabricante|original(?:es)?|oem|oe)"
    r"|n(?:[uú]m(?:ero)?\.?|\.?[º°]\.?) ?(?:de )?pieza"
    r"|oem(?: ?(?:number|nr\.?|no\.?))?|mpn|part ?(?:number|no\.?|n[º°])"
    r")(?![0-9A-Za-zÀ-ÿ])[ :#.\-]{0,4}",
    re.IGNORECASE,
)
# La referencia que sigue a la etiqueta: «1K0 698 151», «0 986 494 527» (por grupos, sin
# tragarse un año detrás) o un token con separadores («5K1-941-005»). Sin «/»: separa dos
# referencias («5K1941005/5K1941006»), no los grupos de una. Cuantificadores acotados y una sola
# posición de inicio.
_REF_TRAS_ETIQUETA = re.compile(
    r"[0-9A-Za-z]{1,4}(?: (?!(?:19|20)\d\d(?!\d))\d{1,4}(?![0-9A-Za-z])){2,8}"
    r"|[0-9A-Za-z]{1,40}(?:[-.][0-9A-Za-z]{1,40}){0,8}"
)
# Palabras que, en el nombre de un atributo, dicen que la referencia no es la OEM del fabricante.
_NO_OEM = frozenset(
    {
        "equivalente",
        "equivalentes",
        "alternativa",
        "alternativas",
        "compatible",
        "compatibles",
        "interna",
        "interno",
        "local",
        "tienda",
        "almacen",
        "proveedor",
        "stock",
        "sku",
        # Otros códigos «del fabricante» que no son la referencia de la pieza.
        "color",
        "pintura",
        "motor",
        "serie",
        "bastidor",
        "chasis",
        "vin",
        "ean",
        "gtin",
        "barras",
    }
)
_REFERENCIA_PALABRAS = frozenset(
    {"ref", "refs", "referencia", "referencias", "cod", "codigo", "n", "num", "numero", "part"}
)
_FABRICANTE_PALABRAS = frozenset({"fabricante", "original", "originales", "manufacturer", "oe"})
# Parámetros que añade el buscador predictivo de Shopify para sus estadísticas.
_RASTREO_SHOPIFY = frozenset({"_pos", "_sid", "_ss", "_psq", "_fid", "_v"})

_USADO, _NUEVO, _REACONDICIONADO = "Usado", "Nuevo", "Reacondicionado"
_ESTADOS: dict[str, str] = {
    "usado": _USADO,
    "usada": _USADO,
    "usados": _USADO,
    "usadas": _USADO,
    "segunda mano": _USADO,
    "de segunda mano": _USADO,
    "ocasion": _USADO,
    "de ocasion": _USADO,
    "como nuevo": _USADO,
    "como nueva": _USADO,
    "seminuevo": _USADO,
    "seminueva": _USADO,
    "used": _USADO,
    "nuevo": _NUEVO,
    "nueva": _NUEVO,
    "nuevos": _NUEVO,
    "nuevas": _NUEVO,
    "new": _NUEVO,
    "reacondicionado": _REACONDICIONADO,
    "reacondicionada": _REACONDICIONADO,
    "reconstruido": _REACONDICIONADO,
    "reconstruida": _REACONDICIONADO,
    "remanufacturado": _REACONDICIONADO,
    "remanufacturada": _REACONDICIONADO,
    "refurbished": _REACONDICIONADO,
}
# Frases literales, de la más larga a la más corta («como nuevo» antes que «nuevo»): sin
# cuantificadores anidados, el coste es lineal.
_FRASE_ESTADO = re.compile(r"\b(?:" + "|".join(sorted(_ESTADOS, key=len, reverse=True)) + r")\b")
_NOMBRES_ESTADO = frozenset({"estado", "condicion", "condition", "estado de la pieza"})
_NOMBRES_MARCA = frozenset(
    {"marca", "brand", "fabricante", "marca fabricante", "marca del fabricante"}
)
_NOMBRES_OEM = frozenset(
    {
        "referencia oem",
        "ref oem",
        "oem",
        "referencia original",
        "ref original",
        "numero oem",
        "n oem",
        "codigo oem",
        "referencia fabricante",
        "referencia del fabricante",
        "numero de pieza",
        "n de pieza",
        "mpn",
        "part number",
        "referencia",
    }
)


def plantilla_api(plataforma: Plataforma, base: str) -> str | None:
    """URL de búsqueda de la API pública de `plataforma` con `{consulta}`, o `None` si no hay.

    Tiene la misma forma que `Perfil.url_busqueda`, así que puede guardarse en un perfil `api_*`.
    """
    ruta = _RUTAS_API.get(plataforma)
    origen = origen_de(base)
    return origen + ruta if ruta is not None and origen is not None else None


def url_api(plataforma: Plataforma, base: str, consulta: str) -> str | None:
    """URL de búsqueda en la API de la tienda, con la consulta codificada entera.

    Se codifica todo (`&`, `=`, `#`, `/`...): la consulta no puede añadir parámetros ni
    cambiar la ruta.
    """
    plantilla = plantilla_api(plataforma, base)
    return plantilla.replace(MARCADOR, quote_plus(consulta)) if plantilla is not None else None


def url_detalle(plataforma: Plataforma, url_ficha: str, dominio: str) -> str | None:
    """`https://tienda/products/{handle}.js` de una ficha de Shopify, o `None`.

    Acepta `/products/{handle}` con o sin prefijo (`/collections/x/`, `/es/`) y solo de la
    tienda de `dominio`. Las demás plataformas no tienen un detalle público equivalente.
    """
    dominio_tienda = dominio_normalizado(dominio)
    if plataforma is not Plataforma.SHOPIFY or not dominio_tienda:
        return None
    url = url_de_la_tienda(url_ficha, f"https://{dominio_tienda}/", dominio_tienda)
    if url is None:
        return None
    partes = urlsplit(url)
    segmentos = partes.path.rstrip("/").split("/")
    if len(segmentos) < 3 or segmentos[-2] != "products":
        return None
    handle = segmentos[-1]
    if _HANDLE.fullmatch(handle) is None:
        return None
    return urlunsplit((partes.scheme, partes.netloc, f"/products/{handle}.js", "", ""))


def ofertas_api(
    plataforma: Plataforma,
    pagina: Pagina,
    dominio: str,
    campos: Sequence[str],
    *,
    moneda: str = "EUR",
    maximo: int = MAX_FICHAS,
) -> list[FichaExtraida]:
    """Fichas de la respuesta JSON de la API de la tienda (`pagina.html` lleva el JSON).

    Solo se lee una respuesta 2xx servida por la propia tienda (`pagina.url_final` de
    `dominio`): un error o una redirección a otra web dan una lista vacía.

    `moneda` es la de la tienda Shopify (sus respuestas no la llevan; ver
    `plataformas.moneda_shopify`); WooCommerce trae la suya. El precio de la oferta se da
    siempre; en `campos` de cada ficha, solo los pedidos. Nunca lanza por la respuesta.
    """
    dominio_tienda = dominio_normalizado(dominio) if dominio else None
    if dominio_tienda is None or maximo <= 0 or not 200 <= pagina.estado < 300:
        return []
    base = pagina.url_final or pagina.url
    # El descargador sigue redirecciones a cualquier host público: si la respuesta la sirvió
    # otra web, su JSON no es de la tienda aunque sus `permalink` digan que sí.
    if url_de_la_tienda(base, base, dominio_tienda) is None:
        return []
    dato = _cargar(pagina.html)
    if dato is None:
        return []
    # URL ya leídas: un producto repetido se descarta antes de pasar su HTML a texto.
    vistas: set[str] = set()
    productos: Iterable[_Producto | None]
    match plataforma:
        case Plataforma.WOOCOMMERCE:
            productos = (_producto_woo(p, base, dominio_tienda, vistas) for p in _lista_woo(dato))
        case Plataforma.SHOPIFY:
            moneda_valida = moneda if _MONEDA.fullmatch(moneda) else None
            productos = _productos_shopify(dato, base, dominio_tienda, moneda_valida, vistas)
        case _:
            return []
    pedidos, avisos = _campos_validos(campos)
    fichas: list[FichaExtraida] = []
    for producto in productos:
        if producto is None:
            continue
        fichas.append(producto.ficha(pedidos, avisos))
        if len(fichas) >= maximo:
            break
    return fichas


@dataclass(slots=True)
class _Producto:
    """Producto ya leído de cualquiera de las APIs, con los valores limpios."""

    titulo: str
    url: str
    precio: Precio | None = None
    referencia: str | None = None
    marca: str | None = None
    estado: str | None = None
    descripcion: str | None = None
    imagenes: int = 0
    agotado: bool = False
    avisos: list[str] = field(default_factory=list)

    def ficha(self, pedidos: tuple[str, ...], avisos: Sequence[str]) -> FichaExtraida:
        valores = {
            "precio": formatear_precio(self.precio) if self.precio is not None else None,
            "referencia_oem": self.referencia,
            "marca": self.marca,
            "estado": self.estado,
            "descripcion": self.descripcion,
            "fotos": _fotos(self.imagenes) if self.imagenes else None,
        }
        campos = {
            campo: ValorCampo(valor, OrigenDato.API, anclado=True, confianza=1.0)
            for campo in pedidos
            if (valor := valores.get(campo))
        }
        lista_avisos = [*avisos, *self.avisos]
        if self.agotado:
            lista_avisos.append(AVISO_AGOTADO)
        return FichaExtraida(self.url, self.titulo, self.precio, campos, tuple(lista_avisos))


# --- WooCommerce ----------------------------------------------------------------------------


def _lista_woo(dato: object) -> list[dict[str, Any]]:
    # `/products/{id}` devuelve el producto suelto; el error («rest_no_route») no tiene `prices`.
    if isinstance(dato, dict):
        return [dato] if "prices" in dato and "permalink" in dato else []
    return _dicts(dato, MAX_ENTRADAS)


def _producto_woo(
    datos: dict[str, Any], base: str, dominio: str, vistas: set[str]
) -> _Producto | None:
    url = _url(datos.get("permalink"), base, dominio)
    if url is None or url in vistas:
        return None
    titulo = _texto_html(datos.get("name"), MAX_TITULO)
    if not titulo:
        return None
    vistas.add(url)
    atributos = _atributos_woo(datos.get("attributes"))
    marcas = (_texto_html(m.get("name"), MAX_MARCA) for m in _dicts(datos.get("brands")))
    etiquetas = (m.get("name") for m in _dicts(datos.get("tags"), MAX_ETIQUETAS))
    claves = ("short_description", "description")
    textos = [_texto_html(datos.get(clave), MAX_DESCRIPCION) for clave in claves]
    oem_atributos = [v for nombre, valores in atributos if _es_nombre_oem(nombre) for v in valores]
    return _Producto(
        titulo=titulo,
        url=url,
        precio=_precio_woo(datos.get("prices")),
        referencia=_referencia_oem(
            oem_atributos, datos.get("sku"), titulo, textos, una_pieza=not _variable_woo(datos)
        ),
        marca=_primero(
            [*marcas, *(_texto_html(v, MAX_MARCA) for v in _valores(atributos, _NOMBRES_MARCA))]
        ),
        estado=_estado(atributos, etiquetas),
        descripcion=_primero(textos),
        imagenes=sum(
            1
            for imagen in _dicts(datos.get("images"), MAX_IMAGENES)
            if _no_vacio(imagen.get("src"))
        ),
        agotado=datos.get("is_in_stock") is False,
    )


def _variable_woo(datos: dict[str, Any]) -> bool:
    """Producto con variaciones: su precio es el de una cualquiera (izquierdo o derecho)."""
    return datos.get("type") == "variable" or bool(_lista(datos.get("variations"), 1))


def _precio_woo(precios: object) -> Precio | None:
    """`prices.price` en unidades menores: `"8990"` con `currency_minor_unit` 2 → 89,90."""
    if not isinstance(precios, dict):
        return None
    importe = precios.get("price")
    unidad = precios.get("currency_minor_unit")
    moneda = precios.get("currency_code")
    if not isinstance(importe, str) or _ENTERO.fullmatch(importe) is None:
        return None
    # `type(...) is int`: un `true` de JSON es `bool`, que también es `int` en Python.
    if type(unidad) is not int or not 0 <= unidad <= MAX_UNIDAD_MENOR:
        return None
    if not isinstance(moneda, str) or _MONEDA.fullmatch(moneda) is None:
        return None
    return _precio(Decimal(importe).scaleb(-unidad), moneda, importe)


def _atributos_woo(valor: object) -> list[tuple[str, list[object]]]:
    """`[{"name": "Estado", "terms": [{"name": "Usado"}]}]` → `[("estado", ["Usado"])]`."""
    atributos: list[tuple[str, list[object]]] = []
    for atributo in _dicts(valor, MAX_ATRIBUTOS):
        nombre = atributo.get("name")
        if isinstance(nombre, str):
            terminos = [t.get("name") for t in _dicts(atributo.get("terms"), MAX_TERMINOS)]
            atributos.append((_normalizar(nombre[:MAX_VALOR_ESTADO]), terminos))
    return atributos


# --- Shopify --------------------------------------------------------------------------------


def _productos_shopify(
    dato: object, base: str, dominio: str, moneda: str | None, vistas: set[str]
) -> Iterable[_Producto | None]:
    if not isinstance(dato, dict):
        return []
    recursos = dato.get("resources")
    if recursos is not None:  # /search/suggest.json
        resultados = recursos.get("results") if isinstance(recursos, dict) else None
        lista = resultados.get("products") if isinstance(resultados, dict) else None
        return (
            _producto_shopify(p, base, dominio, moneda, _precio_texto, vistas)
            for p in _dicts(lista, MAX_ENTRADAS)
        )
    if "variants" in dato and "title" in dato:  # /products/{handle}.js
        return [_producto_shopify(dato, base, dominio, moneda, _precio_centimos, vistas)]
    return []


def _producto_shopify(
    datos: dict[str, Any],
    base: str,
    dominio: str,
    moneda: str | None,
    leer: _LectorPrecio,
    vistas: set[str],
) -> _Producto | None:
    """Producto del buscador predictivo (precio en texto, `"89.90"`) o del `.js` (en céntimos).

    La variante es la primera disponible con precio; si no hay, la primera con precio.
    """
    url = _url(datos.get("url"), base, dominio)
    if url is None:
        return None
    url = _sin_rastreo_shopify(url)
    if url in vistas:
        return None
    titulo = _texto_html(datos.get("title"), MAX_TITULO)
    if not titulo:
        return None
    vistas.add(url)
    variantes = _dicts(datos.get("variants"), MAX_VARIANTES)
    con_precio = [(v, p) for v in variantes if (p := leer(v.get("price"), moneda)) is not None]
    elegida = next((vp for vp in con_precio if vp[0].get("available") is True), None)
    elegida = elegida or (con_precio[0] if con_precio else None)
    precio = elegida[1] if elegida else leer(datos.get("price"), moneda)
    opciones = _opciones_shopify(datos.get("options"))
    textos = [_texto_html(datos.get(clave), MAX_DESCRIPCION) for clave in ("description", "body")]
    return _Producto(
        titulo=titulo,
        url=url,
        precio=precio,
        referencia=_referencia_shopify(elegida[0] if elegida else None, variantes, titulo, textos),
        marca=_texto_html(datos.get("vendor"), MAX_MARCA),
        estado=_estado(opciones, _lista(datos.get("tags"), MAX_ETIQUETAS)),
        descripcion=_primero(textos),
        imagenes=_imagenes_shopify(datos),
        agotado=datos.get("available") is False,
    )


def _referencia_shopify(
    elegida: dict[str, Any] | None,
    variantes: list[dict[str, Any]],
    titulo: str,
    textos: Sequence[str | None],
) -> str | None:
    """OEM de la variante cuyo precio se da (o de la única variante, si no hay precio).

    Shopify no publica atributos en estas respuestas: la OEM es el SKU de esa variante si el
    título o la descripción lo corroboran, o la referencia etiquetada de la descripción. Las
    variantes pueden ser piezas distintas (lado izquierdo y derecho, con OEM distinta): ni el SKU
    de otra variante ni, con varias variantes, la referencia de la descripción (puede ser la de
    otra) se pegan al precio de esta.
    """
    if elegida is None and len(variantes) == 1:
        elegida = variantes[0]
    sku = elegida.get("sku") if elegida is not None else None
    return _referencia_oem([], sku, titulo, textos, una_pieza=len(variantes) <= 1)


def _precio_texto(valor: object, moneda: str | None) -> Precio | None:
    """Precio del buscador predictivo: texto con punto decimal (`"89.90"`)."""
    if moneda is None or not isinstance(valor, str) or _DECIMAL.fullmatch(valor) is None:
        return None
    return _precio(Decimal(valor), moneda, valor)


def _precio_centimos(valor: object, moneda: str | None) -> Precio | None:
    """Precio de `/products/{handle}.js`: entero en céntimos (`8990`)."""
    if moneda is None or type(valor) is not int:
        return None
    return _precio(Decimal(valor).scaleb(-2), moneda, str(valor))


def _opciones_shopify(valor: object) -> list[tuple[str, list[object]]]:
    """`options` del `.js` (`[{"name": "Estado", "values": ["Usado"]}]`), como atributos."""
    opciones: list[tuple[str, list[object]]] = []
    for opcion in _dicts(valor, MAX_ATRIBUTOS):
        nombre = opcion.get("name")
        if isinstance(nombre, str):
            valores = _lista(opcion.get("values"), MAX_TERMINOS)
            opciones.append((_normalizar(nombre[:MAX_VALOR_ESTADO]), valores))
    return opciones


def _imagenes_shopify(datos: dict[str, Any]) -> int:
    imagenes = _lista(datos.get("images"), MAX_IMAGENES)
    if imagenes:
        return sum(1 for imagen in imagenes if _no_vacio(imagen))
    destacada = datos.get("featured_image")
    if isinstance(destacada, dict):
        destacada = destacada.get("url")
    return 1 if _no_vacio(datos.get("image")) or _no_vacio(destacada) else 0


def _sin_rastreo_shopify(url: str) -> str:
    partes = urlsplit(url)
    if not partes.query:
        return url
    parametros = parse_qsl(partes.query, keep_blank_values=True)
    limpios = [(k, v) for k, v in parametros if k not in _RASTREO_SHOPIFY]
    if len(limpios) == len(parametros):
        return url
    return urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(limpios), ""))


# --- Comunes --------------------------------------------------------------------------------


def _cargar(texto: str) -> object | None:
    """JSON de la respuesta o `None` si es demasiado grande, está roto o anida sin fin."""
    if not isinstance(texto, str) or len(texto) > MAX_JSON:
        return None
    try:
        dato: object = json.loads(texto, parse_constant=_constante_no_valida)
    except (ValueError, RecursionError):
        return None
    return dato


def _constante_no_valida(nombre: str) -> object:
    raise ValueError(f"Constante no admitida en JSON: {nombre}")


def _campos_validos(campos: Sequence[str]) -> tuple[tuple[str, ...], list[str]]:
    validos: list[str] = []
    avisos: list[str] = []
    for campo in campos:
        if campo not in CLAVES_CAMPOS:
            avisos.append(f"Campo desconocido «{campo}»: se ignora.")
        elif campo not in validos:
            validos.append(campo)
    return tuple(validos), avisos


def _url(valor: object, base: str, dominio: str) -> str | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    return url_de_la_tienda(valor[:4_000], base, dominio)


def _precio(importe: Decimal, moneda: str, texto: str) -> Precio | None:
    if not importe.is_finite() or not 0 < importe < MAX_IMPORTE:
        return None
    return Precio(importe, moneda, texto)


def _referencia_oem(
    atributos: Sequence[object],
    sku: object,
    titulo: str,
    textos: Sequence[str | None],
    *,
    una_pieza: bool = True,
) -> str | None:
    """OEM del producto: atributo OEM, SKU corroborado o referencia etiquetada del texto.

    El SKU vale solo si su forma normalizada aparece como referencia en el título o justo detrás
    de una etiqueta OEM de la descripción: que la tienda lo muestre como referencia es la prueba
    de que no es solo su código de almacén. El título basta porque el emparejado ya lee las
    referencias del título (`emparejado.agrupador`): aceptarlo no añade un riesgo nuevo.
    """
    etiquetadas = [r for texto in textos if texto for r in _oem_etiquetadas(texto)]
    corroboradas = referencias_en(titulo) | {normalizar_referencia(r) for r in etiquetadas}
    candidatas: list[object] = list(atributos)
    if isinstance(sku, str) and normalizar_referencia(sku[: MAX_REFERENCIA * 2]) in corroboradas:
        candidatas.append(sku)
    if una_pieza:
        candidatas.extend(etiquetadas)
    return _referencia(candidatas)


def _oem_etiquetadas(texto: str) -> list[str]:
    """Referencias que siguen a una etiqueta OEM («Ref. original 1K0 698 151» → «1K0 698 151»)."""
    encontradas: list[str] = []
    for etiqueta in _ETIQUETA_OEM.finditer(texto[:MAX_DESCRIPCION]):
        inicio = etiqueta.end()
        ref = _REF_TRAS_ETIQUETA.match(texto, inicio, inicio + MAX_TRAS_ETIQUETA)
        if ref is not None:
            encontradas.append(ref.group())
            if len(encontradas) >= MAX_OEM_ETIQUETADAS:
                break
    return encontradas


def _es_nombre_oem(nombre: str) -> bool:
    """Indica si el nombre (ya `_normalizar`) de un atributo es el de la referencia OEM.

    Además de los nombres conocidos, vale una palabra de referencia con otra de fabricante
    («Ref.Fabricante», «Código del fabricante», «Nº OEM»); no, si alguna palabra dice que es
    otra cosa («Ref.Local», «Ref.Equivalente», «Referencia OEM equivalente»).
    """
    palabras = set(nombre.split())
    if palabras & _NO_OEM:
        return False
    if nombre in _NOMBRES_OEM or palabras & {"oem", "mpn"}:
        return True
    return bool(palabras & _REFERENCIA_PALABRAS and palabras & _FABRICANTE_PALABRAS)


def _referencia(candidatas: Iterable[object]) -> str | None:
    """Primera candidata (atributo OEM, SKU corroborado o texto) con forma de referencia OEM.

    Además de `es_referencia`, se exige que `referencias_en` la reconozca (descarta medidas,
    años y precios: «100MM», «2015») y que un SKU solo de cifras tenga al menos 7.
    """
    for candidata in candidatas:
        if not isinstance(candidata, str):
            continue
        texto = _ESPACIOS.sub(" ", candidata).strip()
        if not texto or len(texto) > MAX_REFERENCIA:
            continue
        normal = normalizar_referencia(texto)
        if not es_referencia(normal):
            continue
        if normal.isdigit() and len(normal) < DIGITOS_MINIMOS_SKU_NUMERICO:
            continue
        if normal in referencias_en(texto):
            return texto
    return None


def _estado(atributos: list[tuple[str, list[object]]], etiquetas: Iterable[object]) -> str | None:
    """`Nuevo`, `Usado` o `Reacondicionado` (como `extraccion.jsonld`), o `None`.

    En un atributo «Estado»/«Condición» basta con que el valor contenga la frase («Pieza
    usada»); una etiqueta tiene que ser exactamente la frase («Nuevo modelo 2024» no cuenta).
    Si las fuentes se contradicen, no se decide.
    """
    encontrados: set[str] = set()
    for valor in _valores(atributos, _NOMBRES_ESTADO):
        if isinstance(valor, str) and len(valor) <= MAX_VALOR_ESTADO:
            encontrados.update(
                _ESTADOS[m.group()] for m in _FRASE_ESTADO.finditer(_normalizar(valor))
            )
    if not encontrados:
        for etiqueta in etiquetas:
            if not isinstance(etiqueta, str) or len(etiqueta) > MAX_VALOR_ESTADO:
                continue
            if (estado := _ESTADOS.get(_normalizar(etiqueta))) is not None:
                encontrados.add(estado)
    return encontrados.pop() if len(encontrados) == 1 else None


def _valores(atributos: list[tuple[str, list[object]]], nombres: frozenset[str]) -> list[object]:
    return [valor for nombre, valores in atributos if nombre in nombres for valor in valores]


def _texto_html(valor: object, maximo: int) -> str | None:
    """Texto plano de un valor que puede traer HTML o entidades, recortado a `maximo`."""
    if not isinstance(valor, str):
        return None
    recortado = valor[: maximo * 4]
    if "<" in recortado or "&" in recortado:
        recortado = analizar(recortado).texto()
    limpio = _ESPACIOS.sub(" ", recortado).strip()
    if len(limpio) <= maximo:
        return limpio or None
    cortado = limpio[:maximo]
    corte = cortado.rfind(" ")
    return (cortado[:corte] if corte > maximo // 2 else cortado).rstrip() or None


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y solo letras y cifras separadas por un espacio."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c)
    )
    return _NO_ALFANUMERICO.sub(" ", sin_acentos).strip()


def _primero(valores: Iterable[str | None]) -> str | None:
    return next((v for v in valores if v), None)


def _dicts(valor: object, maximo: int = MAX_ENTRADAS) -> list[dict[str, Any]]:
    return [v for v in _lista(valor, maximo) if isinstance(v, dict)]


def _lista(valor: object, maximo: int) -> list[object]:
    return list(valor[:maximo]) if isinstance(valor, list) else []


def _no_vacio(valor: object) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _fotos(numero: int) -> str:
    return "1 foto" if numero == 1 else f"{numero} fotos"
