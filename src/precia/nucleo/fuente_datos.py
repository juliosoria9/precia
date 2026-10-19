"""Interfaz de la fuente de datos que consume la web.

La web solo depende de este protocolo, así que la fuente de ejemplo (`FuenteDemo`) y la real
(orquestador + SQLite, desde H2) se intercambian sin tocar rutas ni plantillas.
"""

from decimal import Decimal
from typing import Protocol

from precia.nucleo.modelos import (
    Ajustes,
    Alerta,
    ConsultaBusqueda,
    GrupoProducto,
    ResultadoBusqueda,
    ResultadoEvaluacion,
    ResumenIA,
    SerieHistorico,
    Tienda,
)


class FuenteDatos(Protocol):
    es_demo: bool

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda: ...

    def tiendas(self) -> list[Tienda]: ...

    def tienda(self, dominio: str) -> Tienda | None: ...

    def producto(self, id_producto: str) -> GrupoProducto | None: ...

    def historico(self, id_producto: str) -> list[SerieHistorico]: ...

    def alertas(self) -> list[Alerta]: ...

    def crear_alerta(self, id_producto: str, umbral: Decimal, canal: str) -> Alerta: ...

    def resumen_ia(self) -> ResumenIA: ...

    def ajustes(self) -> Ajustes: ...

    def guardar_ajustes(self, ajustes: Ajustes) -> None: ...

    def evaluacion(self) -> list[ResultadoEvaluacion]: ...
