"""Validación de URL contra SSRF.

Toda URL que viene de fuera (usuario, Google, IA o una redirección) pasa por `validar_url` antes de
pedirse. Se rechaza lo que no sea una web pública: otros esquemas, credenciales, puertos raros, IP
privadas escritas de cualquier forma (decimal, octal, hexadecimal, IPv6 con una IPv4 dentro) y
nombres que resuelven a direcciones internas.

La comprobación del DNS aquí no basta contra el *DNS rebinding* (el nombre puede resolver distinto
al conectar); por eso `http.py` vuelve a validar la IP en el momento de abrir el socket.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

from precia.navegador.tipos import URLNoPermitida

Resolver = Callable[[str], Awaitable[Iterable[str]]]
"""Recibe un nombre de host y devuelve sus IP como texto."""
Validador = Callable[[str], Awaitable[str]]
"""Firma de `validar_url` ya configurada: se inyecta en los descargadores (y en los tests)."""

IP = ipaddress.IPv4Address | ipaddress.IPv6Address

_PUERTO_POR_DEFECTO = {"http": 80, "https": 443}
ESQUEMAS = frozenset(_PUERTO_POR_DEFECTO)
PUERTOS = frozenset(_PUERTO_POR_DEFECTO.values())
LONGITUD_MAXIMA = 2048
ESPERA_DNS_S = 10.0

# Sufijos que nunca son una web pública aunque un DNS local los resuelva.
SUFIJOS_INTERNOS = ("localhost", "local", "internal", "localdomain", "lan", "home.arpa")
_ETIQUETA = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
_NUMERO_URL = re.compile(r"(0x[0-9a-f]*|[0-9]+)")
# Se aceptan los caracteres ya escapados (%) y los reservados de RFC 3986; el resto se escapa.
_SEGUROS_RUTA = "/%:@!$&'()*+,;=-._~"
_SEGUROS_CONSULTA = _SEGUROS_RUTA + "?"

_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


async def validar_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Devuelve la URL normalizada si apunta a una web pública; si no, lanza `URLNoPermitida`."""
    partes = _analizar(url)
    host = _normalizar_host(partes)
    puerto = _puerto(partes)

    ip = _ip_literal(host)
    if ip is not None:
        if not ip_publica(ip):
            raise URLNoPermitida("La URL apunta a una dirección IP no pública.")
    else:
        _comprobar_nombre(host)
        await _comprobar_resolucion(host, resolver or resolver_sistema)

    netloc = f"[{host}]" if ":" in host else host
    if puerto is not None and puerto != _PUERTO_POR_DEFECTO[partes.scheme]:
        netloc = f"{netloc}:{puerto}"
    ruta = quote(partes.path, safe=_SEGUROS_RUTA) or "/"
    consulta = quote(partes.query, safe=_SEGUROS_CONSULTA)
    return urlunsplit((partes.scheme, netloc, ruta, consulta, ""))


