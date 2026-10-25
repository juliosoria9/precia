"""robots.txt según el RFC 9309.

No se usa `urllib.robotparser`: no entiende los comodines `*` y `$`, aplica la primera regla en
lugar de la más larga y elige el grupo por subcadena del User-Agent. Con él, reglas muy comunes en
las tiendas como `Disallow: /*?order=` no se respetarían.

- Se aplica el grupo de nuestro *product token* (los grupos repetidos se fusionan) y, si no hay,
  el de `*`. Sin grupo aplicable se permite todo.
- Gana la regla más larga que coincide; a igual longitud, `Allow`.
- Un 4xx significa que no hay robots.txt (se permite todo); un 5xx o un error de red, que no se
  puede saber (se prohíbe todo). Un 429 se trata como 5xx: la web pide que bajemos el ritmo.
  Ese «prohíbe todo» no es una regla leída: el origen queda marcado (`ilegible`) y el navegador no
  lo anota como ignorado, porque el aviso «lo prohíbe; se ignora» sería falso.
- Excepción al RFC: si la respuesta es un desafío anti-bot (p. ej. un 403 de Cloudflare, caso
  real de oscaro.es), no es que no haya robots.txt, es que no se puede leer. Se prohíbe todo y se
  recuerda el motivo (`bloqueo`): el navegador, que nunca deja de visitar por el robots.txt
  (ADR-017), lo trata como si no lo hubiera, sin anotarlo como ignorado: la página detecta su
  propio bloqueo y tiene su rescate con Chromium (ADR-023, causa R3-B).
- Lo que se lee aquí solo sirve para avisar de lo que prohíbe, para el Crawl-delay y para los
  sitemaps: ninguna regla impide una visita (enmienda de ADR-017 del 2026-10-25).
- El robots.txt es una petición más a la tienda: con un limitador (`usar_turnos`, lo pone el
  `Navegador`) se pide en un turno de su dominio, así que nunca coincide con otra petición nuestra
  a la misma tienda (ADR-017), tampoco con la de otra búsqueda simultánea (B6).
"""

import asyncio
import math
import re
import string
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

from precia.navegador.bloqueo import detectar_bloqueo
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.tipos import Descargador, DescargadorConTiempo, ErrorNavegador, Pagina

MAXIMO_BYTES = 500 * 1024
MAXIMO_RETARDO_S = 60.0
TTL_S = 24 * 3600.0
# Un fallo pasajero del servidor no debe dejar la tienda prohibida un día entero.
TTL_ERROR_S = 10 * 60.0
TIEMPO_ROBOTS_S = 10.0
"""Tiempo total del robots.txt (A7), si el descargador lo admite: un fichero de texto pequeño que
una tienda lenta tardaba en dar hasta 19,6 s. Si falla, cuenta como error y se guarda
`TTL_ERROR_S`. No baja de 10 s (lo mismo que una página): con 5 s fallaban robots.txt válidos de
la campaña (tiendanimal.es 8,18 s, amazon.es 8,31 s), la tienda salía como «Lo prohíbe» y se
perdían sus sitemaps durante `TTL_ERROR_S`."""
MAX_SITEMAPS_ROBOTS = 20
"""Líneas `Sitemap:` que se guardan de un robots.txt (nota 16, sección B); las demás se ignoran."""
MAX_LONGITUD_SITEMAP = 2000
"""Caracteres de una URL de sitemap: una más larga no es de fiar y se descarta."""

_NO_RESERVADOS = frozenset(string.ascii_letters + string.digits + "-._~")
_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
# Se conservan los reservados de RFC 3986, el «%» ya escrito y los comodines; el resto se escapa.
_SEGUROS = "/%:@!$&'()*+,;=?#[]-._~"


@dataclass(frozen=True, slots=True)
class ReglasRobots:
    """Reglas que aplican a nuestro User-Agent: (permitir, patrón normalizado)."""

    reglas: tuple[tuple[bool, str], ...] = ()
    retardo: float | None = None
    sitemaps: tuple[str, ...] = ()
    """URL absolutas http(s) de las líneas `Sitemap:` (de fuera de grupo), sin repetir."""

    def permite(self, ruta: str) -> bool:
        """`ruta` es la ruta con la consulta (`/buscar?q=x`), tal como sale de la URL."""
        if ruta == "/robots.txt":
            return True
        ruta = _normalizar(ruta)
        mejor: tuple[int, bool] | None = None
        for permitir, patron in self.reglas:
            if _coincide(patron, ruta):
                candidata = (len(patron), permitir)
                if mejor is None or candidata > mejor:
                    mejor = candidata
        return True if mejor is None else mejor[1]


PERMITIR_TODO = ReglasRobots()
PROHIBIR_TODO = ReglasRobots(reglas=((False, "/"),))


def token_producto(user_agent: str) -> str:
    """«PrecIA/0.1 (+https://...)» → «precia»: lo que se compara con las líneas `User-agent`."""
    return re.split(r"[/\s]", user_agent.strip(), maxsplit=1)[0].lower()


