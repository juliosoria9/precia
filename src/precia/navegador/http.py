"""Descargador HTTP con `urllib`, ejecutado en un hilo para no bloquear el bucle de `asyncio`.

Defensas, porque todo lo que manda la web es no confiable:

- Las redirecciones no las sigue `urllib`: se siguen a mano (máximo 5) y cada destino pasa por
  `validar_url`, así una tienda no puede mandarnos a `http://169.254.169.254/`.
- Al abrir el socket se vuelve a resolver el nombre y se comprueba la IP a la que se conecta de
  verdad (contra el *DNS rebinding*: la IP validada antes podría no ser la de ahora).
- Sin proxies del entorno, sin cookies y sin compresión.
- El cuerpo se lee por trozos con un tope de bytes y de tiempo total, y solo se acepta HTML o texto.
"""

import asyncio
import codecs
import contextlib
import functools
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urljoin

from precia.navegador.tipos import ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import IP, Validador, ip_publica, validar_url

MAXIMO_BYTES = 5 * 1024 * 1024
MAXIMO_REDIRECCIONES = 5
TAMANO_TROZO = 64 * 1024
TIPOS_ADMITIDOS = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
REDIRECCIONES = frozenset({301, 302, 303, 307, 308})
# Códecs que Python acepta como «charset» pero que no son de la web (o, como UTF-7, se han usado
# para colar XSS): con ellos se decodifica como UTF-8.
_CODECS_VETADOS = frozenset({"utf-7", "unicode-escape", "raw-unicode-escape", "idna", "punycode"})
_META_CHARSET = re.compile(rb"""<meta[^>]*?charset\s*=\s*["']?\s*([a-zA-Z0-9_.:-]+)""", re.I)


class Lector(Protocol):
    def read(self, n: int, /) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RespuestaCruda:
    """Una respuesta HTTP sin interpretar. `cabeceras` con los nombres en minúsculas."""

    estado: int
    cabeceras: Mapping[str, str]
    cuerpo: Lector


Transporte = Callable[[str, Mapping[str, str], float], RespuestaCruda]
"""Hace **una** petición GET (sin seguir redirecciones). Se ejecuta en un hilo."""
CrearSocket = Callable[..., socket.socket]


class DescargadorHTTP:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 20.0,
        max_bytes: int = MAXIMO_BYTES,
        validador: Validador = validar_url,
        transporte: Transporte | None = None,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        if any(c in user_agent for c in "\r\n"):
            raise ValueError("El User-Agent no puede contener saltos de línea.")
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._validador = validador
        self._transporte = transporte or TransporteURLLib()
        self._reloj = reloj
        self._cabeceras = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
            "Accept-Encoding": "identity",
        }

    async def descargar(self, url: str) -> Pagina:
        pedida = await self._validador(url)
        actual = pedida
        for salto in range(MAXIMO_REDIRECCIONES + 1):
            resultado = await asyncio.to_thread(self._pedir, actual)
            if resultado.destino is None:
                return Pagina(
                    url=pedida,
                    url_final=actual,
                    estado=resultado.estado,
                    html=resultado.html,
                    tipo_contenido=resultado.tipo,
                    obtenida=datetime.now(UTC),
                )
            if salto == MAXIMO_REDIRECCIONES:
                break
            actual = await self._validador(urljoin(actual, resultado.destino))
        raise ErrorDescarga("La tienda encadena demasiadas redirecciones.")

    async def cerrar(self) -> None:
        """No hay conexiones persistentes que cerrar; existe para cumplir `Descargador`."""

    def _pedir(self, url: str) -> "_Resultado":
        try:
            crudo = self._transporte(url, self._cabeceras, self._timeout)
        except (OSError, http.client.HTTPException) as error:
            raise ErrorDescarga("No se pudo conectar con la tienda.") from error
        try:
            return self._procesar(crudo)
        except (OSError, http.client.HTTPException) as error:
            raise ErrorDescarga("La conexión con la tienda se cortó.") from error
        finally:
            crudo.cuerpo.close()

    def _procesar(self, crudo: RespuestaCruda) -> "_Resultado":
        cabeceras = {k.lower(): v for k, v in crudo.cabeceras.items()}
        destino = cabeceras.get("location", "").strip()
        if crudo.estado in REDIRECCIONES and destino:
            return _Resultado(crudo.estado, destino=destino)

        mime, charset = _tipo_contenido(cabeceras.get("content-type"))
        if mime not in TIPOS_ADMITIDOS:
            if 200 <= crudo.estado < 300:
                raise ErrorDescarga(f"La respuesta no es una página web (tipo «{mime}»).")
            # En un error (404, 503...) lo que importa es el estado: el cuerpo ni se lee.
            return _Resultado(crudo.estado, tipo=mime)
        if cabeceras.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
            raise ErrorDescarga("La tienda envió el contenido comprimido sin pedirlo.")
        longitud = cabeceras.get("content-length", "").strip()
        if longitud.isdigit() and int(longitud) > self._max_bytes:
            raise ErrorDescarga("La página es demasiado grande.")
        datos = self._leer(crudo.cuerpo)
        return _Resultado(crudo.estado, html=decodificar(datos, charset), tipo=mime)

    def _leer(self, cuerpo: Lector) -> bytes:
        # El timeout del socket solo limita cada lectura: una web que suelta un byte cada pocos
        # segundos lo esquivaría. Por eso también hay un límite de tiempo total.
        limite = self._reloj() + self._timeout
        trozos: list[bytes] = []
        total = 0
        while True:
            if self._reloj() > limite:
                raise ErrorDescarga("Se agotó el tiempo de descarga.")
            trozo = cuerpo.read(TAMANO_TROZO)
            if not trozo:
                return b"".join(trozos)
            total += len(trozo)
            if total > self._max_bytes:
                raise ErrorDescarga("La página es demasiado grande.")
            trozos.append(trozo)


