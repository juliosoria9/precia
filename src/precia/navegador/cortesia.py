"""Cortesía con las tiendas: una petición cada vez por dominio, con un intervalo mínimo entre ellas,
y un tope global de descargas simultáneas.

El intervalo se cuenta desde el **final** de la petición anterior: si la web va lenta, la
siguiente espera igualmente, así nunca hay dos peticiones nuestras en vuelo al mismo dominio.

La fila es de la tienda: «www.tienda.es» y «tienda.es» comparten la suya (`fila_de`), como en el
resto del programa (`con_proxy.dominio_de`). Otro subdominio tiene su propia fila: un subdominio
de imágenes o un CDN suele ser otro servidor.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class _Turno:
    """El turno abierto en la tarea actual (para `pausa_de_cortesia`)."""

    limitador: "LimitadorDominio"
    dominio: str
    intervalo: float
    con_plaza: bool = True
    """Si el turno tiene cogida su plaza del semáforo global (la suelta durante la pausa)."""


_turno_actual: ContextVar[_Turno | None] = ContextVar("turno_actual", default=None)


def fila_de(dominio: str) -> str:
    """La fila de cortesía de un host: en minúsculas y sin «www.»."""
    return dominio.lower().removeprefix("www.")


async def pausa_de_cortesia(dominio: str) -> bool:
    """Dentro de un turno de `dominio`, espera el intervalo de cortesía como si se pidiera de nuevo.

    Para repetir una petición sin salir del turno (`DescargadorConProxy`): se espera el mayor del
    retardo y el Crawl-delay, igual que entre dos peticiones, y mientras tanto se suelta la plaza
    del semáforo global para que otras tiendas no esperen. El dominio sigue ocupado: nada más
    sale hacia él entre medias. Devuelve `False`, sin esperar, si no hay un turno de `dominio`
    abierto en esta tarea.
    """
    turno = _turno_actual.get()
    if turno is None or turno.dominio != fila_de(dominio) or not turno.con_plaza:
        return False
    limitador = turno.limitador
    turno.con_plaza = False
    limitador._semaforo.release()
    # Si se cancela durante la espera, el turno ya no tiene plaza y no la devuelve al salir.
    await limitador._dormir(turno.intervalo)
    await limitador._semaforo.acquire()
    turno.con_plaza = True
    return True


class LimitadorDominio:
    def __init__(
        self,
        retardo_s: float,
        *,
        concurrencia: int = 4,
        margen_cancelado_s: float = 0.0,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`margen_cancelado_s`: cuánto más se da el dominio por ocupado tras un turno cancelado.

        Cancelar una búsqueda (o agotar su plazo, C1) no para la descarga que va en un hilo
        (`asyncio.to_thread`): sigue hasta su timeout. Sin el margen, la búsqueda siguiente
        podría abrir otra petición al mismo dominio con la primera aún en vuelo (ADR-017).
        """
        if retardo_s < 0:
            raise ValueError("El retardo no puede ser negativo.")
        if concurrencia < 1:
            raise ValueError("La concurrencia debe ser al menos 1.")
        if margen_cancelado_s < 0:
            raise ValueError("El margen tras una cancelación no puede ser negativo.")
        self._retardo_s = retardo_s
        self._margen_cancelado_s = margen_cancelado_s
        self._reloj = reloj
        self._dormir = dormir
        self._semaforo = asyncio.Semaphore(concurrencia)
        self._ultima: dict[str, float] = {}
        self._candados: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def turno(
        self, dominio: str, *, retardo_minimo: float | None = None
    ) -> AsyncIterator[None]:
        """Ocupa el dominio durante el bloque `async with` (la descarga).

        `retardo_minimo` es el Crawl-delay del robots.txt: se espera el mayor de los dos.
        """
        dominio = fila_de(dominio)
        intervalo = max(self._retardo_s, retardo_minimo or 0.0)
        # Primero la fila del dominio y después el semáforo global: mientras se espera el
        # intervalo de una tienda no se ocupa un hueco que podría usar otra.
        async with self._candados.setdefault(dominio, asyncio.Lock()):
            ultima = self._ultima.get(dominio)
            if ultima is not None:
                espera = ultima + intervalo - self._reloj()
                if espera > 0:
                    await self._dormir(espera)
            await self._semaforo.acquire()
            turno = _Turno(self, dominio, intervalo)
            marca = _turno_actual.set(turno)
            cancelado = False
            try:
                yield
            except asyncio.CancelledError:
                cancelado = True
                raise
            finally:
                _turno_actual.reset(marca)
                margen = self._margen_cancelado_s if cancelado else 0.0
                self._ultima[dominio] = self._reloj() + margen
                if turno.con_plaza:
                    self._semaforo.release()

    def heredar(self, otro: "LimitadorDominio") -> None:
        """Comparte con `otro` (el del navegador al que sustituye) la fila de cada dominio y
        cuándo queda libre.

        Unos ajustes nuevos crean otro navegador con otro limitador, pero las descargas
        canceladas del viejo siguen en su hilo (C1) y, con varias búsquedas a la vez (B6), otra
        búsqueda puede seguir pidiendo con el viejo. Compartiendo la fila y la última petición
        de cada dominio, los dos nunca tienen dos peticiones en vuelo al mismo dominio, y cada
        uno espera su propio retardo desde la última de cualquiera de ellos (ADR-017). Los dos
        limitadores usan el mismo reloj monotónico. El tope global de concurrencia no se
        comparte: es de cada navegador.
        """
        for dominio, libre in self._ultima.items():
            otro._ultima[dominio] = max(otro._ultima.get(dominio, libre), libre)
        for dominio, candado in self._candados.items():
            otro._candados.setdefault(dominio, candado)
        self._ultima = otro._ultima
        self._candados = otro._candados

    async def esperar_turno(self, dominio: str, *, retardo_minimo: float | None = None) -> None:
        """Espera a que toque y cuenta la petición como hecha ahora mismo."""
        async with self.turno(dominio, retardo_minimo=retardo_minimo):
            pass
