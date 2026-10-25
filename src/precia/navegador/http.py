"""Descargador HTTP con `urllib`, ejecutado en un hilo para no bloquear el bucle de `asyncio`.

Defensas, porque todo lo que manda la web es no confiable:

- Las redirecciones no las sigue `urllib`: se siguen a mano (máximo 5) y cada destino pasa por
  `validar_url`, así una tienda no puede mandarnos a `http://169.254.169.254/`.
- Al abrir el socket se vuelve a resolver el nombre y se comprueba la IP a la que se conecta de
  verdad (contra el *DNS rebinding*: la IP validada antes podría no ser la de ahora).
- Sin proxies del entorno, sin cookies y sin compresión. La única excepción son los sitemaps en
  gzip (nota 16): los tipos `application/gzip` y `application/x-gzip`, y en una URL `.xml.gz`
  también `Content-Encoding: gzip` y `application/octet-stream`. Se descomprime con un tope de
  bytes **descomprimidos** (contra las bombas gzip) y por trozos con `max_length`.
- El cuerpo se lee por trozos con un tope de bytes y de tiempo total, y solo se acepta HTML o texto
  y, para las APIs públicas de plataforma y el descriptor OpenSearch (nota 15), JSON o XML, con el
  mismo límite de bytes. Esos datos llegan tal cual en `Pagina.html`: no se interpretan aquí.
- Con un `Proxy` configurado (`navegador.proxy`), toda petición, http o https, va por un túnel
  `CONNECT` al proxy. TLS se negocia dentro del túnel con la tienda y se verifica contra el nombre
  de la tienda (SNI y certificado), no contra el del proxy. Si el proxy está en la red local, el
  túnel va a la IP de la tienda ya validada, no a su nombre (contra el *DNS rebinding*, `_Salida`).
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
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

from precia.navegador.proxy import Proxy
from precia.navegador.tipos import DemasiadoGrande, ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import IP, Validador, ip_publica, validar_url

MAXIMO_BYTES = 5 * 1024 * 1024
MAX_BYTES_SITEMAP = 20 * 1024 * 1024
"""Tope de `descargar_grande` (sitemaps, nota 16): leídos y, si viene en gzip, descomprimidos."""
MAX_MIEMBROS_GZIP = 64
"""Miembros gzip concatenados en un fichero (los sitemaps reales traen uno): cada uno es un
descompresor nuevo y miles de miembros vacíos serían trabajo sin salida."""
MAX_CAPAS_GZIP = 2
"""Un `.xml.gz` servido además con `Content-Encoding: gzip` llega comprimido dos veces."""
TIMEOUT_HTTP_S = 10.0
"""Tiempo total de una página (A7): la p99 medida es 5,1 s y solo 2 de 842 peticiones pasaban de
10 s. Una tienda lenta frenaba la búsqueda entera, que espera a la última.

