"""Planificador (H7): repite una tarea cada cierto tiempo dentro del bucle de la aplicación.

Sin dependencias: una tarea de `asyncio` que duerme y ejecuta. La primera ejecución llega tras
la primera espera, no al arrancar: abrir la web no visita ninguna tienda. La misma tarea se
puede lanzar a mano (`lanzar`, el botón «Comprobar ahora»); nunca hay dos a la vez.

Un fallo de la tarea se apunta en el registro y no para el planificador.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

registro = logging.getLogger(__name__)

Dormir = Callable[[float], Awaitable[None]]


class Planificador[T]:
    def __init__(
        self,
        tarea: Callable[[], Awaitable[T]],
        cada_s: float,
        *,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        """`cada_s` = 0: sin repetición (solo se ejecuta al lanzarla a mano)."""
        if cada_s < 0:
            raise ValueError("cada_s no puede ser negativo")
        self._tarea = tarea
        self._cada_s = cada_s
        self._dormir = dormir
        self._bucle: asyncio.Task[None] | None = None
        self._en_marcha: asyncio.Task[None] | None = None
        self._cerrado = False
        self.ultimo: T | None = None
        """Lo que devolvió la última ejecución que terminó bien."""

    @property
    def cada_s(self) -> float:
        return self._cada_s

    @property
    def en_curso(self) -> bool:
        return self._en_marcha is not None and not self._en_marcha.done()

    def arrancar(self) -> None:
        """Empieza a repetir la tarea. Hay que llamarlo dentro del bucle de `asyncio`."""
        if self._cada_s > 0 and self._bucle is None and not self._cerrado:
            self._bucle = asyncio.create_task(self._repetir())

    def lanzar(self) -> bool:
        """Ejecuta la tarea ya, en segundo plano. `False` si ya hay una en marcha o está cerrado."""
        if self._cerrado or self.en_curso:
            return False
        self._en_marcha = asyncio.create_task(self._ejecutar())
        return True

    async def _repetir(self) -> None:
        while True:
            await self._dormir(self._cada_s)
            if self.lanzar():
                tarea = self._en_marcha
                assert tarea is not None
                # `shield`: cancelar el bucle no cancela aquí la ejecución; lo hace `cerrar`.
                await asyncio.shield(tarea)

    async def _ejecutar(self) -> None:
        try:
            self.ultimo = await self._tarea()
        except Exception:
            registro.exception("La tarea planificada ha fallado; se repetirá en la próxima vuelta")

    async def cerrar(self) -> None:
        """Cancela el bucle y la ejecución en marcha, y espera a que terminen de recoger."""
        self._cerrado = True
        pendientes = [t for t in (self._bucle, self._en_marcha) if t is not None and not t.done()]
        for tarea in pendientes:
            tarea.cancel()
        if pendientes:
            await asyncio.gather(*pendientes, return_exceptions=True)
        self._bucle = None
