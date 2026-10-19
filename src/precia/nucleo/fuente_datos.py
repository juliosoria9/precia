"""Interfaz de la fuente de datos que consume la web.

La web solo depende de este protocolo, así que la fuente de ejemplo (`FuenteDemo`) y la real
(`FuenteReal`: orquestador + SQLite) se intercambian sin tocar rutas ni plantillas.

Los errores de este módulo llevan un mensaje pensado para el usuario: la web lo enseña tal
cual junto al formulario que lo ha provocado.
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


class ErrorFuente(Exception):
    """Base de los errores de una fuente de datos. El mensaje se puede enseñar al usuario."""


class DominioNoValido(ErrorFuente, ValueError):
    """El texto no es un dominio de tienda que se pueda visitar (sintaxis o anti-SSRF)."""


class TiendaYaExiste(ErrorFuente):
    """Ya hay una tienda dada de alta con ese dominio."""


class TiendaNoExiste(ErrorFuente, LookupError):
    """No hay ninguna tienda con ese dominio."""


class RevisionNoValida(ErrorFuente):
    """No se puede fijar esa versión del perfil (no es la actual, ya está revisada...)."""


class FuenteDatos(Protocol):
    es_demo: bool

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda: ...

    def tiendas(self) -> list[Tienda]: ...

    def tienda(self, dominio: str) -> Tienda | None: ...

    async def alta_tienda(self, dominio: str, nombre: str) -> Tienda:
        """Da de alta la tienda (sin perfil). `DominioNoValido` o `TiendaYaExiste` si no."""
        ...

    def revisar_perfil(self, dominio: str, version: int) -> Tienda:
        """Memoria §4: la persona revisa y fija la versión actual del perfil de la tienda.

        `TiendaNoExiste` o `RevisionNoValida` si no se puede.
        """
        ...

    def producto(self, id_producto: str) -> GrupoProducto | None: ...

    def historico(self, id_producto: str) -> list[SerieHistorico]: ...

    def alertas(self) -> list[Alerta]: ...

    def crear_alerta(self, id_producto: str, umbral: Decimal, canal: str) -> Alerta: ...

    def resumen_ia(self) -> ResumenIA: ...

    def ajustes(self) -> Ajustes: ...

    def guardar_ajustes(self, ajustes: Ajustes) -> None: ...

    def evaluacion(self) -> list[ResultadoEvaluacion]: ...

    async def cerrar(self) -> None:
        """Libera navegador, base de datos... Se llama al apagar la aplicación."""
        ...
