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

import heapq
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

from precia.emparejado.referencias import es_id_numerico, referencias_en
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
MAX_HIJOS_BUSQUEDA = 2
"""Sitemaps hijos sin leer que se descargan como mucho en una búsqueda (`hijos_de_la_consulta`)."""
MIN_PALABRA_HIJO = 4
"""Letras de una palabra de la consulta (o del nombre de un sitemap) que casa por prefijo."""
MAX_CONSULTA_HIJOS = 500
MAX_PALABRAS_HIJO = 12


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

# Palabras del nombre de un sitemap. Las que no son de producto ganan: `product_cat`,
# `product_tag` y `product_brand` (WooCommerce) son categorías, etiquetas y marcas.
_DE_PRODUCTO = frozenset(
    {
        "product", "products", "producto", "productos", "produit", "produits", "item", "items",
        "article", "articles", "articulo", "articulos", "catalog", "catalogo", "catalogue",
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
        "proveedores", "subcategory", "subcategories", "subcategoria", "subcategorias",
        "subcollection", "subcollections", "subcoleccion", "subcolecciones",
    }
)  # fmt: skip
# Sitemaps que pueden enlazar alguna ficha, pero sobre todo páginas sin un producto concreto
# (landings, temáticas, comparativas, promociones, contenidos, palabras clave SEO como el
# `keyword.xml` de Brico Dépôt, R2-C10): no se excluyen, van al final.
_SECUNDARIAS = frozenset(
    {
        "landing", "landings", "tematica", "tematicas", "comparativa", "comparativas",
        "comparative", "comparatives", "comparador", "comparadores", "promotion", "promotions",
        "promocion", "promociones", "content", "contents", "contenido", "contenidos", "outlet",
        "oferta", "ofertas", "offers", "friday", "blackfriday", "keyword", "keywords", "seo",
    }
)  # fmt: skip
# Nombres compuestos (`_compuesta`): las palabras de producto de 6 letras o más, que pueden ir
# dentro de otra (`productdetailspages`); los finales de un sitemap de páginas o imágenes
# (`brandpages`, `productimages`), y las palabras que dicen que son fichas.
_DE_PRODUCTO_LARGAS = frozenset(p for p in _DE_PRODUCTO if len(p) >= 6)
_FINALES_DE_PAGINA = ("pages", "page", "posts", "images", "imagenes", "videos")
_DE_FICHA = ("detail", "detalle", "ficha")
# La familia de categorías, que excluye también dentro de una palabra compuesta (causa R4-C:
# `filteredcategoriesurl` de Hawkers). Solo esta: «images» o «pages» pegadas no excluyen.
_CATEGORIAS_EN_COMPUESTA = ("categor", "taxonom")
PRIORIDAD_PRODUCTO = 0
PRIORIDAD_NEUTRO = 1
PRIORIDAD_SECUNDARIO = 2
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
        elif (
            profundidad == 1
            and en_entrada
            and nombre not in vistos
            and nombre in (nombre_loc, nombre_lastmod)
        ):
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
    mismo dominio que la tienda, con o sin `www.`, o de un subdominio suyo: `url_de_sitemap`),
    ordenados por `prioridad` (Coolmod declara
    primero un `/sitemap.xml` de categorías y después `sitemap_products.xml`) y sin los que no
    son de producto (categorías, marcas, blog); después las rutas habituales y las de la
    plataforma. Ninguna está comprobada: el configurador se queda con las que existan.
    """
    origen = origen_de(base)
    dominio = _dominio_de(origen)
    if origen is None or dominio is None:
        return []
    raiz = origen + "/"
    urls = _por_prioridad(declarados_de_la_tienda(base, declarados))
    rutas = _RUTAS_FIJAS + (_RUTAS_PLATAFORMA.get(plataforma, ()) if plataforma else ())
    for ruta in rutas:
        if url := url_de_la_tienda(origen + ruta, raiz, dominio):
            urls.append(url)
    return list(dict.fromkeys(urls))


def declarados_de_la_tienda(base: str, declarados: Sequence[str]) -> list[str]:
    """Los declarados en robots.txt que `candidatos` probaría, normalizados y en su orden.

    Como mucho `MAX_DECLARADOS`, absolutos y de la tienda de `base` o de un subdominio suyo
    (`url_de_sitemap`). El catálogo los distingue así de las rutas habituales y de la plataforma
    (causa R4-D).
    """
    return [url for d in declarados[:MAX_DECLARADOS] if (url := url_de_sitemap(d, base))]


def url_de_sitemap(url: str, base: str) -> str | None:
    """`url` normalizada si es un fichero sitemap de la tienda de `base`, o `None`.

    Vale lo que admite `url_de_la_tienda` (mismo dominio, con o sin `www.`) y, además, un
    subdominio estricto del dominio de la tienda tal y como se dio (causa R4-B: Xtralife declara
    `https://static.xtralife.com/sitemap.xml`; el protocolo sitemaps permite que el robots.txt
    de un host declare un sitemap en otro). Con las mismas condiciones: absoluta, `http(s)`, sin
    usuario ni contraseña y en el puerto por defecto. Sin lista de sufijos públicos, la tienda
    `compraonline.alcampo.es` solo cubre `*.compraonline.alcampo.es`: nunca otro dominio.

    Solo para el fichero sitemap (descargarlo y seguir sus redirecciones o los hijos de un
    índice): las URL que lleva dentro pasan por el filtro estricto (`de_la_tienda`).
    """
    origen = origen_de(base)
    dominio = _dominio_de(origen)
    if origen is None or dominio is None or not _absoluta(url):
        return None
    raiz = origen + "/"
    if propia := url_de_la_tienda(url, raiz, dominio):
        return propia
    subdominio = _dominio_de(url.strip())
    if subdominio is None or not subdominio.endswith("." + dominio):
        return None
    return url_de_la_tienda(url, raiz, subdominio)


def sitemaps_de_la_tienda(urls: Iterable[str], base: str) -> list[str]:
    """Las `urls` que son ficheros sitemap de la tienda de `base` (`url_de_sitemap`), sin
    repetir: los hijos de un índice, que pueden estar en el mismo subdominio (causa R4-B)."""
    return list(dict.fromkeys(propia for url in urls if (propia := url_de_sitemap(url, base))))


def prioridad(url: str) -> int | None:
    """Orden en que se lee un sitemap por su nombre, o `None` si no es de producto.

    `PRIORIDAD_PRODUCTO` (`product-sitemap.xml`, `sitemap_articles.xml`), `PRIORIDAD_NEUTRO`
    (no dice nada: `1_es_0_sitemap.xml`, `sitemap-portatiles-es.xml`) o `PRIORIDAD_SECUNDARIO`
    (landings, temáticas, comparativas, promociones). Los de páginas, entradas, categorías,
    etiquetas, marcas, autores o imágenes no son de producto; los de categorías, tampoco cuando
    la palabra va pegada a otras (`filteredcategoriesurl`, causa R4-C).

    Las palabras de exclusión y las de producto cuentan en toda la ruta (`/blog/sitemap.xml`,
    `/articulos/sitemap_index.xml`); las secundarias, solo en el nombre del fichero y en los
    valores de la query: una carpeta `contents`, `statics` o `seo` es la ruta del CMS o del CDN y
    no dice qué hay dentro (El Corte Inglés tiene sus productos en
    `…/contents/sitemap-variants/sitemap.xml.gz`, causa R3-C).
    """
    palabras = _palabras_nombre(url)
    if palabras & _NO_DE_PRODUCTO or any(
        familia in p for p in palabras for familia in _CATEGORIAS_EN_COMPUESTA
    ):
        return None
    if palabras & _DE_PRODUCTO:
        return PRIORIDAD_PRODUCTO
    if any(_compuesta(p) == PRIORIDAD_PRODUCTO for p in palabras - _SECUNDARIAS):
        return PRIORIDAD_PRODUCTO
    del_nombre = _palabras_nombre(url, solo_nombre=True)
    if del_nombre & _SECUNDARIAS or any(
        _compuesta(p) == PRIORIDAD_SECUNDARIO for p in del_nombre - _SECUNDARIAS
    ):
        return PRIORIDAD_SECUNDARIO
    return PRIORIDAD_NEUTRO


def _compuesta(palabra: str) -> int | None:
    """Clase de una palabra compuesta del nombre de un sitemap, o `None` si no dice nada.

    MediaMarkt junta las palabras: `productdetailspages`, `brandpages`, `productimages`. Solo con
    reglas acotadas, porque una palabra corta dentro de otra no dice nada («video» en
    «videojuegos», «post» en «postprocesado», «tag» en «vintage», «product» en «reproductores»):
    es de producto si empieza o acaba por una palabra de producto larga (`_DE_PRODUCTO_LARGAS`) y
    no acaba en una de páginas o imágenes (`_FINALES_DE_PAGINA`), salvo que diga que son fichas
    (`productdetailspages`); y secundaria si acaba en una de páginas o imágenes con al menos 3
    letras delante. Nunca excluye.
    """
    pagina = any(
        palabra.endswith(final) and len(palabra) - len(final) >= MIN_PALABRA
        for final in _FINALES_DE_PAGINA
    )
    producto = any(
        palabra.startswith(larga) or palabra.endswith(larga) for larga in _DE_PRODUCTO_LARGAS
    )
    if producto and (not pagina or any(ficha in palabra for ficha in _DE_FICHA)):
        return PRIORIDAD_PRODUCTO
    return PRIORIDAD_SECUNDARIO if pagina else None


def es_listado(url: str) -> bool:
    """`url` es una página de marca, categoría, etiqueta, blog… y no la ficha de un producto.

    Lo es si un tramo entero de la ruta es una palabra de las que no son de producto (las mismas
    que en el nombre de un sitemap: `/es/brand/jvc/auriculares-bluetooth` de MediaMarkt) y
    ningún tramo es de producto (Shopify pone las fichas en `/collections/x/products/y`). Una
    palabra dentro del slug no cuenta. El catálogo local deja estas páginas para el final.

    Se llama una vez por URL que casa con la búsqueda (pueden ser decenas de miles): la ruta se
    corta a mano, sin `urlsplit`, y solo se normalizan los tramos que no son una palabra ASCII.
    """
    ruta = url[:MAX_URL].partition("://")[2].partition("#")[0].partition("?")[0].partition("/")[2]
    if "%" in ruta:
        ruta = unquote(ruta)
    tramos = {
        tramo.lower() if tramo.isascii() and tramo.isalnum() else normalizar_titulo(tramo)
        for tramo in ruta.split("/")
    }
    return bool(tramos & _NO_DE_PRODUCTO) and not tramos & _DE_PRODUCTO


def de_producto(indice: Sitemap) -> list[str]:
    """Los sitemaps hijos de un índice que se siguen, por `prioridad` (como mucho
    `MAX_FICHEROS_SITEMAP`).

    Primero los de producto, después los neutros y al final los secundarios, cada grupo en el
    orden del índice; los que no son de producto no cuentan. Los neutros no se descartan aunque
    haya alguno de producto: en PcComponentes solo dos feeds de categoría casan por accidente
    con «articulos» y «productos», y los cientos restantes son las fichas. Un `urlset` no tiene
    sitemaps hijos: `[]`.
    """
    return hijos_de_producto(indice)[:MAX_FICHEROS_SITEMAP]


def hijos_de_producto(indice: Sitemap) -> list[str]:
    """Todos los sitemaps hijos de un índice que se siguen, por `prioridad` y sin tope."""
    if indice.tipo != "indice":
        return []
    return _por_prioridad(dict.fromkeys(entrada.url for entrada in indice.entradas))


def hijos_de_la_consulta(hijos: Iterable[str], consulta: str, *, maximo: int) -> list[str]:
    """Los `maximo` sitemaps de `hijos` cuyo nombre nombra la consulta, el mejor primero (R2-C14b).

    Para los hijos de un índice que no se leyeron al hacer el catálogo (tope de ficheros): en
    PcComponentes, `sitemap-portatiles-es.xml` con «portátil 16gb». Cuentan las palabras de la
    consulta de `MIN_PALABRA_HIJO` letras o más que no son solo cifras, normalizadas, y casan por
    prefijo en cualquier sentido («portatil» con «portatiles» y «portátiles» con «portatil»).
    Van primero los que nombran más palabras de la consulta; a igualdad, los que tienen menos
    palabras ajenas a ella (`sitemap-portatiles-es` antes que `sitemap-neveras-portatiles-es`); y
    después, en el orden de `hijos`. Los que no son de producto (`prioridad`) no cuentan.
    """
    if maximo <= 0:
        return []
    texto = normalizar_titulo(consulta[:MAX_CONSULTA_HIJOS])
    palabras = tuple(
        p for p in dict.fromkeys(texto.split()) if len(p) >= MIN_PALABRA_HIJO and not p.isdigit()
    )[:MAX_PALABRAS_HIJO]
    if not palabras:
        return []
    puntuados: list[tuple[int, int, int, str]] = []
    for orden, hijo in enumerate(dict.fromkeys(hijos)):
        suyas = _palabras_nombre(hijo)
        casan = [s for s in suyas if any(_casan(s, p) for p in palabras)]
        if casan and prioridad(hijo) is not None:
            nombradas = sum(any(_casan(s, p) for s in casan) for p in palabras)
            puntuados.append((-nombradas, len(suyas) - len(casan), orden, hijo))
    return [hijo for *_, hijo in heapq.nsmallest(maximo, puntuados)]


def _casan(suya: str, palabra: str) -> bool:
    """Una palabra del nombre de un sitemap y una de la consulta casan por prefijo."""
    if len(suya) < MIN_PALABRA_HIJO:
        return False
    return suya.startswith(palabra) or palabra.startswith(suya)


def _por_prioridad(urls: Iterable[str]) -> list[str]:
    """Las `urls` que son de producto, ordenadas por `prioridad` (estable)."""
    con_prioridad = [(p, url) for url in urls if (p := prioridad(url)) is not None]
    return [url for _, url in sorted(con_prioridad, key=lambda par: par[0])]


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


def _palabras_nombre(url: str, *, solo_nombre: bool = False) -> set[str]:
    """Palabras de la ruta y de los valores de la consulta de un sitemap hijo, sin cifras finales
    (`product-sitemap2.xml` → {product, sitemap, xml}). Los nombres de los parámetros no cuentan:
    en `xmlsitemap.php?type=products&page=2` (BigCommerce), `page` es la paginación. Con
    `solo_nombre`, de la ruta solo cuenta el último tramo (el nombre del fichero), sin carpetas."""
    try:
        partes = urlsplit(url[:MAX_URL])
    except ValueError:
        return set()
    valores = " ".join(valor for _, valor in parse_qsl(partes.query, keep_blank_values=True))
    ruta = unquote(partes.path)
    if solo_nombre:
        ruta = ruta.rstrip("/").rpartition("/")[2]
    texto = normalizar_titulo(f"{ruta} {valores}")
    return {palabra.rstrip("0123456789") for palabra in texto.split()} - {""}


# --- Palabras de la URL ------------------------------------------------------------------------


def palabras_url(url: str) -> str:
    """Palabras de la ruta de `url` para el catálogo local, separadas por un espacio.

    Normaliza como `emparejado.texto.normalizar_titulo` (minúsculas, sin acentos ni signos),
    decodifica los `%XX`, quita las extensiones (`.html`), los números de id (menos de
    `emparejado.referencias.MIN_DIGITOS_REFERENCIA` cifras, en un tramo propio o delante del
    slug: `_sin_ids`) y las repeticiones, y añade las referencias OEM escritas por
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
        if not es_id_numerico(referencia):
            palabras.setdefault(referencia, None)
    return _recortar(" ".join(palabras), MAX_PALABRAS_URL)


