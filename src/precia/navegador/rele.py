"""Relé SOCKS5 local de Chromium: su única salida a la red y el anti-SSRF de cada conexión.

Chromium usa su propia red (HTTP/2 y TLS de Chrome, ADR-022/023) y `route` no ve los saltos de
una redirección ni todo lo que abre el navegador. Por eso Chromium arranca **siempre** contra este
relé (`socks5://127.0.0.1:N`), también sin proxy, y el relé decide cada conexión TCP:

1. Solo SOCKS5 `CONNECT` sin autenticación (RFC 1928). `BIND` y `UDP ASSOCIATE` se rechazan, y
   cualquier otra cosa (una petición HTTP de una web abierta en otro navegador, SOCKS4) se cierra
   sin contestar.
2. El host es estricto: ASCII de `[A-Za-z0-9.-]` o una IP literal. Así no hay forma de colar algo
   que el validador entienda de otra manera (`validar_url("http://tienda.com#@127.0.0.1:80/")`
   devuelve `http://tienda.com/`).
3. El puerto lo comprueba el propio relé (80 o 443, `url.PUERTOS`) y va en el esquema de la URL
   que se valida, nunca en la URL.
4. La URL pasa por el **mismo** `Validador` que usa el descargador (IP literales, sufijos internos
   y DNS) y, a partir de ahí, solo se usa el host normalizado que devuelve.
5. Sin proxy, el relé resuelve, comprueba **todas** las IP (`ips_permitidas`, contra el *DNS
   rebinding*) y conecta a una de ellas. Con proxy, `CONNECT` al proxy residencial añadiendo
   `Proxy-Authorization`; con un proxy de la red local, a la IP ya validada (como `_Salida` de
   `navegador.http`), y con uno remoto (Evomi), con el nombre.

Las credenciales del proxy solo las conoce el relé: Playwright las copiaría en las
`httpCredentials` de cada contexto y contestaría con ellas a cualquier `401 Basic` de un destino.

Escucha en 127.0.0.1, en un puerto al azar y solo mientras Chromium está abierto. Otro programa de
este equipo podría usarlo durante ese rato, pero solo llegaría a webs públicas por los puertos 80 y
443. El saludo y la respuesta del proxy se leen con tope de bytes y de tiempo. Los avisos nunca
citan las credenciales.

Una página hostil puede abrir muchas conexiones a nombres cuyas IP descartan el SYN. Para que eso
no congele la aplicación: el plazo de conexión es **total** (para todas las IP, no uno por IP), se
conecta con `asyncio` sin hilos, el DNS va a un grupo de hilos propio y pequeño (nunca al executor
por defecto, el de las descargas, la caché, el almacén y la IA) y como mucho
`MAX_CONEXIONES_PENDIENTES` conexiones se validan y abren a la vez; las demás esperan su turno.
"""

import asyncio
import contextlib
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlsplit

from precia.navegador.http import Resolver, ips_permitidas
from precia.navegador.proxy import Proxy
from precia.navegador.tipos import URLNoPermitida
from precia.navegador.url import IP, PUERTOS, Validador, ip_publica, validar_url

registro = logging.getLogger(__name__)

MAX_CABECERA = 16 * 1024
"""Bytes de la respuesta del proxy al `CONNECT` (sin el túnel)."""
TIEMPO_S = 30.0
TROZO = 64 * 1024
ESPERA_CIERRE_S = 1.0
MAX_CONEXIONES_PENDIENTES = 8
"""Conexiones que el relé valida y abre a la vez (las ya abiertas no cuentan)."""
HILOS_DNS = 4
"""Hilos del grupo propio del relé para resolver nombres."""

