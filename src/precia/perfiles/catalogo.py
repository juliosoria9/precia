"""Catálogo de una tienda sin buscador por su `sitemap.xml` (ADR-018, nota 16 §B).

El configurador lo usa cuando ni la API de la plataforma ni el buscador de la tienda sirven, y el
orquestador, para rehacer el catálogo de un perfil `sitemap` caducado. Pasos:

1. Las líneas `Sitemap:` del robots.txt de la tienda (`Navegador.sitemaps`, que lo lee aunque se
   ignore por ADR-017) y, detrás, las rutas habituales y las de la plataforma
   (`fuentes.sitemap.candidatos`, que ya descarta las declaradas de otro dominio o que no son de
   producto y pone delante las de producto).
2. Se descargan con `Navegador.obtener(..., grande=True)` (tope de 20 MiB descomprimidos, `.xml.gz`
   incluidos, sin Chromium ni caché), como mucho `MAX_FICHEROS_SITEMAP` ficheros entre índices y
   `urlset`, **contando los que fallan**: una tienda (o su robots.txt) no puede multiplicar las
   descargas. De un índice no se siguen los sitemaps de categorías, marcas, páginas o blog
   (`fuentes.sitemap.de_producto`), y los pendientes se leen por `fuentes.sitemap.prioridad` en
   cualquier nivel: primero los de producto, después los neutros y al final landings,
   comparativas y promociones.
3. El primer candidato que da URL de la tienda es el sitemap raíz del catálogo: de cada URL se
   guardan solo la URL, las palabras de su ruta (`palabras_url`) y el `lastmod` (art. 133 TRLPI:
   nada de precios ni descripciones), como mucho `MAX_URLS_CATALOGO`. `catalogos` da además los
   siguientes, uno detrás de otro y con el mismo tope de ficheros, para que el configurador pruebe
   otro si las fichas del primero no sirven (causa R3-C: Carrefour declara primero un sitemap de
   páginas informativas); `descubrir_catalogo` se queda con el primero.

Todo lo que sale de un sitemap es dato de la web: cada URL pasa por el filtro de mismo dominio
(`url_de_la_tienda`: con o sin `www.`, sin usuario ni puerto raro, normalizada) antes de guardarla;
cada fichero sitemap, por el mismo filtro o un subdominio estricto de la tienda (`url_de_sitemap`,
causa R4-B: Xtralife lo publica en `static.`) antes de descargarlo, y cada descarga, por el
validador anti-SSRF (`validar_url`) y la comprobación de que la respuesta sigue siendo un sitemap
de la tienda tras las redirecciones. Cortesía y
bloqueos son los del navegador: un `Bloqueado` en un sitemap se propaga y se para; un sitemap que
no existe, no se puede descargar o no se puede leer solo se salta. El robots.txt se ignora
(ADR-017): si es un desafío anti-bot no da sitemaps (`()`) y se sigue con las rutas habituales; si
también son un desafío, el `Bloqueado` sale de esa descarga (causa R3-B).

Los hijos de producto de un índice que no caben en el tope (PcComponentes tiene 424 feeds por
categoría) quedan en `Catalogo.pendientes`, en el orden en que se habrían leído, sin descargarlos.
También los de un índice con `MAX_GRANDES_SEGUIDOS` hijos seguidos que pasan del tope de tamaño
(`DemasiadoGrande`, A5 del plan de velocidad): los demás suelen ser igual de grandes. Así,
una búsqueda lee después con `leer_hijos` los que nombran la consulta (R2-C14b,
`fuentes.sitemap.hijos_de_la_consulta`), como mucho `MAX_HIJOS_BUSQUEDA`, con los mismos filtros.

Otros idiomas y países (causa R4-C de la campaña): si la portada final tiene prefijo de idioma o
país (`/es/es/` en Decántalo, `/es/` en Kenay), los sitemaps hijos con otro prefijo se leen los
últimos (no se borran: quedan pendientes) y sus URL no entran. Si la portada no tiene prefijo, un
primer tramo de dos letras no prueba nada (`/tv/`, `/pc/`): solo salen los prefijos cuyas URL
repiten, en su mayoría, el último tramo de una URL sin prefijo (las traducciones de Hawkers).
El prefijo va con el catálogo (`Catalogo.idioma`) y el almacén lo guarda: al rehacerlo o leer
sus hijos pendientes se pasa otra vez (`idioma=`) y se filtra igual que al configurar (R5-C).

El catálogo es `parcial` si se llegó a algún tope o algún sitemap venía recortado. Leer y
normalizar un sitemap grande es trabajo de CPU (unos 7 s por 50 000 URL medidos en los tests): se
hace en un hilo (`asyncio.to_thread`) para no parar el bucle de eventos.
"""

