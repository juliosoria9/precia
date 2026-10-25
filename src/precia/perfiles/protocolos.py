"""Lo mínimo que `perfiles` necesita del navegador y de la IA, como protocolos estructurales.

Así el validador, el configurador y el reparador no dependen de `navegador.Navegador` ni de
`ia.servicio.SesionIA` (que cumplen estos protocolos sin heredar de ellos) y en los tests basta
con un objeto cualquiera que tenga el mismo método.
"""

from typing import Protocol

from precia.ia.tipos import PeticionIA, RespuestaIA
from precia.navegador.tipos import Pagina


class Navegador(Protocol):
    """Descarga una página respetando la cortesía y la caché; con `js`, usando Chromium.

    Lanza los errores de `navegador.tipos` (`Bloqueado`, `ErrorDescarga`...).
    """

    async def obtener(self, url: str, *, js: bool = False) -> Pagina: ...


class SesionIA(Protocol):
    """Las llamadas de IA de una búsqueda, con su tope de llamadas y su presupuesto."""

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA: ...
