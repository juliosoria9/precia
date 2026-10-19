"""Servicio de IA: límites de gasto, reintento ante salida no válida y registro de métricas.

Cada búsqueda abre una `SesionIA`, que lleva la cuenta de sus llamadas y su coste. Antes de
cada llamada se comprueban el tope de llamadas de la búsqueda y el presupuesto del mes; así una
web que provoque fallos en bucle (o una búsqueda con muchas tiendas) no dispara el gasto. Cada
intento se registra con sus métricas, nunca con el contenido de la petición ni de la respuesta.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from precia.ia.salida import RechazoDelModelo, SalidaNoValidaConConsumo
from precia.ia.tipos import (
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


def _ahora() -> datetime:
    return datetime.now(UTC)


def _resultado(error: ErrorIA) -> str:
    if isinstance(error, SalidaNoValida):
        return "salida_no_valida"
    if isinstance(error, RechazoDelModelo):
        return "rechazo"
    if isinstance(error, IANoDisponible):
        return "no_disponible"
    return "error"


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
    ) -> None:
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

    def nueva_busqueda(self) -> "SesionIA":
        return SesionIA(self)


class SesionIA:
    """Las llamadas de IA de una búsqueda."""

    def __init__(self, servicio: ServicioIA) -> None:
        self._servicio = servicio
        self._llamadas = 0
        self._coste_usd = Decimal(0)

    @property
    def llamadas(self) -> int:
        return self._llamadas

    @property
    def coste_usd(self) -> Decimal:
        return self._coste_usd

    def _reservar(self) -> None:
        # Comprobar y reservar sin `await` por medio: con varias tiendas en paralelo, dos
        # peticiones no pueden colarse a la vez por el último hueco.
        servicio = self._servicio
        if self._llamadas >= servicio.tope_llamadas:
            raise PresupuestoAgotado(
                f"Se ha alcanzado el tope de {servicio.tope_llamadas} llamadas de IA por búsqueda"
            )
        if servicio.registro.gasto_mes_usd() >= servicio.presupuesto_mes_usd:
            raise PresupuestoAgotado(
                f"Se ha agotado el presupuesto de IA del mes ({servicio.presupuesto_mes_usd} $)"
            )
        self._llamadas += 1

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA:
        """Hace la petición respetando los límites; reintenta una vez si la salida no vale."""
        self._reservar()
        try:
            return await self._llamar(peticion)
        except SalidaNoValida as primera:
            try:
                self._reservar()
            except PresupuestoAgotado:
                raise primera from None
        return await self._llamar(peticion)

    async def _llamar(self, peticion: PeticionIA) -> RespuestaIA:
        servicio = self._servicio
        inicio = servicio.cronometro()
        try:
            respuesta = await servicio.proveedor.completar(peticion)
        except ErrorIA as error:
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
            raise
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
        return respuesta

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