import asyncio
import heapq
import logging
import re
from collections import Counter
from collections.abc import AsyncGenerator, Iterable, Iterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from itertools import count, islice
from typing import NamedTuple, Protocol, runtime_checkable

from precia.emparejado.texto import normalizar_titulo
from precia.extraccion.listado import MAX_URL, dominio_normalizado, url_de_la_tienda
from precia.fuentes.plataformas import Plataforma, origen_de
from precia.fuentes.sitemap import (
    MAX_FICHEROS_SITEMAP,
    MAX_HIJOS_BUSQUEDA,
    PRIORIDAD_PRODUCTO,
    PRIORIDAD_SECUNDARIO,
    Sitemap,
    candidatos,
    declarados_de_la_tienda,
    hijos_de_producto,
    leer_sitemap,
    palabras_url,
    prioridad,
    sitemaps_de_la_tienda,
    url_de_sitemap,
)
from precia.fuentes.sitemap import con_palabras as fraccion_con_palabras
from precia.navegador.tipos import (
    Bloqueado,
    DemasiadoGrande,
    ErrorDescarga,
    Pagina,
    URLNoPermitida,
)
from precia.navegador.url import Validador, validar_url
from precia.nucleo.modelos import InfoCatalogo
from precia.perfiles.validador import MAX_FICHAS

registro = logging.getLogger(__name__)

MAX_URLS_CATALOGO = 100_000
"""URL que se guardan como mucho (como `almacen.sqlite.MAX_CATALOGO`, que no se importa aquí para
que `perfiles` no dependa del almacén)."""
MAX_PENDIENTES = 5_000
"""Hijos sin leer que se recuerdan (como `almacen.sqlite.MAX_PENDIENTES_CATALOGO`)."""
MIN_CON_PALABRAS = 0.6
"""Fracción de URL con slug legible por debajo de la cual el catálogo no sirve para buscar
(`/p/123456`): se pasa al listado del usuario (A2) o se falla."""
MAX_SITEMAPS_PROBADOS = 3
"""Sitemaps candidatos con URL que el configurador valida como mucho, uno detrás de otro, cuando
las fichas del anterior no sirven (causa R3-C): cada intento abre hasta `MAX_FICHAS` fichas."""
MIN_PALABRA = 3
MAX_CONSULTA = 500
MAX_PALABRAS_CONSULTA = 12
MAX_TRAMOS_IDIOMA = 2
"""Tramos de idioma y país que puede tener la portada delante (`/es/es/` de Decántalo)."""
MIN_TRADUCIDAS = 2
"""Fichas repetidas fuera del prefijo que, como mínimo, prueban que es una traducción (R4-C)."""
MIN_FORMA_DOMINANTE = 0.6
"""Fracción de las URL empatadas en palabras de la consulta que deben compartir forma (`_forma`)
para que esas vayan antes que las demás (causa R4-I). Por debajo no se reordena: en un catálogo
equilibrado no hay manera de saber cuáles son las fichas."""
MAX_EXTENSION = 5
"""Letras que puede tener la extensión del último tramo (`html`, `php`); lo demás es un punto
dentro del slug."""
PRIORIDAD_OTRO_IDIOMA = PRIORIDAD_SECUNDARIO + 1
MAX_GRANDES_SEGUIDOS = 2
"""Hijos seguidos de un mismo índice que pasan del tope de tamaño (`DemasiadoGrande`) tras los
cuales los demás hijos de ese índice ya no se descargan: quedan en `Catalogo.pendientes` (A5 del
plan de velocidad; Decathlon descargaba seis seguidos de 20 MiB para nada)."""
"""Los sitemaps de otro idioma o país van detrás de todos los demás (R4-C)."""
_IDIOMA = re.compile(r"[a-z]{2}(?:[-_][a-z]{2})?")
"""Un tramo de ruta con forma de prefijo de idioma o país (`es`, `es-es`, `en_gb`), como
`perfiles.descubrimiento`. La forma sola no prueba nada: `/tv/` o `/pc/` son secciones."""


