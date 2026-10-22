"""Relé local del proxy para Chromium: Playwright nunca recibe las credenciales del proxy.

Con `launch(proxy={"server", "username", "password"})`, Playwright copia el usuario y la contraseña
en las `httpCredentials` de cada contexto, sin origen (comprobado en Playwright 1.64:
`authenticateProxyViaCredentials`). Su `APIRequestContext`, que es el que hace `route.fetch` (y
por ahí pasan todas nuestras peticiones, ver `navegador.chromium`), repite con ellas en
`Authorization: Basic` **cualquier** respuesta `401` con `WWW-Authenticate: Basic`. Un script de
terceros en la página de una tienda, o la propia tienda, se llevaría las credenciales de Evomi y
con ellas el saldo de pago. Pasarle `http_credentials` con origen al contexto no sirve: esa
función las sobrescribe al crearlo.

Por eso Chromium se arranca contra este relé, en 127.0.0.1 y sin credenciales. El relé solo
acepta `CONNECT` (el `HttpsProxyAgent` de Playwright lo usa también para http) y lo reenvía al
proxy de verdad añadiendo `Proxy-Authorization`, que solo lee el proxy:

- Escucha en 127.0.0.1, en un puerto al azar y solo mientras Chromium está abierto. Otro programa
  de este equipo podría usarlo durante ese rato: el mismo riesgo que tener la clave en `.env`.
- El destino del túnel se trata como en `navegador.http` (`_Salida`): con un proxy de la red
  local o de este equipo, el nombre se resuelve aquí, se valida (`ip_publica`) y el `CONNECT` va a
  esa IP, contra el *DNS rebinding*. Con un proxy remoto (Evomi), va el nombre.
- La petición del cliente y la respuesta del proxy se leen con tope de bytes y de tiempo. Los
  avisos nunca citan las credenciales.
"""

import asyncio
import contextlib
import ipaddress
import logging
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from precia.navegador.http import Resolver, ips_permitidas
from precia.navegador.proxy import Proxy
from precia.navegador.tipos import URLNoPermitida
from precia.navegador.url import IP, ip_publica

registro = logging.getLogger(__name__)

MAX_CABECERA = 16 * 1024
"""Bytes de la petición `CONNECT` y de la respuesta del proxy (sin el túnel)."""
TIEMPO_S = 30.0
TROZO = 64 * 1024
_ESTABLECIDO = b"HTTP/1.1 200 Connection established\r\n\r\n"


