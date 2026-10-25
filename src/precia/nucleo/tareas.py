"""Búsquedas en segundo plano (C1): cada búsqueda es una tarea con id que la web consulta.

`GET /buscar` ya no hace el scraping dentro de la petición: lanza una `TareaBusqueda` aquí y la
página sondea su estado (el de cada tienda y los resultados provisionales, que le llegan por el
protocolo `Seguimiento`). Atrás o F5 consultan la tarea por su id y no vuelven a buscar.

Límites:

- Como mucho `MAX_BUSQUEDAS_SIMULTANEAS` a la vez (3, B6, decisión de Julio): la segunda ya no
  espera a que termine la primera, que podía tardar minutos si configuraba una tienda. Es
  seguro porque lo que compartían ya está resuelto en `FuenteReal`: las llamadas de IA en vuelo
  de todas las búsquedas cuentan en el presupuesto del mes (`ia.servicio.LlamadasEnVuelo`), una
  tienda se configura o se repara una sola vez a la vez (`orquestador.TurnosDePerfil`) y el
  navegador, con su limitador de cortesía, es el mismo (y si cambian los ajustes, el nuevo
  comparte con el viejo la fila de cada dominio): nunca hay dos peticiones en vuelo al mismo
  dominio (ADR-017). Las demás esperan en cola, como mucho `MAX_EN_COLA`; una consulta idéntica
  a una en marcha (doble clic) reutiliza su tarea.
- Las terminadas se guardan en memoria `TTL_TERMINADAS_S` y como mucho `MAX_TERMINADAS`; se
  limpian al lanzar o consultar. Tras reiniciar la aplicación ya no están.

El gestor guarda una referencia a cada `asyncio.Task` (asyncio solo guarda referencias débiles)
y recoge su final. El semáforo se crea en el bucle que lo usa: los tests abren uno por petición.
"""

import asyncio
import logging
import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from precia.nucleo.fuente_datos import ErrorFuente, FuenteDatos
from precia.nucleo.modelos import (
    ESTADOS_FINALES,
    ConsultaBusqueda,
    EstadoTienda,
    ResultadoBusqueda,
)

MAX_BUSQUEDAS_SIMULTANEAS = 3
MAX_EN_COLA = 5
TTL_TERMINADAS_S = 3600.0
MAX_TERMINADAS = 20
ESPERA_CIERRE_S = 5.0
"""Lo que se espera a que una búsqueda cancelada termine de recoger (al cancelar o al apagar)."""
FALLIDA = "Error inesperado: la búsqueda se ha parado (detalle en el registro)"

registro = logging.getLogger(__name__)


class EstadoBusqueda(StrEnum):
    EN_COLA = "en_cola"
    EN_CURSO = "en_curso"
    TERMINADA = "terminada"
    CANCELADA = "cancelada"
    FALLIDA = "fallida"


ACTIVAS = frozenset({EstadoBusqueda.EN_COLA, EstadoBusqueda.EN_CURSO})


class DemasiadasBusquedas(ErrorFuente):
    """Ya hay demasiadas búsquedas esperando su turno."""


def _nuevo_id() -> str:
    return secrets.token_hex(8)


