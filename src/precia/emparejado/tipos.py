"""Contrato del paquete `emparejado`: la entrada del agrupador y la sesión de IA que usa."""

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
