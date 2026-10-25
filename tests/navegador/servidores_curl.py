"""Servidores hostiles en 127.0.0.1 para los tests de `TransporteCurl` (sin Internet).

Hablan HTTP «a mano» sobre sockets para poder mandar lo que un servidor normal no manda: un RST
tras la petición o tras el ClientHello, cabeceras a goteo, respuestas mal formadas o comprimidas
de formas raras. Todos guardan lo que reciben para que el test compruebe qué mandó el cliente.
"""

import contextlib
import socket
import ssl
import struct
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

Respuesta = bytes | Callable[[bytes], bytes]
"""Lo que se contesta: siempre lo mismo o según la petición recibida (sus bytes)."""


@dataclass
class Registro:
    puerto: int
    peticiones: list[bytes] = field(default_factory=list)
    conexiones: int = 0


def respuesta_http(
    cuerpo: bytes = b"<p>Hola</p>",
    *,
    estado: str = "200 OK",
    tipo: str = "text/html; charset=utf-8",
    cerrar: bool = True,
    **otras: str,
) -> bytes:
    """Una respuesta HTTP/1.1 completa (con `Content-Length` salvo que se pase otra cosa)."""
    cabeceras = {"Content-Type": tipo, "Content-Length": str(len(cuerpo))}
    if cerrar:
        cabeceras["Connection"] = "close"
    cabeceras.update({k.replace("_", "-"): v for k, v in otras.items()})
    lineas = "".join(f"{k}: {v}\r\n" for k, v in cabeceras.items())
    return f"HTTP/1.1 {estado}\r\n{lineas}\r\n".encode("latin-1") + cuerpo


def _leer_peticion(conexion: socket.socket) -> bytes:
    datos = b""
    while b"\r\n\r\n" not in datos:
        trozo = conexion.recv(65536)
        if not trozo:
            return datos
        datos += trozo
    return datos


def _reset(conexion: socket.socket) -> None:
    """Cierra con RST (SO_LINGER a 0), como un WAF que corta la conexión."""
    conexion.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    conexion.close()


@contextlib.contextmanager
def _escuchar(
    atender: Callable[[socket.socket, Registro], None], host: str = "127.0.0.1", puerto: int = 0
) -> Iterator[Registro]:
    escucha = socket.create_server((host, puerto))
    escucha.settimeout(0.1)
    registro = Registro(escucha.getsockname()[1])
    parar = threading.Event()
    hilos: list[threading.Thread] = []

    def aceptar() -> None:
        while not parar.is_set():
            try:
                conexion, _ = escucha.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conexion.settimeout(10)
            registro.conexiones += 1
            hilo = threading.Thread(target=_atender_seguro, args=(conexion,), daemon=True)
            hilos.append(hilo)
            hilo.start()

    def _atender_seguro(conexion: socket.socket) -> None:
        try:
            atender(conexion, registro)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                conexion.close()

    principal = threading.Thread(target=aceptar, daemon=True)
    principal.start()
    try:
        yield registro
    finally:
        parar.set()
        escucha.close()
        principal.join(5)
        for hilo in hilos:
            hilo.join(5)


@contextlib.contextmanager
def servidor_crudo(
    respuesta: Respuesta,
    *,
    keep_alive: bool = False,
    host: str = "127.0.0.1",
    puerto: int = 0,
    tls: ssl.SSLContext | None = None,
) -> Iterator[Registro]:
    """Contesta cada petición con `respuesta`. Con `keep_alive`, atiende varias por conexión."""

    def atender(conexion: socket.socket, registro: Registro) -> None:
        if tls is not None:
            conexion = tls.wrap_socket(conexion, server_side=True)
        while True:
            peticion = _leer_peticion(conexion)
            if not peticion:
                return
            registro.peticiones.append(peticion)
            datos = respuesta(peticion) if callable(respuesta) else respuesta
            conexion.sendall(datos)
            if not keep_alive:
                with contextlib.suppress(OSError):
                    conexion.shutdown(socket.SHUT_WR)
                return

    with _escuchar(atender, host, puerto) as registro:
        yield registro


@contextlib.contextmanager
def servidor_reset(*, tras_clienthello: bool = False) -> Iterator[Registro]:
    """Corta con RST después de leer la petición o, por https, tras el inicio del ClientHello."""

    def atender(conexion: socket.socket, registro: Registro) -> None:
        if tras_clienthello:
            registro.peticiones.append(conexion.recv(5))
        else:
            registro.peticiones.append(_leer_peticion(conexion))
        _reset(conexion)

    with _escuchar(atender) as registro:
        yield registro


@contextlib.contextmanager
def servidor_goteo(inicio: bytes, *, pausa_s: float = 0.1) -> Iterator[Registro]:
    """Manda `inicio` y luego un byte cada `pausa_s` (acotado a 20 s): solo un tiempo total lo para.

    Con un `inicio` que empieza por la cabecera de un registro TLS, gotea el handshake.
    """
    parar = threading.Event()

    def atender(conexion: socket.socket, registro: Registro) -> None:
        registro.peticiones.append(conexion.recv(65536))
        conexion.sendall(inicio)
        for _ in range(int(20 / pausa_s)):
            if parar.wait(pausa_s):
                return
            conexion.sendall(b"a")

    try:
        with _escuchar(atender) as registro:
            yield registro
    finally:
        parar.set()


@contextlib.contextmanager
def servidor_clienthello() -> Iterator[Registro]:
    """Guarda el primer registro TLS (el ClientHello entero) y cierra."""

    def atender(conexion: socket.socket, registro: Registro) -> None:
        cabecera = b""
        while len(cabecera) < 5:
            trozo = conexion.recv(5 - len(cabecera))
            if not trozo:
                return
            cabecera += trozo
        largo = int.from_bytes(cabecera[3:5], "big")
        cuerpo = b""
        while len(cuerpo) < largo:
            trozo = conexion.recv(largo - len(cuerpo))
            if not trozo:
                break
            cuerpo += trozo
        registro.peticiones.append(cabecera + cuerpo)

    with _escuchar(atender) as registro:
        yield registro


@contextlib.contextmanager
def servidor_alpn(clave: str, cert: str) -> Iterator[Registro]:
    """TLS que ofrece `h2`: guarda el protocolo elegido y lo primero que manda el cliente."""
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(cert, clave)
    contexto.set_alpn_protocols(["h2", "http/1.1"])

    def atender(conexion: socket.socket, registro: Registro) -> None:
        cifrada = contexto.wrap_socket(conexion, server_side=True)
        elegido = cifrada.selected_alpn_protocol() or ""
        registro.peticiones.append(elegido.encode() + b"|" + cifrada.recv(65536))

    with _escuchar(atender) as registro:
        yield registro


def valores_grease(clienthello: bytes) -> list[int]:
    """Valores GREASE (0x0a0a, 0x1a1a... RFC 8701) entre los cifrados del ClientHello."""
    # Registro (5) + tipo y largo del handshake (4) + versión (2) + aleatorio (32).
    posicion = 5 + 4 + 2 + 32
    largo_sesion = clienthello[posicion]
    posicion += 1 + largo_sesion
    largo_cifrados = int.from_bytes(clienthello[posicion : posicion + 2], "big")
    posicion += 2
    cifrados = [
        int.from_bytes(clienthello[i : i + 2], "big")
        for i in range(posicion, posicion + largo_cifrados, 2)
    ]
    return [c for c in cifrados if c & 0x0F0F == 0x0A0A and c >> 8 == c & 0xFF]
