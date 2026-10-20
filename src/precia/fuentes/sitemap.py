"""Catálogo de una tienda por su `sitemap.xml` (ADR-018, nota 16 §B): funciones puras.

Muchas tiendas pequeñas no tienen buscador, pero casi todas publican un `sitemap.xml` para los
robots (WordPress/Yoast, WooCommerce, Shopify, PrestaShop y Magento lo generan solos). De él se
sacan solo la URL de cada ficha, las palabras de su ruta y el `lastmod`, nunca precios ni
descripciones: los precios se leen siempre en vivo de unas pocas fichas.

El sitemap es un fichero de una web **no confiable** y puede pesar hasta 20 MiB, así que el
lector es propio y lineal (sin `xml.etree` ni ningún otro analizador XML de la stdlib):

- rechaza cualquier `<!DOCTYPE` o `<!ENTITY` (nada de DTD ni de entidades externas o
  recursivas: «billion laughs», XXE) y devuelve un `Sitemap` vacío;
- decodifica solo las 5 entidades predefinidas de XML y las numéricas, con longitud acotada;
- recorre el marcado con una sola expresión con cuantificadores posesivos (sin retroceso) y
  se para en el primer marcado sin cerrar, con topes de caracteres, etiquetas, profundidad y
  entradas (`MAX_ENTRADAS_SITEMAP`, el máximo del protocolo);
- un XML roto o recortado da las entradas **completas** anteriores al corte (con
  `recortado=True`) y nunca lanza una excepción.

Las URL que se leen aquí todavía no son de fiar: antes de descargarlas pasan por el filtro de
mismo dominio (`de_la_tienda`, `extraccion.listado.url_de_la_tienda`) y por `validar_url`.
"""

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

from precia.emparejado.referencias import referencias_en
from precia.emparejado.texto import normalizar_titulo
from precia.extraccion.listado import MAX_URL, dominio_normalizado, url_de_la_tienda
from precia.fuentes.plataformas import Plataforma, origen_de

MAX_ENTRADAS_SITEMAP = 50_000
"""Entradas por fichero: el máximo del protocolo sitemaps.org."""
MAX_FICHEROS_SITEMAP = 10
"""Ficheros (índices y `urlset`) que se descargan como mucho al configurar una tienda."""
MAX_CARACTERES_SITEMAP = 20 * 1024 * 1024
"""Caracteres que se leen de un sitemap (como `MAX_BYTES_SITEMAP` del navegador); el resto se
ignora y el sitemap queda `recortado`."""
MAX_ETIQUETAS_SITEMAP = 2_000_000
"""Etiquetas que se procesan por fichero. 50 000 entradas con imagen, idiomas y `PageMap` dan
poco más de 1 millón; más es relleno hostil."""
MAX_PROFUNDIDAD_SITEMAP = 16
"""Anidamiento máximo. Un sitemap real no pasa de 5 (`urlset > url > video:video > ...`)."""
MAX_CAMPO = 4 * MAX_URL
"""Caracteres sin decodificar de un `loc` o un `lastmod` (`&amp;` ocupa 5)."""
MAX_LASTMOD = 40
MAX_DECLARADOS = 20
"""Sitemaps declarados en robots.txt que se tienen en cuenta (como `ReglasRobots.sitemaps`)."""
MAX_PALABRAS_URL = 300
"""Caracteres de `palabras_url`."""
MAX_RUTA_PALABRAS = MAX_PALABRAS_URL
"""Caracteres de la ruta (ya decodificada) que miran `palabras_url` y `con_palabras`. Las
palabras no pasan de `MAX_PALABRAS_URL`, y analizar rutas enteras de 2000 caracteres en 100 000
URL de un sitemap hostil costaba minutos de CPU."""
MIN_PALABRA = 3
"""Letras de una palabra que cuenta (como `perfiles.descubrimiento.MIN_PALABRA`)."""
MIN_PALABRAS_SLUG = 2
"""Palabras que necesita el slug de una URL para que `con_palabras` la cuente."""
MIN_DIGITOS_REFERENCIA = 8
"""Un número suelto de la ruta con menos cifras es un id («/123-faro.html», «/p/123456»); con
8 o más se conserva, porque puede ser una referencia numérica (Bosch «0986494104») o un EAN."""

