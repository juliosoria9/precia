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
    EstadoProxy,
    GrupoProducto,
    ResultadoBusqueda,
    ResultadoEvaluacion,
    ResumenIA,
    Seguimiento,
    SerieHistorico,
    Tienda,
)
from precia.perfiles.entrada import EntradaNoValida, normalizar_entrada


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


class URLEntradaNoValida(ErrorFuente, ValueError):
    """La URL de entrada de la tienda no cumple las reglas de `perfiles.entrada` (nota 16 §A)."""


def entrada_normalizada(dominio: str, texto: str | None) -> str | None:
    """La URL de entrada de la tienda `dominio` normalizada, o `None` si `texto` está vacío.

    Lanza `URLEntradaNoValida` con el mensaje de `perfiles.entrada.normalizar_entrada`.
    """
    try:
        return normalizar_entrada(dominio, texto or "")
    except EntradaNoValida as error:
        raise URLEntradaNoValida(str(error)) from error


class FuenteDatos(Protocol):
    es_demo: bool

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        """Busca en las tiendas de la consulta (todas si no elige ninguna).

        `seguimiento` (C1) recibe el estado de cada tienda y los resultados provisionales.
        """
        ...

    def tiendas(self) -> list[Tienda]: ...

    def tienda(self, dominio: str) -> Tienda | None: ...

    async def alta_tienda(self, dominio: str, nombre: str, *, entrada: str | None = None) -> Tienda:
        """Da de alta la tienda (sin perfil), con la URL de entrada si se escribe alguna.

        `entrada` es el texto tal cual lo escribe el usuario: vacío o `None` es «sin entrada».
        `DominioNoValido`, `URLEntradaNoValida` o `TiendaYaExiste` si no se puede.
        """
        ...

    def cambiar_entrada(self, dominio: str, entrada: str | None) -> Tienda:
        """Pone, cambia o (con un texto vacío o `None`) quita la URL de entrada de la tienda.

        `TiendaNoExiste` o `URLEntradaNoValida` si no se puede; entonces no cambia nada.
        """
        ...

    def reintentar_configuracion(self, dominio: str) -> Tienda:
        """A1: quita la espera tras una configuración fallida; la tienda se configura en la
        próxima búsqueda en modo Auto o IA. A8: también olvida el bloqueo recordado en memoria.
        `TiendaNoExiste` si no existe."""
        ...

    def revisar_perfil(self, dominio: str, version: int) -> Tienda:
        """Memoria §4: la persona revisa y fija la versión actual del perfil de la tienda.

        `TiendaNoExiste` o `RevisionNoValida` si no se puede.
        """
        ...

    def producto(self, id_producto: str) -> GrupoProducto | None: ...

    def historico(self, id_producto: str) -> list[SerieHistorico]: ...

    def alertas(self) -> list[Alerta]: ...

    def crear_alerta(
        self, id_producto: str, umbral: Decimal, canal: str, *, solo_exactas: bool = True
    ) -> Alerta:
        """Con `solo_exactas` (por defecto), no cuentan equivalentes ni revisiones (B5)."""
        ...

    def resumen_ia(self) -> ResumenIA: ...

    def ajustes(self) -> Ajustes: ...

    def guardar_ajustes(self, ajustes: Ajustes) -> None: ...

    def evaluacion(self) -> list[ResultadoEvaluacion]: ...

    def estado_proxy(self) -> EstadoProxy:
        """Proxy de salida y su uso en esta ejecución, para «Ajustes» (sin credenciales)."""
        ...

    async def cerrar(self) -> None:
        """Libera navegador, base de datos... Se llama al apagar la aplicación."""
        ...
