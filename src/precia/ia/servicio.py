"""Servicio de IA: límites de gasto, reintento ante salida no válida y registro de métricas.

Cada búsqueda abre una `SesionIA`, que lleva la cuenta de sus llamadas y su coste. Antes de
cada llamada se comprueban el tope de llamadas de la búsqueda y el presupuesto del mes; así una
web que provoque fallos en bucle (o una búsqueda con muchas tiendas) no dispara el gasto. Cada
intento se registra con sus métricas, nunca con el contenido de la petición ni de la respuesta.

El presupuesto del mes se comprueba de forma atómica entre búsquedas simultáneas (B6): cada
llamada aparta su coste máximo (`coste_maximo`) en `LlamadasEnVuelo`, que comparten los servicios
de todas las búsquedas, hasta que se registra su coste real. Comprobar y apartar van sin `await`
por medio, así que dos búsquedas no pueden colarse a la vez por el último hueco.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Collection
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from precia.ia.costes import calcular_coste
from precia.ia.salida import RechazoDelModelo, SalidaCortada, SalidaNoValidaConConsumo
from precia.ia.tipos import (
    CARACTERES_POR_TOKEN,
    ErrorIA,
    IANoDisponible,
    PeticionIA,
    PresupuestoAgotado,
    ProveedorIA,
    RegistroLlamadas,
    RespuestaIA,
    SalidaNoValida,
)
from precia.nucleo.modelos import LlamadaIA

registro = logging.getLogger(__name__)

TECHO_MAX_TOKENS = 4096
"""Como mucho, el tope de tokens con que se repite una salida cortada (R2-TOK)."""


def _ahora() -> datetime:
    return datetime.now(UTC)


def coste_maximo(proveedor: str, modelo: str, peticion: PeticionIA) -> Decimal:
    """Lo más que puede costar `peticion` (B6): la entrada contada a `CARACTERES_POR_TOKEN` por
    ficha (por lo alto: el texto corriente da 3 o 4) y todo el tope de salida.

    Un modelo sin tarifa (el simulado de los tests) no aparta nada: el proveedor real sin tarifa
    ya no se crea (`ProveedorClaude`).
    """
    entrada = (len(peticion.instrucciones) + len(peticion.datos)) // CARACTERES_POR_TOKEN
    try:
        return calcular_coste(proveedor, modelo, entrada, peticion.max_tokens)
    except ErrorIA:
        return Decimal(0)


def _resultado(error: ErrorIA) -> str:
    if isinstance(error, SalidaNoValida):
        return "salida_no_valida"
    if isinstance(error, RechazoDelModelo):
        return "rechazo"
    if isinstance(error, IANoDisponible):
        return "no_disponible"
    return "error"


class LlamadasEnVuelo:
    """Las llamadas de IA en vuelo de todas las búsquedas (se comparte entre sus servicios).

    - Las de búsquedas canceladas que siguen en su hilo (C1): se registran cuando terminan
      (`SesionIA._llamar`). Al apagar hay que esperarlas antes de cerrar el registro: si no, el
      proveedor las cobra y el gasto del mes no las cuenta.
    - El coste máximo apartado por las llamadas reservadas y aún sin registrar (`apartado_usd`,
      B6): cuenta como gastado al comprobar el presupuesto del mes.
    """

    def __init__(self) -> None:
        self._tareas: set[asyncio.Future[Any]] = set()
        self._apartado = Decimal(0)

    @property
    def apartado_usd(self) -> Decimal:
        return self._apartado

    def apartar(self, coste_usd: Decimal) -> None:
        self._apartado += coste_usd

    def soltar(self, coste_usd: Decimal) -> None:
        self._apartado = max(self._apartado - coste_usd, Decimal(0))

    def __len__(self) -> int:
        return len(self._tareas)

    def anotar(self, tarea: "asyncio.Future[Any]") -> None:
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)

    async def esperar(self, plazo_s: float) -> None:
        """Espera a que terminen (y se registren), como mucho `plazo_s` segundos."""
        if self._tareas:
            await asyncio.wait(set(self._tareas), timeout=plazo_s)


class ServicioIA:
    def __init__(
        self,
        proveedor: ProveedorIA,
        registro: RegistroLlamadas,
        *,
        presupuesto_mes_usd: Decimal,
        tope_llamadas: int,
        reloj: Callable[[], datetime] = _ahora,
        cronometro: Callable[[], float] = time.monotonic,
        en_vuelo: LlamadasEnVuelo | None = None,
        reservadas: Collection[str] = (),
    ) -> None:
        """`en_vuelo` se comparte entre los servicios de cada búsqueda para esperarlas al cerrar.

        `reservadas` son componentes («revision») que tienen guardada una llamada del tope de la
        búsqueda hasta que la usan: los demás no pueden gastarla.
        """
        if tope_llamadas < 0:
            raise ValueError("El tope de llamadas por búsqueda no puede ser negativo")
        if presupuesto_mes_usd < 0:
            raise ValueError("El presupuesto mensual no puede ser negativo")
        self.proveedor = proveedor
        self.registro = registro
        self.presupuesto_mes_usd = presupuesto_mes_usd
        self.tope_llamadas = tope_llamadas
        self.reloj = reloj
        self.cronometro = cronometro
        self.en_vuelo = en_vuelo if en_vuelo is not None else LlamadasEnVuelo()
        self.reservadas = frozenset(reservadas)

    def nueva_busqueda(self) -> "SesionIA":
        return SesionIA(self)


class SesionIA:
    """Las llamadas de IA de una búsqueda."""

    def __init__(self, servicio: ServicioIA) -> None:
        self._servicio = servicio
        self._llamadas = 0
        self._coste_usd = Decimal(0)
        self._por_componente: dict[str, int] = {}

    @property
    def llamadas(self) -> int:
        return self._llamadas

    @property
    def coste_usd(self) -> Decimal:
        return self._coste_usd

    @property
    def modelo(self) -> str:
        """Proveedor y modelo («api/claude-haiku-5-5»): parte de la clave de la caché."""
        proveedor = self._servicio.proveedor
        return f"{proveedor.nombre}/{proveedor.modelo}"

    @property
    def por_componente(self) -> dict[str, int]:
        """Llamadas de esta búsqueda por componente (`extractor`, `configurador`...).

        Cuenta también los reintentos: la suma es `llamadas`.
        """
        return dict(self._por_componente)

    def _reservar(self, peticion: PeticionIA) -> Decimal:
        """Comprueba los límites y aparta la llamada; devuelve el coste máximo apartado.

        Comprobar y apartar van sin `await` por medio: con varias tiendas en paralelo (o varias
        búsquedas a la vez, B6), dos peticiones no pueden colarse a la vez por el último hueco.
        """
        componente = peticion.componente
        servicio = self._servicio
        guardadas = sum(
            1 for r in servicio.reservadas if r != componente and r not in self._por_componente
        )
        if self._llamadas + guardadas >= servicio.tope_llamadas:
            raise PresupuestoAgotado(
                f"Se ha alcanzado el tope de {servicio.tope_llamadas} llamadas de IA por búsqueda"
            )
        maximo = coste_maximo(servicio.proveedor.nombre, servicio.proveedor.modelo, peticion)
        # Lo que aún no se ha registrado de las llamadas en vuelo (de esta y de las demás
        # búsquedas) cuenta como gastado por su coste máximo.
        gastado = servicio.registro.gasto_mes_usd() + servicio.en_vuelo.apartado_usd
        if gastado >= servicio.presupuesto_mes_usd:
            raise PresupuestoAgotado(
                f"Se ha agotado el presupuesto de IA del mes ({servicio.presupuesto_mes_usd} $)"
            )
        servicio.en_vuelo.apartar(maximo)
        self._llamadas += 1
        self._por_componente[componente] = self._por_componente.get(componente, 0) + 1
        return maximo

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA:
        """Hace la petición respetando los límites; reintenta una vez si la salida no vale.

        Si se cortó en el tope de tokens (`SalidaCortada`), igual se volvería a cortar: se repite
        con el doble de `max_tokens`, hasta `TECHO_MAX_TOKENS`, y no se repite si ya estaba en
        el techo (R2-TOK).
        """
        apartado = self._reservar(peticion)
        try:
            return await self._llamar(peticion, apartado)
        except SalidaNoValida as primera:
            if isinstance(primera, SalidaCortada):
                mas = min(2 * peticion.max_tokens, TECHO_MAX_TOKENS)
                if mas <= peticion.max_tokens:
                    raise
                peticion = replace(peticion, max_tokens=mas)
            try:
                apartado = self._reservar(peticion)
            except PresupuestoAgotado:
                raise primera from None
        return await self._llamar(peticion, apartado)

    async def _llamar(self, peticion: PeticionIA, apartado: Decimal) -> RespuestaIA:
        """Llama al proveedor y registra la llamada; después suelta su coste `apartado` (B6)."""
        servicio = self._servicio
        inicio = servicio.cronometro()
        # La llamada a la API va en un hilo: aunque se cancele la búsqueda (C1), sigue y se cobra.
        # El `shield` la deja terminar y entonces se registra, para que el gasto del mes no se
        # quede corto.
        tarea = asyncio.ensure_future(servicio.proveedor.completar(peticion))
        try:
            respuesta = await asyncio.shield(tarea)
        except asyncio.CancelledError:
            # Primero el registro: quien espera en `en_vuelo` la ve ya registrada. Lo apartado
            # sigue contando hasta entonces: la llamada se cobrará.
            tarea.add_done_callback(
                lambda t: self._registrar_al_terminar(peticion, t, inicio, apartado)
            )
            servicio.en_vuelo.anotar(tarea)
            raise
        except ErrorIA as error:
            self._registrar_error(peticion, error, inicio)
            servicio.en_vuelo.soltar(apartado)
            raise
        except BaseException:
            servicio.en_vuelo.soltar(apartado)
            raise
        self._registrar_respuesta(peticion, respuesta)
        servicio.en_vuelo.soltar(apartado)
        return respuesta

    def _registrar_al_terminar(
        self,
        peticion: PeticionIA,
        tarea: "asyncio.Future[RespuestaIA]",
        inicio: float,
        apartado: Decimal,
    ) -> None:
        """Registra la llamada de una búsqueda que se canceló mientras esperaba al proveedor."""
        try:
            if tarea.cancelled():
                return  # No ha llegado a terminar (se cerró el bucle): no hay nada que cobrar.
            error = tarea.exception()
            if error is None:
                self._registrar_respuesta(peticion, tarea.result())
            elif isinstance(error, ErrorIA):
                self._registrar_error(peticion, error, inicio)
        except Exception:
            registro.exception("No se ha podido registrar una llamada de IA cancelada")
        finally:
            self._servicio.en_vuelo.soltar(apartado)

    def _registrar_error(self, peticion: PeticionIA, error: ErrorIA, inicio: float) -> None:
        servicio = self._servicio
        consumo = (
            error.consumo
            if isinstance(error, SalidaNoValidaConConsumo | RechazoDelModelo)
            else None
        )
        self._registrar(
            peticion,
            proveedor=servicio.proveedor.nombre,
            modelo=servicio.proveedor.modelo,
            tokens_entrada=consumo.tokens_entrada if consumo else 0,
            tokens_salida=consumo.tokens_salida if consumo else 0,
            coste_usd=consumo.coste_usd if consumo else Decimal(0),
            resultado=_resultado(error),
            duracion_ms=round((servicio.cronometro() - inicio) * 1000),
        )

    def _registrar_respuesta(self, peticion: PeticionIA, respuesta: RespuestaIA) -> None:
        self._registrar(
            peticion,
            proveedor=respuesta.proveedor,
            modelo=respuesta.modelo,
            tokens_entrada=respuesta.tokens_entrada,
            tokens_salida=respuesta.tokens_salida,
            coste_usd=respuesta.coste_usd,
            resultado="ok",
            duracion_ms=respuesta.duracion_ms,
        )

    def _registrar(
        self,
        peticion: PeticionIA,
        *,
        proveedor: str,
        modelo: str,
        tokens_entrada: int,
        tokens_salida: int,
        coste_usd: Decimal,
        resultado: str,
        duracion_ms: int,
    ) -> None:
        self._coste_usd += coste_usd
        self._servicio.registro.registrar_llamada(
            LlamadaIA(
                fecha=self._servicio.reloj(),
                componente=peticion.componente,
                proveedor=proveedor,
                modelo=modelo,
                tokens_entrada=tokens_entrada,
                tokens_salida=tokens_salida,
                coste_usd=coste_usd,
                resultado=resultado,
                duracion_ms=duracion_ms,
            )
        )