@dataclass(eq=False)
class TareaBusqueda:
    """Una búsqueda lanzada desde la web. Implementa `Seguimiento`.

    `version` sube con cada cambio: la página que sondea no se repinta si no ha cambiado.
    """

    id: str
    consulta: ConsultaBusqueda
    tiendas: dict[str, EstadoTienda]
    estado: EstadoBusqueda = EstadoBusqueda.EN_COLA
    resultado: ResultadoBusqueda | None = None
    """El último parcial mientras sigue (o al cancelarla) y el definitivo al terminar."""
    version: int = 0
    error: str = ""
    terminada_en: float | None = field(default=None, repr=False)
    detalles: dict[str, str] = field(default_factory=dict)
    """En qué está cada tienda (A10): «Ficha 2 de 5», «Primera vez en esta tienda…»."""
    reloj: Callable[[], float] = field(default=time.monotonic, repr=False)
    _inicios: dict[str, float] = field(default_factory=dict, repr=False)
    _vistas: set[str] = field(default_factory=set, repr=False)
    """URL de las ofertas que se han enseñado en algún parcial (A1)."""

    @property
    def activa(self) -> bool:
        return self.estado in ACTIVAS

    @property
    def terminadas(self) -> int:
        """Tiendas que ya han terminado su parte (hecha, bloqueada, plazo...)."""
        return sum(estado in ESTADOS_FINALES for estado in self.tiendas.values())

    def tienda(self, dominio: str, estado: EstadoTienda) -> None:
        # Solo las tiendas de la consulta: la tabla de la página no crece con lo que llegue.
        if dominio in self.tiendas:
            if estado is not EstadoTienda.EN_COLA:
                self._inicios.setdefault(dominio, self.reloj())
            self.tiendas[dominio] = estado
            self.version += 1

    def parcial(self, resultado: ResultadoBusqueda) -> None:
        self._vistas.update(o.url for g in resultado.grupos for o in g.ofertas)
        self.resultado = resultado
        self.version += 1

    def detalle(self, dominio: str, texto: str) -> None:
        if dominio in self.tiendas and self.detalles.get(dominio) != texto:
            self.detalles[dominio] = texto
            self.version += 1

    def segundos(self, dominio: str) -> int | None:
        """Segundos desde que la tienda empezó (A10), o `None` si aún está en cola."""
        inicio = self._inicios.get(dominio)
        return None if inicio is None else int(self.reloj() - inicio)

    @property
    def retiradas(self) -> int:
        """Ofertas que se vieron en los resultados provisionales y que el final ha descartado
        (la revisión con IA o la regla del núcleo, A1): la web lo dice para que no parezca que
        han desaparecido sin más."""
        if self.resultado is None or self.resultado.provisional:
            return 0
        return sum(1 for d in self.resultado.descartados if d.url and d.url in self._vistas)


