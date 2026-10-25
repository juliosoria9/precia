"""Credenciales del proxy residencial de Evomi a partir de la clave de su API (`EVOMI_API_KEY`).

`GET https://api.evomi.com/public` con la cabecera `x-apikey` devuelve, por producto (`rp`,
`rpc`, `mp`, `dcp`...), el servidor, los puertos, el usuario, la contraseña y el saldo en MB. La
geolocalización va en la contraseña: `contraseña_country-ES`.

Cloudflare, delante de la API, rechaza (error 1010) el User-Agent por defecto de `urllib`: se
manda el de PrecIA. La petición sale por `TransporteURLLib` (sin proxies del entorno, sin seguir
redirecciones, solo a IP públicas y con TLS verificado) y la respuesta se lee con un tope de
bytes y de tiempo, como cualquier otra cosa que venga de fuera.

Ni la clave ni las credenciales aparecen en los mensajes de error, y las excepciones se lanzan sin
la cadena de la original (`from None`): un `JSONDecodeError` guarda el documento entero.
"""

import asyncio
import http.client
import json
import re
import time
from typing import Any

from precia.navegador.http import TAMANO_TROZO, RespuestaCruda, Transporte, TransporteURLLib
from precia.navegador.proxy import ErrorProxy, Proxy
from precia.navegador.tipos import ErrorNavegador

URL_API_EVOMI = "https://api.evomi.com/public"
PRODUCTO_POR_DEFECTO = "rpc"
PAIS_POR_DEFECTO = "ES"
MAX_BYTES_EVOMI = 256 * 1024
TIEMPO_EVOMI_S = 15.0
NOMBRES = {
    "rp": "residential",
    "rpc": "core residential",
    "mp": "mobile",
    "dcp": "datacenter",
    "static_residential": "static residential",
}
"""Nombre legible de cada producto, para la web («Evomi core residential (ES)»)."""
_PAIS = re.compile(r"[A-Z]{2}")
_PRODUCTO = re.compile(r"[a-z][a-z0-9_]{0,39}")


def _comprobar(producto: str, pais: str) -> None:
    if _PAIS.fullmatch(pais) is None:
        raise ErrorProxy("El país del proxy tiene que ser un código de dos letras (ES, FR, PT...).")
    if _PRODUCTO.fullmatch(producto) is None:
        raise ErrorProxy("El producto de Evomi no es válido (por ejemplo, «rpc»).")


def leer_productos_evomi(
    datos: Any, *, producto: str = PRODUCTO_POR_DEFECTO, pais: str = PAIS_POR_DEFECTO
) -> Proxy:
    """El `Proxy` del `producto` pedido, con la geolocalización `pais` en la contraseña."""
    _comprobar(producto, pais)
    if not isinstance(datos, dict) or datos.get("success") is not True:
        raise ErrorProxy("La API de Evomi no devolvió una respuesta válida.")
    productos = datos.get("products")
    if not isinstance(productos, dict):
        raise ErrorProxy("La API de Evomi no devolvió la lista de productos.")
    info = productos.get(producto)
    if not isinstance(info, dict):
        raise ErrorProxy(f"La cuenta de Evomi no tiene el producto «{producto}».")
    saldo = info.get("balance_mb")
    if isinstance(saldo, int | float) and not isinstance(saldo, bool) and saldo <= 0:
        raise ErrorProxy(f"El producto «{producto}» de Evomi no tiene saldo.")
    puertos = info.get("ports")
    puerto = puertos.get("http") if isinstance(puertos, dict) else None
    host, usuario, contrasena = info.get("endpoint"), info.get("username"), info.get("password")
    sin_datos = ErrorProxy(f"El producto «{producto}» de Evomi no trae servidor o credenciales.")
    if not all(isinstance(v, str) and v for v in (host, usuario, contrasena)):
        raise sin_datos
    if isinstance(puerto, bool) or not isinstance(puerto, int):
        raise sin_datos
    assert isinstance(host, str) and isinstance(usuario, str) and isinstance(contrasena, str)
    nombre = f"Evomi {NOMBRES.get(producto, producto)} ({pais})"
    try:
        return Proxy(host, puerto, usuario, f"{contrasena}_country-{pais}", nombre=nombre)
    except ValueError:
        raise sin_datos from None


