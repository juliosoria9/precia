"""Plataforma de comercio electrónico de una tienda, detectada por su portada.

Las tiendas pequeñas (el hueco de PrecIA) suelen montarse sobre Shopify, WooCommerce,
PrestaShop o Magento. Saber cuál es permite probar primero sus rutas de búsqueda conocidas y,
en Shopify y WooCommerce, su API pública de solo lectura (`fuentes.apis`), sin gastar IA.

Solo se miran señales del **marcado**, nunca el texto visible: el `meta generator`, las rutas de
los recursos que carga la página (`src` de scripts e imágenes, `href` de `link`), las clases, unos
pocos atributos propios y el código de los `<script>` de JavaScript en línea. Así un artículo que
*habla* de «cdn.shopify.com» o un `alt` con esas palabras no cambian la plataforma. Tampoco lo
hacen la foto de otra tienda enlazada en un artículo (las imágenes, `source` e `iframe` solo
cuentan si son del propio sitio) ni el `articleBody` de un JSON-LD (un `<script>` que no es
JavaScript es texto). Cada señal pesa según
lo propia que es de la plataforma; con dos plataformas empatadas no se decide (`None`), porque
equivocarse aquí haría probar rutas o una API que no existen. WordPress solo se devuelve si no
hay señales de ninguna tienda: sin WooCommerce no tiene catálogo propio.

El HTML es de una web no confiable: el árbol tiene sus topes (`extraccion.dom`), el código de
los scripts se recorta y todas las búsquedas son subcadenas o expresiones sin retroceso
cuadrático.
"""

import re
from collections.abc import Iterable
from enum import StrEnum
from urllib.parse import urlsplit

from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.listado import dominio_normalizado
from precia.navegador.tipos import Pagina

MAX_SCRIPT = 200_000
"""Caracteres de cada `<script>` en línea que se miran (las señales van al principio)."""
MAX_SCRIPTS = 1_000_000
"""Caracteres de script en línea en total."""
PESO_MINIMO = 2
"""Una tienda necesita al menos una señal propia (peso 2) para darse por detectada."""


class Plataforma(StrEnum):
    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"
    PRESTASHOP = "prestashop"
    MAGENTO = "magento"
    WORDPRESS = "wordpress"
    """WordPress sin WooCommerce: un blog o una web sin catálogo propio."""


_TIENDAS = (Plataforma.SHOPIFY, Plataforma.WOOCOMMERCE, Plataforma.PRESTASHOP, Plataforma.MAGENTO)

_Senal = tuple[str, Plataforma, int]

# `meta name="generator"`: lo pone la propia plataforma (Shopify y PrestaShop 1.7+ no siempre).
_GENERADORES: tuple[_Senal, ...] = (
    ("woocommerce", Plataforma.WOOCOMMERCE, 3),
    ("prestashop", Plataforma.PRESTASHOP, 3),
    ("shopify", Plataforma.SHOPIFY, 3),
    ("magento", Plataforma.MAGENTO, 3),
    ("wordpress", Plataforma.WORDPRESS, 1),
)
# Rutas de los recursos que carga la página (en minúsculas).
_RUTAS: tuple[_Senal, ...] = (
    ("cdn.shopify.com", Plataforma.SHOPIFY, 2),
    ("/cdn/shop/", Plataforma.SHOPIFY, 2),  # Temas nuevos: el CDN se sirve en el propio dominio.
    ("wp-content/plugins/woocommerce", Plataforma.WOOCOMMERCE, 2),
    ("/modules/ps_", Plataforma.PRESTASHOP, 2),  # Módulos nativos: ps_searchbar, ps_shoppingcart...
    ("/magento_", Plataforma.MAGENTO, 2),
    ("/mage/", Plataforma.MAGENTO, 1),  # Con las dos barras: «/img/image/logo.png» no cuenta.
    ("wp-content/", Plataforma.WORDPRESS, 1),
    ("wp-includes/", Plataforma.WORDPRESS, 1),
)
_ESTATICOS_MAGENTO = re.compile(r"/static/(?:version\d{1,20}/)?frontend/")
# Código de los scripts en línea (distingue mayúsculas: son identificadores de JavaScript).
_SCRIPTS: tuple[tuple[re.Pattern[str], Plataforma, int], ...] = (
    (re.compile(r"\bShopify\.(?:theme|shop|routes|currency)\b"), Plataforma.SHOPIFY, 2),
    (re.compile(r"\bprestashop\s{0,20}=\s{0,20}\{"), Plataforma.PRESTASHOP, 2),
    (
        re.compile(r"\b(?:wc_add_to_cart_params|woocommerce_params|wc_cart_fragments_params)\b"),
        Plataforma.WOOCOMMERCE,
        2,
    ),
    (re.compile(r"\bMagento_[A-Z]"), Plataforma.MAGENTO, 2),
    (re.compile(r"wp-(?:includes|content)\\?/"), Plataforma.WORDPRESS, 1),
)
# `type` de los `<script>` que llevan datos o plantillas, no código: su contenido es texto (el
# `articleBody` de un JSON-LD). Se excluyen estos y no se admite una lista de tipos de JavaScript
# porque los optimizadores (Rocket Loader, LiteSpeed, WP Rocket) cambian el `type` del código
# para retrasarlo (`5f1a...-text/javascript`, `litespeed/javascript`, `rocketlazyloadscript`).
# `text/x-magento-init` es JSON, pero es de Magento y ya es una señal propia.
_TIPOS_DATOS = ("json", "template", "tmpl", "html", "plain", "importmap", "speculationrules")
_HOST_VALIDO = re.compile(r"[a-z0-9_-]{1,63}(?:\.[a-z0-9_-]{1,63}){0,126}")
_IPV6 = re.compile(r"[0-9a-f:.]{2,45}")
_MONEDA_SHOPIFY = re.compile(r"\bShopify\.currency\s{0,20}=\s{0,20}\{([^{}]{0,300})\}")
_MONEDA_ACTIVA = re.compile(r'"active"\s{0,20}:\s{0,20}"([A-Z]{3})"')

