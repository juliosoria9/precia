"""Transporte con la huella TLS y HTTP/2 de Chrome, sobre `curl_cffi` (ADR-022).

`urllib` + `ssl` no pueden imitar el ClientHello de Chrome (JA3/JA4, GREASE, ALPN `h2`) ni sus
ajustes de HTTP/2, y los WAF deciden por la coherencia entre TLS, HTTP/2 y cabeceras. Este
transporte usa el perfil `chrome` de `curl_cffi` con **sus** cabeceras (User-Agent incluido); de
las de `DescargadorHTTP` no manda ninguna. Es el transporte por defecto (decisión de Julio).

Las defensas de `TransporteURLLib` siguen, adaptadas a curl:

- Una sola petición: sin redirecciones (las sigue y valida `DescargadorHTTP`) ni reintentos, y
  solo `http` y `https`.
- Contra el *DNS rebinding*, el nombre se resuelve aquí, se validan **todas** sus IP y se fijan
  con `CURLOPT_RESOLVE`: curl no pregunta al DNS. Tras la respuesta se comprueba también la IP a
  la que se conectó de verdad (`primary_ip`). Nunca se usan los proxies del entorno.
- El tope de bytes cuenta lo **descomprimido** (contra las bombas gzip) y el tiempo es total:
  DNS, espera de la sesión, conexión, TLS, cabeceras y cuerpo.
- Cookies solo en memoria y aisladas por tienda (una sesión por dominio, con tope; S02), que se
  pierden al cerrar. Nunca hay logins.
- Con un proxy remoto (Evomi), toda petición va por un túnel `CONNECT` con el nombre de la
  tienda, como con urllib; las credenciales solo viajan en el `CONNECT` y ningún error las cita.
  Con un proxy de la red local o de este equipo se usa `TransporteURLLib` (decisión de Julio,
  S03): su `CONNECT` a la IP validada no necesita la API privada de `curl_cffi`.

Un corte de la tienda (RST, también tras el ClientHello, que es como corta un WAF por la huella
TLS) se lanza con un `ConnectionResetError` en la causa para que `con_proxy` lo reconozca. El
tiempo agotado no lleva causa. Los errores de HTTP/2 (`RST_STREAM`, códigos 92 y 16) no cuentan
como corte mientras Julio no decida otra cosa.
"""

import errno
import io
import ipaddress
import socket
import ssl
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from curl_cffi import CurlError, CurlHttpVersion, CurlInfo, CurlOpt
from curl_cffi.requests import Response, Session

from precia.navegador.con_proxy import dominio_de
from precia.navegador.http import (
    MAX_BYTES_SITEMAP,
    MENSAJE_PROXY_INALCANZABLE,
    MENSAJE_PROXY_NO_HTTP,
    MENSAJE_PROXY_OTRO,
    Resolver,
    RespuestaCruda,
    TransporteURLLib,
    ips_permitidas,
    mensaje_tunel_rechazado,
)
from precia.navegador.proxy import Proxy
from precia.navegador.tipos import DemasiadoGrande, ErrorDescarga, URLNoPermitida
from precia.navegador.url import IP, ip_publica

PERFIL_CHROME = "chrome"
"""Alias de `curl_cffi` para el Chrome más reciente que trae (hoy `chrome150`)."""
ACCEPT_LANGUAGE_NAVEGADOR = "es-ES,es;q=0.9"
"""El de un Chrome en español: la única cabecera que se añade a las del perfil."""
MAX_SESIONES = 32
"""Sesiones (una por tienda) abiertas a la vez; la menos usada se cierra al pasar de aquí."""
MAX_COOKIES = 200
"""Cookies por tienda: si una tienda pasa de aquí, se vacían (solo están en memoria)."""
MIN_PLAZO_S = 0.001
"""Lo que queda de plazo por debajo de esto cuenta como agotado (ver `_restante`)."""
_CODIFICACIONES_CURL = frozenset({"identity", "gzip", "x-gzip", "deflate", "br", "zstd"})
_ERRNO_RESET = frozenset({errno.ECONNRESET, 104, 10054})
"""`ECONNRESET` en este sistema, en Linux (la CI) y en Windows (`WSAECONNRESET`)."""
_ERRNO_ABORTADA = frozenset({errno.ECONNABORTED, 103, 10053})
_CODIGO_RESOLVER_PROXY = 5
_CODIGO_CONECTAR = 7
_CODIGO_RESPUESTA_VACIA = 52
_CODIGO_RECIBIR = 56
_CODIGO_TLS = 35
_CODIGO_TIEMPO = 28
_CODIGO_COMPRESION = 61
_CODIGO_DEMASIADO_GRANDE = 63

