"""Avisos de las alertas de precio: el texto y los canales de email y Telegram.

Solo librería estándar: `smtplib` y `email` para el correo y `urllib` para la API de Telegram
(nota 06). Las llamadas bloquean, así que van en un hilo (`asyncio.to_thread`).

Seguridad:

- El nombre del producto, la tienda y la URL vienen de webs ajenas (dato no confiable). El
  aviso es **texto plano** en los dos canales (Telegram sin `parse_mode`), se le quitan los
  caracteres de control y la URL solo se escribe si es `http(s)`. El asunto del correo va en
  una línea: no se puede inyectar una cabecera («Bcc: …»).
- El correo siempre va cifrado (`STARTTLS` o SSL, con verificación del certificado): sin
  cifrado no se envía la contraseña.
- Los secretos (contraseña de SMTP, token de Telegram) llegan del entorno o de `.env` y nunca
  salen en un error ni al imprimir la configuración. La URL de Telegram lleva el token: los
  errores de red se relanzan sin su causa.
"""

import asyncio
import contextlib
import json
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import urlsplit

from precia.nucleo.formato import euros

TIMEOUT_S = 15.0
MAX_ASUNTO = 200
MAX_NOMBRE = 300
MAX_URL = 2048
MAX_TEXTO_TELEGRAM = 4096
"""Límite de `sendMessage` de la API de Telegram."""
PUERTO_STARTTLS = 587
SEGURIDADES = ("starttls", "ssl")
_DIRECCION = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}")
_SERVIDOR = re.compile(r"[A-Za-z0-9.-]{1,253}")
_TOKEN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{30,64}")
_CHAT = re.compile(r"-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")


class ErrorAviso(Exception):
    """El aviso no se ha podido enviar. Su mensaje nunca lleva secretos."""


class CanalNoConfigurado(ErrorAviso):
    """Faltan los datos del canal o no tienen un formato válido."""


@dataclass(frozen=True, slots=True)
class Aviso:
    """Lo que se cuenta al usuario cuando salta una alerta."""

    producto: str
    umbral: Decimal
    precio: Decimal
    tienda: str
    url: str


def _limpio(texto: str, maximo: int) -> str:
    """Una sola línea, sin caracteres de control ni invisibles, y recortada."""
    visible = "".join(c if c.isprintable() else " " for c in texto)
    return " ".join(visible.split())[:maximo]


def asunto(aviso: Aviso) -> str:
    """Una línea: va en la cabecera `Subject` del correo."""
    precio = euros(aviso.precio)
    margen = MAX_ASUNTO - len(f"PrecIA:  a {precio}")
    return f"PrecIA: {_limpio(aviso.producto, margen)} a {precio}"


def cuerpo(aviso: Aviso) -> str:
    lineas = [
        f"{_limpio(aviso.producto, MAX_NOMBRE)} está a {euros(aviso.precio)} en "
        f"{_limpio(aviso.tienda, MAX_NOMBRE)}.",
        f"Tu precio objetivo era {euros(aviso.umbral)}.",
    ]
    if _enlazable(aviso.url):
        lineas.append(aviso.url)
    lineas.append("")
    lineas.append(
        "No se te volverá a avisar de esta alerta hasta que el precio suba y vuelva a bajar."
    )
    return "\n".join(lineas)


def _enlazable(url: str) -> bool:
    if not url or len(url) > MAX_URL or any(c.isspace() or not c.isprintable() for c in url):
        return False
    try:
        partes = urlsplit(url)
    except ValueError:
        return False
    return partes.scheme in ("http", "https") and bool(partes.hostname)


class Canal(Protocol):
    async def enviar(self, aviso: Aviso) -> None:
        """Envía el aviso o lanza `ErrorAviso`."""
        ...


