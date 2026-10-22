"""Punto de entrada del paquete: obtener una página respetando todas las reglas.

Orden: validar la URL → robots.txt → caché → turno del dominio → descargar → detectar bloqueo →
guardar en caché. La caché va antes del turno para que una página ya guardada no espere el
intervalo de cortesía (no se pide nada a la tienda), pero después de robots: lo guardado tampoco
se sirve si el robots.txt ahora lo prohíbe.

Los ficheros grandes (`grande=True`, los sitemaps de la nota 16) siguen el mismo orden sin la
caché: ni se leen ni se guardan (un sitemap de 20 MiB no es una página que se vaya a reutilizar,
y el catálogo ya lo guarda el almacén). Nunca van por Chromium.

**Rescate con Chromium (ADR-023).** Si la descarga por HTTP de una página normal acaba en un
bloqueo (`detectar_bloqueo`) o en un corte de la conexión (`con_proxy.conexion_cortada`), se
prueba **una vez** con Chromium, en su propio turno de cortesía. Nunca tras un 429
(`MOTIVO_LIMITE`), un 401 ni un 451 (`bloqueo.ESTADOS_SIN_RESCATE`), ni para `js`, ficheros
grandes o el robots.txt.
Si Chromium también se bloquea, `Bloqueado`; si falla por otra cosa (no está instalado, no
arranca, murió o lanza un error inesperado), se relanza el error original, así que quien llama ve
lo mismo que sin Chromium. Una
tienda rescatada queda marcada en memoria (salvo que Chromium también diera un 403) y sus páginas
normales van ya directas a Chromium. Lo que descarga Chromium lleva `Pagina.por_chromium` y se
guarda en la caché como la versión `js`: pedir luego la misma URL con `js=True` no la descarga
otra vez, y quien llama puede saltarse esa segunda pasada. Con
un proxy, el descargador de Chromium es un `DescargadorConProxy` con el mismo uso: si la tienda ya
está marcada para el proxy, Chromium sale por él (un solo intento que cambia IP y cliente).
"""

import asyncio
import logging
from dataclasses import replace
from urllib.parse import urlsplit

from precia.navegador.bloqueo import ESTADOS_SIN_RESCATE, MOTIVO_LIMITE, detectar_bloqueo
from precia.navegador.cache import CachePaginas
from precia.navegador.con_proxy import conexion_cortada, dominio_de
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.http import MAX_BYTES_SITEMAP, MAXIMO_BYTES
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import (
    Bloqueado,
    Descargador,
    DescargadorGrande,
    ErrorDescarga,
    ErrorNavegador,
    Pagina,
    ProhibidoPorRobots,
    URLNoPermitida,
)
from precia.navegador.url import Validador, validar_url

registro = logging.getLogger(__name__)