def con_palabras(urls: Iterable[str]) -> float:
    """Fracción de `urls` cuyo slug tiene al menos 2 palabras de 3 letras o más.

    Distingue las tiendas con slugs legibles (`/producto/faro-golf/`) de las que solo ponen ids
    (`/p/123456`, `/catalog/product/view/id/4521/`): en estas, el catálogo por sitemap no sirve
    para buscar. El slug es el último tramo de la ruta con alguna palabra que no sea un número,
    una extensión ni un nombre genérico (`index.html` → el tramo anterior). Antes se quitan los
    marcadores cortos de tipo de página del final de la ruta (`/<slug>/<EAN>/p` → el slug), pero
    no los de en medio: en `/ropa-hombre/p/123456` el producto sigue sin nombre.
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


def _sin_ids(palabras: list[str]) -> list[str]:
    """Las palabras de un tramo de la ruta sin extensiones ni ids.

    Un id es un tramo que solo tiene números (`/p/123456/`, `/id/4521/`) o los números del
    principio del slug (`123-faro-golf.html` de PrestaShop). Los demás números se conservan: son
    el modelo o la medida («peugeot-308», «205-55-r16») y la consulta los pide.
    """
    palabras = [p for p in palabras if p not in _EXTENSIONES]
    inicio = 0
    while inicio < len(palabras) and es_id_numerico(palabras[inicio]):
        inicio += 1
    return palabras[inicio:]


def _palabras_slug(url: str) -> list[str]:
    tramos = _ruta(url).split("/")
    while tramos and (not tramos[-1] or _es_marcador(tramos[-1])):
        tramos.pop()
    for tramo in reversed(tramos):
        palabras = [
            p
            for p in normalizar_titulo(tramo).split()
            if not p.isdigit() and p not in _EXTENSIONES and p not in _GENERICAS
        ]
        if palabras:
            return palabras
    return []


def _es_marcador(tramo: str) -> bool:
    """Si `tramo` es un marcador de tipo de página («p», «dp», «pd»): sin contar las extensiones,
    solo palabras que no son números y miden menos de `MIN_PALABRA` letras."""
    palabras = [p for p in normalizar_titulo(tramo).split() if p not in _EXTENSIONES]
    return bool(palabras) and all(not p.isdigit() and len(p) < MIN_PALABRA for p in palabras)


def _recortar(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    corte = texto.rfind(" ", 0, maximo + 1)
    return texto[:corte] if corte > 0 else texto[:maximo]
