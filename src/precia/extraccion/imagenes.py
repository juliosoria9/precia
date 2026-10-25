"""La foto principal de un artículo: una URL de la tienda que se enlaza y nunca se descarga.

La URL viene de una web no confiable y acaba en el `<img src>` de la web de PrecIA, que pide el
navegador del usuario. Por eso solo se acepta una URL que:

- sale del marcado de la propia página (JSON-LD, `<meta>`, el atributo de un `<img>`) o del JSON
  de la API de la tienda, nunca de la IA ni del texto visible;
- es `https` (la CSP solo permite imágenes `https:`), sin usuario ni contraseña y en el puerto por
  defecto, y su host no es una IP literal ni un nombre interno (`.local`, `.lan`...): así no apunta
  al router de la red del usuario;
- es de la tienda (su dominio, `www.` o un subdominio), de la misma marca con otro dominio de
  primer nivel de `TLD_DE_MARCA` (`oscaro.es` → `oscaro.media`; no `.tk` ni `.xyz`, que registra
  cualquiera por casi nada), de una CDN de `CDN_CONOCIDAS` o de un proxy de imágenes de
  `PROXIES_DE_IMAGEN` cuya ruta empieza por el host de la tienda (Photon sirve cualquier origen).
  Cualquier otro host se descarta: mejor una oferta sin foto que con un píxel de rastreo o la
  foto de otra tienda;
- no es un marcador de posición (`placeholder.gif`, `imagen_no_disponible.jpg`,
  `image-not-found.jpg`, `default-product.jpg`, `logo.png`, un `.svg`): se mira por palabras del
  nombre del fichero, en cualquier orden.

No se resuelve el DNS: la imagen no la pide el servidor, así que no hay SSRF que evitar, y los
tests no tocan la red.
"""

import ipaddress
import re
from collections.abc import Iterable, Iterator
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from precia.extraccion.dom import Nodo
from precia.navegador.url import SUFIJOS_INTERNOS

MAX_URL_FOTO = 2000
CDN_CONOCIDAS: frozenset[str] = frozenset(
    {
        "cdn.shopify.com",
        "m.media-amazon.com",
        "images-eu.ssl-images-amazon.com",
    }
)
"""Hosts exactos de CDN de plataformas que sirven las fotos de muchas tiendas. Lista cerrada."""
PROXIES_DE_IMAGEN: frozenset[str] = frozenset({"i0.wp.com", "i1.wp.com", "i2.wp.com"})
"""Photon de Jetpack (`i0.wp.com/<host de origen>/<ruta>`): sirve cualquier URL de origen, así
que solo vale si el origen es la tienda."""
TLD_DE_MARCA: frozenset[str] = frozenset(
    {
        "com",
        "net",
        "org",
        "eu",
        "media",
        "es",
        "pt",
        "fr",
        "de",
        "it",
        "nl",
        "be",
        "at",
        "ch",
        "pl",
        "ie",
    }
)
"""Dominios de primer nivel en los que se acepta la misma marca que la tienda: los habituales de
las tiendas europeas y de sus servidores de fotos. Lista cerrada."""
MIN_MARCA = 4
"""Lo mínimo que mide el nombre de la tienda para aceptar su misma marca con otro dominio de
primer nivel: «es.com» o «abc.media» coincidirían con demasiados."""
MAX_IMAGENES_TARJETA = 10
"""`<img>` y `<source>` que se miran como mucho en una tarjeta del listado."""
ATRIBUTOS_IMAGEN = ("data-src", "data-lazy-src", "data-original", "srcset", "data-srcset", "src")
"""Por orden: la carga diferida pone la foto buena en `data-*` y un marcador en `src`."""
_MARCADORES = frozenset(
    {
        "placeholder",
        "blank",
        "spacer",
        "loading",
        "loader",
        "lazy",
        "spinner",
        "transparent",
        "logo",
        "icon",
        "icons",
        "favicon",
        "sprite",
        "sprites",
        "noimage",
        "nophoto",
        "nofoto",
        "1x1",
        "unavailable",
    }
)
"""Palabras enteras del nombre del fichero que delatan un marcador, un logo o un icono. Se mira
por palabras: «tubo-silicona.jpg» no es un icono."""
_CARPETAS_NO_FOTO = frozenset(
    {
        "logo",
        "logos",
        "brand",
        "brands",
        "marcas",
        "manufacturer",
        "manufacturers",
        "icon",
        "icons",
        "sprites",
        "badges",
        "flags",
    }
)
"""Palabras de las carpetas de la ruta que guardan logos de marca, iconos o insignias: la foto de
la tarjeta sin foto de producto sería el logo del fabricante (`/suppliers/manufacturer/…`)."""
_NO = frozenset({"no", "sin", "not", "without", "missing"})
_IMAGEN = frozenset(
    {"image", "images", "imagen", "imagenes", "img", "photo", "foto", "fotos", "picture", "pic"}
)
_DISPONIBLE = frozenset({"disponible", "available", "found", "encontrada", "encontrado"})
_DEFECTO = frozenset({"default", "defecto", "generic", "generica", "generico"})
_OBJETO = _IMAGEN | {"product", "producto", "thumb", "thumbnail", "articulo", "article"}
_MARCADORES_JUNTOS = frozenset(
    {
        "noimagen",
        "noimg",
        "sinimagen",
        "sinfoto",
        "notfound",
        "imagenotfound",
        "nodisponible",
        "notavailable",
        "noimageavailable",
        "defaultimage",
        "defaultproduct",
    }
)
"""Las mismas combinaciones escritas sin separador («imagenotfound.jpg»): palabra entera."""
_ENLACES = frozenset({"de", "del", "la", "el", "en", "the", "is", "a", "an", "of", "for"})
_VOCABULARIO = _NO | _IMAGEN | _DISPONIBLE | _DEFECTO | _OBJETO | _ENLACES
"""Las palabras que puede tener el nombre de un marcador por combinación (además de medidas,
versiones o hashes, que llevan cifras). Una sola palabra más («marco», «cristal», «fondo») y es
una foto de producto: «foto-sin-marco.jpg» o «no-photo-frame.jpg» lo son."""
MAX_PALABRAS_MARCADOR = 5
"""Palabras (sin contar las que llevan cifras, como «300x300») que tiene como mucho el nombre de
un marcador por combinación: «gasolina-sin-plomo-…-foto.jpg» es una foto de producto con «sin» y
«foto» sueltas."""
_PROHIBIDOS = frozenset("\"'<>\\`")
_SEGUROS = "/%:@!$&()*+,;=-._~"
_PALABRAS = re.compile(r"[^a-z0-9]+")