Es total desde que empieza la descarga, redirecciones incluidas: cada salto solo tiene lo que
queda. Con curl lo corta el transporte. Con urllib, las cabeceras las corta un temporizador y el
cuerpo se mira entre lecturas, así que la última lectura puede pasarse lo que dura una lectura
(como mucho `TIMEOUT_HTTP_S`): el margen tras cancelar (C1) lo cuenta."""
FACTOR_TIEMPO_GRANDE = 3
"""`descargar_grande` lee hasta 4 veces más bytes: tiene el triple de tiempo total."""
MAGIA_GZIP = b"\x1f\x8b"
TIPOS_GZIP = frozenset({"application/gzip", "application/x-gzip"})
CODIFICACIONES_GZIP = frozenset({"gzip", "x-gzip"})
_TIPOS_BINARIOS = frozenset({"application/octet-stream", "binary/octet-stream"})
"""Tipos genéricos con los que muchos servidores (nginx sin `gz` en `mime.types`, S3, CDN) sirven
un `.xml.gz`. Solo valen en una URL `.xml.gz` y si el cuerpo empieza por la firma de gzip."""
MAXIMO_REDIRECCIONES = 5
TAMANO_TROZO = 64 * 1024
TIPOS_ADMITIDOS = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
"""Páginas. Chromium solo acepta estas: un JSON renderizado sería el visor del navegador."""
TIPOS_DATOS = frozenset(
    {
        "application/json",
        "application/opensearchdescription+xml",
        "application/xml",
        "text/xml",
    }
)
"""Datos que solo descarga `DescargadorHTTP`: APIs públicas (JSON) y descriptor OpenSearch (XML)."""
_TIPOS_DESCARGABLES = TIPOS_ADMITIDOS | TIPOS_DATOS
# Lo que se acepta, y nada más (sin `*/*`): el HTML primero y los datos con menos peso.
ACCEPT = (
    "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,"
    "application/json;q=0.5,application/opensearchdescription+xml;q=0.5,"
    "application/xml;q=0.4,text/xml;q=0.4"
)
REDIRECCIONES = frozenset({301, 302, 303, 307, 308})
# Códecs que Python acepta como «charset» pero que no son de la web (o, como UTF-7, se han usado
# para colar XSS): con ellos se decodifica como UTF-8.
_CODECS_VETADOS = frozenset({"utf-7", "unicode-escape", "raw-unicode-escape", "idna", "punycode"})
_META_CHARSET = re.compile(rb"""<meta[^>]*?charset\s*=\s*["']?\s*([a-zA-Z0-9_.:-]+)""", re.I)
# Lo que lanza `http.client` cuando el proxy no contesta 200 al `CONNECT`.
_TUNEL_FALLIDO = re.compile(r"Tunnel connection failed: (\d{3})")
# Declaración XML (`<?xml version="1.0" encoding="..."?>`): solo vale al principio del documento,
# así que se busca con `match` y acotada; los cuantificadores son acotados o posesivos.
_DECLARACION_XML = re.compile(
    rb"""<\?xml\s[^>?]{0,200}?encoding\s*+=\s*+["']([a-zA-Z0-9_.:-]{1,40})"""
)


class Lector(Protocol):
    def read(self, n: int, /) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RespuestaCruda:
    """Una respuesta HTTP sin interpretar. `cabeceras` con los nombres en minúsculas."""

    estado: int
    cabeceras: Mapping[str, str]
    cuerpo: Lector
    descomprimida: bool = False
    """El transporte ya deshizo el `Content-Encoding` y quitó la cabecera (curl, ADR-022)."""


Transporte = Callable[[str, Mapping[str, str], float], RespuestaCruda]
"""Hace **una** petición GET (sin seguir redirecciones). Se ejecuta en un hilo."""
CrearSocket = Callable[..., socket.socket]


@runtime_checkable
class TransporteNavegador(Protocol):
    """Transporte con huella de navegador (`TransporteCurl`, ADR-022).

    Recibe el tiempo **total** de la descarga y su tope exacto de bytes (para no bajar 20 MiB de
    una página que solo admite 5: con el proxy se pagan) y guarda conexiones y cookies, así que
    hay que cerrarlo. Recibe las cabeceras de `DescargadorHTTP` y elige cuáles manda.
    """

    def __call__(
        self, url: str, cabeceras: Mapping[str, str], timeout: float
    ) -> RespuestaCruda: ...

    def pedir(
        self, url: str, cabeceras: Mapping[str, str], timeout: float, max_bytes: int
    ) -> RespuestaCruda: ...

    def cerrar(self) -> None: ...