class GestorBusquedas:
    def __init__(
        self,
        fuente: FuenteDatos,
        *,
        simultaneas: int = MAX_BUSQUEDAS_SIMULTANEAS,
        max_en_cola: int = MAX_EN_COLA,
        ttl_s: float = TTL_TERMINADAS_S,
        maximo: int = MAX_TERMINADAS,
        reloj: Callable[[], float] = time.monotonic,
        nuevo_id: Callable[[], str] = _nuevo_id,
    ) -> None:
        if simultaneas < 1 or max_en_cola < 0 or ttl_s <= 0 or maximo < 1:
            raise ValueError("Límites de búsquedas en segundo plano no válidos")
        self._fuente = fuente
        self._simultaneas = simultaneas
        self._max_en_cola = max_en_cola
        self._ttl_s = ttl_s
        self._maximo = maximo
        self._reloj = reloj
        self._nuevo_id = nuevo_id
        self._tareas: dict[str, TareaBusqueda] = {}
        self._asincronas: dict[str, asyncio.Task[None]] = {}
        self._semaforo: asyncio.Semaphore | None = None
        self._bucle: asyncio.AbstractEventLoop | None = None

    def lanzar(self, consulta: ConsultaBusqueda, tiendas: Iterable[str]) -> TareaBusqueda:
        """Lanza la búsqueda en el bucle en marcha, o devuelve la que ya busca lo mismo.

        `tiendas` son los dominios que se enseñan en el progreso. `DemasiadasBusquedas` si la
        cola está llena.
        """
        self._limpiar()
        activas = [t for t in self._tareas.values() if t.activa]
        for tarea in activas:
            if tarea.consulta == consulta:
                return tarea
        if len(activas) >= self._simultaneas + self._max_en_cola:
            raise DemasiadasBusquedas(
                "Hay demasiadas búsquedas en marcha: espera a que termine alguna o cancélala."
            )
        id_tarea = self._nuevo_id()
        while id_tarea in self._tareas:
            id_tarea = self._nuevo_id()
        tarea = TareaBusqueda(
            id_tarea, consulta, dict.fromkeys(tiendas, EstadoTienda.EN_COLA), reloj=self._reloj
        )
        self._tareas[id_tarea] = tarea
        asincrona = asyncio.get_running_loop().create_task(
            self._correr(tarea), name=f"busqueda-{id_tarea}"
        )
        self._asincronas[id_tarea] = asincrona
        asincrona.add_done_callback(lambda a: self._al_terminar(tarea, a))
        return tarea

    def tarea(self, id_tarea: str) -> TareaBusqueda | None:
        self._limpiar()
        return self._tareas.get(id_tarea)

    async def esperar(self, tarea: TareaBusqueda, plazo_s: float) -> None:
        """Espera como mucho `plazo_s` a que termine `tarea` (no la cancela si no termina)."""
        asincrona = self._asincronas.get(tarea.id)
        if asincrona is not None and plazo_s > 0:
            await asyncio.wait({asincrona}, timeout=plazo_s)

    async def cancelar(self, id_tarea: str) -> TareaBusqueda | None:
        """Cancela la búsqueda y espera (como mucho `ESPERA_CIERRE_S`) a que recoja."""
        tarea = self.tarea(id_tarea)
        if tarea is None:
            return None
        asincrona = self._asincronas.get(id_tarea)
        if asincrona is not None and not asincrona.done():
            asincrona.cancel()
            await asyncio.wait({asincrona}, timeout=ESPERA_CIERRE_S)
        return tarea

    async def cerrar(self) -> None:
        """Al apagar: cancela las búsquedas en marcha y espera a que recojan."""
        pendientes = [a for a in self._asincronas.values() if not a.done()]
        for asincrona in pendientes:
            asincrona.cancel()
        if pendientes:
            await asyncio.wait(pendientes, timeout=ESPERA_CIERRE_S)

    # --- Interno ------------------------------------------------------------------------------

    def _semaforo_del_bucle(self) -> asyncio.Semaphore:
        bucle = asyncio.get_running_loop()
        if self._semaforo is None or self._bucle is not bucle:
            self._semaforo = asyncio.Semaphore(self._simultaneas)
            self._bucle = bucle
        return self._semaforo

    async def _correr(self, tarea: TareaBusqueda) -> None:
        try:
            async with self._semaforo_del_bucle():
                tarea.estado = EstadoBusqueda.EN_CURSO
                tarea.version += 1
                resultado = await self._fuente.buscar(tarea.consulta, seguimiento=tarea)
        except asyncio.CancelledError:
            self._terminar(tarea, EstadoBusqueda.CANCELADA)
            raise
        except Exception:
            # La fuente ya convierte los errores de cada tienda en pasos del progreso: esto es
            # un fallo que nadie espera. El detalle va al registro y no a la página.
            registro.exception("Error inesperado en la búsqueda %s", tarea.id)
            tarea.error = FALLIDA
            self._terminar(tarea, EstadoBusqueda.FALLIDA)
            return
        tarea.resultado = resultado
        self._terminar(tarea, EstadoBusqueda.TERMINADA)

    def _terminar(self, tarea: TareaBusqueda, estado: EstadoBusqueda) -> None:
        pendiente = EstadoTienda.HECHA if estado is EstadoBusqueda.TERMINADA else None
        for dominio, de_tienda in tarea.tiendas.items():
            if de_tienda not in ESTADOS_FINALES:
                tarea.tiendas[dominio] = pendiente or EstadoTienda.CANCELADA
        tarea.estado = estado
        tarea.terminada_en = self._reloj()
        tarea.version += 1

    def _al_terminar(self, tarea: TareaBusqueda, asincrona: "asyncio.Task[None]") -> None:
        self._asincronas.pop(tarea.id, None)
        if asincrona.cancelled():
            # Cancelada antes de empezar (en cola, o al cerrarse el bucle): `_correr` no llegó a
            # apuntarlo.
            if tarea.activa:
                self._terminar(tarea, EstadoBusqueda.CANCELADA)
            return
        if (error := asincrona.exception()) is not None:
            registro.error("La búsqueda %s terminó con un error no recogido: %r", tarea.id, error)

    def _limpiar(self) -> None:
        ahora = self._reloj()
        terminadas = sorted(
            (t for t in self._tareas.values() if not t.activa and t.terminada_en is not None),
            key=lambda t: t.terminada_en or 0.0,
        )
        sobran = max(len(terminadas) - self._maximo, 0)
        for indice, tarea in enumerate(terminadas):
            caducada = tarea.terminada_en is not None and ahora - tarea.terminada_en > self._ttl_s
            if indice < sobran or caducada:
                del self._tareas[tarea.id]