TipoSitemap = Literal["indice", "urlset"]


@dataclass(frozen=True, slots=True)
class EntradaSitemap:
    """Un `<url>` de un `urlset` o un `<sitemap>` de un índice."""

    url: str
    lastmod: str | None = None
    """Fecha W3C tal cual (`2026-10-20`, `2026-10-20T10:00:00+02:00`) o `None`."""


@dataclass(frozen=True, slots=True)
class Sitemap:
    """Un fichero de sitemap leído. Vacío (`urlset` sin entradas) si no es un sitemap válido."""

    tipo: TipoSitemap
    entradas: tuple[EntradaSitemap, ...]
    recortado: bool = False
    """La lectura se paró antes del final (tope, XML roto o recortado): puede haber más URL."""


_VACIO = Sitemap("urlset", ())
_RAICES: dict[str, TipoSitemap] = {"urlset": "urlset", "sitemapindex": "indice"}
_ENTRADA = {"urlset": "url", "indice": "sitemap"}

_DTD = re.compile(r"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
# Un solo recorrido del marcado. La última alternativa (`<` suelto) siempre casa: así `finditer`
# no se salta ningún `<` y un marcado sin cerrar se detecta en el acto (y se para la lectura).
# Los cuantificadores posesivos (`++`, `*+`) no devuelven caracteres: un atributo sin cerrar
# cuesta un solo recorrido aunque el nombre sea largo.
_MARCADO = re.compile(
    r"<!--.*?-->"
    r"|<!\[CDATA\[(?P<cdata>.*?)\]\]>"
    r"|<\?.*?\?>"
    r"|<(?P<cierre>/?)(?P<nombre>[^\s/>!?<\"'=]++)"
    r"(?P<atributos>(?:[^<>\"']++|\"[^<\"]*+\"|'[^<']*+')*+)>"
    r"|<",
    re.DOTALL,
)
_ENTIDAD = re.compile(
    r"&(?:#x(?P<hex>[0-9A-Fa-f]{1,6});|#(?P<dec>[0-9]{1,7});|(?P<nombre>amp|lt|gt|quot|apos);)"
)
_PREDEFINIDAS = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_URL_VALIDA = re.compile(r"https?://[^\s\x00-\x1f\x7f-\x9f]+", re.IGNORECASE)
_FECHA_W3C = re.compile(
    r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2}"
    r"(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,9})?)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?)?)?"
)

# Rutas de sitemap habituales, en el orden en que se prueban (después de las de robots.txt).
_RUTAS_FIJAS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")
_RUTAS_PLATAFORMA: dict[Plataforma, tuple[str, ...]] = {
    Plataforma.SHOPIFY: ("/sitemap.xml",),
    Plataforma.WOOCOMMERCE: ("/product-sitemap.xml",),  # Yoast SEO.
    Plataforma.PRESTASHOP: ("/1_index_sitemap.xml",),  # Módulo gsitemap.
    Plataforma.MAGENTO: ("/sitemap/sitemap.xml",),
}

# Palabras del nombre de un sitemap hijo. Las que no son de producto ganan: `product_cat`,
# `product_tag` y `product_brand` (WooCommerce) son categorías, etiquetas y marcas.
_DE_PRODUCTO = frozenset(
    {
        "product", "products", "producto", "productos", "produit", "produits",
        "item", "items", "articulo", "articulos", "catalog", "catalogo", "catalogue",
    }
)  # fmt: skip
_NO_DE_PRODUCTO = frozenset(
    {
        "page", "pages", "pagina", "paginas", "post", "cms", "blog", "blogs", "news", "noticias",
        "cat", "cats", "category", "categories", "categoria", "categorias", "taxonomies",
        "taxonomy", "collection", "collections", "coleccion", "colecciones",
        "tag", "tags", "etiqueta", "etiquetas", "image", "images", "imagen", "imagenes",
        "video", "videos", "author", "authors", "autor", "autores", "user", "users",
        "usuario", "usuarios", "brand", "brands", "marca", "marcas", "manufacturer",
        "manufacturers", "fabricante", "fabricantes", "supplier", "suppliers", "proveedor",
        "proveedores",
    }
)  # fmt: skip
# Palabras de la ruta que no dicen nada del producto.
_EXTENSIONES = frozenset({"html", "htm", "shtml", "php", "asp", "aspx", "jsp", "do", "xml"})
_GENERICAS = frozenset({"index", "default", "home", "inicio"})


