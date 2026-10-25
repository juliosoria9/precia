"""Punto de entrada del paquete: obtener una página respetando todas las reglas.

Orden: validar la URL → robots.txt (en su propio turno del dominio) → caché → turno del
dominio → descargar → detectar bloqueo → guardar en caché. La caché va antes del turno para que
una página ya guardada no espere el intervalo de cortesía (no se pide nada a la tienda), pero
después de robots: también lo guardado cuenta para el aviso de «lo prohíbe; se ignora».

**El robots.txt nunca impide visitar (ADR-017 y su enmienda del 2026-10-25).** Se lee para su
Crawl-delay, sus sitemaps y para anotar lo que prohíbe (`robots_ignorado`), que el progreso enseña
como aviso. Si el propio robots.txt es un desafío anti-bot, no se ha leído nada que prohíba: se
registra un aviso (una vez por dominio), no se anota como «ignorado» y se sigue con la página, que
detecta su propio bloqueo y tiene el rescate de abajo (causa R3-B, notino.es).

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

**Tiendas bloqueadas recordadas (A8).** Si una página normal sale `Bloqueado` después de haberlo
probado todo (el reintento por el proxy, si lo hay, y Chromium: el rescate o la tienda ya iba por
él), la tienda se recuerda en memoria `BLOQUEO_RECORDADO_S`. Mientras dura, lo que no está en la
caché sale `Bloqueado` al instante, sin pedir nada a la tienda (son menos peticiones, ADR-017).
No se recuerdan un 429 normal (la tienda pide ir más despacio), un rechazo seco (puede ser solo esa
ruta, causa R4-D) ni un bloqueo sin Chromium. «Reintentar» lo olvida (`olvidar_bloqueo`).
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import urlsplit

from precia.navegador.bloqueo import (
    ESTADOS_SIN_RESCATE,
    MOTIVO_LIMITE,
    detectar_bloqueo,
    es_rechazo_seco,
)
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
    URLNoPermitida,
)
from precia.navegador.url import Validador, validar_url

registro = logging.getLogger(__name__)

BLOQUEO_RECORDADO_S = 30 * 60.0
"""Cuánto se da por bloqueada una tienda que ya lo estaba tras el proxy y Chromium (A8)."""


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
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._chromium = chromium
        self._robots = robots
        # El robots.txt también es una petición a la tienda: va en su turno (ADR-017).
        robots.usar_turnos(limitador)
        self._limitador = limitador
        self._cache = cache
        self._validador = validador
        # ADR-017 (y su enmienda del 2026-10-25): el robots.txt nunca impide visitar nada. Se
        # sigue leyendo para respetar su Crawl-delay, sacar sus sitemaps y poder enseñar en el
        # progreso qué se ha ignorado.
        self._ignorados: set[str] = set()
        self._por_chromium: set[str] = set()
        """Tiendas (dominio sin «www.») rescatadas con Chromium: sus páginas van ya por él."""
        self._robots_ilegibles: set[str] = set()
        """Hosts cuyo robots.txt era un desafío anti-bot, ya avisados en el registro (R3-B)."""
        self._reloj = reloj
        self._bloqueadas: dict[str, tuple[str, float]] = {}
        """Tienda (dominio sin «www.») → (motivo, hasta cuándo) del bloqueo recordado (A8)."""

    def olvidar_bloqueo(self, dominio: str) -> None:
        """«Reintentar» (A8): la próxima página de la tienda se vuelve a pedir."""
        self._bloqueadas.pop(dominio.lower().removeprefix("www."), None)

    def _bloqueo_recordado(self, tienda: str, url: str) -> None:
        recordado = self._bloqueadas.get(tienda)
        if recordado is None:
            return
        motivo, hasta = recordado
        if self._reloj() >= hasta:
            del self._bloqueadas[tienda]
            return
        minutos = max(1, round((hasta - self._reloj()) / 60))
        raise Bloqueado(
            url,
            f"{motivo}; no se vuelve a pedir nada a la tienda en {minutos} min "
            "(pulsa «Reintentar» para probar antes)",
        )

    def _recordar_bloqueo(self, tienda: str, motivo: str, *, seco: bool) -> None:
        if motivo == MOTIVO_LIMITE or seco:
            return
        self._bloqueadas[tienda] = (motivo, self._reloj() + BLOQUEO_RECORDADO_S)

    def robots_ignorado(self, dominio: str) -> bool:
        """Si en este dominio se ha visitado algo que su robots.txt prohíbe (ADR-017)."""
        return dominio.lower() in self._ignorados

    async def _comprobar_robots(self, url: str, dominio: str) -> None:
        """Anota si el robots.txt prohíbe `url`, para el aviso del progreso. Nunca corta."""
        permitido = await self._robots.permitido(url)
        motivo = self._robots.bloqueo(url)
        if motivo is not None:
            # ADR-017: no se ha leído nada que prohíba. Ni se anota como ignorado (el desafío no
            # es una regla de la tienda) ni se corta aquí: la página detecta su propio bloqueo y
            # tiene su rescate con Chromium (ADR-023).
            self._avisar_robots_ilegible(dominio, motivo)
            return
        if not permitido and not self._robots.ilegible(url):
            # Un 5xx, un 429 o un error de red tampoco son reglas leídas: sin aviso.
            self._ignorados.add(dominio.lower())

    async def sitemaps(self, url: str) -> tuple[str, ...]:
        """Sitemaps que declara el robots.txt de la tienda de `url` (nota 16, sección B).

        El robots.txt se lee aunque se ignore (ADR-017). Si es un desafío anti-bot, no hay
        ninguno (`()`): quien llama sigue con las rutas habituales, y si también son un desafío,
        sale `Bloqueado` de su descarga (R3-B).
        Las URL devueltas son datos de la web: se descargan con `obtener`, que las valida, y quien
        las use comprueba que son de la misma tienda.
        """
        url = await self._validador(url)
        sitemaps = await self._robots.sitemaps(url)
        motivo = self._robots.bloqueo(url)
        if motivo is not None:
            self._avisar_robots_ilegible(urlsplit(url).hostname or "", motivo)
            return ()
        return sitemaps

    def _avisar_robots_ilegible(self, dominio: str, motivo: str) -> None:
        if dominio.lower() in self._robots_ilegibles:
            return
        self._robots_ilegibles.add(dominio.lower())
        registro.warning(
            "El robots.txt de %s es un desafío anti-bot (%s): no se puede leer y se ignora "
            "(ADR-017); se sigue con las páginas.",
            dominio,
            motivo,
        )

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
        await self._comprobar_robots(url, dominio)

        tienda = dominio_de(url)
        if self._cache is not None and usar_cache and not grande:
            # En una tienda rescatada, lo guardado de Chromium vale también para `js=False`.
            claves = (js,) if js or tienda not in self._por_chromium else (False, True)
            for clave in claves:
                guardada = await asyncio.to_thread(self._cache.obtener, url, js=clave)
                if guardada is not None:
                    return replace(guardada, por_chromium=True) if clave else guardada

        self._bloqueo_recordado(tienda, url)
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
                seco = es_rechazo_seco(pagina, motivo)
                if (
                    not rescatable
                    or motivo == MOTIVO_LIMITE
                    or pagina.estado in ESTADOS_SIN_RESCATE
                ):
                    # Se para y se registra; nunca se intenta resolver el desafío. El reintento
                    # por el proxy (desde otra IP) ya lo ha hecho `DescargadorConProxy`, si lo hay.
                    registro.warning("Bloqueo en %s: %s", dominio, motivo)
                    if chromium is not None and descargador is chromium:
                        # Ya iba por Chromium (y por el proxy, si lo hay): no queda nada más.
                        self._recordar_bloqueo(tienda, motivo, seco=seco)
                    raise Bloqueado(url, motivo, seco=seco)
                original = Bloqueado(url, motivo, seco=seco)
        if original is not None:
            assert chromium is not None
            pagina = await self._rescatar(chromium, url, dominio, original)
            motivo = _motivo(pagina, grande=False)
            if motivo is not None:
                registro.warning("Bloqueo en %s también con Chromium: %s", dominio, motivo)
                seco = es_rechazo_seco(pagina, motivo)
                self._recordar_bloqueo(tienda, motivo, seco=seco)
                raise Bloqueado(url, motivo, seco=seco)
            if pagina.estado != 403:
                # Un 403 grande con Chromium (sin señales de bloqueo) no es un rescate que haya
                # funcionado: la tienda no se marca y sus páginas siguen yendo por HTTP.
                self._por_chromium.add(tienda)
        if chromium is not None and (descargador is chromium or original is not None):
            pagina = replace(pagina, por_chromium=True)
        if pagina.url_final != url:
            # El destino también cuenta para el aviso de «lo prohíbe; se ignora» (ADR-017).
            destino = urlsplit(pagina.url_final).hostname or ""
            await self._comprobar_robots(pagina.url_final, destino)
        if (
            self._cache is not None
            and not grande
            and 200 <= pagina.estado < 300
            and not _a_medias(pagina)
        ):
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
        except URLNoPermitida:
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


def _a_medias(pagina: Pagina) -> bool:
    """Un HTML con un 2xx que no es 200 y sin `<body>`: no se guarda en la caché.

    Encontrado por Julio al probar la app con amazon.es: tras el desafío anti-bot de AWS WAF,
    Chromium devolvió el 202 del desafío con el documento siguiente a medio cargar (solo el
    `<head>`). Guardado, cualquier reintento lo volvía a servir durante la vida de la caché. Un
    200 sin `<body>` sí se guarda (el HTML puede omitirlo), y lo que no es HTML también.
    """
    return (
        pagina.estado != 200
        and "html" in pagina.tipo_contenido.lower()
        and "<body" not in pagina.html.lower()
    )


def _motivo(pagina: Pagina, *, grande: bool) -> str | None:
    # Un fichero grande solo se mira hasta el tope de una página normal: una página de bloqueo
    # nunca es mayor, y la detección corre en el bucle de eventos (20 MiB costaban ~2 s).
    vista = replace(pagina, html=pagina.html[:MAXIMO_BYTES]) if grande else pagina
    return detectar_bloqueo(vista)
