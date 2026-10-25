"""Middleware ASGI de seguridad: cabeceras, protección CSRF y límite del cuerpo de la petición.

Se escribe como middleware ASGI puro (no `BaseHTTPMiddleware`) para poder cortar la petición
antes de que llegue a la aplicación y para añadir cabeceras a todas las respuestas, también a
los errores (incluidos los 500) y a los ficheros estáticos.
"""

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Mensaje = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Mensaje]]
Send = Callable[[Mensaje], Awaitable[None]]
ASGIApp = Callable[[Mensaje, Receive, Send], Awaitable[None]]

registro = logging.getLogger(__name__)

LIMITE_CUERPO = 16 * 1024
METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})
# GET que disparan trabajo con coste (scraping, llamadas de pago a la IA, Google). Otra web no
# puede lanzarlos aunque sean GET; sí se permiten escribiendo la URL o desde la propia app.
RUTAS_CON_COSTE = frozenset({"/buscar"})

CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        # Las fotos de los artículos se enlazan desde la tienda o su CDN (solo https). Nunca
        # `data:`, `blob:` ni `*`; la URL ya llega validada y el autoescape impide colar un `<img>`.
        "img-src 'self' https:",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    )
)

CABECERAS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)


class CuerpoDemasiadoGrande(Exception):
    """El cliente envía más bytes de los permitidos o de los que declaró."""


def origen_permitido(cabeceras: dict[str, str], esquema: str) -> bool:
    """Decide si una petición que modifica datos viene de la propia aplicación."""
    sitio = cabeceras.get("sec-fetch-site")
    if sitio is not None:
        return sitio == "same-origin"
    origen = cabeceras.get("origin")
    if origen is not None:
        return origen == f"{esquema}://{cabeceras.get('host', '')}"
    return False


def lanzada_desde_otra_web(cabeceras: dict[str, str]) -> bool:
    # «none»: el usuario escribe la URL o usa un marcador. Sin cabecera: cliente que no es un
    # navegador moderno (curl, tests), que no puede ser una web ajena actuando en su nombre.
    return cabeceras.get("sec-fetch-site", "none") not in ("same-origin", "none")


class MiddlewareSeguridad:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Mensaje, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        iniciada = False

        async def enviar_con_cabeceras(mensaje: Mensaje) -> None:
            nonlocal iniciada
            if mensaje["type"] == "http.response.start":
                iniciada = True
                mensaje["headers"] = [*mensaje.get("headers", []), *CABECERAS]
            await send(mensaje)

        cabeceras = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        recibir = receive

        if scope["method"] in METODOS_SEGUROS:
            if scope["path"] in RUTAS_CON_COSTE and lanzada_desde_otra_web(cabeceras):
                await _rechazar(enviar_con_cabeceras, 403, "Búsqueda lanzada desde otra web.")
                return
        else:
            if not origen_permitido(cabeceras, scope.get("scheme", "http")):
                await _rechazar(enviar_con_cabeceras, 403, "Petición de otro origen rechazada.")
                return
            if "transfer-encoding" in cabeceras:
                # Con Content-Length y chunked a la vez, el servidor lee el chunked y la
                # longitud declarada deja de servir como límite (petición ambigua).
                await _rechazar(enviar_con_cabeceras, 400, "Transfer-Encoding no admitido.")
                return
            longitud = cabeceras.get("content-length", "")
            if not (longitud.isascii() and longitud.isdigit()):
                await _rechazar(enviar_con_cabeceras, 411, "Falta la longitud del cuerpo.")
                return
            declarada = int(longitud)
            if declarada > LIMITE_CUERPO:
                await _rechazar(enviar_con_cabeceras, 413, "El formulario es demasiado grande.")
                return
            recibir = _receive_limitado(receive, declarada)

        try:
            await self.app(scope, recibir, enviar_con_cabeceras)
        except CuerpoDemasiadoGrande:
            if not iniciada:
                await _rechazar(enviar_con_cabeceras, 413, "El formulario es demasiado grande.")
        except Exception:
            # Sin esto, el 500 lo genera Starlette por fuera de este middleware y sale sin
            # cabeceras de seguridad. Al usuario no se le enseña el detalle del error.
            registro.exception("Error no controlado en %s %s", scope["method"], scope["path"])
            if not iniciada:
                await _rechazar(enviar_con_cabeceras, 500, "Error interno.")


def _receive_limitado(receive: Receive, declarada: int) -> Receive:
    """Cuenta los bytes que llegan de verdad, sin fiarse solo de la cabecera."""
    recibidos = 0

    async def recibir() -> Mensaje:
        nonlocal recibidos
        mensaje = await receive()
        if mensaje["type"] == "http.request":
            recibidos += len(mensaje.get("body", b""))
            if recibidos > min(declarada, LIMITE_CUERPO):
                raise CuerpoDemasiadoGrande
        return mensaje

    return recibir


async def _rechazar(send: Send, estado: int, texto: str) -> None:
    cuerpo = texto.encode()
    await send(
        {
            "type": "http.response.start",
            "status": estado,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(cuerpo)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": cuerpo})