@dataclass(frozen=True, slots=True)
class _Resultado:
    estado: int
    destino: str | None = None
    html: str = ""
    tipo: str = ""


def decodificar(datos: bytes, charset: str | None = None) -> str:
    """Texto de la respuesta: BOM, después el charset de la cabecera, el `<meta>` y UTF-8."""
    for bom, nombre in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if datos.startswith(bom):
            return datos.decode(nombre, "replace")
    for candidato in (charset, _charset_meta(datos)):
        codec = _codec(candidato)
        if codec is None:
            continue
        try:
            return datos.decode(codec, "replace")
        except (LookupError, ValueError):
            continue  # «base64», «rot13»...: códecs que no son de texto
    return datos.decode("utf-8", "replace")


def conexion_validada(
    *,
    resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
    conectar: CrearSocket = socket.create_connection,
    ip_permitida: Callable[[IP], bool] = ip_publica,
) -> CrearSocket:
    """Sustituto de `socket.create_connection` que solo conecta a IP permitidas."""

    def crear(
        direccion: tuple[str, int], timeout: object = None, origen: object = None
    ) -> socket.socket:
        host, puerto = direccion
        ips: list[IP] = []
        for info in resolver(host, puerto, type=socket.SOCK_STREAM):
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError as error:
                raise URLNoPermitida("El DNS devolvió una dirección no válida.") from error
            if not ip_permitida(ip):
                raise URLNoPermitida("El servidor resuelve a una dirección IP no pública.")
            ips.append(ip)
        if not ips:
            raise URLNoPermitida("No se puede resolver el nombre del servidor.")
        error_final: OSError | None = None
        for ip in ips:
            try:
                return conectar((str(ip), puerto), timeout, origen)
            except OSError as error:
                error_final = error
        assert error_final is not None
        raise error_final

    return crear


class _ConexionHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, *, corte: "_Corte", **opciones: Any) -> None:
        super().__init__(host, **opciones)
        self._create_connection = corte.crear_socket
        self._corte = corte
        corte.registrar(self)

    def close(self) -> None:
        # Antes de soltar el descriptor: así el corte no puede caer sobre un socket ya reutilizado.
        self._corte.terminar()
        super().close()


class _ConexionHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, *, corte: "_Corte", **opciones: Any) -> None:
        super().__init__(host, **opciones)
        self._create_connection = corte.crear_socket
        self._corte = corte
        corte.registrar(self)

    def close(self) -> None:
        self._corte.terminar()
        super().close()


class _ManejadorHTTP(urllib.request.HTTPHandler):
    def __init__(self, corte: "_Corte") -> None:
        super().__init__()
        self._conexion = functools.partial(_ConexionHTTP, corte=corte)

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._conexion, req)


class _ManejadorHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, corte: "_Corte", contexto: ssl.SSLContext) -> None:
        self._contexto = contexto
        super().__init__(context=self._contexto)
        self._conexion = functools.partial(_ConexionHTTPS, corte=corte)

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._conexion, req, context=self._contexto)