def foto_valida(valor: str | None, *, base: str) -> str | None:
    """`valor` resuelto contra `base` (la URL de la ficha o del listado) y normalizado, o `None`.

    Devuelve una URL `https` sin fragmento, con la ruta y la consulta escapadas, que se puede
    pintar tal cual en un `<img src>` (ver las reglas en la cabecera del módulo).
    """
    if not isinstance(valor, str):
        return None
    crudo = valor.strip()
    if not crudo or len(crudo) > MAX_URL_FOTO:
        return None
    if any(c.isspace() or not c.isprintable() or c in _PROHIBIDOS for c in crudo):
        return None
    tienda = _host_de_la_tienda(base)
    if tienda is None:
        return None
    if crudo.startswith("//"):
        crudo = "https:" + crudo
    try:
        partes = urlsplit(urljoin(base, crudo))
        puerto = partes.port
    except ValueError:
        return None
    # Una relativa en una ficha servida por http queda en http: no se reescribe, se descarta.
    if partes.scheme.lower() != "https":
        return None
    if partes.username is not None or partes.password is not None:
        return None
    if puerto not in (None, 443):
        return None
    host = _host(partes.hostname or "")
    if host is None or not _host_permitido(host, tienda, partes.path) or _es_marcador(partes.path):
        return None
    ruta = quote(partes.path or "/", safe=_SEGUROS)
    consulta = quote(partes.query, safe=_SEGUROS + "?")
    url = urlunsplit(("https", host, ruta, consulta, ""))
    return url if len(url) <= MAX_URL_FOTO else None


def primera_foto(candidatas: Iterable[str | None], *, base: str) -> str | None:
    """La primera de `candidatas` que pasa `foto_valida`, ya normalizada."""
    for candidata in candidatas:
        if (foto := foto_valida(candidata, base=base)) is not None:
            return foto
    return None


def foto_de_tarjeta(nodo: Nodo, *, base: str) -> str | None:
    """La foto de una tarjeta del listado (o de un `<img>` suelto): su primer `<img>` o `<source>`.

    De cada uno se prueban, por orden, `ATRIBUTOS_IMAGEN` (de `srcset`, su primer candidato) y se
    queda la primera URL válida. Una insignia («logo-envio.png») o un píxel de otro host se saltan.
    """
    return primera_foto(candidatas_de_imagen(nodo), base=base)