class DescargadorHTTP:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = TIMEOUT_HTTP_S,
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
            "Accept": ACCEPT,
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
            "Accept-Encoding": "identity",
        }

    @property
    def transporte(self) -> Transporte:
        return self._transporte

    async def descargar(self, url: str) -> Pagina:
        return await self._descargar(url, self._max_bytes, self._timeout)

    async def descargar_grande(self, url: str, *, max_bytes: int = MAX_BYTES_SITEMAP) -> Pagina:
        """Como `descargar`, con otro tope de bytes (leídos y descomprimidos) y más tiempo total.

        Para los sitemaps (nota 16): mismas validaciones en cada redirección y mismos tipos.
        """
        return await self._descargar(url, max_bytes, self._timeout * FACTOR_TIEMPO_GRANDE)

    async def descargar_con_tiempo(self, url: str, tiempo_s: float) -> Pagina:
        """Como `descargar`, con otro tiempo total (el robots.txt, A7). Se corta en su hilo (ver
        `TIMEOUT_HTTP_S`): tras el corte no queda ninguna petición en vuelo a la tienda
        (ADR-017)."""
        return await self._descargar(url, self._max_bytes, tiempo_s)

    async def _descargar(self, url: str, max_bytes: int, tiempo: float) -> Pagina:
        pedida = await self._validador(url)
        actual = pedida
        limite: float | None = None
        for salto in range(MAXIMO_REDIRECCIONES + 1):
            if limite is None:
                restante, limite = tiempo, self._reloj() + tiempo
            else:
                restante = limite - self._reloj()
                if restante <= 0:
                    raise ErrorDescarga("Se agotó el tiempo de descarga.")
            resultado = await asyncio.to_thread(self._pedir, actual, max_bytes, restante, limite)
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
        """Cierra las conexiones y cookies del transporte con huella de navegador, si lo es.

        Con urllib no hay nada que cerrar: cada petición abre y cierra su conexión.
        """
        if isinstance(self._transporte, TransporteNavegador):
            await asyncio.to_thread(self._transporte.cerrar)

    def _pedir(self, url: str, max_bytes: int, tiempo: float, limite: float) -> "_Resultado":
        """Un salto con `tiempo` segundos, lo que queda hasta `limite` (en el reloj `_reloj`)."""
        try:
            if isinstance(self._transporte, TransporteNavegador):
                crudo = self._transporte.pedir(url, self._cabeceras, tiempo, max_bytes)
            else:
                # urllib: el tiempo de cada lectura de siempre, o lo que queda si es menor; el
                # total del cuerpo lo vigila `_leer`.
                crudo = self._transporte(url, self._cabeceras, min(self._timeout, tiempo))
        except (OSError, http.client.HTTPException) as error:
            raise ErrorDescarga("No se pudo conectar con la tienda.") from error
        try:
            return self._procesar(crudo, url, max_bytes, limite)
        except (OSError, http.client.HTTPException) as error:
            raise ErrorDescarga("La conexión con la tienda se cortó.") from error
        finally:
            crudo.cuerpo.close()

    def _procesar(
        self, crudo: RespuestaCruda, url: str, max_bytes: int, limite: float
    ) -> "_Resultado":
        cabeceras = {k.lower(): v for k, v in crudo.cabeceras.items()}
        destino = cabeceras.get("location", "").strip()
        if crudo.estado in REDIRECCIONES and destino:
            return _Resultado(crudo.estado, destino=destino)

        mime, charset = _tipo_contenido(cabeceras.get("content-type"))
        xml_gz = urlsplit(url).path.lower().endswith(".xml.gz")
        # El tipo genérico solo vale en un 2xx: en un error no hay sitemap que leer.
        binario = xml_gz and mime in _TIPOS_BINARIOS and 200 <= crudo.estado < 300
        if mime not in _TIPOS_DESCARGABLES and mime not in TIPOS_GZIP and not binario:
            if 200 <= crudo.estado < 300:
                raise ErrorDescarga(f"La respuesta no es una página web (tipo «{mime}»).")
            # En un error (404, 503...) lo que importa es el estado: el cuerpo ni se lee.
            return _Resultado(crudo.estado, tipo=mime)
        codificacion = cabeceras.get("content-encoding", "identity").strip().lower()
        if crudo.descomprimida:
            codificacion = "identity"
        elif codificacion not in ("", "identity") and not (
            xml_gz and codificacion in CODIFICACIONES_GZIP
        ):
            # Pedimos `identity`: comprimir sin pedirlo solo se tolera en el fichero `.xml.gz`.
            raise ErrorDescarga("La tienda envió el contenido comprimido sin pedirlo.")
        longitud = cabeceras.get("content-length", "").strip()
        if longitud.isdigit() and int(longitud) > max_bytes:
            raise DemasiadoGrande("La página es demasiado grande.")
        datos = self._leer(crudo.cuerpo, max_bytes, limite)
        con_firma = datos.startswith(MAGIA_GZIP)
        if crudo.descomprimida:
            # Sin `Content-Encoding` ya: solo queda gzip si el fichero mismo lo es (un `.xml.gz`
            # servido además con `Content-Encoding: gzip` llega con una sola capa, o con ninguna).
            if con_firma and (xml_gz or mime in TIPOS_GZIP):
                datos = descomprimir_gzip(datos, max_bytes)
            if mime not in _TIPOS_DESCARGABLES:
                mime = "application/xml"  # lo de dentro de un sitemap `.xml.gz`
            return _Resultado(crudo.estado, html=decodificar(datos, charset, mime=mime), tipo=mime)
        if binario and not con_firma:
            raise ErrorDescarga(f"La respuesta no es una página web (tipo «{mime}»).")
        if mime in TIPOS_GZIP or codificacion in CODIFICACIONES_GZIP or (xml_gz and con_firma):
            # Un XML nunca empieza por la firma de gzip: en un `.xml.gz` con el tipo del `.xml` y
            # sin `Content-Encoding` (servidor mal configurado) no hay ambigüedad.
            datos = descomprimir_gzip(datos, max_bytes)
            if mime not in _TIPOS_DESCARGABLES:
                mime = "application/xml"  # lo de dentro de un sitemap `.xml.gz`
        return _Resultado(crudo.estado, html=decodificar(datos, charset, mime=mime), tipo=mime)

    def _leer(self, cuerpo: Lector, max_bytes: int, limite: float) -> bytes:
        # El timeout del socket solo limita cada lectura: una web que suelta un byte cada pocos
        # segundos lo esquivaría. Por eso también hay un límite de tiempo total, el de toda la
        # descarga (cuenta lo que tardaron las cabeceras y las redirecciones).
        trozos: list[bytes] = []
        total = 0
        while True:
            if self._reloj() > limite:
                raise ErrorDescarga("Se agotó el tiempo de descarga.")
            trozo = cuerpo.read(TAMANO_TROZO)
            if not trozo:
                return b"".join(trozos)
            total += len(trozo)
            if total > max_bytes:
                raise DemasiadoGrande("La página es demasiado grande.")
            trozos.append(trozo)


