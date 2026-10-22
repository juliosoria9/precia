"""Contrato del paquete `navegador`: página descargada, errores y descargadores."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


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
    via_proxy: bool = False
    """Llegó por el proxy de salida (`navegador.con_proxy`): para enseñarlo en el progreso."""
    por_chromium: bool = False
    """La descargó Chromium (con `js`, en un rescate o porque la tienda ya estaba rescatada):
    pedirla otra vez con `js=True` no traería nada distinto."""


class ErrorNavegador(Exception):
    """Base de los errores del navegador. El mensaje se puede enseñar al usuario."""


class URLNoPermitida(ErrorNavegador):
    """Esquema, host, puerto o IP no permitidos (defensa SSRF)."""


class ProhibidoPorRobots(ErrorNavegador):
    """El robots.txt del dominio prohíbe la ruta para nuestro User-Agent."""


class Bloqueado(ErrorNavegador):
    """La web responde con un CAPTCHA o un bloqueo anti-bot. Nunca se intenta resolver.

    Antes de llegar aquí ya se ha probado lo que hay: con un proxy configurado, una vez desde otra
    IP (`navegador.con_proxy`), y, si hay Chromium, una vez con Chromium tras el bloqueo por HTTP
    (`navegador.navegador`, ADR-023; nunca tras un 401, un 451 o un 429 normal, el de límite de
    peticiones). Si también se bloquea, la tienda se para.
    """

    def __init__(self, url: str, motivo: str) -> None:
        super().__init__(f"Bloqueo en {url}: {motivo}")
        self.url = url
        self.motivo = motivo


class ErrorDescarga(ErrorNavegador):
    """Red, tiempo de espera, tamaño excesivo o contenido que no es HTML."""


class ConexionCortada(ErrorDescarga):
    """La tienda cortó la conexión sin responder (`ECONNRESET`), como hacen algunos WAF con las IP
    de centros de datos. Con un proxy, cuenta como bloqueo (`navegador.con_proxy`)."""


class Descargador(Protocol):
    """Hace la petición sin más: la validación, robots, cortesía y caché van por fuera."""

    async def descargar(self, url: str) -> Pagina: ...

    async def cerrar(self) -> None: ...


@runtime_checkable
class DescargadorGrande(Descargador, Protocol):
    """Un descargador que además baja ficheros grandes (sitemaps) con otro tope de bytes."""

    async def descargar_grande(self, url: str, *, max_bytes: int) -> Pagina: ...
