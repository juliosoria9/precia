"""Catálogo de una tienda sin buscador por su `sitemap.xml` (ADR-018, nota 16 §B).

El configurador lo usa cuando ni la API de la plataforma ni el buscador de la tienda sirven, y el
orquestador, para rehacer el catálogo de un perfil `sitemap` caducado. Pasos:

1. Las líneas `Sitemap:` del robots.txt de la tienda (`Navegador.sitemaps`, que lo lee aunque se
   ignore por ADR-017) y, detrás, las rutas habituales y las de la plataforma
   (`fuentes.sitemap.candidatos`, que ya descarta las declaradas de otro dominio).
2. Se descargan con `Navegador.obtener(..., grande=True)` (tope de 20 MiB descomprimidos, `.xml.gz`
   incluidos, sin Chromium ni caché), como mucho `MAX_FICHEROS_SITEMAP` ficheros entre índices y
   `urlset`, **contando los que fallan**: una tienda (o su robots.txt) no puede multiplicar las
   descargas. De un índice solo se siguen los sitemaps de producto (`fuentes.sitemap.de_producto`).
3. El primer candidato que da URL de la tienda es el sitemap raíz del catálogo: de cada URL se
   guardan solo la URL, las palabras de su ruta (`palabras_url`) y el `lastmod` (art. 133 TRLPI:
   nada de precios ni descripciones), como mucho `MAX_URLS_CATALOGO`.

Todo lo que sale de un sitemap es dato de la web: cada sitemap hijo y cada URL pasan por el filtro
de mismo dominio (`url_de_la_tienda`: con o sin `www.`, sin usuario ni puerto raro, normalizada)
antes de descargarla o guardarla, y cada descarga, por el validador anti-SSRF (`validar_url`) y la
comprobación de que la respuesta sigue siendo de la tienda tras las redirecciones. Cortesía y
bloqueos son los del navegador: un `Bloqueado` (en el robots.txt o en un sitemap) se propaga y
se para; un sitemap que no existe, no se puede descargar o no se puede leer solo se salta.

El catálogo es `parcial` si se llegó a algún tope o algún sitemap venía recortado. Leer y
normalizar un sitemap grande es trabajo de CPU (unos 7 s por 50 000 URL medidos en los tests): se
hace en un hilo (`asyncio.to_thread`) para no parar el bucle de eventos.
"""

import asyncio
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import islice
from typing import NamedTuple, Protocol, runtime_checkable

from precia.emparejado.texto import normalizar_titulo
from precia.extraccion.listado import dominio_normalizado, url_de_la_tienda
from precia.fuentes.plataformas import Plataforma, origen_de
from precia.fuentes.sitemap import (
    MAX_FICHEROS_SITEMAP,
    Sitemap,
    candidatos,
    de_la_tienda,
    de_producto,
    leer_sitemap,
    palabras_url,
)
from precia.fuentes.sitemap import con_palabras as fraccion_con_palabras
from precia.navegador.tipos import ErrorDescarga, Pagina, ProhibidoPorRobots, URLNoPermitida
from precia.navegador.url import Validador, validar_url
from precia.nucleo.modelos import InfoCatalogo
from precia.perfiles.validador import MAX_FICHAS
from precia.perfiles.validador import de_la_tienda as sigue_en_la_tienda

MAX_URLS_CATALOGO = 100_000
"""URL que se guardan como mucho (como `almacen.sqlite.MAX_CATALOGO`, que no se importa aquí para
que `perfiles` no dependa del almacén)."""
MIN_CON_PALABRAS = 0.6
"""Fracción de URL con slug legible por debajo de la cual el catálogo no sirve para buscar
(`/p/123456`): se pasa al listado del usuario (A2) o se falla."""
MIN_PALABRA = 3
MAX_CONSULTA = 500
MAX_PALABRAS_CONSULTA = 12


@runtime_checkable
class NavegadorGrande(Protocol):
    """El navegador del catálogo: descarga ficheros grandes (sitemaps) con `grande=True`."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina: ...


@runtime_checkable
class NavegadorSitemaps(NavegadorGrande, Protocol):
    """Además, lee las líneas `Sitemap:` del robots.txt de la tienda (lanza `Bloqueado`)."""

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

    `dominio` es el de la tienda (con o sin `www.`). Ver el docstring del módulo.
    """
    lectura = _Lectura.para(base, dominio, navegador, validador)
    if lectura is None:
        return None
    try:
        declarados = await navegador.sitemaps(lectura.raiz)
    except (ErrorDescarga, ProhibidoPorRobots, URLNoPermitida):
        declarados = ()  # Sin robots.txt legible quedan las rutas habituales.
    for candidato in candidatos(lectura.origen, declarados, plataforma):
        if lectura.agotada:
            break
        catalogo = await lectura.desde(candidato)
        if catalogo is not None:
            return catalogo
    return None