class _Rechazo(Exception):
    """El relé contesta al cliente con este estado y cierra (sin abrir el túnel)."""

    def __init__(self, estado: int, texto: str) -> None:
        super().__init__(f"{estado} {texto}")
        self.estado = estado
        self.texto = texto

    def respuesta(self) -> bytes:
        return (
            f"HTTP/1.1 {self.estado} {self.texto}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode("ascii")


class ReleProxy:
    """Proxy HTTP local sin autenticación que reenvía cada `CONNECT` al proxy de verdad."""

    def __init__(
        self,
        proxy: Proxy,
        *,
        timeout: float = TIEMPO_S,
        resolver: Resolver = socket.getaddrinfo,
        ip_permitida: Callable[[IP], bool] = ip_publica,
        proxy_remoto: Callable[[IP], bool] = ip_publica,
    ) -> None:
        """`resolver`, `ip_permitida` y `proxy_remoto` solo se cambian en los tests."""
        self._proxy = proxy
        self._timeout = timeout
        self._resolver = resolver
        self._ip_permitida = ip_permitida
        self._proxy_remoto = proxy_remoto
        self._servidor: asyncio.Server | None = None
        self._tareas: set[asyncio.Task[None]] = set()

    def __repr__(self) -> str:
        return f"ReleProxy({self._proxy!r})"

    async def abrir(self) -> str:
        """Arranca el relé (si no lo estaba) y devuelve su dirección, `http://127.0.0.1:puerto`."""
        if self._servidor is None:
            self._servidor = await asyncio.start_server(
                self._atender, "127.0.0.1", 0, limit=MAX_CABECERA
            )
        puerto = self._servidor.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{puerto}"

    async def cerrar(self) -> None:
        servidor, self._servidor = self._servidor, None
        if servidor is None:
            return
        servidor.close()
        servidor.close_clients()
        for tarea in list(self._tareas):
            tarea.cancel()
        await asyncio.gather(*self._tareas, return_exceptions=True)
        await servidor.wait_closed()

    async def _atender(self, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        tarea = asyncio.current_task()
        if tarea is not None:
            self._tareas.add(tarea)
        arriba: asyncio.StreamWriter | None = None
        try:
            try:
                host, puerto = await self._leer_connect(lector)
                lector_arriba, arriba = await self._abrir_tunel(host, puerto)
            except _Rechazo as rechazo:
                escritor.write(rechazo.respuesta())
                await escritor.drain()
                return
            escritor.write(_ESTABLECIDO)
            await escritor.drain()
            await asyncio.gather(_copiar(lector, arriba), _copiar(lector_arriba, escritor))
        except OSError:
            pass  # el cliente se fue: no hay a quién contestar
        finally:
            for extremo in (arriba, escritor):
                if extremo is not None:
                    with contextlib.suppress(OSError, RuntimeError):
                        extremo.close()
            if tarea is not None:
                self._tareas.discard(tarea)

    async def _leer_connect(self, lector: asyncio.StreamReader) -> tuple[str, int]:
        cabecera = await self._leer_cabecera(lector, _Rechazo(400, "Bad Request"))
        partes = cabecera.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
        if len(partes) != 3 or not partes[2].startswith("HTTP/1."):
            raise _Rechazo(400, "Bad Request")
        metodo, destino, _ = partes
        if metodo != "CONNECT":
            # Playwright solo pide túneles: cualquier otra cosa no viene de él.
            raise _Rechazo(405, "Method Not Allowed")
        try:
            autoridad = urlsplit(f"//{destino}")
            host, puerto = autoridad.hostname, autoridad.port
        except ValueError:
            raise _Rechazo(400, "Bad Request") from None
        if (
            not host
            or puerto is None
            or not host.isascii()
            or autoridad.username is not None
            or autoridad.path
        ):
            raise _Rechazo(400, "Bad Request")
        return host, puerto

    async def _leer_cabecera(self, lector: asyncio.StreamReader, rechazo: _Rechazo) -> bytes:
        try:
            return await asyncio.wait_for(lector.readuntil(b"\r\n\r\n"), self._timeout)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, OSError):
            raise rechazo from None

    async def _abrir_tunel(
        self, host: str, puerto: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            lector, escritor = await asyncio.wait_for(
                asyncio.open_connection(self._proxy.host, self._proxy.puerto, limit=MAX_CABECERA),
                self._timeout,
            )
        except (OSError, TimeoutError):
            registro.warning("El relé de Chromium no pudo conectar con el proxy.")
            raise _Rechazo(502, "Bad Gateway") from None
        try:
            destino = await self._destino(host, puerto, escritor)
            escritor.write(self._peticion_connect(destino, puerto))
            await escritor.drain()
            respuesta = await self._leer_cabecera(lector, _Rechazo(502, "Bad Gateway"))
            estado = _estado(respuesta)
            if estado != 200:
                # Solo el estado: la respuesta del proxy podría repetir lo que se le mandó.
                registro.warning("El proxy rechazó el túnel de Chromium (HTTP %s).", estado)
                raise _Rechazo(502, "Bad Gateway")
        except OSError:
            escritor.close()
            raise _Rechazo(502, "Bad Gateway") from None
        except BaseException:
            escritor.close()
            raise
        return lector, escritor

    async def _destino(self, host: str, puerto: int, escritor: asyncio.StreamWriter) -> str:
        """El nombre, o la IP ya validada si el proxy está en la red local (ver arriba)."""
        ip_proxy = ipaddress.ip_address(escritor.get_extra_info("peername")[0])
        if self._proxy_remoto(ip_proxy):
            return host
        try:
            ips = await asyncio.to_thread(
                ips_permitidas,
                host,
                puerto,
                resolver=self._resolver,
                ip_permitida=self._ip_permitida,
            )
        except URLNoPermitida:
            raise _Rechazo(403, "Forbidden") from None
        return str(ips[0])

    def _peticion_connect(self, destino: str, puerto: int) -> bytes:
        direccion = f"[{destino}]" if ":" in destino else destino
        lineas = [f"CONNECT {direccion}:{puerto} HTTP/1.1", f"Host: {direccion}:{puerto}"]
        autorizacion = self._proxy.cabecera_autorizacion
        if autorizacion:
            lineas.append(f"Proxy-Authorization: {autorizacion}")
        return ("\r\n".join(lineas) + "\r\n\r\n").encode("latin-1")


def _estado(respuesta: bytes) -> int:
    partes = respuesta.split(b"\r\n", 1)[0].split(b" ", 2)
    if len(partes) < 2 or not partes[0].startswith(b"HTTP/1."):
        raise _Rechazo(502, "Bad Gateway")
    try:
        return int(partes[1])
    except ValueError:
        raise _Rechazo(502, "Bad Gateway") from None


async def _copiar(origen: asyncio.StreamReader, destino: asyncio.StreamWriter) -> None:
    """Copia hasta que `origen` se cierra; entonces cierra la escritura de `destino`."""
    with contextlib.suppress(OSError):
        while datos := await origen.read(TROZO):
            destino.write(datos)
            await destino.drain()
    with contextlib.suppress(OSError, RuntimeError):
        if destino.can_write_eof():
            destino.write_eof()