def candidatas_de_imagen(nodo: Nodo) -> Iterator[str]:
    """Los valores crudos de `ATRIBUTOS_IMAGEN` de `nodo` y sus `<img>` y `<source>`, en orden.

    Como mucho `MAX_IMAGENES_TARJETA` elementos; de un `srcset`, su primer candidato.
    """
    vistas = 0
    for elemento in (nodo, *nodo.descendientes()):
        if elemento.etiqueta not in ("img", "source"):
            continue
        for atributo in ATRIBUTOS_IMAGEN:
            valor = elemento.atributo(atributo)
            if valor and atributo.endswith("srcset"):
                valor = _primer_candidato(valor)
            if valor:
                yield valor
        vistas += 1
        if vistas >= MAX_IMAGENES_TARJETA:
            return


def anclada_en_html(crudo: str, html: str) -> bool:
    """Si la URL `crudo` (tal y como se leyó, antes de resolverla) aparece en el `html`.

    Se toleran el `&amp;` de un atributo y el `\\/` de un JSON embebido.
    """
    if not crudo:
        return False
    escapada = crudo.replace("&", "&amp;")
    variantes = {crudo, escapada, crudo.replace("/", "\\/"), escapada.replace("/", "\\/")}
    return any(v in html for v in variantes)


def _primer_candidato(srcset: str) -> str | None:
    """«a.jpg 1x, b.jpg 2x» → «a.jpg»."""
    primero = srcset.strip().split(",", 1)[0].split()
    return primero[0] if primero else None


def _host_de_la_tienda(base: str) -> str | None:
    """Host de `base` sin el `www.` del principio, o `None` si no es una URL web válida."""
    try:
        partes = urlsplit(base)
    except ValueError:
        return None
    if partes.scheme.lower() not in ("http", "https"):
        return None
    host = _host(partes.hostname or "")
    return host.removeprefix("www.") if host else None


def _host(nombre: str) -> str | None:
    """Host en minúsculas e IDNA; `None` si es una IP, un nombre interno o está mal formado."""
    host = nombre.strip().lower().rstrip(".")
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    etiquetas = host.split(".")
    # Sin etiquetas vacías y con un dominio de primer nivel que no sea un número (`0x7f.1`).
    if len(etiquetas) < 2 or "" in etiquetas or etiquetas[-1].isdigit():
        return None
    if any(host == s or host.endswith("." + s) for s in SUFIJOS_INTERNOS):
        return None
    return host


def _host_permitido(host: str, tienda: str, ruta: str) -> bool:
    if host in CDN_CONOCIDAS or _de_la_tienda(host, tienda):
        return True
    if host in PROXIES_DE_IMAGEN:
        origen = _host(ruta.lstrip("/").split("/", 1)[0])
        return origen is not None and _de_la_tienda(origen.removeprefix("www."), tienda)
    # La misma marca con otro dominio de primer nivel: «oscaro.es» → «oscaro.media».
    nombre, _, resto = tienda.partition(".")
    if len(nombre) < MIN_MARCA or "." in resto:
        return False
    marca, tld = host.split(".")[-2:]
    return marca == nombre and tld in TLD_DE_MARCA


def _de_la_tienda(host: str, tienda: str) -> bool:
    return host == tienda or host.endswith("." + tienda)


def _es_marcador(ruta: str) -> bool:
    """Si el fichero es un marcador de posición, un logo o un icono, por las palabras de su nombre.

    Además de las palabras sueltas de `_MARCADORES`, en un nombre corto hecho solo de palabras de
    `_VOCABULARIO` basta con que estén, en cualquier orden, una negación y la imagen o su
    disponibilidad («imagen_no_disponible», «image-not-found», «no-disponible») o «por defecto» y
    el producto o la imagen («default-product»). Genérico: no depende de la tienda.
    """
    carpetas, _, fichero = unquote(ruta).lower().rpartition("/")
    if fichero.endswith(".svg") or _CARPETAS_NO_FOTO.intersection(_PALABRAS.split(carpetas)):
        return True
    raiz = fichero.rsplit(".", 1)[0] if "." in fichero else fichero
    palabras = [p for p in _PALABRAS.split(raiz) if p]
    if _MARCADORES.intersection(palabras) or _MARCADORES_JUNTOS.intersection(palabras):
        return True
    if any("placeholder" in p for p in palabras):
        return True
    utiles = {p for p in palabras if not any(c.isdigit() for c in p)}
    if len(utiles) > MAX_PALABRAS_MARCADOR or not utiles <= _VOCABULARIO:
        return False
    negado = bool(utiles & _NO) and bool(utiles & (_IMAGEN | _DISPONIBLE))
    return negado or (bool(utiles & _DEFECTO) and bool(utiles & _OBJETO))