@dataclass(frozen=True, slots=True)
class _Resultado:
    estado: int
    destino: str | None = None
    html: str = ""
    tipo: str = ""


def decodificar(datos: bytes, charset: str | None = None, *, mime: str = "text/html") -> str:
    """Texto de la respuesta: BOM, charset de la cabecera, `<meta>`, declaración XML y UTF-8.

    El `<meta charset>` solo vale en HTML y la declaración `<?xml?>` solo en HTML y XML. Un JSON
    (UTF-8 por RFC 8259) o un XML pueden llevar dentro HTML de una descripción con su propio
    `<meta charset=windows-1252>`: hacerle caso destrozaría los acentos y el «€» de todo el texto.
    """
    for bom, nombre in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if datos.startswith(bom):
            return datos.decode(nombre, "replace")
    es_json = mime == "application/json"
    es_xml = mime in TIPOS_DATOS and not es_json
    meta = None if es_json or es_xml else _charset_meta(datos)
    xml = None if es_json else _charset_xml(datos)
    for candidato, de_dentro in ((charset, False), (meta, True), (xml, True)):
        codec = _codec(candidato)
        if codec is None:
            continue
        if de_dentro and codec.startswith(("utf-16", "utf-32")):
            # Si se ha leído como ASCII, el documento no está en UTF-16/32 (WHATWG: es UTF-8).
            continue
        try:
            return datos.decode(codec, "replace")
        except (LookupError, ValueError):
            continue  # «base64», «rot13»...: códecs que no son de texto
    return datos.decode("utf-8", "replace")