# --- Email ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigSMTP:
    servidor: str
    puerto: int
    usuario: str
    # repr=False: la contraseña nunca aparece al imprimir la configuración ni en un error.
    clave: str = field(repr=False)
    remitente: str
    destinatario: str
    seguridad: str = "starttls"

    def __post_init__(self) -> None:
        if not _SERVIDOR.fullmatch(self.servidor):
            raise CanalNoConfigurado("El servidor SMTP no es un nombre de servidor válido.")
        if isinstance(self.puerto, bool) or not 1 <= self.puerto <= 65535:
            raise CanalNoConfigurado("El puerto SMTP tiene que estar entre 1 y 65535.")
        if not self.usuario or not self.usuario.isprintable() or len(self.usuario) > 320:
            raise CanalNoConfigurado("El usuario de SMTP no es válido.")
        if not self.clave:
            raise CanalNoConfigurado("Falta la contraseña de SMTP.")
        for nombre, direccion in (
            ("remitente", self.remitente),
            ("destinatario", self.destinatario),
        ):
            if not _DIRECCION.fullmatch(direccion):
                raise CanalNoConfigurado(f"El {nombre} del correo no es una dirección válida.")
        if self.seguridad not in SEGURIDADES:
            raise CanalNoConfigurado("La seguridad de SMTP tiene que ser starttls o ssl.")


class ServidorSMTP(Protocol):
    """Lo que el canal usa de `smtplib.SMTP`."""

    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, msg: EmailMessage) -> object: ...

    def quit(self) -> object: ...


Conectar = Callable[[ConfigSMTP], ServidorSMTP]


def _conectar_smtp(config: ConfigSMTP) -> ServidorSMTP:
    if config.seguridad == "ssl":
        return smtplib.SMTP_SSL(
            config.servidor, config.puerto, timeout=TIMEOUT_S, context=ssl.create_default_context()
        )
    return smtplib.SMTP(config.servidor, config.puerto, timeout=TIMEOUT_S)


class CanalEmail:
    def __init__(self, config: ConfigSMTP, *, conectar: Conectar = _conectar_smtp) -> None:
        self.config = config
        self._conectar = conectar

    async def enviar(self, aviso: Aviso) -> None:
        await asyncio.to_thread(self._enviar, aviso)

    def _enviar(self, aviso: Aviso) -> None:
        config = self.config
        mensaje = EmailMessage()
        mensaje["From"] = config.remitente
        mensaje["To"] = config.destinatario
        mensaje["Subject"] = asunto(aviso)
        mensaje.set_content(cuerpo(aviso))
        try:
            servidor = self._conectar(config)
        except (OSError, smtplib.SMTPException) as error:
            raise ErrorAviso(
                f"No se pudo conectar con el servidor de correo ({type(error).__name__})."
            ) from None
        try:
            if config.seguridad == "starttls":
                servidor.starttls(context=ssl.create_default_context())
            servidor.login(config.usuario, config.clave)
            servidor.send_message(mensaje)
        except (OSError, smtplib.SMTPException) as error:
            # Solo el tipo: el texto del servidor puede repetir lo que se le envió.
            raise ErrorAviso(
                f"El servidor de correo rechazó el envío ({type(error).__name__})."
            ) from None
        finally:
            # Si la conexión ya estaba rota no hay nada que cerrar.
            with contextlib.suppress(OSError, smtplib.SMTPException):
                servidor.quit()


# --- Telegram ---------------------------------------------------------------------------------

EnviarHTTP = Callable[[str, bytes], tuple[int, bytes]]
"""POST de un JSON a una URL `https`: estado y cuerpo de la respuesta."""


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None


