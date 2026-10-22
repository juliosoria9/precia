"""Tienda y proxy `CONNECT` falsos en 127.0.0.1, para los tests del proxy (HTTP y Chromium).

El proxy falso hace lo que haría Evomi: lee el `CONNECT`, comprueba `Proxy-Authorization` y abre
el túnel. El destino lo «resuelve» él: pidan el nombre o la IP que pidan, el túnel siempre llega a
la tienda local. Así se ve qué destino pide el cliente sin preguntar a ningún DNS.
"""

import contextlib
import http.server
import select
import socket
import socketserver
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

CUERPO_TIENDA = "<p>Ficha servida por el túnel</p>"


class _Tienda(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        servidor: ServidorTienda = self.server  # type: ignore[assignment]
        cabeceras = {k.lower(): v for k, v in self.headers.items()}
        servidor.peticiones.append((self.path, cabeceras))
        if servidor.manejar is not None and servidor.manejar(self):
            return
        cuerpo = CUERPO_TIENDA.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_: object) -> None:
        pass


class ServidorTienda(http.server.ThreadingHTTPServer):
    daemon_threads = True
    peticiones: list[tuple[str, dict[str, str]]]
    manejar: Callable[[http.server.BaseHTTPRequestHandler], bool] | None
    """Respuesta propia para una ruta: devuelve `True` si ya ha respondido."""


def arrancar(servidor: socketserver.BaseServer) -> threading.Thread:
    hilo = threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    hilo.start()
    return hilo


@contextlib.contextmanager
def tienda(
    contexto: ssl.SSLContext | None = None,
    manejar: Callable[[http.server.BaseHTTPRequestHandler], bool] | None = None,
) -> Iterator[ServidorTienda]:
    servidor = ServidorTienda(("127.0.0.1", 0), _Tienda)
    servidor.peticiones = []
    servidor.manejar = manejar
    if contexto is not None:
        servidor.socket = contexto.wrap_socket(servidor.socket, server_side=True)
    arrancar(servidor)
    try:
        yield servidor
    finally:
        servidor.shutdown()
        servidor.server_close()


@dataclass
class EstadoProxy:
    puerto_tienda: int
    esperada: str | None
    respuesta_fallo: bytes = b""
    """Lo que contesta al `CONNECT` en lugar de abrir el túnel (un error o algo que no es HTTP)."""
    goteo_s: float = 0.0
    """Si no es cero, el «200» del `CONNECT` sale byte a byte con esta pausa (un proxy colgado)."""
    connects: list[str] = field(default_factory=list)
    autorizaciones: list[str | None] = field(default_factory=list)


class _Proxy(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        estado: EstadoProxy = self.server.estado  # type: ignore[attr-defined]
        datos = b""
        while b"\r\n\r\n" not in datos:
            trozo = self.request.recv(4096)
            if not trozo:
                return
            datos += trozo
        cabecera, _, resto = datos.partition(b"\r\n\r\n")
        lineas = cabecera.decode("latin-1").split("\r\n")
        estado.connects.append(lineas[0])
        cabeceras = {}
        for linea in lineas[1:]:
            nombre, _, valor = linea.partition(":")
            cabeceras[nombre.strip().lower()] = valor.strip()
        autorizacion = cabeceras.get("proxy-authorization")
        estado.autorizaciones.append(autorizacion)
        if estado.respuesta_fallo:
            self.request.sendall(estado.respuesta_fallo)
            return
        if autorizacion != estado.esperada:
            self.request.sendall(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b'Proxy-Authenticate: Basic realm="prueba"\r\nContent-Length: 0\r\n\r\n'
            )
            return
        if estado.goteo_s:
            with contextlib.suppress(OSError):
                for byte in b"HTTP/1.1 200 Connection established\r\n\r\n":
                    self.request.sendall(bytes([byte]))
                    time.sleep(estado.goteo_s)
            return
        with socket.create_connection(("127.0.0.1", estado.puerto_tienda), timeout=5) as destino:
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if resto:
                destino.sendall(resto)
            tubo(self.request, destino)


def tubo(a: socket.socket, b: socket.socket) -> None:
    while True:
        listos, _, _ = select.select([a, b], [], [], 5)
        if not listos:
            return
        for origen in listos:
            try:
                datos = origen.recv(65536)
            except OSError:
                return
            if not datos:
                return
            (b if origen is a else a).sendall(datos)


@contextlib.contextmanager
def proxy_falso(
    puerto_tienda: int,
    *,
    esperada: str | None,
    respuesta_fallo: bytes = b"",
    goteo_s: float = 0.0,
) -> Iterator[tuple[int, EstadoProxy]]:
    servidor = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Proxy)
    servidor.daemon_threads = True
    estado = EstadoProxy(puerto_tienda, esperada, respuesta_fallo, goteo_s)
    servidor.estado = estado  # type: ignore[attr-defined]
    arrancar(servidor)
    try:
        yield servidor.server_address[1], estado
    finally:
        servidor.shutdown()
        servidor.server_close()
