"""Proxy de salida para las tiendas que bloquean al cliente automático por la reputación de la IP.

Julio decidió usar proxies residenciales (Evomi) para esas tiendas: la petición sale por otra IP,
pero con el mismo User-Agent honesto, la misma cortesía por dominio y las mismas defensas. Nunca
se resuelve un CAPTCHA ni un desafío de JavaScript, ni se entra con una cuenta.

Las credenciales del proxy son secretos: no aparecen en `repr`, en `str` ni en ningún mensaje de
error. Solo salen en la cabecera `Proxy-Authorization` del `CONNECT`, que va al proxy y nunca a la
tienda. A Playwright no se le dan nunca: las usaría también para contestar a cualquier `401 Basic`
de un destino (ver `navegador.rele`).
"""

import base64
import ipaddress
import re
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import unquote, urlsplit

NOMBRE_PROPIO = "proxy propio"
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")


class ModoProxy(StrEnum):
    """Cuándo sale una petición por el proxy (`PRECIA_PROXY_MODO`)."""

    BLOQUEO = "bloqueo"
    """Directa; si la tienda bloquea la conexión, se repite una vez por el proxy y la tienda
    queda marcada para ir siempre por él (el proxy se paga por MB: solo cuando hace falta)."""
    SIEMPRE = "siempre"
    NUNCA = "nunca"


def modo_proxy(texto: str | None) -> ModoProxy:
    """El modo escrito en la configuración. Vacío o desconocido: `BLOQUEO`, el de por defecto.

    Una errata no debe mandar todo por el proxy de pago (`siempre`) ni dejar sin él a las tiendas
    que bloquean (`nunca`): el valor por defecto es el término medio.
    """
    try:
        return ModoProxy((texto or "").strip().lower())
    except ValueError:
        return ModoProxy.BLOQUEO


class ErrorProxy(Exception):
    """No se pudo configurar u obtener el proxy. El mensaje no lleva secretos: se puede enseñar."""


class URLProxyNoValida(ValueError):
    """`PRECIA_PROXY` mal escrita. El mensaje es siempre propio: nunca cita la URL."""


@dataclass(frozen=True, slots=True, repr=False)
class Proxy:
    """Un proxy HTTP con `CONNECT` (también para https) y autenticación Basic opcional."""

    host: str
    puerto: int
    usuario: str = field(default="")
    contrasena: str = field(default="")
    nombre: str = NOMBRE_PROPIO
    """Lo que se enseña en la web y en los avisos («Evomi core residential (ES)»)."""

    def __post_init__(self) -> None:
        # Los mensajes no citan ningún valor: podrían ser la contraseña mal puesta.
        if not _host_valido(self.host):
            raise ValueError("El servidor del proxy no es un nombre ni una IP válidos.")
        if isinstance(self.puerto, bool) or not isinstance(self.puerto, int):
            raise ValueError("El puerto del proxy tiene que ser un número.")
        if not 1 <= self.puerto <= 65535:
            raise ValueError("El puerto del proxy tiene que estar entre 1 y 65535.")
        if not (self.usuario.isprintable() and self.contrasena.isprintable()):
            raise ValueError("Las credenciales del proxy no pueden llevar saltos de línea.")
        if ":" in self.usuario:
            raise ValueError("El usuario del proxy no puede llevar «:».")
        if self.contrasena and not self.usuario:
            raise ValueError("El proxy tiene contraseña pero no usuario.")
        if not self.nombre.isprintable():
            raise ValueError("El nombre del proxy no puede llevar saltos de línea.")

    def __repr__(self) -> str:
        return f"Proxy(nombre={self.nombre!r}, servidor={self.servidor!r})"

    def __str__(self) -> str:
        return self.nombre

    @property
    def servidor(self) -> str:
        """`http://host:puerto`, sin credenciales (con corchetes si es una IPv6)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.puerto}"

    @property
    def cabecera_autorizacion(self) -> str | None:
        """Valor de `Proxy-Authorization` (Basic), o `None` si el proxy no pide usuario."""
        if not self.usuario:
            return None
        credenciales = f"{self.usuario}:{self.contrasena}".encode()
        return f"Basic {base64.b64encode(credenciales).decode('ascii')}"


def _host_valido(host: object) -> bool:
    if not isinstance(host, str) or not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return _HOST.fullmatch(host) is not None
    return True


def proxy_desde_url(texto: str, *, nombre: str = NOMBRE_PROPIO) -> Proxy:
    """`PRECIA_PROXY`: `http://usuario:contraseña@servidor:puerto`, con `%xx` si hace falta.

    Solo el esquema `http` (el proxy se habla en claro y el https va dentro del `CONNECT`) y el
    puerto es obligatorio: un proxy casi nunca está en el 80 y suponerlo escondería una errata.
    Los errores (`URLProxyNoValida`) nunca citan la URL, que lleva la contraseña.
    """
    texto = texto.strip()
    # Antes de `urlsplit`, que borra los saltos de línea sin avisar.
    if not texto.isprintable():
        raise URLProxyNoValida("La URL del proxy no puede llevar saltos de línea.")
    try:
        # Los `ValueError` de `urlsplit` y de sus propiedades citan el trozo de la URL (con una
        # almohadilla de ancho completo, U+FF03, en la contraseña, el `netloc` entero): fuera.
        partes = urlsplit(texto)
        esquema, ruta = partes.scheme.lower(), partes.path
        consulta, fragmento = partes.query, partes.fragment
        host, usuario, contrasena = partes.hostname, partes.username, partes.password
    except ValueError:
        raise URLProxyNoValida("La URL del proxy no es válida.") from None
    try:
        puerto = partes.port
    except ValueError:
        raise URLProxyNoValida("El puerto del proxy no es válido.") from None
    if esquema != "http":
        raise URLProxyNoValida(
            "El proxy tiene que ser una URL http://usuario:contraseña@servidor:puerto "
            "(solo se admite el esquema http)."
        )
    if ruta not in ("", "/") or consulta or fragmento:
        raise URLProxyNoValida("La URL del proxy no puede llevar ruta ni parámetros.")
    if puerto is None:
        raise URLProxyNoValida("Falta el puerto del proxy (por ejemplo, «:8080»).")
    if not host:
        raise URLProxyNoValida("Falta el servidor del proxy.")
    try:
        return Proxy(host, puerto, unquote(usuario or ""), unquote(contrasena or ""), nombre=nombre)
    except ValueError as error:
        # Los mensajes de `Proxy` son propios y no citan ningún valor.
        raise URLProxyNoValida(str(error)) from None