# --- Lector ------------------------------------------------------------------------------------


def leer_sitemap(texto: str) -> Sitemap:
    """Las entradas de un índice de sitemaps o de un `urlset`.

    Un texto con DTD o entidades declaradas, o que no es un sitemap (otra raíz, HTML, vacío),
    da un `Sitemap` vacío. Un XML roto o recortado da las entradas completas anteriores al
    fallo, con `recortado=True`. Nunca lanza una excepción.
    """
    recortado = len(texto) > MAX_CARACTERES_SITEMAP
    texto = texto[:MAX_CARACTERES_SITEMAP]
    if _DTD.search(texto):
        return _VACIO
    marcas = _MARCADO.finditer(texto)
    raiz = _raiz(marcas)
    if raiz is None:
        return _VACIO
    tipo, nombre_raiz = raiz
    entradas, cortado = _entradas(texto, marcas, tipo, nombre_raiz)
    return Sitemap(tipo, tuple(entradas), recortado or cortado)


def _raiz(marcas: Iterator[re.Match[str]]) -> tuple[TipoSitemap, str] | None:
    """Tipo y nombre del elemento raíz, o `None` si no es un sitemap (o es `<urlset/>`)."""
    for marca in marcas:
        cierre, nombre, atributos = marca.group(2, 3, 4)
        if nombre is None:
            if marca.end() - marca.start() == 1:
                return None  # `<` suelto antes de la raíz.
            continue  # Declaración XML, hoja de estilo, comentario o CDATA suelto.
        tipo = _RAICES.get(nombre.rpartition(":")[2].lower())
        if cierre or tipo is None or atributos.endswith("/"):
            return None
        return tipo, nombre
    return None


def _entradas(
    texto: str, marcas: Iterator[re.Match[str]], tipo: TipoSitemap, nombre_raiz: str
) -> tuple[list[EntradaSitemap], bool]:
    """Las entradas completas del cuerpo de la raíz y si la lectura se cortó antes del final.

    Es el bucle caliente (hasta `MAX_ETIQUETAS_SITEMAP` vueltas): variables locales y nombres
    completos precalculados en lugar de partir cada etiqueta en prefijo y nombre local. Los
    nombres distinguen mayúsculas, como en XML; los del sitemap llevan el prefijo de la raíz
    (`image:loc` no es el `loc` de un `urlset` sin prefijo).
    """
    prefijo = nombre_raiz.rpartition(":")[0]
    con_prefijo = f"{prefijo}:" if prefijo else ""
    nombre_entrada = con_prefijo + _ENTRADA[tipo]
    nombre_loc = con_prefijo + "loc"
    nombre_lastmod = con_prefijo + "lastmod"
    entradas: list[EntradaSitemap] = []
    pila: list[str] = []  # Elementos abiertos dentro de la raíz.
    en_entrada = False
    loc: str | None = None
    lastmod: str | None = None
    vistos: set[str] = set()  # Campos ya leídos en la entrada en curso.
    campo: _Campo | None = None  # `loc` o `lastmod` mientras se lee su texto.
    fin_anterior = 0
    etiquetas = 0
    for marca in marcas:
        cdata, cierre, nombre, atributos = marca.group(1, 2, 3, 4)
        if campo is not None and marca.start() > fin_anterior:
            campo.texto(texto, fin_anterior, marca.start())
        fin_anterior = marca.end()
        if nombre is None:
            if cdata is not None:
                if campo is not None:
                    campo.cdata(cdata)
            elif fin_anterior - marca.start() == 1:
                return entradas, True  # `<` que no abre nada válido: XML roto o cortado.
            continue  # Comentario o instrucción de proceso.
        etiquetas += 1
        if etiquetas > MAX_ETIQUETAS_SITEMAP:
            return entradas, True
        if cierre:
            if not pila:
                # Cierre de la raíz (lo que venga después no cuenta) o de otra cosa: roto.
                return entradas, nombre != nombre_raiz or atributos.strip() != ""
            if pila.pop() != nombre or atributos.strip():
                return entradas, True
            profundidad = len(pila)
            if profundidad == 1 and campo is not None:
                if campo.nombre == "loc":
                    loc = campo.url()
                else:
                    lastmod = campo.fecha()
                campo = None
            elif profundidad == 0 and en_entrada:
                en_entrada = False
                if loc is not None:
                    entradas.append(EntradaSitemap(loc, lastmod))
            continue
        vacio = atributos.endswith("/")
        profundidad = len(pila)
        if profundidad >= MAX_PROFUNDIDAD_SITEMAP:
            return entradas, True
        if campo is not None:
            campo.valido = False  # Un elemento dentro de `loc` o `lastmod`.
        if profundidad == 0:
            en_entrada = nombre == nombre_entrada and not vacio
            if en_entrada:
                if len(entradas) >= MAX_ENTRADAS_SITEMAP:
                    return entradas, True
                loc = lastmod = None
                vistos.clear()
        elif profundidad == 1 and en_entrada and nombre not in vistos:
            if nombre in (nombre_loc, nombre_lastmod):
                vistos.add(nombre)
                if not vacio:
                    campo = _Campo("loc" if nombre == nombre_loc else "lastmod")
        if not vacio:
            pila.append(nombre)
    return entradas, True  # Se acabó el texto sin cerrar la raíz.