def descomprimir_gzip(datos: bytes, max_bytes: int) -> bytes:
    """Descomprime gzip (hasta `MAX_CAPAS_GZIP` capas) sin pasar de `max_bytes` descomprimidos.

    Lanza `ErrorDescarga` si el fichero está dañado, cortado, con el CRC o el tamaño mal, o si lo
    descomprimido pasa del tope: una bomba gzip se para ahí sin agotar la memoria.
    """
    for _ in range(MAX_CAPAS_GZIP):
        datos = _descomprimir_capa(datos, max_bytes)
        if not datos.startswith(MAGIA_GZIP):
            return datos
    raise ErrorDescarga("El fichero comprimido tiene demasiadas capas.")


def _descomprimir_capa(datos: bytes, max_bytes: int) -> bytes:
    """Una capa: uno o varios miembros gzip seguidos y, como mucho, relleno de ceros al final.

    Se alimenta el descompresor por trozos con `max_length` = lo que queda de tope + 1: nunca
    produce más de eso de una vez, y si lo produce es que se ha pasado. Cada vuelta consume entrada
    o produce salida, así que el bucle está acotado por los bytes de entrada.
    """
    vista = memoryview(datos)
    salida: list[bytes] = []
    total = posicion = miembros = 0
    while True:
        if vista[posicion : posicion + 2] != MAGIA_GZIP:
            # Tras el último miembro solo puede quedar relleno de ceros (se mira una sola vez).
            if miembros and not bytes(vista[posicion:]).strip(b"\x00"):
                return b"".join(salida)
            raise ErrorDescarga("El fichero comprimido está dañado.")
        miembros += 1
        if miembros > MAX_MIEMBROS_GZIP:
            raise ErrorDescarga("El fichero comprimido tiene demasiados trozos.")
        descompresor = zlib.decompressobj(16 + zlib.MAX_WBITS)  # solo gzip, con CRC y tamaño
        while not descompresor.eof:
            entrada = vista[posicion : posicion + TAMANO_TROZO]
            if not entrada:
                raise ErrorDescarga("El fichero comprimido está cortado.")
            try:
                trozo = descompresor.decompress(entrada, max_bytes - total + 1)
            except zlib.error as error:
                raise ErrorDescarga("El fichero comprimido está dañado.") from error
            total += len(trozo)
            if total > max_bytes:
                raise DemasiadoGrande("La página es demasiado grande.")
            salida.append(trozo)
            posicion += len(entrada) - len(descompresor.unconsumed_tail)
        # Lo que sobró del último trozo es lo que va detrás de este miembro.
        posicion -= len(descompresor.unused_data)


Resolver = Callable[..., Sequence[tuple[Any, ...]]]
"""`socket.getaddrinfo` (o un doble en los tests)."""


def ips_permitidas(
    host: str, puerto: int, *, resolver: Resolver, ip_permitida: Callable[[IP], bool]
) -> list[IP]:
    """Las IP de `host`, si **todas** están permitidas. Si no, `URLNoPermitida`."""
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
    return ips


def conexion_validada(
    *,
    resolver: Resolver = socket.getaddrinfo,
    conectar: CrearSocket = socket.create_connection,
    ip_permitida: Callable[[IP], bool] = ip_publica,
) -> CrearSocket:
    """Sustituto de `socket.create_connection` que solo conecta a IP permitidas."""

    def crear(
        direccion: tuple[str, int], timeout: object = None, origen: object = None
    ) -> socket.socket:
        host, puerto = direccion
        ips = ips_permitidas(host, puerto, resolver=resolver, ip_permitida=ip_permitida)
        error_final: OSError | None = None
        for ip in ips:
            try:
                return conectar((str(ip), puerto), timeout, origen)
            except OSError as error:
                error_final = error
        assert error_final is not None
        raise error_final

    return crear