def _post_https(url: str, datos: bytes) -> tuple[int, bytes]:
    # La URL es fija (api.telegram.org): no viene del usuario ni de una tienda.
    peticion = urllib.request.Request(
        url, data=datos, method="POST", headers={"Content-Type": "application/json"}
    )
    abridor = urllib.request.build_opener(
        _SinRedirecciones, urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    try:
        with abridor.open(peticion, timeout=TIMEOUT_S) as respuesta:
            return int(respuesta.status), respuesta.read(65536)
    except urllib.error.HTTPError as error:
        return error.code, error.read(65536)


class CanalTelegram:
    def __init__(self, token: str, chat: str, *, enviar: EnviarHTTP = _post_https) -> None:
        if not _TOKEN.fullmatch(token):
            raise CanalNoConfigurado("El token de Telegram no tiene un formato válido.")
        if not _CHAT.fullmatch(chat):
            raise CanalNoConfigurado(
                "El chat de Telegram tiene que ser su número o un @nombre de canal."
            )
        self._token = token
        self._chat = chat
        self._enviar = enviar

    async def enviar(self, aviso: Aviso) -> None:
        await asyncio.to_thread(self._enviar_ya, aviso)

    def _enviar_ya(self, aviso: Aviso) -> None:
        texto = f"{asunto(aviso)}\n\n{cuerpo(aviso)}"[:MAX_TEXTO_TELEGRAM]
        datos = json.dumps(
            {"chat_id": self._chat, "text": texto, "disable_web_page_preview": True}
        ).encode()
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            estado, respuesta = self._enviar(url, datos)
        except (OSError, ValueError) as error:
            # `from None`: la causa cita la URL, que lleva el token.
            raise ErrorAviso(
                f"No se pudo conectar con Telegram ({type(error).__name__})."
            ) from None
        try:
            aceptado = estado == 200 and json.loads(respuesta).get("ok") is True
        except (ValueError, AttributeError):
            aceptado = False
        if not aceptado:
            raise ErrorAviso(f"Telegram rechazó el aviso (HTTP {estado}).")


# --- Canales según la configuración -----------------------------------------------------------

Entorno = Callable[[str], str | None]
"""Lee una variable de la configuración (entorno o `.env`); `None` si no está."""
VARIABLES_EMAIL = (
    "PRECIA_SMTP_SERVIDOR",
    "PRECIA_SMTP_USUARIO",
    "PRECIA_SMTP_CLAVE",
    "PRECIA_ALERTAS_EMAIL",
)
VARIABLES_TELEGRAM = ("PRECIA_TELEGRAM_TOKEN", "PRECIA_TELEGRAM_CHAT")
VARIABLES = (
    *VARIABLES_EMAIL,
    "PRECIA_SMTP_PUERTO",
    "PRECIA_SMTP_SEGURIDAD",
    "PRECIA_SMTP_REMITENTE",
    *VARIABLES_TELEGRAM,
)
"""Todas las variables de los avisos: las lee `nucleo.config`."""


def _faltan(entorno: Entorno, variables: tuple[str, ...]) -> str:
    faltan = [v for v in variables if not (entorno(v) or "").strip()]
    return f"Falta configurar {', '.join(faltan)} en .env." if faltan else ""


def canales_desde(entorno: Entorno) -> tuple[dict[str, Canal], dict[str, str]]:
    """Los canales configurados y, de los que no lo están, el motivo (sin secretos)."""
    canales: dict[str, Canal] = {}
    motivos: dict[str, str] = {}

    def valor(nombre: str) -> str:
        return (entorno(nombre) or "").strip()

    if falta := _faltan(entorno, VARIABLES_EMAIL):
        motivos["email"] = falta
    else:
        try:
            puerto = valor("PRECIA_SMTP_PUERTO") or str(PUERTO_STARTTLS)
            if not re.fullmatch(r"[0-9]{1,5}", puerto):
                raise CanalNoConfigurado("El puerto SMTP tiene que estar entre 1 y 65535.")
            canales["email"] = CanalEmail(
                ConfigSMTP(
                    servidor=valor("PRECIA_SMTP_SERVIDOR"),
                    puerto=int(puerto),
                    usuario=valor("PRECIA_SMTP_USUARIO"),
                    clave=valor("PRECIA_SMTP_CLAVE"),
                    remitente=valor("PRECIA_SMTP_REMITENTE") or valor("PRECIA_SMTP_USUARIO"),
                    destinatario=valor("PRECIA_ALERTAS_EMAIL"),
                    seguridad=(valor("PRECIA_SMTP_SEGURIDAD") or "starttls").lower(),
                )
            )
        except CanalNoConfigurado as error:
            motivos["email"] = str(error)
    if falta := _faltan(entorno, VARIABLES_TELEGRAM):
        motivos["telegram"] = falta
    else:
        try:
            canales["telegram"] = CanalTelegram(
                valor("PRECIA_TELEGRAM_TOKEN"), valor("PRECIA_TELEGRAM_CHAT")
            )
        except CanalNoConfigurado as error:
            motivos["telegram"] = str(error)
    return canales, motivos