MENSAJE_GRANDE = "La página es demasiado grande."
MENSAJE_TIEMPO = "Se agotó el tiempo de espera de la tienda."
MENSAJE_CORTE = "La conexión con la tienda se cortó."
MENSAJE_NO_CONECTA = "No se pudo conectar con la tienda."
MENSAJE_COMPRESION = "La tienda envió el contenido con una compresión desconocida."
MENSAJE_NO_VALIDA = "La tienda envió una respuesta HTTP no válida."


class _Tope(Exception):
    """El cuerpo descomprimido pasó del tope: se lanza desde el `content_callback`."""


@dataclass(eq=False)
class _EntradaSesion:
    """La sesión de una tienda. Su candado la reserva para una petición cada vez."""

    sesion: Session[Response]
    candado: threading.Lock = field(default_factory=threading.Lock)
    retirada: bool = False
    """Ya no está en el LRU (desalojada o el transporte se cerró): se cierra al soltarla."""
    cerrada: bool = False


class TransporteCurl:
    """Implementa `TransporteNavegador` con `curl_cffi` (una `Session` síncrona por tienda).

    Las peticiones van dentro del `asyncio.to_thread` de `DescargadorHTTP`. Varias tiendas a la
    vez van en paralelo; dos peticiones a la misma tienda esperan su turno (ya lo hacen por la
    cortesía de `LimitadorDominio`).
    """

    def __init__(
        self,
        *,
        ip_permitida: Callable[[IP], bool] = ip_publica,
        resolver: Resolver = socket.getaddrinfo,
        proxy: Proxy | None = None,
        proxy_remoto: Callable[[IP], bool] = ip_publica,
        resolver_proxy: Resolver = socket.getaddrinfo,
        certificados_ca: str | None = None,
        max_sesiones: int = MAX_SESIONES,
        max_cookies: int = MAX_COOKIES,
    ) -> None:
        """`certificados_ca` solo se cambia en los tests (para confiar en un certificado propio).

        Con `proxy`, su nombre se resuelve en la primera petición (en el hilo, no en el bucle de
        eventos) y se fija con `RESOLVE`; se vuelve a resolver si no se puede conectar con él (el
        proveedor puede cambiar sus IP). Si alguna de sus IP no es remota
        (`proxy_remoto`), todas las peticiones van por `TransporteURLLib`.
        """
        self._ip_permitida = ip_permitida
        self._resolver = resolver
        self._proxy = proxy
        self._proxy_remoto = proxy_remoto
        self._resolver_proxy = resolver_proxy
        self._ca = certificados_ca
        self._max_sesiones = max_sesiones
        self._max_cookies = max_cookies
        self._sesiones: OrderedDict[str, _EntradaSesion] = OrderedDict()
        self._candado = threading.Lock()
        """Solo protege `_sesiones` y `_cerrado`: nunca se espera otro candado con él tomado."""
        self._cerrado = False
        self._candado_proxy = threading.Lock()
        self._resolve_proxy: list[str] | None = None
        """`RESOLVE` del nombre del proxy remoto (vacía si es una IP), cuando ya se sabe."""
        self._urllib: TransporteURLLib | None = None
        """El transporte de un proxy local, cuando ya se sabe que lo es."""

    @property
    def proxy(self) -> Proxy | None:
        return self._proxy

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        """El contrato `Transporte`, con el tope de los sitemaps (el más alto)."""
        return self.pedir(url, cabeceras, timeout, MAX_BYTES_SITEMAP)

    def pedir(
        self, url: str, cabeceras: Mapping[str, str], timeout: float, max_bytes: int
    ) -> RespuestaCruda:
        """Una petición GET con un tiempo total de `timeout` y como mucho `max_bytes` de cuerpo.

        De `cabeceras` solo hace uso el respaldo urllib (proxy local): curl manda las de Chrome.
        """
        limite = time.monotonic() + timeout
        if self._cerrado:
            raise ErrorDescarga("El cliente HTTP ya está cerrado.")
        host, puerto = _destino(url)
        if self._proxy is None:
            opciones = self._opciones_directas(host, puerto)
        else:
            resolve_proxy = self._salida_proxy(limite)
            if resolve_proxy is None:
                assert self._urllib is not None
                return self._urllib(url, cabeceras, _restante(limite))
            opciones = self._opciones_proxy(self._proxy, resolve_proxy)
        opciones[CurlOpt.MAXFILESIZE_LARGE] = max_bytes
        if self._ca is not None:
            opciones[CurlOpt.CAINFO] = self._ca
        entrada = self._tomar(dominio_de(url), limite)
        try:
            respuesta, cuerpo = self._hacer(entrada.sesion, url, opciones, limite, max_bytes)
        finally:
            self._soltar(entrada)
        if self._proxy is None:
            # Defensa extra: la IP a la que conectó curl (también si reutilizó una conexión, que
            # fue a una IP ya validada). No se compara con las de esta petición: las CDN rotan.
            try:
                conectada = ipaddress.ip_address(respuesta.primary_ip)
            except ValueError:
                raise URLNoPermitida("No se sabe a qué dirección IP se conectó.") from None
            if not self._ip_permitida(conectada):
                raise URLNoPermitida("El servidor resuelve a una dirección IP no pública.")
        return RespuestaCruda(
            estado=respuesta.status_code,
            cabeceras=_cabeceras(respuesta),
            cuerpo=io.BytesIO(cuerpo),
            descomprimida=True,
        )

    def cerrar(self) -> None:
        """Cierra todas las sesiones (y sus cookies) sin esperar a las peticiones en vuelo.

        Una sesión ocupada se cierra al acabar su petición. Después, toda petición nueva falla.
        """
        with self._candado:
            self._cerrado = True
            entradas = list(self._sesiones.values())
            self._sesiones.clear()
            libres = [e for e in entradas if self._retirar(e)]
        for entrada in libres:
            self._cerrar_entrada(entrada)

    # --- Opciones de cada petición --------------------------------------------------------------

    def _opciones_directas(self, host: str, puerto: int) -> dict[CurlOpt, Any]:
        opciones = _opciones_comunes()
        ip = _ip_literal(host)
        if ip is not None:
            if not self._ip_permitida(ip):
                raise URLNoPermitida("La URL apunta a una dirección IP no pública.")
            return opciones
        try:
            ips = ips_permitidas(
                host, puerto, resolver=self._resolver, ip_permitida=self._ip_permitida
            )
        except OSError as error:
            raise ErrorDescarga(MENSAJE_NO_CONECTA) from error
        opciones[CurlOpt.RESOLVE] = [_entrada_resolve(host, puerto, ips)]
        return opciones

    def _opciones_proxy(self, proxy: Proxy, resolve_proxy: list[str]) -> dict[CurlOpt, Any]:
        # El nombre de la tienda va en el `CONNECT` y lo resuelve el proxy remoto, como espera el
        # proveedor (y como hace urllib): este equipo no pregunta al DNS por la tienda.
        opciones = _opciones_comunes()
        opciones[CurlOpt.PROXY] = proxy.servidor
        opciones[CurlOpt.HTTPPROXYTUNNEL] = 1
        opciones[CurlOpt.SUPPRESS_CONNECT_HEADERS] = 1
        if proxy.usuario:
            opciones[CurlOpt.PROXYUSERNAME] = proxy.usuario
            opciones[CurlOpt.PROXYPASSWORD] = proxy.contrasena
        if resolve_proxy:
            opciones[CurlOpt.RESOLVE] = resolve_proxy
        return opciones

    def _salida_proxy(self, limite: float) -> list[str] | None:
        """`RESOLVE` del proxy remoto, o `None` si es local (y entonces se usa urllib).

        Mientras otra petición resuelve el nombre, se espera como mucho el plazo de esta.
        """
        if not self._candado_proxy.acquire(timeout=_restante(limite)):
            raise ErrorDescarga(MENSAJE_TIEMPO)
        try:
            return self._resolver_salida_proxy()
        finally:
            self._candado_proxy.release()

    def _resolver_salida_proxy(self) -> list[str] | None:
        """Con `_candado_proxy` tomado (ver `_salida_proxy`)."""
        proxy = self._proxy
        assert proxy is not None
        if self._urllib is not None:
            return None
        if self._resolve_proxy is not None:
            return self._resolve_proxy
        ip = _ip_literal(proxy.host)
        try:
            ips = (
                [ip]
                if ip is not None
                else [
                    ipaddress.ip_address(info[4][0])
                    for info in self._resolver_proxy(
                        proxy.host, proxy.puerto, type=socket.SOCK_STREAM
                    )
                ]
            )
        except (OSError, ValueError):
            raise ErrorDescarga(MENSAJE_PROXY_INALCANZABLE) from None
        if not ips:
            raise ErrorDescarga(MENSAJE_PROXY_INALCANZABLE)
        if not all(self._proxy_remoto(i) for i in ips):
            contexto = None if self._ca is None else ssl.create_default_context(cafile=self._ca)
            self._urllib = TransporteURLLib(
                ip_permitida=self._ip_permitida,
                resolver=self._resolver,
                contexto_tls=contexto,
                proxy=proxy,
                proxy_remoto=self._proxy_remoto,
            )
            return None
        self._resolve_proxy = (
            [] if ip is not None else [_entrada_resolve(proxy.host, proxy.puerto, ips)]
        )
        return self._resolve_proxy

    # --- La petición ----------------------------------------------------------------------------

    def _hacer(
        self,
        sesion: Session[Response],
        url: str,
        opciones: dict[CurlOpt, Any],
        limite: float,
        max_bytes: int,
    ) -> tuple[Response, bytes]:
        trozos: list[bytes] = []
        total = 0

        def recibir(datos: bytes) -> int:
            nonlocal total
            total += len(datos)
            if total > max_bytes:
                raise _Tope
            trozos.append(datos)
            return len(datos)

        mensaje: str | None = None
        causa: BaseException | None = None
        respuesta: Response | None = None
        sesion.curl_options = opciones
        try:
            respuesta = sesion.get(
                url,
                headers={"Accept-Language": ACCEPT_LANGUAGE_NAVEGADOR},
                timeout=_restante(limite),
                content_callback=recibir,
            )
        except _Tope:
            mensaje = MENSAJE_GRANDE
        except CurlError as error:
            mensaje, causa = self._traducir(error)
        except (ValueError, LookupError) as error:
            # Lo que `curl_cffi` no espera al leer las cabeceras (una plegada justo tras la
            # línea de estado da `IndexError`): nunca debe tumbar la búsqueda.
            mensaje = MENSAJE_NO_VALIDA
            causa = None if self._proxy is not None else error
        finally:
            # Sin punteros a listas de curl ya liberadas para la petición siguiente.
            sesion.curl_options = {}
        if len(sesion.cookies) > self._max_cookies:
            sesion.cookies.clear()
        if mensaje is not None:
            # Fuera del `except`: con el proxy no queda ni el contexto (un `setopt` que falla
            # cita el valor, y podría ser la contraseña).
            tipo = DemasiadoGrande if mensaje == MENSAJE_GRANDE else ErrorDescarga
            raise tipo(mensaje) from causa
        assert respuesta is not None
        return respuesta, b"".join(trozos)

    def _traducir(self, error: CurlError) -> tuple[str, BaseException | None]:
        """Mensaje y causa de un error de curl. La causa decide si `con_proxy` ve un corte."""
        codigo = int(error.code)
        respuesta = getattr(error, "response", None)  # un fallo de `setopt` no trae respuesta
        infos: Mapping[Any, Any] = respuesta.infos if respuesta is not None else {}
        sistema = int(infos.get(CurlInfo.OS_ERRNO) or 0)
        tunel = int(infos.get(CurlInfo.HTTP_CONNECTCODE) or 0)
        if codigo == _CODIGO_DEMASIADO_GRANDE:
            return MENSAJE_GRANDE, None
        if codigo == _CODIGO_TIEMPO:
            return MENSAJE_TIEMPO, None
        if codigo == _CODIGO_COMPRESION:
            return MENSAJE_COMPRESION, None
        if self._proxy is not None:
            # Nada encadenado: el error de curl podría citar las credenciales.
            if tunel >= 300:
                return mensaje_tunel_rechazado(tunel), None
            if codigo == _CODIGO_RESOLVER_PROXY or (codigo == _CODIGO_CONECTAR and tunel == 0):
                # La IP fijada puede ya no ser del proxy: la próxima petición vuelve a resolver.
                # Sin candado: asignar es atómico y, como mucho, otra petición resuelve otra vez.
                self._resolve_proxy = None
                return MENSAJE_PROXY_INALCANZABLE, None
            if codigo in (_CODIGO_RECIBIR, _CODIGO_TLS) and tunel == 0:
                return MENSAJE_PROXY_NO_HTTP, None
            return MENSAJE_PROXY_OTRO, None
        if sistema in _ERRNO_RESET or codigo == _CODIGO_RESPUESTA_VACIA:
            return MENSAJE_CORTE, ConnectionResetError()
        if sistema in _ERRNO_ABORTADA:
            return MENSAJE_CORTE, ConnectionAbortedError()
        return MENSAJE_NO_CONECTA, error

    # --- Sesiones -------------------------------------------------------------------------------

    def _tomar(self, dominio: str, limite: float) -> _EntradaSesion:
        """La sesión de la tienda, con su candado tomado. Hay que devolverla con `_soltar`."""
        while True:
            desalojadas: list[_EntradaSesion] = []
            with self._candado:
                if self._cerrado:
                    raise ErrorDescarga("El cliente HTTP ya está cerrado.")
                entrada = self._sesiones.get(dominio)
                if entrada is None:
                    entrada = _EntradaSesion(self._nueva_sesion())
                    self._sesiones[dominio] = entrada
                    while len(self._sesiones) > self._max_sesiones:
                        _, vieja = self._sesiones.popitem(last=False)
                        if self._retirar(vieja):
                            desalojadas.append(vieja)
                else:
                    self._sesiones.move_to_end(dominio)
            for vieja in desalojadas:
                self._cerrar_entrada(vieja)
            restante = limite - time.monotonic()
            if restante <= 0 or not entrada.candado.acquire(timeout=restante):
                raise ErrorDescarga(MENSAJE_TIEMPO)
            if not entrada.cerrada:
                return entrada
            entrada.candado.release()  # la cerraron mientras se esperaba: se pide otra

    def _soltar(self, entrada: _EntradaSesion) -> None:
        with self._candado:
            retirada = entrada.retirada
            if not retirada:
                entrada.candado.release()
        if retirada:
            self._cerrar_entrada(entrada)

    def _retirar(self, entrada: _EntradaSesion) -> bool:
        """Con `_candado` tomado. Devuelve `True` si la sesión está libre y hay que cerrarla ya
        (con su candado tomado); si está ocupada, la cierra quien la suelte."""
        entrada.retirada = True
        return entrada.candado.acquire(blocking=False)

    @staticmethod
    def _cerrar_entrada(entrada: _EntradaSesion) -> None:
        """Con el candado de la entrada tomado: la cierra y lo suelta."""
        try:
            entrada.sesion.close()
        finally:
            entrada.cerrada = True
            entrada.candado.release()

    @staticmethod
    def _nueva_sesion() -> Session[Response]:
        return Session(
            impersonate=PERFIL_CHROME,
            use_thread_local_curl=False,
            allow_redirects=False,
            max_redirects=0,
            retry=0,
            trust_env=False,
            curl_infos=[CurlInfo.OS_ERRNO, CurlInfo.HTTP_CONNECTCODE],
        )