class TransporteURLLib:
    """Transporte real. Solo maneja http y https: ni redirecciones, ni proxies, ni `file:`."""

    def __init__(
        self,
        *,
        ip_permitida: Callable[[IP], bool] = ip_publica,
        resolver: Callable[..., Sequence[tuple[Any, ...]]] = socket.getaddrinfo,
        contexto_tls: ssl.SSLContext | None = None,
    ) -> None:
        """`contexto_tls` solo se cambia en los tests (para confiar en un certificado propio)."""
        self._crear = conexion_validada(resolver=resolver, ip_permitida=ip_permitida)
        self._contexto_tls = contexto_tls or ssl.create_default_context()

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        corte = _Corte(self._crear)
        # Un OpenerDirector vacío en lugar de build_opener: así no se cuelan los manejadores por
        # defecto (redirecciones automáticas, proxies del entorno, file://, ftp://, data:).
        abridor = urllib.request.OpenerDirector()
        for manejador in (
            _ManejadorHTTP(corte),
            _ManejadorHTTPS(corte, self._contexto_tls),
            urllib.request.UnknownHandler(),
        ):
            abridor.add_handler(manejador)
        peticion = urllib.request.Request(url, headers=dict(cabeceras), method="GET")
        # El timeout del socket solo limita cada lectura: un servidor que manda las cabeceras
        # byte a byte lo esquivaría y el hilo no acabaría nunca. Si al cabo de `timeout` no han
        # llegado, se corta la conexión (el cuerpo ya tiene su propio límite en `_leer`).
        temporizador = threading.Timer(timeout, corte.cortar)
        temporizador.daemon = True
        temporizador.start()
        try:
            respuesta = abridor.open(peticion, timeout=timeout)
        except urllib.error.URLError as error:
            corte.terminar()
            raise ErrorDescarga("No se pudo conectar con la tienda.") from error
        finally:
            temporizador.cancel()
        if not corte.terminar():
            respuesta.close()
            raise ErrorDescarga("Se agotó el tiempo de espera de la tienda.")
        return RespuestaCruda(
            estado=respuesta.status,
            cabeceras={k.lower(): v for k, v in respuesta.headers.items()},
            cuerpo=respuesta,
        )


class _Corte:
    """Corta desde otro hilo las conexiones de una petición que se pasa de tiempo."""

    def __init__(self, crear: CrearSocket) -> None:
        self.crear_socket = crear
        self._conexiones: list[http.client.HTTPConnection] = []
        self._candado = threading.Lock()
        self._terminado = False
        self._cortado = False

    def registrar(self, conexion: http.client.HTTPConnection) -> None:
        self._conexiones.append(conexion)

    def cortar(self) -> None:
        with self._candado:
            if self._terminado:
                return
            self._cortado = True
            for conexion in self._conexiones:
                # `conexion.sock` es el socket cifrado si es HTTPS (el que abrió `crear_socket`
                # queda separado de su descriptor al envolverlo). Se llama al `shutdown` de
                # `socket.socket` para actuar sobre el descriptor sin tocar el estado TLS, que
                # está usando el otro hilo; despierta el `recv` bloqueado en todos los sistemas.
                sock = conexion.sock
                if sock is not None:
                    with contextlib.suppress(OSError):
                        socket.socket.shutdown(sock, socket.SHUT_RDWR)

    def terminar(self) -> bool:
        """Marca la petición como respondida o cerrada. Devuelve `False` si ya se había cortado.

        Con el candado, el corte no puede llegar a medias del cuerpo: un `shutdown` entonces
        se leería como un final normal y la página quedaría truncada sin avisar.
        """
        with self._candado:
            self._terminado = True
            return not self._cortado


def _tipo_contenido(valor: str | None) -> tuple[str, str | None]:
    if not valor:
        return "application/octet-stream", None
    mime, _, parametros = valor.partition(";")
    charset = None
    for parametro in parametros.split(";"):
        clave, _, dato = parametro.partition("=")
        if clave.strip().lower() == "charset":
            charset = dato.strip().strip("\"'") or None
    mime = mime.strip().lower()
    # El tipo acaba en un mensaje de error: se recorta y se limpia lo que no sea un MIME.
    return re.sub(r"[^a-z0-9.+/-]", "", mime)[:80], charset


def _charset_meta(datos: bytes) -> str | None:
    encontrado = _META_CHARSET.search(datos, 0, 4096)
    return encontrado[1].decode("ascii") if encontrado else None


def _codec(nombre: str | None) -> str | None:
    if not nombre:
        return None
    try:
        codec = codecs.lookup(nombre).name
    except LookupError:
        return None
    if codec.replace("_", "-") in _CODECS_VETADOS:
        return None
    # Como hacen los navegadores (WHATWG Encoding): latin-1 y ASCII se leen como windows-1252.
    if codec in ("iso8859-1", "latin-1", "ascii"):
        return "cp1252"
    return codec