def ip_publica(ip: IP) -> bool:
    """Indica si la IP es alcanzable en Internet (no privada, loopback, multicast, reservada...)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id or int(ip) >> 32 == 0:
            # Con zona (fe80::1%eth0) o «IPv4-compatible» (::7f00:1, obsoleta): nunca es pública.
            return False
        incrustada = _ipv4_incrustada(ip)
        if incrustada is not None and not ip_publica(incrustada):
            return False
        if ip.is_site_local:
            return False
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def resolver_sistema(host: str) -> list[str]:
    """Resolver por defecto: el DNS del sistema, sin bloquear el bucle de eventos."""
    bucle = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            bucle.getaddrinfo(host, None, type=socket.SOCK_STREAM), ESPERA_DNS_S
        )
    except (OSError, TimeoutError) as error:
        raise URLNoPermitida("No se puede resolver el nombre del servidor.") from error
    return [str(info[4][0]) for info in infos]


def _analizar(url: str) -> SplitResult:
    url = url.strip()
    if not url:
        raise URLNoPermitida("La URL está vacía.")
    if len(url) > LONGITUD_MAXIMA:
        raise URLNoPermitida("La URL es demasiado larga.")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        raise URLNoPermitida("La URL contiene caracteres de control.")
    if "\\" in url:
        # Los navegadores leen «\» como «/» y urllib no: la misma URL tendría dos hosts.
        raise URLNoPermitida("La URL contiene barras invertidas.")
    try:
        partes = urlsplit(url)
    except ValueError as error:
        raise URLNoPermitida("La URL está mal formada.") from error
    if partes.scheme.lower() not in ESQUEMAS:
        raise URLNoPermitida("Solo se admiten URL http y https.")
    if "@" in partes.netloc:
        raise URLNoPermitida("La URL no puede llevar usuario ni contraseña.")
    return partes._replace(scheme=partes.scheme.lower())


def _normalizar_host(partes: SplitResult) -> str:
    try:
        host = partes.hostname or ""
    except ValueError as error:
        raise URLNoPermitida("La URL está mal formada.") from error
    if not host:
        raise URLNoPermitida("La URL no tiene servidor.")
    if "%" in host:
        raise URLNoPermitida("La URL contiene un servidor no válido.")
    if ":" in host:
        return host  # IPv6 entre corchetes: se comprueba como IP.
    if "[" in partes.netloc:
        # «IPvFuture» (`[v1.fe]`): urlsplit lo acepta y devuelve el nombre sin corchetes, que es
        # otro host distinto del escrito.
        raise URLNoPermitida("La URL contiene un servidor no válido.")
    if not host.isascii():
        try:
            # IDNA normaliza (NFKC) antes de codificar: «127.0.0.1» escrito con dígitos de ancho
            # completo acaba siendo 127.0.0.1, así que las comprobaciones de IP se hacen siempre
            # sobre el resultado.
            host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise URLNoPermitida("El nombre del servidor no es válido.") from error
    host = host.lower().removesuffix(".")
    etiquetas_validas = all(_ETIQUETA.fullmatch(e) for e in host.split("."))
    if (len(host) > 253 or not etiquetas_validas) and not _parece_ipv4(host):
        raise URLNoPermitida("El nombre del servidor no es válido.")
    return host


def _puerto(partes: SplitResult) -> int | None:
    try:
        puerto = partes.port
    except ValueError as error:
        raise URLNoPermitida("El puerto de la URL no es válido.") from error
    if puerto is not None and puerto not in PUERTOS:
        raise URLNoPermitida("Solo se admiten los puertos 80 y 443.")
    return puerto


def _parece_ipv4(host: str) -> bool:
    # Como hace el estándar URL de WHATWG: si la última etiqueta es un número, el host es una
    # IPv4 (o un error), nunca un nombre. Así «0x7f.1» o «2130706433» no se cuelan como nombres.
    ultima = host.rsplit(".", 1)[-1]
    return bool(_NUMERO_URL.fullmatch(ultima))


def _ip_literal(host: str) -> IP | None:
    if ":" in host:
        try:
            return ipaddress.IPv6Address(host)
        except ValueError as error:
            raise URLNoPermitida("La dirección IPv6 no es válida.") from error
    if not _parece_ipv4(host):
        return None
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        ip = None
    if ip is None or str(ip) != host:
        # Formas como «0177.0.0.1», «127.1» o «0x7f000001» son válidas para algunos clientes y
        # no para otros: en lugar de adivinar a qué IP van, se rechazan todas.
        raise URLNoPermitida("La IP de la URL no está escrita en formato estándar.")
    return ip


def _comprobar_nombre(host: str) -> None:
    if "." not in host:
        raise URLNoPermitida("El servidor debe tener un nombre de dominio completo.")
    if any(host == s or host.endswith("." + s) for s in SUFIJOS_INTERNOS):
        raise URLNoPermitida("El servidor pertenece a una red interna.")


async def _comprobar_resolucion(host: str, resolver: Resolver) -> None:
    ips = list(await resolver(host))
    if not ips:
        raise URLNoPermitida("No se puede resolver el nombre del servidor.")
    for texto in ips:
        try:
            ip = ipaddress.ip_address(texto)
        except ValueError as error:
            raise URLNoPermitida("El DNS devolvió una dirección no válida.") from error
        # Basta una IP interna: el cliente podría conectar a cualquiera de las que resuelve.
        if not ip_publica(ip):
            raise URLNoPermitida("El servidor resuelve a una dirección IP no pública.")


def _ipv4_incrustada(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if any(ip in red for red in _NAT64):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None