def _opciones_comunes() -> dict[CurlOpt, Any]:
    return {
        # Sin proxy (salvo el configurado) y sin excepciones: NO_PROXY=* en el entorno haría que
        # curl fuera directo, con su propio DNS (rebinding) y la IP real.
        CurlOpt.PROXY: "",
        CurlOpt.NOPROXY: "",
        CurlOpt.PROTOCOLS_STR: "http,https",
        CurlOpt.REDIR_PROTOCOLS_STR: "http,https",
        # HTTP/2 solo por ALPN en TLS, como Chrome (sin `Upgrade: h2c` en claro).
        CurlOpt.HTTP_VERSION: CurlHttpVersion.V2TLS,
    }


def _destino(url: str) -> tuple[str, int]:
    try:
        partes = urlsplit(url)
        esquema, host, puerto = partes.scheme.lower(), partes.hostname, partes.port
    except ValueError:
        raise ErrorDescarga("La URL no es válida.") from None
    if esquema not in ("http", "https") or not host:
        raise ErrorDescarga("Solo se admiten URL http y https.")
    return host, puerto or (443 if esquema == "https" else 80)


def _ip_literal(host: str) -> IP | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _entrada_resolve(host: str, puerto: int, ips: list[IP]) -> str:
    direcciones = ",".join(f"[{ip}]" if ip.version == 6 else str(ip) for ip in ips)
    return f"{host}:{puerto}:{direcciones}"


def _restante(limite: float) -> float:
    restante = limite - time.monotonic()
    # curl_cffi lo pasa como `int(timeout * 1000)`: menos de 1 ms sería TIMEOUT_MS = 0, que en curl
    # significa «sin límite».
    if restante < MIN_PLAZO_S:
        raise ErrorDescarga(MENSAJE_TIEMPO)
    return restante


def _cabeceras(respuesta: Response) -> dict[str, str]:
    """Cabeceras en minúsculas, sin `Content-Encoding` ni `Content-Length` si curl descomprimió."""
    cabeceras = {k.lower(): v for k, v in respuesta.headers.items() if v is not None}
    codificacion = cabeceras.get("content-encoding")
    if codificacion is not None:
        fichas = [f.strip().lower() for f in codificacion.split(",") if f.strip()]
        if not all(f in _CODIFICACIONES_CURL for f in fichas):
            raise ErrorDescarga(MENSAJE_COMPRESION)
        cabeceras.pop("content-encoding")
        cabeceras.pop("content-length", None)
    return cabeceras
