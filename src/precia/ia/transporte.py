"""Transporte HTTP de los proveedores: una función síncrona que se ejecuta en un hilo.

Es inyectable para que los tests no usen la red. El de verdad usa `urllib`, solo admite
`http`/`https` (nada de `file://`), no sigue redirecciones (una redirección haría reenviar la
clave a otro sitio) y limita el tamaño de la respuesta.
"""

import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from email.message import Message
from typing import IO, Any
from urllib.parse import urlsplit

RespuestaHTTP = tuple[int, dict[str, str], bytes]
Transporte = Callable[[str, Mapping[str, str], bytes], RespuestaHTTP]
"""`(url, cabeceras, cuerpo) -> (estado, cabeceras, cuerpo)`; los errores de red lanzan
`ErrorTransporte`. Las respuestas 4xx y 5xx se devuelven, no se lanzan."""

Abrir = Callable[[urllib.request.Request, float], Any]


class ErrorTransporte(Exception):
    """Fallo de red, tiempo de espera agotado o respuesta demasiado grande."""


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> urllib.request.Request | None:
        # Devolver None hace que urllib entregue el 3xx como HTTPError en vez de seguirlo.
        return None


def _leer(fuente: IO[bytes] | None, max_bytes: int) -> bytes:
    if fuente is None:
        return b""
    datos = fuente.read(max_bytes + 1)
    if len(datos) > max_bytes:
        raise ErrorTransporte("La respuesta supera el tamaño máximo")
    return datos


def _cabeceras(mensaje: Message | None) -> dict[str, str]:
    if mensaje is None:
        return {}
    return {clave.lower(): valor for clave, valor in mensaje.items()}


def crear_transporte_urllib(
    *, timeout: float, max_bytes: int, abrir: Abrir | None = None
) -> Transporte:
    """Transporte real con `urllib` (`abrir` solo se sustituye en los tests)."""
    if abrir is None:
        abridor = urllib.request.build_opener(_SinRedirecciones)

        def abrir_real(peticion: urllib.request.Request, espera: float) -> Any:
            return abridor.open(peticion, timeout=espera)

        abrir = abrir_real

    def transporte(url: str, cabeceras: Mapping[str, str], cuerpo: bytes) -> RespuestaHTTP:
        if urlsplit(url).scheme not in ("http", "https"):
            raise ErrorTransporte("Solo se admiten URL http o https")
        peticion = urllib.request.Request(url, data=cuerpo, headers=dict(cabeceras), method="POST")
        try:
            try:
                with abrir(peticion, timeout) as respuesta:
                    estado = respuesta.status
                    return estado, _cabeceras(respuesta.headers), _leer(respuesta, max_bytes)
            except urllib.error.HTTPError as error:
                # Leer el cuerpo del error también puede fallar: lo recoge el `try` de fuera.
                with error:
                    return error.code, _cabeceras(error.headers), _leer(error.fp, max_bytes)
        except (OSError, http.client.HTTPException, ValueError) as error:
            # URLError, TimeoutError, los errores de socket, las respuestas HTTP malformadas
            # (IncompleteRead, BadStatusLine) y los ValueError de http.client (puerto no válido,
            # cabecera no válida), que copian el valor de la cabecera, es decir, la clave. Solo
            # el tipo: así no se arrastra nada de la petición.
            raise ErrorTransporte(f"Error de red ({type(error).__name__})") from None

    return transporte