class _Campo:
    """Texto de un `loc` o un `lastmod` mientras se lee, con su tope de caracteres."""

    __slots__ = ("largo", "nombre", "trozos", "valido")

    def __init__(self, nombre: str) -> None:
        self.nombre = nombre
        self.trozos: list[str] = []
        self.largo = 0
        self.valido = True

    def texto(self, texto: str, inicio: int, fin: int) -> None:
        """Texto entre dos marcas: se decodifican sus entidades."""
        if not self.valido:
            return
        trozo = texto[inicio : min(fin, inicio + MAX_CAMPO - self.largo + 1)]
        self.largo += len(trozo)
        decodificado = _decodificar(trozo) if self.largo <= MAX_CAMPO else None
        if decodificado is None:
            self.valido = False
        else:
            self.trozos.append(decodificado)

    def cdata(self, contenido: str) -> None:
        """Una sección CDATA: va tal cual, sin entidades."""
        self.largo += len(contenido)
        if self.largo > MAX_CAMPO:
            self.valido = False
        elif self.valido:
            self.trozos.append(contenido)

    def _valor(self) -> str:
        return "".join(self.trozos).strip() if self.valido else ""

    def url(self) -> str | None:
        return _url_valida(self._valor())

    def fecha(self) -> str | None:
        return _fecha_valida(self._valor())


def _decodificar(trozo: str) -> str | None:
    """Texto con las 5 entidades predefinidas y las numéricas decodificadas, o `None` si lleva
    otra entidad (necesitaría una DTD), un `&` suelto o un carácter que XML no admite."""
    if "&" not in trozo:
        return trozo
    partes: list[str] = []
    posicion = 0
    for entidad in _ENTIDAD.finditer(trozo):
        if "&" in trozo[posicion : entidad.start()]:
            return None
        partes.append(trozo[posicion : entidad.start()])
        nombre = entidad.group("nombre")
        if nombre is not None:
            partes.append(_PREDEFINIDAS[nombre])
        else:
            hexadecimal = entidad.group("hex")
            codigo = int(hexadecimal, 16) if hexadecimal else int(entidad.group("dec"))
            if not _caracter_xml(codigo):
                return None
            partes.append(chr(codigo))
        posicion = entidad.end()
    if "&" in trozo[posicion:]:
        return None
    partes.append(trozo[posicion:])
    return "".join(partes)


def _caracter_xml(codigo: int) -> bool:
    """`Char` de XML 1.0: sin controles (salvo tab y saltos), sustitutos ni no-caracteres."""
    return (
        codigo in (0x9, 0xA, 0xD)
        or 0x20 <= codigo <= 0xD7FF
        or 0xE000 <= codigo <= 0xFFFD
        or 0x10000 <= codigo <= 0x10FFFF
    )


def _url_valida(valor: str) -> str | None:
    """URL absoluta `http(s)`, sin espacios ni controles y de longitud razonable."""
    if not valor or len(valor) > MAX_URL or not _URL_VALIDA.fullmatch(valor):
        return None
    return valor