async def credenciales_evomi(
    clave: str,
    *,
    user_agent: str,
    producto: str = PRODUCTO_POR_DEFECTO,
    pais: str = PAIS_POR_DEFECTO,
    transporte: Transporte | None = None,
    timeout: float = TIEMPO_EVOMI_S,
    max_bytes: int = MAX_BYTES_EVOMI,
) -> Proxy:
    """Pide a la API de Evomi las credenciales y devuelve el `Proxy`. Lanza `ErrorProxy`."""
    clave = clave.strip()
    # Va en una cabecera: nada de espacios ni saltos de línea (inyección de cabeceras), y solo
    # ASCII. Una clave pegada con comillas tipográficas («“...”») es imprimible, pero
    # `http.client` codifica en latin-1 y su `UnicodeEncodeError` guarda la clave entera.
    if (
        not clave
        or not clave.isascii()
        or not clave.isprintable()
        or any(c.isspace() for c in clave)
    ):
        raise ErrorProxy("La clave EVOMI_API_KEY no tiene un formato válido.")
    if any(c in user_agent for c in "\r\n"):
        raise ErrorProxy("El User-Agent no puede contener saltos de línea.")
    if not user_agent.isascii():
        raise ErrorProxy("El User-Agent solo puede llevar caracteres ASCII (sin «€» ni tildes).")
    _comprobar(producto, pais)
    cabeceras = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "x-apikey": clave,
    }
    pedir = transporte or TransporteURLLib()
    datos = await asyncio.to_thread(_pedir, pedir, cabeceras, timeout, max_bytes)
    return leer_productos_evomi(datos, producto=producto, pais=pais)


def _pedir(
    transporte: Transporte, cabeceras: dict[str, str], timeout: float, max_bytes: int
) -> Any:
    try:
        crudo = transporte(URL_API_EVOMI, cabeceras, timeout)
    except (ErrorNavegador, OSError, http.client.HTTPException):
        raise ErrorProxy("No se pudo conectar con la API de Evomi.") from None
    except (UnicodeError, ValueError):
        # Una cabecera que `http.client` no puede enviar: el error cita su valor (la clave).
        raise ErrorProxy("No se pudo enviar la petición a la API de Evomi.") from None
    try:
        cuerpo = _leer(crudo, timeout, max_bytes)
    except (OSError, http.client.HTTPException):
        raise ErrorProxy("La conexión con la API de Evomi se cortó.") from None
    finally:
        crudo.cuerpo.close()
    try:
        return json.loads(cuerpo)
    except ValueError:
        raise ErrorProxy("La API de Evomi no devolvió JSON.") from None


def _leer(crudo: RespuestaCruda, timeout: float, max_bytes: int) -> bytes:
    if crudo.estado in (401, 403):
        raise ErrorProxy(
            f"La API de Evomi rechazó la clave (HTTP {crudo.estado}): revisa EVOMI_API_KEY."
        )
    if not 200 <= crudo.estado < 300:
        raise ErrorProxy(f"La API de Evomi respondió con el estado {crudo.estado}.")
    tipo = crudo.cabeceras.get("content-type", "").split(";")[0].strip().lower()
    if tipo != "application/json":
        raise ErrorProxy("La API de Evomi no devolvió JSON.")
    limite = time.monotonic() + timeout
    trozos: list[bytes] = []
    total = 0
    while trozo := crudo.cuerpo.read(TAMANO_TROZO):
        total += len(trozo)
        if total > max_bytes:
            raise ErrorProxy("La respuesta de la API de Evomi es demasiado grande.")
        if time.monotonic() > limite:
            raise ErrorProxy("La API de Evomi tarda demasiado en responder.")
        trozos.append(trozo)
    return b"".join(trozos)
