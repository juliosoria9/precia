"""Punto de entrada del paquete: obtener una página respetando todas las reglas.

Orden: validar la URL → robots.txt → caché → turno del dominio → descargar → detectar bloqueo →
guardar en caché. La caché va antes del turno para que una página ya guardada no espere el
intervalo de cortesía (no se pide nada a la tienda), pero después de robots: lo guardado tampoco
se sirve si el robots.txt ahora lo prohíbe.
"""

import asyncio
import logging
from urllib.parse import urlsplit

from precia.navegador.bloqueo import detectar_bloqueo
from precia.navegador.cache import CachePaginas
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import (
    Bloqueado,
    Descargador,
    ErrorDescarga,
    Pagina,
    ProhibidoPorRobots,
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

    async def obtener(self, url: str, *, js: bool = False, usar_cache: bool = True) -> Pagina:
        """Descarga `url` (con Chromium si `js`). Lanza los errores de `navegador.tipos`."""
        url = await self._validador(url)
        dominio = urlsplit(url).hostname or ""
        await self._comprobar_robots(
            url, dominio, f"El robots.txt de {dominio} no permite visitar esa página."
        )

        if self._cache is not None and usar_cache:
            guardada = await asyncio.to_thread(self._cache.obtener, url, js=js)
            if guardada is not None:
                return guardada

        descargador = self._chromium if js else self._http
        if descargador is None:
            raise ErrorDescarga("No hay un navegador Chromium disponible para esta tienda.")
        async with self._limitador.turno(dominio, retardo_minimo=self._robots.retardo(dominio)):
            pagina = await descargador.descargar(url)

        motivo = detectar_bloqueo(pagina)
        if motivo is not None:
            # Se para y se registra; nunca se reintenta ni se intenta resolver el desafío.
            registro.warning("Bloqueo en %s: %s", dominio, motivo)
            raise Bloqueado(url, motivo)
        if pagina.url_final != url:
            # La redirección ya ocurrió, pero el contenido de una ruta prohibida no se usa.
            destino = urlsplit(pagina.url_final).hostname or ""
            await self._comprobar_robots(
                pagina.url_final,
                destino,
                f"La página redirige a una ruta que el robots.txt de {destino} no permite.",
            )
        if self._cache is not None and 200 <= pagina.estado < 300:
            await asyncio.to_thread(self._cache.guardar, pagina, js=js)
        return pagina

    async def cerrar(self) -> None:
        await self._http.cerrar()
        if self._chromium is not None:
            await self._chromium.cerrar()