@dataclass(slots=True)
class _Grupo:
    agentes: list[str] = field(default_factory=list)
    reglas: list[tuple[bool, str]] = field(default_factory=list)
    retardo: float | None = None


def analizar_robots(texto: str, token: str) -> ReglasRobots:
    """Interpreta el robots.txt y devuelve las reglas que aplican al `token` de producto."""
    grupos: list[_Grupo] = []
    actual: _Grupo | None = None
    leyendo_agentes = False
    sitemaps: list[str] = []
    for linea in _recortar(texto).removeprefix("﻿").splitlines():
        linea = linea.split("#", 1)[0].strip()
        clave, separador, valor = linea.partition(":")
        if not separador:
            continue
        clave, valor = clave.strip().lower(), valor.strip()
        if clave == "sitemap":
            # Registro de fuera de grupo (RFC 9309 §2.2.4): vale esté donde esté y no corta el
            # grupo en curso. Se guardan los primeros, sin repetir.
            if len(sitemaps) < MAX_SITEMAPS_ROBOTS:
                url = _url_sitemap(valor)
                if url is not None and url not in sitemaps:
                    sitemaps.append(url)
        elif clave == "user-agent":
            if actual is None or not leyendo_agentes:
                actual = _Grupo()
                grupos.append(actual)
            actual.agentes.append(valor.lower())
            leyendo_agentes = True
        elif clave in ("allow", "disallow", "crawl-delay"):
            leyendo_agentes = False
            if actual is None:
                continue  # regla antes de cualquier User-agent: no pertenece a ningún grupo
            if clave == "crawl-delay":
                actual.retardo = _leer_retardo(valor)
            elif valor:
                actual.reglas.append((clave == "allow", _normalizar(valor)))

    token = token.lower()
    aplicables = [g for g in grupos if any(_agente_coincide(a, token) for a in g.agentes)]
    if not aplicables:
        aplicables = [g for g in grupos if "*" in g.agentes]
    retardos = [g.retardo for g in aplicables if g.retardo is not None]
    return ReglasRobots(
        reglas=tuple(r for g in aplicables for r in g.reglas),
        retardo=max(retardos, default=None),
        sitemaps=tuple(sitemaps),
    )


@dataclass(slots=True)
class _Entrada:
    reglas: ReglasRobots
    caduca: float