class _FalloProxy(OSError):
    """No se pudo abrir la conexión TCP con el proxy (para dar el mensaje correcto)."""


class _ProxyNoHTTP(OSError):
    """El proxy contestó al `CONNECT` algo que no es HTTP (otro servicio, el puerto SOCKS5...).

    Es un `OSError` para que `urllib` lo envuelva en `URLError` como al resto de fallos del túnel.
    """


def _conectar_al_proxy(
    direccion: tuple[str, int], timeout: object = None, origen: object = None
) -> socket.socket:
    """Conexión al proxy, sin la comprobación de IP pública que se hace con las tiendas.

    El proxy lo configura el usuario en `.env` (no viene de la web), así que puede estar en la
    red local o en este equipo. Lo que se protege es el **destino** del túnel (`_Salida`).
    """
    try:
        return socket.create_connection(direccion, timeout, origen)  # type: ignore[arg-type]
    except OSError as error:
        raise _FalloProxy("No se pudo conectar con el proxy.") from error


def _cabeceras_tunel(proxy: Proxy) -> dict[str, str]:
    # Solo van en el CONNECT, que lee el proxy: la tienda nunca ve la autorización.
    autorizacion = proxy.cabecera_autorizacion
    return {"Proxy-Authorization": autorizacion} if autorizacion else {}


@dataclass(frozen=True, slots=True)
class _Salida:
    """Cómo se pide el túnel por el proxy, contra el SSRF por *DNS rebinding*.

    La URL de la tienda ya pasó `validar_url`, que resolvió el nombre **antes** de la petición.
    Si el `CONNECT` lleva el nombre, el proxy lo vuelve a resolver y esa IP no la comprueba nadie:

    - con un proxy remoto (IP pública, como Evomi), lo resuelve un equipo que no ve la red local
      de este; el `CONNECT` lleva el nombre, como espera el proveedor;
    - con un proxy de la red local o de este equipo (un Squid propio), una tienda con *rebinding*
      (TTL 0: primero una IP pública y luego 192.168.1.1) llegaría a la red interna a través de
      él. Por eso el nombre se resuelve aquí, se valida como en la conexión directa y el
      `CONNECT` va a esa IP. `Host`, SNI y certificado siguen siendo los del nombre.
    """

    proxy: Proxy
    resolver: Resolver
    ip_permitida: Callable[[IP], bool]
    proxy_remoto: Callable[[IP], bool]

    def preparar(self, conexion: http.client.HTTPConnection) -> None:
        """Justo antes del `CONNECT`, con la conexión al proxy ya abierta."""
        sock = conexion.sock
        assert sock is not None
        ip_proxy = ipaddress.ip_address(sock.getpeername()[0])
        if self.proxy_remoto(ip_proxy):
            return
        host: str = conexion._tunnel_host  # type: ignore[attr-defined]
        puerto: int = conexion._tunnel_port  # type: ignore[attr-defined]
        ip = ips_permitidas(host, puerto, resolver=self.resolver, ip_permitida=self.ip_permitida)[0]
        direccion = f"[{ip}]" if ip.version == 6 else str(ip)
        conexion._tunnel_host = str(ip)  # type: ignore[attr-defined]
        conexion._tunnel_headers["Host"] = f"{direccion}:{puerto}"  # type: ignore[attr-defined]


def _abrir_tunel(
    conexion: http.client.HTTPConnection, salida: _Salida | None, abrir: Callable[[], None]
) -> None:
    if salida is not None:
        salida.preparar(conexion)
    try:
        abrir()
    except http.client.HTTPException as error:
        # `BadStatusLine`, demasiadas cabeceras...: lo que contestó no es un proxy HTTP.
        raise _ProxyNoHTTP("El proxy no respondió como un proxy HTTP.") from error