def _fecha_valida(valor: str) -> str | None:
    if not valor or len(valor) > MAX_LASTMOD or not _FECHA_W3C.fullmatch(valor):
        return None
    return valor


# --- Candidatos y sitemaps de producto ---------------------------------------------------------


def candidatos(base: str, declarados: Sequence[str], plataforma: Plataforma | None) -> list[str]:
    """URL de sitemap que probar en la tienda de `base`, en orden y sin repetir.

    Primero los declarados en robots.txt (como mucho `MAX_DECLARADOS`, absolutos y solo del
    mismo dominio que la tienda, con o sin `www.`), después las rutas habituales y las de la
    plataforma. Ninguna está comprobada: el configurador se queda con las que existan.
    """
    origen = origen_de(base)
    dominio = _dominio_de(origen)
    if origen is None or dominio is None:
        return []
    raiz = origen + "/"
    urls: list[str] = []
    for declarado in declarados[:MAX_DECLARADOS]:
        if _absoluta(declarado) and (url := url_de_la_tienda(declarado, raiz, dominio)):
            urls.append(url)
    rutas = _RUTAS_FIJAS + (_RUTAS_PLATAFORMA.get(plataforma, ()) if plataforma else ())
    for ruta in rutas:
        if url := url_de_la_tienda(origen + ruta, raiz, dominio):
            urls.append(url)
    return list(dict.fromkeys(urls))


def de_producto(indice: Sitemap) -> list[str]:
    """Los sitemaps de producto de un índice, por su nombre (como mucho `MAX_FICHEROS_SITEMAP`).

    Los de páginas, entradas, categorías, etiquetas, marcas, autores o imágenes no cuentan. Si
    ninguno se distingue como de producto (PrestaShop: `1_es_0_sitemap.xml`), se devuelven todos
    los demás. Un `urlset` no tiene sitemaps hijos: `[]`.
    """
    if indice.tipo != "indice":
        return []
    productos: list[str] = []
    neutros: list[str] = []
    for url in dict.fromkeys(entrada.url for entrada in indice.entradas):
        palabras = _palabras_nombre(url)
        if palabras & _NO_DE_PRODUCTO:
            continue
        (productos if palabras & _DE_PRODUCTO else neutros).append(url)
    return (productos or neutros)[:MAX_FICHEROS_SITEMAP]


def de_la_tienda(urls: Iterable[str], base: str) -> list[str]:
    """Las `urls` absolutas que son de la tienda de `base`, normalizadas y sin repetir.

    Es el filtro de mismo dominio de `extraccion.listado.url_de_la_tienda` (con o sin `www.`,
    sin usuario, en el puerto por defecto, sin fragmento ni parámetros de seguimiento) para
    las URL que salen de un sitemap, antes de descargarlas o guardarlas en el catálogo.
    """
    origen = origen_de(base)
    dominio = _dominio_de(origen)
    if origen is None or dominio is None:
        return []
    raiz = origen + "/"
    vistas: dict[str, None] = {}
    for url in urls:
        if _absoluta(url) and (propia := url_de_la_tienda(url, raiz, dominio)):
            vistas[propia] = None
    return list(vistas)


def _dominio_de(origen: str | None) -> str | None:
    if origen is None:
        return None
    try:
        host = urlsplit(origen).hostname
    except ValueError:
        return None
    return dominio_normalizado(host) if host else None


def _absoluta(url: str) -> bool:
    """URL `http(s)` absoluta (con host); las relativas de un sitemap o de robots.txt no valen."""
    if len(url) > MAX_URL:
        return False
    try:
        partes = urlsplit(url.strip())
    except ValueError:
        return False
    return partes.scheme.lower() in ("http", "https") and bool(partes.netloc)


def _palabras_nombre(url: str) -> set[str]:
    """Palabras de la ruta y de los valores de la consulta de un sitemap hijo, sin cifras finales
    (`product-sitemap2.xml` → {product, sitemap, xml}). Los nombres de los parámetros no cuentan:
    en `xmlsitemap.php?type=products&page=2` (BigCommerce), `page` es la paginación."""
    try:
        partes = urlsplit(url[:MAX_URL])
    except ValueError:
        return set()
    valores = " ".join(valor for _, valor in parse_qsl(partes.query, keep_blank_values=True))
    texto = normalizar_titulo(f"{unquote(partes.path)} {valores}")
    return {palabra.rstrip("0123456789") for palabra in texto.split()} - {""}