# Rutas de búsqueda HTML de cada plataforma (con `{consulta}`, como `Perfil.url_busqueda`).
_RUTAS_BUSQUEDA: dict[Plataforma, tuple[str, ...]] = {
    Plataforma.SHOPIFY: ("/search?q={consulta}&type=product",),
    Plataforma.WOOCOMMERCE: ("/?s={consulta}&post_type=product",),
    Plataforma.PRESTASHOP: (
        "/busqueda?controller=search&s={consulta}",
        "/index.php?controller=search&s={consulta}",
    ),
    Plataforma.MAGENTO: ("/catalogsearch/result/?q={consulta}",),
    # El buscador de WordPress encuentra entradas y páginas: a veces es el único catálogo.
    Plataforma.WORDPRESS: ("/?s={consulta}",),
}


def detectar_plataforma(pagina: Pagina) -> Plataforma | None:
    """Plataforma de la tienda según las señales del marcado de `pagina`, o `None`."""
    raiz = analizar(pagina.html)
    sitio = _sitio(pagina.url_final or pagina.url)
    senales: dict[str, tuple[Plataforma, int]] = {}
    for nodo in raiz.descendientes():
        _senales_de_nodo(nodo, sitio, senales)
    for codigo in _scripts_en_linea(raiz):
        for patron, plataforma, peso in _SCRIPTS:
            if patron.search(codigo):
                senales[f"script:{patron.pattern}"] = (plataforma, peso)
    return _decidir(senales.values())


def moneda_shopify(pagina: Pagina) -> str | None:
    """Moneda activa de una tienda Shopify (`Shopify.currency = {"active": "EUR"}`) o `None`.

    Las respuestas de su API (`/search/suggest.json`, `/products/{handle}.js`) no llevan la
    moneda: sale de la portada.
    """
    for codigo in _scripts_en_linea(analizar(pagina.html)):
        for bloque in _MONEDA_SHOPIFY.finditer(codigo):
            if activa := _MONEDA_ACTIVA.search(bloque.group(1)):
                return activa.group(1)
    return None


def plantillas_html(plataforma: Plataforma, base: str) -> list[str]:
    """Plantillas de búsqueda HTML (con `{consulta}`) de `plataforma` en la tienda de `base`.

    Solo se usa el origen de `base` (esquema y host): las rutas de búsqueda cuelgan de la raíz.
    Una base que no es `http(s)` con host, que lleva usuario y contraseña o cuyo host tiene
    caracteres raros (barra invertida, espacios, comillas) no da plantillas: `urllib` y Chromium
    no leerían el mismo host (`https://otra.example\\.tienda.es` es `otra.example` para Chromium).
    """
    origen = origen_de(base)
    if origen is None:
        return []
    return [origen + ruta for ruta in _RUTAS_BUSQUEDA.get(plataforma, ())]


def origen_de(base: str) -> str | None:
    """`https://tienda.es` a partir de cualquier URL de la tienda, o `None` si no es válida.

    El host sale en minúsculas y en IDNA (`españa.es` → `xn--espaa-rta.es`), como en
    `extraccion.listado`, y solo con letras, cifras, `.`, `-` y `_` (o una IPv6 entre corchetes).
    """
    try:
        partes = urlsplit(base.strip())
        puerto = partes.port
    except ValueError:
        return None
    esquema = partes.scheme.lower()
    if esquema not in ("http", "https") or not partes.hostname:
        return None
    if partes.username is not None or partes.password is not None:
        return None
    host = _host_ascii(partes.hostname)
    if host is None:
        return None
    return f"{esquema}://{host}" + (f":{puerto}" if puerto is not None else "")