class _ConexionHTTP(http.client.HTTPConnection):
    def __init__(
        self, host: str, *, corte: "_Corte", salida: _Salida | None = None, **opciones: Any
    ) -> None:
        if salida is None:
            super().__init__(host, **opciones)
        else:
            # Se conecta al proxy y se le pide un túnel hasta `host` (con su puerto, si lo lleva).
            super().__init__(salida.proxy.host, salida.proxy.puerto, **opciones)
            self.set_tunnel(host, headers=_cabeceras_tunel(salida.proxy))
        self._salida = salida
        self._create_connection = corte.crear_socket
        self._corte = corte
        corte.registrar(self)

    def _tunnel(self) -> None:
        _abrir_tunel(self, self._salida, super()._tunnel)  # type: ignore[misc]

    def close(self) -> None:
        # Antes de soltar el descriptor: así el corte no puede caer sobre un socket ya reutilizado.
        self._corte.terminar()
        super().close()


class _ConexionHTTPS(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        *,
        corte: "_Corte",
        salida: _Salida | None = None,
        context: ssl.SSLContext | None = None,
        **opciones: Any,
    ) -> None:
        self._contexto = context or ssl.create_default_context()
        if salida is None:
            super().__init__(host, context=self._contexto, **opciones)
            self._nombre_tls = self.host
        else:
            super().__init__(
                salida.proxy.host, salida.proxy.puerto, context=self._contexto, **opciones
            )
            self.set_tunnel(host, headers=_cabeceras_tunel(salida.proxy))
            # Antes de que `_Salida` cambie el destino del túnel por la IP: TLS va con el nombre.
            self._nombre_tls = self._tunnel_host  # type: ignore[attr-defined]
        self._salida = salida
        self._create_connection = corte.crear_socket
        self._corte = corte
        corte.registrar(self)

    def connect(self) -> None:
        # Como `HTTPSConnection.connect`, pero el certificado y el SNI son siempre los del nombre
        # de la tienda, aunque el túnel vaya a su IP (y nunca los del proxy).
        http.client.HTTPConnection.connect(self)
        assert self.sock is not None
        self.sock = self._contexto.wrap_socket(self.sock, server_hostname=self._nombre_tls)

    def _tunnel(self) -> None:
        _abrir_tunel(self, self._salida, super()._tunnel)  # type: ignore[misc]

    def close(self) -> None:
        self._corte.terminar()
        super().close()


class _ManejadorHTTP(urllib.request.HTTPHandler):
    def __init__(self, corte: "_Corte", salida: _Salida | None = None) -> None:
        super().__init__()
        self._conexion = functools.partial(_ConexionHTTP, corte=corte, salida=salida)

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._conexion, req)


class _ManejadorHTTPS(urllib.request.HTTPSHandler):
    def __init__(
        self, corte: "_Corte", contexto: ssl.SSLContext, salida: _Salida | None = None
    ) -> None:
        self._contexto = contexto
        super().__init__(context=self._contexto)
        self._conexion = functools.partial(_ConexionHTTPS, corte=corte, salida=salida)

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._conexion, req, context=self._contexto)


