"""Cortesía con las tiendas: una petición cada vez por dominio, con un intervalo mínimo entre ellas,
y un tope global de descargas simultáneas.

El intervalo se cuenta desde el **final** de la petición anterior: si la web va lenta, la
siguiente espera igualmente, así nunca hay dos peticiones nuestras en vuelo al mismo dominio.
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


async def pausa_de_cortesia(dominio: str) -> bool:
    """Dentro de un turno de `dominio`, espera el intervalo de cortesía como si se pidiera de nuevo.

    Para repetir una petición sin salir del turno (`DescargadorConProxy`): se espera el mayor del
    retardo y el Crawl-delay, igual que entre dos peticiones, y mientras tanto se suelta la plaza
    del semáforo global para que otras tiendas no esperen. El dominio sigue ocupado: nada más
    sale hacia él entre medias. Devuelve `False`, sin esperar, si no hay un turno de `dominio`
    abierto en esta tarea (p. ej., el robots.txt se pide fuera de turno).
    """
    turno = _turno_actual.get()
    if turno is None or turno.dominio != dominio.lower() or not turno.con_plaza:
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
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if retardo_s < 0:
            raise ValueError("El retardo no puede ser negativo.")
        if concurrencia < 1:
            raise ValueError("La concurrencia debe ser al menos 1.")
        self._retardo_s = retardo_s
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
        dominio = dominio.lower()
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
            try:
                yield
            finally:
                _turno_actual.reset(marca)
                self._ultima[dominio] = self._reloj()
                if turno.con_plaza:
                    self._semaforo.release()

    async def esperar_turno(self, dominio: str, *, retardo_minimo: float | None = None) -> None:
        """Espera a que toque y cuenta la petición como hecha ahora mismo."""
        async with self.turno(dominio, retardo_minimo=retardo_minimo):
            pass
