"""Contrato del paquete `emparejado`: la entrada del agrupador, la sesión de IA que usa y la caché
de la revisión con IA."""

from dataclasses import dataclass
from typing import Protocol

from precia.extraccion.tipos import FichaExtraida
from precia.ia.tipos import PeticionIA, RespuestaIA


@dataclass(frozen=True, slots=True)
class OfertaCandidata:
    """Una ficha ya extraída de una tienda, pendiente de emparejar con las de las demás."""

    tienda: str
    ficha: FichaExtraida
    url: str
    disponible: bool = True


class SesionIA(Protocol):
    """Lo que el emparejado necesita de `ia.servicio.SesionIA` (que aplica tope y presupuesto)."""

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA: ...


class CacheRevisiones(Protocol):
    """Veredictos de la revisión con IA por oferta (`semantico.revisar`), con su caducidad.

    `leer` devuelve `(clase, motivo, cumple_filtro)` o `None`; lo que devuelve se vuelve a
    validar al usarlo. Un fallo de la caché no debe parar la búsqueda: cuenta como no guardado.
    """

    def leer(self, clave: str) -> tuple[str, str, bool] | None: ...

    def guardar(self, clave: str, clase: str, motivo: str, cumple_filtro: bool) -> None: ...