class TransporteURLLib:
    """Transporte real. Solo maneja http y https: ni redirecciones, ni proxies del entorno, ni
    `file:`. El único proxy es el que se le pasa (`proxy`), y entonces todo va por él."""

    def __init__(
        self,
        *,
        ip_permitida: Callable[[IP], bool] = ip_publica,
        resolver: Resolver = socket.getaddrinfo,
        contexto_tls: ssl.SSLContext | None = None,
        proxy: Proxy | None = None,
        proxy_remoto: Callable[[IP], bool] = ip_publica,
    ) -> None:
        """`contexto_tls` solo se cambia en los tests (para confiar en un certificado propio).

        Con `proxy`, la conexión es con el proxy. Si su IP es pública (`proxy_remoto`), el nombre
        de la tienda lo resuelve él; si no, se resuelve aquí con `resolver` y se valida con
        `ip_permitida`, como sin proxy (ver `_Salida`).
        """
        self._crear = conexion_validada(resolver=resolver, ip_permitida=ip_permitida)
        self._contexto_tls = contexto_tls or ssl.create_default_context()
        self._proxy = proxy
        self._salida = (
            None
            if proxy is None
            else _Salida(
                proxy, resolver=resolver, ip_permitida=ip_permitida, proxy_remoto=proxy_remoto
            )
        )

    @property
    def proxy(self) -> Proxy | None:
        return self._proxy

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        corte = _Corte(self._crear if self._salida is None else _conectar_al_proxy)
        # Un OpenerDirector vacío en lugar de build_opener: así no se cuelan los manejadores por
        # defecto (redirecciones automáticas, proxies del entorno, file://, ftp://, data:).
        abridor = urllib.request.OpenerDirector()
        for manejador in (
            _ManejadorHTTP(corte, self._salida),
            _ManejadorHTTPS(corte, self._contexto_tls, self._salida),
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
        fallo: OSError | http.client.HTTPException | None = None
        try:
            respuesta = abridor.open(peticion, timeout=timeout)
        except (OSError, http.client.HTTPException) as error:
            # `urllib` solo envuelve en `URLError` lo que falla al enviar; lo que falla al leer
            # la respuesta (`getresponse`) llega tal cual: `OSError` o `HTTPException`.
            fallo = error
        finally:
            temporizador.cancel()
        a_tiempo = corte.terminar()
        if fallo is not None:
            if not a_tiempo:
                # El fallo lo ha provocado nuestro propio corte (`shutdown` del socket): se lanza
                # fuera del `except` y sin encadenar, para que nadie lo lea como un reset de la
                # tienda (`con_proxy` lo tomaría por un bloqueo y la pasaría al proxy de pago).
                raise ErrorDescarga("Se agotó el tiempo de espera de la tienda.")
            motivo = fallo.reason if isinstance(fallo, urllib.error.URLError) else fallo
            if self._proxy is not None:
                raise ErrorDescarga(_mensaje_proxy(motivo)) from fallo
            raise ErrorDescarga("No se pudo conectar con la tienda.") from fallo
        if not a_tiempo:
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


MENSAJE_PROXY_INALCANZABLE = "No se pudo conectar con el proxy."
MENSAJE_PROXY_NO_HTTP = (
    "El proxy no respondió como un proxy HTTP: revisa el servidor y el puerto (PRECIA_PROXY)."
)
MENSAJE_PROXY_OTRO = "No se pudo conectar con la tienda a través del proxy."


def mensaje_tunel_rechazado(estado: int) -> str:
    """El proxy contestó al `CONNECT` con `estado` (no 2xx). Compartido con `TransporteCurl`."""
    if estado == 407:
        return (
            "El proxy rechazó la conexión (407): revisa sus credenciales "
            "(PRECIA_PROXY o EVOMI_API_KEY)."
        )
    return f"El proxy no pudo abrir la conexión con la tienda (HTTP {estado})."


def _mensaje_proxy(motivo: object) -> str:
    """Por qué falló una petición por el proxy. Nunca cita las credenciales."""
    if isinstance(motivo, _FalloProxy):
        return MENSAJE_PROXY_INALCANZABLE
    if isinstance(motivo, _ProxyNoHTTP):
        return MENSAJE_PROXY_NO_HTTP
    encontrado = _TUNEL_FALLIDO.match(str(motivo)) if isinstance(motivo, OSError) else None
    if encontrado is None:
        return MENSAJE_PROXY_OTRO
    return mensaje_tunel_rechazado(int(encontrado[1]))


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


def _charset_xml(datos: bytes) -> str | None:
    encontrado = _DECLARACION_XML.match(datos, 0, 1024)
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