class Navegador:
    def __init__(
        self,
        *,
        http: Descargador,
        chromium: Descargador | None = None,
        robots: ServicioRobots,
        limitador: LimitadorDominio,
        cache: CachePaginas | None = None,
        validador: Validador = validar_url,
        respetar_robots: bool = True,
    ) -> None:
        self._http = http
        self._chromium = chromium
        self._robots = robots
        self._limitador = limitador
        self._cache = cache
        self._validador = validador
        # ADR-017: Julio decide ignorar el robots.txt. Se sigue leyendo para respetar su
        # Crawl-delay, detectar un anti-bot y poder enseñar en el progreso qué se ha ignorado.
        self._respetar_robots = respetar_robots
        self._ignorados: set[str] = set()
        self._por_chromium: set[str] = set()
        """Tiendas (dominio sin «www.») rescatadas con Chromium: sus páginas van ya por él."""

    def robots_ignorado(self, dominio: str) -> bool:
        """Si en este dominio se ha visitado algo que su robots.txt prohíbe (ADR-017)."""
        return dominio.lower() in self._ignorados

    async def _comprobar_robots(self, url: str, dominio: str, mensaje: str) -> None:
        permitido = await self._robots.permitido(url)
        motivo = self._robots.bloqueo(url)
        if motivo is not None:
            # Un desafío anti-bot en el robots.txt no es «no hay robots.txt»: es un muro.
            raise Bloqueado(url, motivo)
        if permitido:
            return
        if self._respetar_robots:
            raise ProhibidoPorRobots(mensaje)
        self._ignorados.add(dominio.lower())

    async def sitemaps(self, url: str) -> tuple[str, ...]:
        """Sitemaps que declara el robots.txt de la tienda de `url` (nota 16, sección B).

        El robots.txt se lee aunque se ignore (ADR-017). Si es un desafío anti-bot, `Bloqueado`.
        Las URL devueltas son datos de la web: se descargan con `obtener`, que las valida, y quien
        las use comprueba que son de la misma tienda.
        """
        url = await self._validador(url)
        sitemaps = await self._robots.sitemaps(url)
        motivo = self._robots.bloqueo(url)
        if motivo is not None:
            raise Bloqueado(url, motivo)
        return sitemaps

    async def obtener(
        self, url: str, *, js: bool = False, usar_cache: bool = True, grande: bool = False
    ) -> Pagina:
        """Descarga `url` (con Chromium si `js`). Lanza los errores de `navegador.tipos`.

        Con `grande` (sitemaps), el tope es `MAX_BYTES_SITEMAP` y no se usa la caché. `js` y
        `grande` a la vez es un error de programación (`ValueError`).
        """
        if js and grande:
            raise ValueError("Un fichero grande (sitemap) nunca se descarga con Chromium.")
        url = await self._validador(url)
        dominio = urlsplit(url).hostname or ""
        await self._comprobar_robots(
            url, dominio, f"El robots.txt de {dominio} no permite visitar esa página."
        )

        tienda = dominio_de(url)
        if self._cache is not None and usar_cache and not grande:
            # En una tienda rescatada, lo guardado de Chromium vale también para `js=False`.
            claves = (js,) if js or tienda not in self._por_chromium else (False, True)
            for clave in claves:
                guardada = await asyncio.to_thread(self._cache.obtener, url, js=clave)
                if guardada is not None:
                    return replace(guardada, por_chromium=True) if clave else guardada

        chromium = self._chromium
        elegido = chromium if js else self._http
        if elegido is None:
            raise ErrorDescarga("No hay un navegador Chromium disponible para esta tienda.")
        descargador: Descargador = elegido
        if grande and not isinstance(descargador, DescargadorGrande):
            raise ErrorDescarga("Este navegador no puede descargar ficheros grandes (sitemaps).")
        rescatable = not js and not grande and chromium is not None
        if chromium is not None and rescatable and tienda in self._por_chromium:
            descargador = chromium
            rescatable = False

        original: ErrorNavegador | None = None
        try:
            pagina = await self._descargar(descargador, url, dominio, grande=grande)
        except ErrorDescarga as error:
            if not (rescatable and conexion_cortada(error)):
                raise
            original = error
        else:
            motivo = _motivo(pagina, grande=grande)
            if motivo is not None:
                if (
                    not rescatable
                    or motivo == MOTIVO_LIMITE
                    or pagina.estado in ESTADOS_SIN_RESCATE
                ):
                    # Se para y se registra; nunca se intenta resolver el desafío. El reintento
                    # por el proxy (desde otra IP) ya lo ha hecho `DescargadorConProxy`, si lo hay.
                    registro.warning("Bloqueo en %s: %s", dominio, motivo)
                    raise Bloqueado(url, motivo)
                original = Bloqueado(url, motivo)
        if original is not None:
            assert chromium is not None
            pagina = await self._rescatar(chromium, url, dominio, original)
            motivo = _motivo(pagina, grande=False)
            if motivo is not None:
                registro.warning("Bloqueo en %s también con Chromium: %s", dominio, motivo)
                raise Bloqueado(url, motivo)
            if pagina.estado != 403:
                # Un 403 grande con Chromium (sin señales de bloqueo) no es un rescate que haya
                # funcionado: la tienda no se marca y sus páginas siguen yendo por HTTP.
                self._por_chromium.add(tienda)
        if chromium is not None and (descargador is chromium or original is not None):
            pagina = replace(pagina, por_chromium=True)
        if pagina.url_final != url:
            # La redirección ya ocurrió, pero el contenido de una ruta prohibida no se usa.
            destino = urlsplit(pagina.url_final).hostname or ""
            await self._comprobar_robots(
                pagina.url_final,
                destino,
                f"La página redirige a una ruta que el robots.txt de {destino} no permite.",
            )
        if self._cache is not None and not grande and 200 <= pagina.estado < 300:
            await asyncio.to_thread(self._cache.guardar, pagina, js=pagina.por_chromium)
        return pagina

    async def _descargar(
        self, descargador: Descargador, url: str, dominio: str, *, grande: bool
    ) -> Pagina:
        """Una petición en su propio turno de cortesía del dominio."""
        async with self._limitador.turno(dominio, retardo_minimo=self._robots.retardo(dominio)):
            if grande:
                assert isinstance(descargador, DescargadorGrande)
                return await descargador.descargar_grande(url, max_bytes=MAX_BYTES_SITEMAP)
            return await descargador.descargar(url)

    async def _rescatar(
        self, chromium: Descargador, url: str, dominio: str, original: ErrorNavegador
    ) -> Pagina:
        """El único intento con Chromium tras un bloqueo o un corte por HTTP (ver arriba)."""
        registro.warning(
            "%s bloqueó la descarga por HTTP (%s): se prueba una vez con Chromium.",
            dominio,
            original,
        )
        try:
            return await self._descargar(chromium, url, dominio, grande=False)
        except (URLNoPermitida, ProhibidoPorRobots):
            raise
        except Exception as fallo:
            # El rescate es un extra: ni un error inesperado de Chromium cambia lo que ve quien
            # llama. Si no es un `ErrorDescarga`, se registra con su traza para poder arreglarlo.
            registro.warning(
                "Chromium tampoco pudo con %s: %s",
                dominio,
                fallo,
                exc_info=not isinstance(fallo, ErrorDescarga),
            )
            if isinstance(original, Bloqueado):
                raise original from fallo
            # El corte original, con su causa intacta (quien mira la cadena sigue viéndolo).
            raise original  # noqa: B904

    async def cerrar(self) -> None:
        await self._http.cerrar()
        if self._chromium is not None:
            await self._chromium.cerrar()


def _motivo(pagina: Pagina, *, grande: bool) -> str | None:
    # Un fichero grande solo se mira hasta el tope de una página normal: una página de bloqueo
    # nunca es mayor, y la detección corre en el bucle de eventos (20 MiB costaban ~2 s).
    vista = replace(pagina, html=pagina.html[:MAXIMO_BYTES]) if grande else pagina
    return detectar_bloqueo(vista)
