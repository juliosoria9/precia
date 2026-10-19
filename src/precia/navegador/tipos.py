"""Contrato del paquete `navegador`: página descargada, errores y descargadores."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Pagina:
    url: str
    """URL pedida (ya validada)."""
    url_final: str
    """URL tras las redirecciones (también validada)."""
    estado: int
    html: str
    tipo_contenido: str
    obtenida: datetime
    desde_cache: bool = False


class ErrorNavegador(Exception):
    """Base de los errores del navegador. El mensaje se puede enseñar al usuario."""


class URLNoPermitida(ErrorNavegador):
    """Esquema, host, puerto o IP no permitidos (defensa SSRF)."""


class ProhibidoPorRobots(ErrorNavegador):
    """El robots.txt del dominio prohíbe la ruta para nuestro User-Agent."""


class Bloqueado(ErrorNavegador):
    """La web responde con un CAPTCHA o un bloqueo anti-bot. Nunca se intenta evadir."""

    def __init__(self, url: str, motivo: str) -> None:
        super().__init__(f"Bloqueo en {url}: {motivo}")
        self.url = url
        self.motivo = motivo


class ErrorDescarga(ErrorNavegador):
    """Red, tiempo de espera, tamaño excesivo o contenido que no es HTML."""


class Descargador(Protocol):
    """Hace la petición sin más: la validación, robots, cortesía y caché van por fuera."""

    async def descargar(self, url: str) -> Pagina: ...

    async def cerrar(self) -> None: ...