def _host_ascii(hostname: str) -> str | None:
    if ":" in hostname:  # IPv6: `urlsplit` ya quita los corchetes.
        return f"[{hostname}]" if _IPV6.fullmatch(hostname) else None
    try:
        host = hostname.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return host if _HOST_VALIDO.fullmatch(host) else None


def _senales_de_nodo(
    nodo: Nodo, sitio: str | None, senales: dict[str, tuple[Plataforma, int]]
) -> None:
    etiqueta = nodo.etiqueta
    if etiqueta == "meta" and (nodo.atributo("name") or "").strip().lower() == "generator":
        contenido = (nodo.atributo("content") or "").lower()
        for clave, plataforma, peso in _GENERADORES:
            if clave in contenido:
                senales[f"generator:{clave}"] = (plataforma, peso)
    recurso = _recurso(nodo, sitio)
    if recurso:
        for clave, plataforma, peso in _RUTAS:
            if clave in recurso:
                senales[f"ruta:{clave}"] = (plataforma, peso)
        if _ESTATICOS_MAGENTO.search(recurso):
            senales["ruta:static-frontend"] = (Plataforma.MAGENTO, 2)
    for clase in nodo.clases:
        if clase == "woocommerce" or clase.startswith("woocommerce-"):
            senales["clase:woocommerce"] = (Plataforma.WOOCOMMERCE, 2)
        elif clase == "shopify-section" or clase.startswith("shopify-section-"):
            senales["clase:shopify-section"] = (Plataforma.SHOPIFY, 2)
    if "data-mage-init" in nodo.atributos:
        senales["atributo:data-mage-init"] = (Plataforma.MAGENTO, 2)
    if etiqueta == "script" and (nodo.atributo("type") or "").lower() == "text/x-magento-init":
        senales["script:x-magento-init"] = (Plataforma.MAGENTO, 2)


def _recurso(nodo: Nodo, sitio: str | None) -> str:
    """Ruta del recurso que carga el elemento (no los enlaces `<a>`, que apuntan a cualquier
    sitio), en minúsculas y recortada.

    Scripts y hojas de estilo son de la plataforma vengan de donde vengan (su CDN). Imágenes,
    `source` e `iframe` pueden ser contenido de otra web, así que solo cuentan si son del sitio
    (o de un subdominio suyo, como `static1.tienda.es`).
    """
    match nodo.etiqueta:
        case "script":
            valor = nodo.atributo("src")
        case "img" | "source" | "iframe":
            valor = nodo.atributo("src")
            if valor and not _del_sitio(valor[:2_000], sitio):
                valor = None
        case "link":
            valor = nodo.atributo("href")
        case _:
            valor = None
    return valor[:2_000].lower() if valor else ""


def _sitio(url: str) -> str | None:
    """Dominio de la página sin `www.`, o `None` si no se sabe."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return dominio_normalizado(host) if host else None


def _del_sitio(valor: str, sitio: str | None) -> bool:
    try:
        partes = urlsplit(valor.strip())
        host = partes.hostname
    except ValueError:
        return False
    if not partes.scheme and not partes.netloc:
        return True  # Ruta relativa: es del propio sitio.
    if not host or sitio is None:
        return False
    propio = dominio_normalizado(host)
    return propio is not None and (propio == sitio or propio.endswith("." + sitio))


def _scripts_en_linea(raiz: Nodo) -> list[str]:
    codigos: list[str] = []
    total = 0
    for nodo in raiz.descendientes():
        if nodo.etiqueta != "script" or nodo.atributo("src"):
            continue
        tipo = (nodo.atributo("type") or "")[:200].lower()
        if any(dato in tipo for dato in _TIPOS_DATOS) and "magento" not in tipo:
            continue  # JSON-LD, plantillas...: es texto, no código de la plataforma.
        codigo = nodo.texto_crudo()[:MAX_SCRIPT]
        if total + len(codigo) > MAX_SCRIPTS:
            break
        total += len(codigo)
        codigos.append(codigo)
    return codigos


def _decidir(senales: Iterable[tuple[Plataforma, int]]) -> Plataforma | None:
    puntos: dict[Plataforma, int] = {}
    for plataforma, peso in senales:
        puntos[plataforma] = puntos.get(plataforma, 0) + peso
    tiendas = {p: puntos[p] for p in _TIENDAS if puntos.get(p, 0) >= PESO_MINIMO}
    if tiendas:
        mejor = max(tiendas.values())
        primeras = [p for p, total in tiendas.items() if total == mejor]
        return primeras[0] if len(primeras) == 1 else None
    return Plataforma.WORDPRESS if puntos.get(Plataforma.WORDPRESS, 0) > 0 else None
