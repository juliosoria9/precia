"""Proveedor simulado para los tests: respuestas guionizadas por componente, sin red ni coste.

Cada componente (`configurador`, `extractor`...) tiene su cola de respuestas, que se consumen
en orden. Una respuesta puede ser un diccionario (los datos), un texto (la salida en bruto del
modelo, para probar JSON roto) o una excepción, que se lanza. Igual que los proveedores reales,
valida la salida contra el esquema de la petición.
"""

from collections import deque
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from precia.ia.salida import Consumo, SalidaNoValidaConConsumo, interpretar, validar
from precia.ia.tipos import ErrorIA, PeticionIA, RespuestaIA, SalidaNoValida

Guion = dict[str, Any] | str | Exception


class ProveedorSimulado:
    """`ProveedorIA` de pega que registra las peticiones recibidas en `peticiones`."""

    def __init__(
        self,
        respuestas: Mapping[str, Iterable[Guion]],
        *,
        nombre: str = "simulado",
        modelo: str = "simulado",
        tokens_entrada: int = 100,
        tokens_salida: int = 20,
        coste_usd: Decimal = Decimal(0),
        duracion_ms: int = 5,
    ) -> None:
        self.nombre = nombre
        self.modelo = modelo
        self.peticiones: list[PeticionIA] = []
        self._colas = {componente: deque(guion) for componente, guion in respuestas.items()}
        self._consumo = Consumo(tokens_entrada, tokens_salida, coste_usd)
        self._duracion_ms = duracion_ms

    async def completar(self, peticion: PeticionIA) -> RespuestaIA:
        self.peticiones.append(peticion)
        cola = self._colas.get(peticion.componente)
        if not cola:
            raise ErrorIA(
                f"El proveedor simulado no tiene más respuestas para «{peticion.componente}»"
            )
        siguiente = cola.popleft()
        if isinstance(siguiente, Exception):
            raise siguiente
        try:
            if isinstance(siguiente, str):
                datos = interpretar(siguiente, peticion.esquema)
            else:
                datos = validar(dict(siguiente), peticion.esquema)
        except SalidaNoValida as error:
            raise SalidaNoValidaConConsumo(str(error), self._consumo) from None
        return RespuestaIA(
            datos=datos,
            tokens_entrada=self._consumo.tokens_entrada,
            tokens_salida=self._consumo.tokens_salida,
            coste_usd=self._consumo.coste_usd,
            duracion_ms=self._duracion_ms,
            proveedor=self.nombre,
            modelo=self.modelo,
        )