# --- Palabras de la URL ------------------------------------------------------------------------


def palabras_url(url: str) -> str:
    """Palabras de la ruta de `url` para el catálogo local, separadas por un espacio.

    Normaliza como `emparejado.texto.normalizar_titulo` (minúsculas, sin acentos ni signos),
    decodifica los `%XX`, quita las extensiones (`.html`), los números de id (menos de
    `MIN_DIGITOS_REFERENCIA` cifras, en un tramo propio o delante del slug: `_sin_ids`) y las
    repeticiones, y añade las referencias OEM escritas por
    grupos («1k0-941-005» → `1k0941005`). Las referencias alfanuméricas del slug se conservan.
    Como mucho `MAX_PALABRAS_URL` caracteres, sin cortar palabras.
    """
    ruta = _ruta(url)
    texto = normalizar_titulo(ruta)
    palabras: dict[str, None] = {}
    for tramo in ruta.split("/"):
        for palabra in _sin_ids(normalizar_titulo(tramo).split()):
            palabras[palabra] = None
    for referencia in sorted(referencias_en(texto)):
        referencia = referencia.lower()
        if not _es_id(referencia):
            palabras.setdefault(referencia, None)
    return _recortar(" ".join(palabras), MAX_PALABRAS_URL)


def con_palabras(urls: Iterable[str]) -> float:
    """Fracción de `urls` cuyo slug tiene al menos 2 palabras de 3 letras o más.

    Distingue las tiendas con slugs legibles (`/producto/faro-golf/`) de las que solo ponen ids
    (`/p/123456`, `/catalog/product/view/id/4521/`): en estas, el catálogo por sitemap no sirve
    para buscar. El slug es el último tramo de la ruta con alguna palabra que no sea un número,
    una extensión ni un nombre genérico (`index.html` → el tramo anterior).
    """
    total = 0
    legibles = 0
    for url in urls:
        total += 1
        if sum(len(p) >= MIN_PALABRA for p in _palabras_slug(url)) >= MIN_PALABRAS_SLUG:
            legibles += 1
    return legibles / total if total else 0.0


def _ruta(url: str) -> str:
    """Ruta decodificada de `url`, como mucho `MAX_RUTA_PALABRAS` caracteres.

    El slug del producto va al final: si hay que recortar, se quedan los tramos enteros del final
    y se pierden las categorías del principio. Si el último tramo no cabe solo, su principio, que
    es donde va el nombre del producto.
    """
    try:
        ruta = unquote(urlsplit(url[:MAX_URL]).path)
    except ValueError:
        return ""
    if len(ruta) <= MAX_RUTA_PALABRAS:
        return ruta
    ruta = ruta.rstrip("/")
    cola = ruta[-MAX_RUTA_PALABRAS:]
    corte = cola.find("/")
    if corte >= 0:
        return cola[corte:]
    return ruta[ruta.rfind("/") + 1 :][:MAX_RUTA_PALABRAS]


def _es_id(palabra: str) -> bool:
    return palabra.isdigit() and len(palabra) < MIN_DIGITOS_REFERENCIA


def _sin_ids(palabras: list[str]) -> list[str]:
    """Las palabras de un tramo de la ruta sin extensiones ni ids.

    Un id es un tramo que solo tiene números (`/p/123456/`, `/id/4521/`) o los números del
    principio del slug (`123-faro-golf.html` de PrestaShop). Los demás números se conservan: son
    el modelo o la medida («peugeot-308», «205-55-r16») y la consulta los pide.
    """
    palabras = [p for p in palabras if p not in _EXTENSIONES]
    inicio = 0
    while inicio < len(palabras) and _es_id(palabras[inicio]):
        inicio += 1
    return palabras[inicio:]


def _palabras_slug(url: str) -> list[str]:
    for tramo in reversed(_ruta(url).split("/")):
        palabras = [
            p
            for p in normalizar_titulo(tramo).split()
            if not p.isdigit() and p not in _EXTENSIONES and p not in _GENERICAS
        ]
        if palabras:
            return palabras
    return []


def _recortar(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    corte = texto.rfind(" ", 0, maximo + 1)
    return texto[:corte] if corte > 0 else texto[:maximo]