@runtime_checkable
class NavegadorGrande(Protocol):
    """El navegador del catálogo: descarga ficheros grandes (sitemaps) con `grande=True`."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina: ...


@runtime_checkable
class NavegadorSitemaps(NavegadorGrande, Protocol):
    """Además, lee las líneas `Sitemap:` del robots.txt de la tienda.

    Si el robots.txt es un desafío anti-bot, `()` (se ignora, ADR-017).
    """

    async def sitemaps(self, url: str) -> tuple[str, ...]: ...


class GuardarCatalogo(Protocol):
    """Dónde se guarda el catálogo: `almacen.sqlite.Almacen` lo cumple tal cual."""

    def guardar_catalogo(
        self,
        dominio: str,
        sitemap: str,
        entradas: Iterable[tuple[str, str, str | None]],
        *,
        parcial: bool,
        pendientes: Iterable[str] = (),
        idioma: Sequence[str] = (),
    ) -> InfoCatalogo: ...


class UrlCatalogo(NamedTuple):
    """Una URL del catálogo, tal como la espera `Almacen.guardar_catalogo` (es una tupla)."""

    url: str
    palabras: str
    lastmod: str | None = None


@dataclass(frozen=True, slots=True)
class Catalogo:
    """El catálogo leído de los sitemaps de una tienda (aún sin guardar)."""

    sitemap: str
    """URL del sitemap raíz (índice o `urlset`): la `url_busqueda` del perfil `sitemap`."""
    entradas: tuple[UrlCatalogo, ...]
    """URL de la tienda, normalizadas y sin repetir, en el orden de los sitemaps."""
    parcial: bool
    """Se llegó a algún tope o algún sitemap venía recortado: puede faltar parte del catálogo."""
    ficheros: int
    """Sitemaps que se intentó descargar en total (también los que fallaron)."""
    pendientes: tuple[str, ...] = ()
    """Sitemaps hijos de producto que no se leyeron por el tope de ficheros, en el orden en que
    se habrían leído (como mucho `MAX_PENDIENTES`): `leer_hijos` (R2-C14b)."""
    idioma: tuple[str, ...] = ()
    """Prefijo de idioma o país de la portada con que se filtró (`/es/es/` → `("es", "es")`;
    `()` si no tiene): hay que guardarlo para rehacer el catálogo igual (R5-C)."""

    @property
    def con_palabras(self) -> float:
        """Fracción de URL con un slug legible (`fuentes.sitemap.con_palabras`)."""
        return fraccion_con_palabras(e.url for e in self.entradas)


async def descubrir_catalogo(
    base: str,
    dominio: str,
    navegador: NavegadorSitemaps,
    plataforma: Plataforma | None,
    *,
    validador: Validador = validar_url,
) -> Catalogo | None:
    """El catálogo de la tienda de `base` (su portada), o `None` si no tiene sitemap útil.

    `dominio` es el de la tienda (con o sin `www.`). Es el primero de `catalogos`. Ver el
    docstring del módulo.
    """
    async with aclosing(
        catalogos(base, dominio, navegador, plataforma, validador=validador)
    ) as leidos:
        async for catalogo in leidos:
            return catalogo
    return None


async def catalogos(
    base: str,
    dominio: str,
    navegador: NavegadorSitemaps,
    plataforma: Plataforma | None,
    *,
    validador: Validador = validar_url,
) -> AsyncGenerator[Catalogo]:
    """Los catálogos de los candidatos de la tienda de `base` que dan URL, en orden (R3-C).

    Una sola lectura para todos: el tope `MAX_FICHEROS_SITEMAP` cuenta las descargas de todos los
    candidatos y un sitemap ya leído (por ejemplo, como hijo de un índice anterior) no se vuelve a
    descargar. Cada catálogo se pide solo cuando el anterior no ha servido: quien llama deja de
    iterar en cuanto uno vale y no se descarga nada más.
    """
    lectura = _Lectura.para(base, dominio, navegador, validador)
    if lectura is None:
        return
    lectura.idioma = _prefijo_de_idioma(base)
    try:
        declarados = await navegador.sitemaps(lectura.raiz)
    except (ErrorDescarga, URLNoPermitida):
        declarados = ()  # Sin robots.txt legible quedan las rutas habituales.
    propios = set(declarados_de_la_tienda(lectura.origen, declarados))
    for candidato in candidatos(lectura.origen, declarados, plataforma):
        if lectura.agotada:
            return
        # Un rechazo seco en una ruta que nadie declaró, con la tienda ya respondiendo, es un
        # sitemap que no existe (causa R4-D); en un declarado, se para (R3-B).
        catalogo = await lectura.desde(candidato, tolerar_seco=candidato not in propios)
        if catalogo is not None:
            yield catalogo


async def leer_catalogo(
    sitemap: str,
    dominio: str,
    navegador: NavegadorGrande,
    *,
    validador: Validador = validar_url,
    idioma: Sequence[str] = (),
) -> Catalogo | None:
    """El catálogo desde un sitemap raíz ya conocido (el de un perfil `sitemap`), o `None`.

    Para rehacer un catálogo caducado: mismos topes y filtros, sin mirar el robots.txt. Un
    `sitemap` que no es de la tienda (ni de un subdominio suyo, `url_de_sitemap`) no se descarga.
    `idioma` es el prefijo del catálogo anterior (`Catalogo.idioma`, R5-C).
    """
    lectura = _Lectura.para(sitemap, dominio, navegador, validador)
    if lectura is None:
        return None
    lectura.idioma = tuple(idioma)
    propio = url_de_sitemap(sitemap, lectura.origen)
    if propio is None:
        return None
    return await lectura.desde(propio)


async def leer_hijos(
    sitemap: str,
    hijos: Sequence[str],
    dominio: str,
    navegador: NavegadorGrande,
    *,
    validador: Validador = validar_url,
    idioma: Sequence[str] = (),
) -> Catalogo | None:
    """Las URL de unos sitemaps hijos pendientes del catálogo de `sitemap` (R2-C14b).

    Se descargan, en orden, los primeros `MAX_HIJOS_BUSQUEDA` de `hijos` que son de la tienda,
    con los mismos filtros y la misma cortesía que al hacer el catálogo (un `Bloqueado` se
    propaga). Los hijos de un índice no se descargan ahora: van a `pendientes`. `None` si
    `sitemap` no es de la tienda. `idioma`, como en `leer_catalogo`.
    """
    lectura = _Lectura.para(sitemap, dominio, navegador, validador)
    if lectura is None:
        return None
    lectura.idioma = tuple(idioma)
    propios = [h for h in sitemaps_de_la_tienda(hijos, lectura.origen) if prioridad(h) is not None]
    entradas: dict[str, UrlCatalogo] = {}
    nuevos: dict[str, None] = {}
    parcial = False
    for hijo in propios[:MAX_HIJOS_BUSQUEDA]:
        lectura.descargas += 1
        leido = await lectura._descargar(hijo)
        if leido is None:
            continue
        parcial = parcial or leido.recortado
        if leido.tipo == "indice":
            nuevos.update(
                dict.fromkeys(sitemaps_de_la_tienda(hijos_de_producto(leido), lectura.origen))
            )
        elif await asyncio.to_thread(lectura._anadir, leido, entradas):
            parcial = True
    pendientes = tuple(h for h in nuevos if h not in propios)[:MAX_PENDIENTES]
    return Catalogo(
        sitemap, tuple(entradas.values()), parcial, lectura.descargas, pendientes, lectura.idioma
    )


def elegir_fichas(catalogo: Catalogo, consulta: str, *, maximo: int = MAX_FICHAS) -> list[str]:
    """Las `maximo` URL del catálogo con las que probar el perfil `sitemap`.

    Primero las que tienen más palabras de `consulta` (de 3 letras o más, normalizadas como en
    el almacén) en sus `palabras`; a igualdad, las de la forma de URL dominante antes que las
    demás (`_dominante_antes`); dentro de cada grupo, las de slug legible antes que la portada,
    un aviso legal o un `/p/123456`; y si no, en el orden del sitemap.
    """
    texto = normalizar_titulo(consulta[:MAX_CONSULTA])
    palabras = [p for p in dict.fromkeys(texto.split()) if len(p) >= MIN_PALABRA]
    palabras = palabras[:MAX_PALABRAS_CONSULTA]
    niveles: dict[int, list[UrlCatalogo]] = {}
    for entrada in catalogo.entradas:
        encajan = sum(p in entrada.palabras for p in palabras)
        niveles.setdefault(encajan, []).append(entrada)
    orden = (
        e.url for nivel in sorted(niveles, reverse=True) for e in _dominante_antes(niveles[nivel])
    )
    return list(islice(orden, max(maximo, 0)))


def _dominante_antes(entradas: Sequence[UrlCatalogo]) -> Iterator[UrlCatalogo]:
    """Las de la forma más frecuente (`_forma`) y después las demás, si esa forma tiene al menos
    `MIN_FORMA_DOMINANTE` de `entradas`; cada grupo, por `_legibles_antes` (causa R4-I).

    Las tiendas tienen más productos que categorías: Kenay (PrestaShop) pone en el mismo sitemap
    251 categorías sin extensión delante de 1 390 fichas `.html`, y sin palabras de la consulta
    en las URL se probaban tres categorías. Ni la categoría ni la categoría filtrada
    (`/gafas-de-sol/?prefn1=…`) llevan una palabra que `es_listado` reconozca. Perezoso: solo se
    mira la forma de los niveles a los que se llega.
    """
    formas = [_forma(entrada.url) for entrada in entradas]
    dominante, veces = Counter(formas).most_common(1)[0] if formas else ((), 0)
    if veces == len(entradas) or veces < MIN_FORMA_DOMINANTE * len(entradas):
        yield from _legibles_antes(entradas)
        return
    pares = list(zip(entradas, formas, strict=True))
    yield from _legibles_antes([entrada for entrada, forma in pares if forma == dominante])
    yield from _legibles_antes([entrada for entrada, forma in pares if forma != dominante])


def _forma(url: str) -> tuple[str, bool, bool]:
    """La forma de `url`: la extensión del último tramo, si la ruta acaba en barra y si lleva
    query (causa R4-I). Cortada a mano: son miles.

    El número de tramos no cuenta: cambia con el prefijo de idioma (`/gafas-de-sol/x.html` y
    `/fr/nos-lunettes-de-soleil/x.html` son fichas de la misma tienda).
    """
    ruta, _, query = url[:MAX_URL].partition("#")[0].partition("?")
    ruta = ruta.partition("://")[2].partition("/")[2]
    base, punto, extension = ruta.rpartition("/")[2].rpartition(".")
    if not (punto and base and extension.isascii() and extension.isalnum()):
        extension = ""
    if len(extension) > MAX_EXTENSION:
        extension = ""
    return extension.lower(), ruta.endswith("/"), bool(query)


def _legibles_antes(entradas: Sequence[UrlCatalogo]) -> Iterator[UrlCatalogo]:
    """Las de slug legible y después las demás. Perezoso: con las tres primeras basta, y mirar el
    slug de 100 000 URL cuesta."""
    legibles: dict[int, bool] = {}
    for posicion, entrada in enumerate(entradas):
        legibles[posicion] = fraccion_con_palabras((entrada.url,)) == 1.0
        if legibles[posicion]:
            yield entrada
    for posicion, entrada in enumerate(entradas):
        if not legibles[posicion]:
            yield entrada


def _tramos(url: str) -> list[str]:
    """Los tramos no vacíos de la ruta de `url`, en minúsculas (cortada a mano: son miles)."""
    ruta = url[:MAX_URL].partition("://")[2].partition("#")[0].partition("?")[0].partition("/")[2]
    return [tramo.lower() for tramo in ruta.split("/") if tramo]


def _prefijo_de_idioma(portada: str) -> tuple[str, ...]:
    """Los primeros tramos de la portada con forma de idioma o país (como mucho
    `MAX_TRAMOS_IDIOMA`): `/es/es/` → `("es", "es")`, `/es/` → `("es",)`, `/` → `()`.

    Que la portada redirija ahí es la prueba de que la tienda separa sus idiomas o países por
    prefijo (R4-C)."""
    prefijo: list[str] = []
    for tramo in _tramos(portada)[:MAX_TRAMOS_IDIOMA]:
        if not _IDIOMA.fullmatch(tramo):
            break
        prefijo.append(tramo)
    return tuple(prefijo)


def _de_otro_idioma(url: str, idioma: tuple[str, ...]) -> bool:
    """`url` es de otro idioma o país que la portada de prefijo `idioma` (R4-C).

    Lo es si, en el primer tramo en que se aparta de `idioma`, tiene otro con forma de idioma o
    país (`/au/en/…` o `/es/en/…` con la portada en `/es/es/`; `/pt/…` con la portada en `/es/`).
    Las URL sin prefijo (`/sitemap-pt-1.xml`, `/module/…`) y las de su idioma con una sección de
    dos letras detrás (`/es/tv/…`) no lo son. Sin `idioma`, ninguna.
    """
    if not idioma:
        return False
    tramos = _tramos(url)
    for posicion, codigo in enumerate(idioma):
        if posicion >= len(tramos):
            return False
        if tramos[posicion] != codigo:
            return bool(_IDIOMA.fullmatch(tramos[posicion]))
    return False


def _sin_traducciones(entradas: dict[str, UrlCatalogo]) -> dict[str, UrlCatalogo]:
    """Las `entradas` sin las de los prefijos que son traducciones (portada sin prefijo, R4-C).

    Un primer tramo con forma de idioma o país (`/fr/`) es una traducción solo si hay pruebas:
    más de la mitad de sus URL, y al menos `MIN_TRADUCIDAS`, acaban en el mismo tramo que alguna
    URL sin prefijo (Hawkers: `/gafas-de-sol/…-ON02.html` y `/fr/nos-lunettes-de-soleil/…-ON02
    .html`). Entonces salen todas las del prefijo (también las traducidas con otro nombre). Una
    sección como `/tv/` o `/pc/` no tiene sus fichas repetidas fuera y se queda entera.
    """
    finales_sin_prefijo: set[str] = set()
    por_prefijo: dict[str, list[str]] = {}
    for url in entradas:
        tramos = _tramos(url)
        if len(tramos) > 1 and _IDIOMA.fullmatch(tramos[0]):
            por_prefijo.setdefault(tramos[0], []).append(tramos[-1])
        elif tramos:
            finales_sin_prefijo.add(tramos[-1])
    traducidos = set()
    for prefijo, finales in por_prefijo.items():
        repetidas = sum(final in finales_sin_prefijo for final in finales)
        if repetidas >= MIN_TRADUCIDAS and 2 * repetidas > len(finales):
            traducidos.add(prefijo)
    if not traducidos:
        return entradas
    return {
        url: entrada
        for url, entrada in entradas.items()
        if not ((tramos := _tramos(url)) and len(tramos) > 1 and tramos[0] in traducidos)
    }


class _Lectura:
    """Los sitemaps de una tienda que se van leyendo, con el tope de ficheros compartido."""

    def __init__(
        self, origen: str, dominio: str, navegador: NavegadorGrande, validador: Validador
    ) -> None:
        self.origen = origen
        self.raiz = origen + "/"
        self.dominio = dominio
        self.navegador = navegador
        self.validador = validador
        self.descargas = 0
        self.respondidas = 0
        """Sitemaps descargados con un 2xx: la prueba de que la tienda responde (causa R4-D)."""
        self.vistos: set[str] = set()
        self.ultima_grande = False
        """La última descarga pasó del tope de tamaño (`DemasiadoGrande`, A5)."""
        self.idioma: tuple[str, ...] = ()
        """Prefijo de idioma o país de la portada (`_prefijo_de_idioma`), o el guardado con el
        catálogo al rehacerlo (R5-C); `()` si no lo tiene."""

    @classmethod
    def para(
        cls, url: str, dominio: str, navegador: NavegadorGrande, validador: Validador
    ) -> "_Lectura | None":
        origen = origen_de(url)
        normalizado = dominio_normalizado(dominio)
        if origen is None or normalizado is None:
            return None
        if url_de_la_tienda(origen + "/", origen + "/", normalizado) is None:
            # Un sitemap en un subdominio de la tienda (causa R4-B): la lectura sigue siendo de
            # la tienda, que es la que filtra las URL del catálogo.
            tienda = origen.split("://", 1)[0] + "://" + normalizado
            if url_de_sitemap(url, tienda) is None:
                return None  # `url` no es de la tienda.
            origen = tienda
        return cls(origen, normalizado, navegador, validador)

    @property
    def agotada(self) -> bool:
        return self.descargas >= MAX_FICHEROS_SITEMAP

    async def desde(self, raiz: str, *, tolerar_seco: bool = False) -> Catalogo | None:
        """El catálogo con `raiz` como sitemap raíz, o `None` si no da ninguna URL de la tienda.

        Con `tolerar_seco`, un rechazo seco (`Bloqueado.seco`) en la propia `raiz`, cuando otro
        sitemap de la lectura ya respondió con un 2xx, se salta como un 404. En sus hijos, nunca.

        Los sitemaps pendientes se leen por `prioridad` en cualquier nivel (de producto, neutros y
        secundarios) y, a igual prioridad, en el orden en que aparecieron: los feeds de un índice
        de artículos van antes que las comparativas del índice raíz, que gastaban el tope.
        """
        pendientes: list[tuple[int, int, str]] = [(PRIORIDAD_PRODUCTO, 0, raiz)]
        vistos_pendientes: set[str] = {raiz}
        llegada = count(1)
        entradas: dict[str, UrlCatalogo] = {}
        parcial = False
        # A5: hijo → el índice del que salió; índice → hijos suyos seguidos que no cupieron; y
        # los hijos de un índice cortado por `MAX_GRANDES_SEGUIDOS`, sin descargar, a pendientes.
        padres: dict[str, str] = {}
        grandes: dict[str, int] = {}
        aparcados: list[tuple[int, int, str]] = []
        while pendientes:
            siguiente = heapq.heappop(pendientes)
            url = siguiente[2]
            if url in self.vistos:
                continue
            padre = padres.get(url)
            if padre is not None and grandes.get(padre, 0) >= MAX_GRANDES_SEGUIDOS:
                parcial = True
                aparcados.append(siguiente)
                continue
            if self.agotada or len(entradas) >= MAX_URLS_CATALOGO:
                parcial = True  # Quedan sitemaps por leer.
                heapq.heappush(pendientes, siguiente)
                break
            self.vistos.add(url)
            self.descargas += 1
            sitemap = await self._descargar(url, tolerar_seco=tolerar_seco and url == raiz)
            if padre is not None:
                grandes[padre] = grandes.get(padre, 0) + 1 if self.ultima_grande else 0
            if sitemap is None:
                continue
            parcial = parcial or sitemap.recortado
            if sitemap.tipo == "indice":
                # Todos los hijos, sin el tope de `de_producto`: los que no se lleguen a leer
                # quedan en `Catalogo.pendientes` (ni más de los que caben: un índice hostil
                # puede traer 50 000).
                hijos = hijos_de_producto(sitemap)[: MAX_FICHEROS_SITEMAP + MAX_PENDIENTES]
                for hijo in sitemaps_de_la_tienda(hijos, self.origen):
                    clase = prioridad(hijo)
                    if (
                        hijo not in self.vistos
                        and hijo not in vistos_pendientes
                        and clase is not None
                    ):
                        if _de_otro_idioma(hijo, self.idioma):
                            clase = PRIORIDAD_OTRO_IDIOMA
                        vistos_pendientes.add(hijo)
                        padres[hijo] = url
                        heapq.heappush(pendientes, (clase, next(llegada), hijo))
            elif await asyncio.to_thread(self._anadir, sitemap, entradas):
                parcial = True
        if not self.idioma and entradas:
            entradas = await asyncio.to_thread(_sin_traducciones, entradas)
        if not entradas:
            return None
        sin_leer = (url for _, _, url in sorted(pendientes + aparcados) if url not in self.vistos)
        return Catalogo(
            raiz,
            tuple(entradas.values()),
            parcial,
            self.descargas,
            tuple(islice(dict.fromkeys(sin_leer), MAX_PENDIENTES)),
            self.idioma,
        )

    def _anadir(self, sitemap: Sitemap, entradas: dict[str, UrlCatalogo]) -> bool:
        """Añade las URL de la tienda de un `urlset`; `True` si alguna no cupo. Las de otro idioma
        o país que el de la portada no entran (R4-C)."""
        for entrada in sitemap.entradas:
            propia = url_de_la_tienda(entrada.url, self.raiz, self.dominio)
            if propia is None or propia in entradas or _de_otro_idioma(propia, self.idioma):
                continue
            if len(entradas) >= MAX_URLS_CATALOGO:
                return True
            entradas[propia] = UrlCatalogo(propia, palabras_url(propia), entrada.lastmod)
        return False

    async def _descargar(self, url: str, *, tolerar_seco: bool = False) -> Sitemap | None:
        """El sitemap de `url`, o `None` si no se puede descargar o no sigue en la tienda.

        Un `Bloqueado` se propaga, salvo un rechazo seco con `tolerar_seco` cuando la tienda ya
        ha respondido con un 2xx en esta lectura (causa R4-D, ver `desde`).
        """
        self.ultima_grande = False
        try:
            pagina = await self.navegador.obtener(await self.validador(url), grande=True)
        except DemasiadoGrande:
            self.ultima_grande = True
            return None
        except (ErrorDescarga, URLNoPermitida):
            return None
        except Bloqueado as bloqueo:
            if not (tolerar_seco and bloqueo.seco and self.respondidas):
                raise
            registro.info("Sitemap %s no disponible (%s): se salta.", url, bloqueo.motivo)
            return None
        if not 200 <= pagina.estado < 300:
            return None
        if url_de_sitemap(pagina.url_final or pagina.url, self.origen) is None:
            return None  # Ha redirigido fuera de la tienda y de sus subdominios.
        self.respondidas += 1
        # Leer 20 MiB de XML y normalizar 50 000 URL cuesta segundos de CPU: en un hilo, para no
        # parar el bucle de eventos (las demás tiendas de la búsqueda siguen descargando).
        return await asyncio.to_thread(leer_sitemap, pagina.html)