AbrirConexion = Callable[..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

# Códigos de respuesta de SOCKS5 (RFC 1928, §6).
REP_BIEN = 0x00
REP_FALLO = 0x01
REP_NO_PERMITIDA = 0x02
REP_RECHAZADA = 0x05
REP_COMANDO = 0x07
REP_DIRECCION = 0x08
_SIN_METODO = b"\x05\xff"
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")


class _Rechazo(Exception):
    """El relé contesta con este código SOCKS5 y cierra. Sin código, cierra sin contestar."""

    def __init__(self, codigo: int | None) -> None:
        super().__init__(codigo)
        self.codigo = codigo


class ReleProxy:
    """Servidor SOCKS5 local que valida cada conexión y sale directo o por el proxy."""

    def __init__(
        self,
        proxy: Proxy | None = None,
        *,
        validador: Validador = validar_url,
        timeout: float = TIEMPO_S,
        resolver: Resolver = socket.getaddrinfo,
        ip_permitida: Callable[[IP], bool] = ip_publica,
        proxy_remoto: Callable[[IP], bool] = ip_publica,
        puertos: frozenset[int] = PUERTOS,
        abrir_conexion: AbrirConexion = asyncio.open_connection,
    ) -> None:
        """`resolver`, `ip_permitida`, `proxy_remoto`, `puertos` y `abrir_conexion` solo se cambian
        en los tests."""
        self._proxy = proxy
        self._validador = validador
        self._timeout = timeout
        self._resolver = resolver
        self._ip_permitida = ip_permitida
        self._proxy_remoto = proxy_remoto
        self._puertos = puertos
        self._abrir_conexion = abrir_conexion
        self._pendientes = asyncio.Semaphore(MAX_CONEXIONES_PENDIENTES)
        self._hilos: ThreadPoolExecutor | None = None
        self._servidor: asyncio.Server | None = None
        self._tareas: set[asyncio.Task[None]] = set()

    def __repr__(self) -> str:
        return f"ReleProxy({self._proxy!r})"

    async def abrir(self) -> str:
        """Arranca el relé (si no lo estaba) y devuelve su dirección, `socks5://127.0.0.1:N`."""
        if self._hilos is None:
            self._hilos = ThreadPoolExecutor(HILOS_DNS, thread_name_prefix="rele-dns")
        if self._servidor is None:
            self._servidor = await asyncio.start_server(
                self._atender, "127.0.0.1", 0, limit=MAX_CABECERA
            )
        puerto = self._servidor.sockets[0].getsockname()[1]
        return f"socks5://127.0.0.1:{puerto}"

    async def cerrar(self) -> None:
        servidor, self._servidor = self._servidor, None
        hilos, self._hilos = self._hilos, None
        if hilos is not None:
            # Un DNS colgado no retiene el cierre: su hilo acaba solo y nadie espera el resultado.
            hilos.shutdown(wait=False, cancel_futures=True)
        if servidor is None:
            return
        servidor.close()
        servidor.close_clients()
        for tarea in list(self._tareas):
            tarea.cancel()
        await asyncio.gather(*self._tareas, return_exceptions=True)
        # Con un plazo: en Windows, una conexión que Chromium corta con RST puede no darse nunca
        # por cerrada (asyncio lanza en `_call_connection_lost`) y `wait_closed` no volvería.
        # El puerto ya está cerrado desde `close()`.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(servidor.wait_closed(), ESPERA_CIERRE_S)

    async def _atender(self, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        tarea = asyncio.current_task()
        if tarea is not None:
            self._tareas.add(tarea)
        arriba: asyncio.StreamWriter | None = None
        try:
            try:
                await self._saludo(lector, escritor)
                host, puerto = await self._leer_peticion(lector)
                async with self._pendientes:
                    host = await self._validar(host, puerto)
                    lector_arriba, arriba = await self._conectar(host, puerto)
            except _Rechazo as rechazo:
                if rechazo.codigo is not None:
                    escritor.write(_respuesta(rechazo.codigo))
                    await escritor.drain()
                return
            escritor.write(_respuesta(REP_BIEN))
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

    async def _leer(self, lector: asyncio.StreamReader, n: int, codigo: int | None) -> bytes:
        try:
            return await asyncio.wait_for(lector.readexactly(n), self._timeout)
        except (asyncio.IncompleteReadError, TimeoutError, OSError):
            raise _Rechazo(codigo) from None

    async def _saludo(self, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        version, cuantos = await self._leer(lector, 2, None)
        if version != 5:
            raise _Rechazo(None)  # no es SOCKS5: ni se contesta
        metodos = await self._leer(lector, cuantos, None) if cuantos else b""
        if 0 not in metodos:
            escritor.write(_SIN_METODO)
            await escritor.drain()
            raise _Rechazo(None)
        escritor.write(b"\x05\x00")
        await escritor.drain()

    async def _leer_peticion(self, lector: asyncio.StreamReader) -> tuple[str, int]:
        """Lee el `CONNECT` y devuelve el host (estricto) y el puerto, aún sin validar."""
        version, comando, _, tipo = await self._leer(lector, 4, None)
        if version != 5:
            raise _Rechazo(REP_FALLO)
        if comando != 1:
            raise _Rechazo(REP_COMANDO)
        host: str | None
        if tipo == 1:
            host = str(ipaddress.IPv4Address(await self._leer(lector, 4, None)))
        elif tipo == 4:
            host = str(ipaddress.IPv6Address(await self._leer(lector, 16, None)))
        elif tipo == 3:
            (longitud,) = await self._leer(lector, 1, None)
            nombre = await self._leer(lector, longitud, None) if longitud else b""
            host = _host_estricto(nombre)
        else:
            raise _Rechazo(REP_DIRECCION)
        puerto = int.from_bytes(await self._leer(lector, 2, None), "big")
        if host is None or puerto not in self._puertos:
            raise _Rechazo(REP_NO_PERMITIDA)
        return host, puerto

    async def _validar(self, host: str, puerto: int) -> str:
        esquema = "https" if puerto == 443 else "http"
        direccion = f"[{host}]" if ":" in host else host
        if puerto not in PUERTOS:
            direccion = f"{direccion}:{puerto}"  # solo en los tests (`puertos`)
        try:
            validada = await self._validador(f"{esquema}://{direccion}/")
        except URLNoPermitida:
            raise _Rechazo(REP_NO_PERMITIDA) from None
        normalizado = urlsplit(validada).hostname
        if not normalizado:
            raise _Rechazo(REP_NO_PERMITIDA)
        return normalizado

    async def _conectar(
        self, host: str, puerto: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._proxy is not None:
            return await self._abrir_tunel(self._proxy, host, puerto)
        bucle = asyncio.get_running_loop()
        limite = bucle.time() + self._timeout
        ips = await self._resolver_validadas(host, puerto, self._timeout)
        for ip in ips:
            restante = limite - bucle.time()
            if restante <= 0:
                break
            # Si se agota el plazo o se cancela, `open_connection` cierra su socket.
            with contextlib.suppress(OSError, TimeoutError):
                return await asyncio.wait_for(
                    self._abrir_conexion(str(ip), puerto, limit=MAX_CABECERA), restante
                )
        raise _Rechazo(REP_RECHAZADA)

    async def _resolver_validadas(self, host: str, puerto: int, plazo: float) -> list[IP]:
        """`ips_permitidas` en el grupo de hilos propio del relé, con plazo."""
        if self._hilos is None:
            raise _Rechazo(REP_FALLO)  # el relé se está cerrando
        consulta = partial(
            ips_permitidas, host, puerto, resolver=self._resolver, ip_permitida=self._ip_permitida
        )
        try:
            futuro = asyncio.get_running_loop().run_in_executor(self._hilos, consulta)
            return await asyncio.wait_for(futuro, plazo)
        except URLNoPermitida:
            raise _Rechazo(REP_NO_PERMITIDA) from None
        except (OSError, TimeoutError, RuntimeError):
            # RuntimeError: el grupo ya se cerró (el relé se está cerrando).
            raise _Rechazo(REP_RECHAZADA) from None

    async def _abrir_tunel(
        self, proxy: Proxy, host: str, puerto: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            lector, escritor = await asyncio.wait_for(
                asyncio.open_connection(proxy.host, proxy.puerto, limit=MAX_CABECERA),
                self._timeout,
            )
        except (OSError, TimeoutError):
            registro.warning("El relé de Chromium no pudo conectar con el proxy.")
            raise _Rechazo(REP_FALLO) from None
        try:
            destino = await self._destino(host, puerto, escritor)
            escritor.write(_peticion_connect(proxy, destino, puerto))
            await escritor.drain()
            respuesta = await self._leer_cabecera(lector)
            estado = _estado(respuesta)
            if estado != 200:
                # Solo el estado: la respuesta del proxy podría repetir lo que se le mandó.
                registro.warning("El proxy rechazó el túnel de Chromium (HTTP %s).", estado)
                raise _Rechazo(REP_RECHAZADA)
        except OSError:
            escritor.close()
            raise _Rechazo(REP_FALLO) from None
        except BaseException:
            escritor.close()
            raise
        return lector, escritor

    async def _leer_cabecera(self, lector: asyncio.StreamReader) -> bytes:
        try:
            return await asyncio.wait_for(lector.readuntil(b"\r\n\r\n"), self._timeout)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, OSError):
            raise _Rechazo(REP_FALLO) from None

    async def _destino(self, host: str, puerto: int, escritor: asyncio.StreamWriter) -> str:
        """El nombre, o la IP ya validada si el proxy está en la red local (ver arriba)."""
        ip_proxy = ipaddress.ip_address(escritor.get_extra_info("peername")[0])
        if self._proxy_remoto(ip_proxy):
            return host
        ips = await self._resolver_validadas(host, puerto, self._timeout)
        return str(ips[0])


def _host_estricto(nombre: bytes) -> str | None:
    """El nombre si es ASCII de `[A-Za-z0-9.-]` o una IP literal; si no, `None`."""
    try:
        texto = nombre.decode("ascii")
    except UnicodeDecodeError:
        return None
    if _HOST.fullmatch(texto):
        return texto
    try:
        return str(ipaddress.ip_address(texto))
    except ValueError:
        return None


def _respuesta(codigo: int) -> bytes:
    # La dirección «ligada» no le sirve de nada a Chromium: 0.0.0.0:0.
    return bytes([5, codigo, 0, 1, 0, 0, 0, 0, 0, 0])


def _peticion_connect(proxy: Proxy, destino: str, puerto: int) -> bytes:
    direccion = f"[{destino}]" if ":" in destino else destino
    lineas = [f"CONNECT {direccion}:{puerto} HTTP/1.1", f"Host: {direccion}:{puerto}"]
    autorizacion = proxy.cabecera_autorizacion
    if autorizacion:
        lineas.append(f"Proxy-Authorization: {autorizacion}")
    return ("\r\n".join(lineas) + "\r\n\r\n").encode("latin-1")


def _estado(respuesta: bytes) -> int:
    partes = respuesta.split(b"\r\n", 1)[0].split(b" ", 2)
    if len(partes) < 2 or not partes[0].startswith(b"HTTP/1."):
        raise _Rechazo(REP_FALLO)
    try:
        return int(partes[1])
    except ValueError:
        raise _Rechazo(REP_FALLO) from None


async def _copiar(origen: asyncio.StreamReader, destino: asyncio.StreamWriter) -> None:
    """Copia hasta que `origen` se cierra; entonces cierra la escritura de `destino`."""
    with contextlib.suppress(OSError):
        while datos := await origen.read(TROZO):
            destino.write(datos)
            await destino.drain()
    with contextlib.suppress(OSError, RuntimeError):
        if destino.can_write_eof():
            destino.write_eof()