async def leer_catalogo(
    sitemap: str,
    dominio: str,
    navegador: NavegadorGrande,
    *,
    validador: Validador = validar_url,
) -> Catalogo | None:
    """El catálogo desde un sitemap raíz ya conocido (el de un perfil `sitemap`), o `None`.

    Para rehacer un catálogo caducado: mismos topes y filtros, sin mirar el robots.txt. Un
    `sitemap` que no es de la tienda no se descarga.
    """
    lectura = _Lectura.para(sitemap, dominio, navegador, validador)
    if lectura is None:
        return None
    propio = url_de_la_tienda(sitemap, lectura.raiz, lectura.dominio)
    if propio is None or not sitemap.lower().startswith(("http://", "https://")):
        return None
    return await lectura.desde(propio)


def elegir_fichas(catalogo: Catalogo, consulta: str, *, maximo: int = MAX_FICHAS) -> list[str]:
    """Las `maximo` URL del catálogo con las que probar el perfil `sitemap`.

    Primero las que tienen más palabras de `consulta` (de 3 letras o más, normalizadas como en
    el almacén) en sus `palabras`; a igualdad, las de slug legible antes que la portada, un
    aviso legal o un `/p/123456`; y si no, en el orden del sitemap.
    """
    texto = normalizar_titulo(consulta[:MAX_CONSULTA])
    palabras = [p for p in dict.fromkeys(texto.split()) if len(p) >= MIN_PALABRA]
    palabras = palabras[:MAX_PALABRAS_CONSULTA]
    niveles: dict[int, list[UrlCatalogo]] = {}
    for entrada in catalogo.entradas:
        encajan = sum(p in entrada.palabras for p in palabras)
        niveles.setdefault(encajan, []).append(entrada)
    orden = (
        e.url for nivel in sorted(niveles, reverse=True) for e in _legibles_antes(niveles[nivel])
    )
    return list(islice(orden, max(maximo, 0)))


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
        self.vistos: set[str] = set()

    @classmethod
    def para(
        cls, url: str, dominio: str, navegador: NavegadorGrande, validador: Validador
    ) -> "_Lectura | None":
        origen = origen_de(url)
        normalizado = dominio_normalizado(dominio)
        if origen is None or normalizado is None:
            return None
        if url_de_la_tienda(origen + "/", origen + "/", normalizado) is None:
            return None  # `url` no es de la tienda.
        return cls(origen, normalizado, navegador, validador)

    @property
    def agotada(self) -> bool:
        return self.descargas >= MAX_FICHEROS_SITEMAP

    async def desde(self, raiz: str) -> Catalogo | None:
        """El catálogo con `raiz` como sitemap raíz, o `None` si no da ninguna URL de la tienda."""
        pendientes = deque([raiz])
        entradas: dict[str, UrlCatalogo] = {}
        parcial = False
        while pendientes:
            url = pendientes.popleft()
            if url in self.vistos:
                continue
            if self.agotada or len(entradas) >= MAX_URLS_CATALOGO:
                parcial = True  # Quedan sitemaps por leer.
                break
            self.vistos.add(url)
            self.descargas += 1
            sitemap = await self._descargar(url)
            if sitemap is None:
                continue
            parcial = parcial or sitemap.recortado
            if sitemap.tipo == "indice":
                hijos = de_la_tienda(de_producto(sitemap), self.origen)
                pendientes.extend(h for h in hijos if h not in self.vistos)
            elif await asyncio.to_thread(self._anadir, sitemap, entradas):
                parcial = True
        if not entradas:
            return None
        return Catalogo(raiz, tuple(entradas.values()), parcial, self.descargas)

    def _anadir(self, sitemap: Sitemap, entradas: dict[str, UrlCatalogo]) -> bool:
        """Añade las URL de la tienda de un `urlset`; `True` si alguna no cupo."""
        for entrada in sitemap.entradas:
            propia = url_de_la_tienda(entrada.url, self.raiz, self.dominio)
            if propia is None or propia in entradas:
                continue
            if len(entradas) >= MAX_URLS_CATALOGO:
                return True
            entradas[propia] = UrlCatalogo(propia, palabras_url(propia), entrada.lastmod)
        return False

    async def _descargar(self, url: str) -> Sitemap | None:
        """El sitemap de `url`, o `None` si no se puede descargar o no sigue en la tienda."""
        try:
            pagina = await self.navegador.obtener(await self.validador(url), grande=True)
        except (ErrorDescarga, ProhibidoPorRobots, URLNoPermitida):
            return None
        if not 200 <= pagina.estado < 300 or not sigue_en_la_tienda(pagina, self.dominio):
            return None
        # Leer 20 MiB de XML y normalizar 50 000 URL cuesta segundos de CPU: en un hilo, para no
        # parar el bucle de eventos (las demás tiendas de la búsqueda siguen descargando).
        return await asyncio.to_thread(leer_sitemap, pagina.html)