class ServicioRobots:
    """Descarga y guarda en memoria el robots.txt de cada origen."""

    def __init__(
        self,
        descargador: Descargador,
        user_agent: str,
        *,
        ttl: float = TTL_S,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._descargador = descargador
        self._token = token_producto(user_agent)
        self._ttl = ttl
        self._reloj = reloj
        self._cache: dict[str, _Entrada] = {}
        self._candados: dict[str, asyncio.Lock] = {}
        self._bloqueos: dict[str, str] = {}
        self._ilegibles: set[str] = set()
        """Orígenes cuyo último robots.txt no se pudo leer (5xx, 429, error de red o desafío)."""
        self._limitador: LimitadorDominio | None = None

    def usar_turnos(self, limitador: LimitadorDominio) -> None:
        """Pide cada robots.txt en un turno de cortesía de su dominio (ADR-017)."""
        self._limitador = limitador

    async def permitido(self, url: str) -> bool:
        """`url` ya validada. Indica si nuestro User-Agent puede pedirla."""
        partes = urlsplit(url)
        ruta = (partes.path or "/") + (f"?{partes.query}" if partes.query else "")
        reglas = await self._reglas(f"{partes.scheme}://{partes.netloc}")
        return reglas.permite(ruta)

    async def sitemaps(self, url: str) -> tuple[str, ...]:
        """`url` ya validada. Sitemaps declarados en el robots.txt de su origen.

        Usa el robots.txt ya leído o lo lee entonces (se ignora, ADR-017, pero se lee). Si no hay
        robots.txt, no se pudo leer o era un desafío anti-bot, no hay ninguno. Las URL son datos
        de la web: quien las descargue las valida y comprueba que son de la misma tienda.
        """
        partes = urlsplit(url)
        return (await self._reglas(f"{partes.scheme}://{partes.netloc}")).sitemaps

    def bloqueo(self, url: str) -> str | None:
        """Motivo si el robots.txt de ese origen respondió con un desafío anti-bot."""
        partes = urlsplit(url)
        return self._bloqueos.get(f"{partes.scheme}://{partes.netloc}")

    def ilegible(self, url: str) -> bool:
        """Si el robots.txt de ese origen no se pudo leer: su «prohíbe todo» no es una regla de la
        tienda, así que no hay nada que avisar como ignorado (ADR-017)."""
        partes = urlsplit(url)
        return f"{partes.scheme}://{partes.netloc}" in self._ilegibles

    def retardo(self, dominio: str) -> float | None:
        """Crawl-delay de los robots.txt ya leídos para ese host (sin esquema ni puerto)."""
        retardos = [
            e.reglas.retardo
            for origen, e in self._cache.items()
            if urlsplit(origen).hostname == dominio.lower() and e.reglas.retardo is not None
        ]
        return max(retardos, default=None)

    async def _reglas(self, origen: str) -> ReglasRobots:
        candado = self._candados.setdefault(origen, asyncio.Lock())
        async with candado:
            entrada = self._cache.get(origen)
            if entrada is not None and self._reloj() < entrada.caduca:
                return entrada.reglas
            reglas, ttl = await self._descargar(origen)
            self._cache[origen] = _Entrada(reglas, self._reloj() + ttl)
            return reglas

    async def _descargar(self, origen: str) -> tuple[ReglasRobots, float]:
        reglas, ttl = await self._descargar_reglas(origen)
        if reglas is PROHIBIR_TODO:
            self._ilegibles.add(origen)
        else:
            self._ilegibles.discard(origen)
        return reglas, ttl

    async def _descargar_reglas(self, origen: str) -> tuple[ReglasRobots, float]:
        try:
            url = f"{origen}/robots.txt"
            if self._limitador is None:
                pagina = await self._pedir(url)
            else:
                host = urlsplit(origen).hostname or ""
                async with self._limitador.turno(host, retardo_minimo=self.retardo(host)):
                    pagina = await self._pedir(url)
        except ErrorNavegador:
            return PROHIBIR_TODO, min(self._ttl, TTL_ERROR_S)
        motivo = detectar_bloqueo(pagina)
        if motivo is not None:
            self._bloqueos[origen] = motivo
            return PROHIBIR_TODO, min(self._ttl, TTL_ERROR_S)
        self._bloqueos.pop(origen, None)
        if 200 <= pagina.estado < 300:
            return analizar_robots(pagina.html, self._token), self._ttl
        if 400 <= pagina.estado < 500 and pagina.estado != 429:
            return PERMITIR_TODO, self._ttl
        return PROHIBIR_TODO, min(self._ttl, TTL_ERROR_S)

    async def _pedir(self, url: str) -> Pagina:
        if isinstance(self._descargador, DescargadorConTiempo):
            return await self._descargador.descargar_con_tiempo(url, TIEMPO_ROBOTS_S)
        return await self._descargador.descargar(url)


def _agente_coincide(agente: str, token: str) -> bool:
    return agente == token or agente.split("/", 1)[0].strip() == token


def _url_sitemap(valor: str) -> str | None:
    """`valor` si es una URL absoluta http(s) con host, sin espacios ni controles; si no, `None`."""
    if not valor or len(valor) > MAX_LONGITUD_SITEMAP:
        return None
    if any(c.isspace() or not c.isprintable() for c in valor):
        return None
    try:
        partes = urlsplit(valor)
    except ValueError:
        return None  # p. ej. un IPv6 sin cerrar
    if partes.scheme.lower() not in ("http", "https") or not partes.hostname:
        return None
    return valor


def _leer_retardo(valor: str) -> float | None:
    try:
        segundos = float(valor)
    except ValueError:
        return None
    if not math.isfinite(segundos) or segundos < 0:
        return None
    return min(segundos, MAXIMO_RETARDO_S)


def _recortar(texto: str) -> str:
    datos = texto.encode("utf-8")
    if len(datos) <= MAXIMO_BYTES:
        return texto
    recortado = datos[:MAXIMO_BYTES].decode("utf-8", "ignore")
    # La última línea puede haber quedado a medias («Allow: /» de «Allow: /solo-esto»).
    fin = max(recortado.rfind("\n"), recortado.rfind("\r"))
    return recortado[:fin] if fin >= 0 else ""


def _normalizar(ruta: str) -> str:
    """Forma canónica para comparar: lo no ASCII, escapado en UTF-8; los `%XX` no reservados, sin
    escapar."""

    def decodificar(m: re.Match[str]) -> str:
        caracter = chr(int(m[1], 16))
        return caracter if caracter in _NO_RESERVADOS else f"%{m[1].upper()}"

    return _ESCAPE.sub(decodificar, quote(ruta, safe=_SEGUROS))


def _coincide(patron: str, ruta: str) -> bool:
    """Coincidencia de prefijo con `*` (cualquier secuencia) y `$` final (fin de la ruta).

    Sin expresiones regulares: un patrón con muchos `*` escrito por la web no puede provocar un
    retroceso exponencial. Basta con buscar cada trozo lo más a la izquierda posible.
    """
    anclado = patron.endswith("$")
    trozos = (patron[:-1] if anclado else patron).split("*")
    if not ruta.startswith(trozos[0]):
        return False
    posicion = len(trozos[0])
    if len(trozos) == 1:
        return not anclado or posicion == len(ruta)
    for trozo in trozos[1:-1]:
        indice = ruta.find(trozo, posicion)
        if indice < 0:
            return False
        posicion = indice + len(trozo)
    ultimo = trozos[-1]
    if anclado:
        return len(ruta) - len(ultimo) >= posicion and ruta.endswith(ultimo)
    return ruta.find(ultimo, posicion) >= 0
