"""Avisos de las alertas: el texto y los canales de email y Telegram, sin red.

El servidor SMTP y la API de Telegram se sustituyen por dobles. El nombre del producto, la
tienda y la URL vienen de webs ajenas: son datos no confiables.
"""

import asyncio
import json
import smtplib
from decimal import Decimal
from email.message import EmailMessage
from typing import Any

import pytest

from precia.seguimiento.avisos import (
    Aviso,
    CanalEmail,
    CanalNoConfigurado,
    CanalTelegram,
    ConfigSMTP,
    ErrorAviso,
    asunto,
    canales_desde,
    cuerpo,
)

AVISO = Aviso(
    producto="Pastillas de freno delanteras",
    umbral=Decimal("30"),
    precio=Decimal("27.5"),
    tienda="recambios.example",
    url="https://recambios.example/p/1k0698151",
)
CLAVE = "clave-smtp-QUE-NUNCA-debe-salir"
TOKEN = "123456789:AAH-token_de_prueba_QUE_NUNCA_debe_salir_0"
SMTP = ConfigSMTP(
    servidor="smtp.example",
    puerto=587,
    usuario="precia@example.org",
    clave=CLAVE,
    remitente="precia@example.org",
    destinatario="julio@example.org",
)


# --- Texto ------------------------------------------------------------------------------------


def test_el_asunto_y_el_cuerpo_dicen_producto_precio_objetivo_y_tienda() -> None:
    assert asunto(AVISO) == "PrecIA: Pastillas de freno delanteras a 27,50 €"
    texto = cuerpo(AVISO)
    assert "27,50 €" in texto and "30,00 €" in texto
    assert "recambios.example" in texto
    assert "https://recambios.example/p/1k0698151" in texto


def test_el_asunto_no_admite_saltos_de_linea_ni_controles() -> None:
    hostil = Aviso(
        "Faro\r\nBcc: otro@example.org\x00\x1b[31m", Decimal(5), Decimal(4), "t.example", ""
    )
    linea = asunto(hostil)
    assert "\r" not in linea and "\n" not in linea and "\x00" not in linea and "\x1b" not in linea
    assert "Faro" in linea


def test_el_asunto_se_recorta() -> None:
    assert len(asunto(Aviso("x" * 5000, Decimal(5), Decimal(4), "t.example", ""))) <= 200


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "file:///etc/passwd", "", "ftp://x.example/"]
)
def test_solo_se_enlazan_url_http(url: str) -> None:
    texto = cuerpo(Aviso("Faro", Decimal(5), Decimal(4), "t.example", url))
    assert "javascript:" not in texto and "file:" not in texto and "ftp:" not in texto


def test_el_cuerpo_quita_los_controles_del_nombre() -> None:
    texto = cuerpo(Aviso("Faro\x00\x1b[2J\r\nOtro", Decimal(5), Decimal(4), "t.example", ""))
    assert "\x00" not in texto and "\x1b" not in texto


# --- Email ------------------------------------------------------------------------------------


class SMTPFalso:
    """Lo que usa el canal de `smtplib.SMTP`: apunta el orden de las llamadas."""

    def __init__(self, *, falla_en: str = "") -> None:
        self.llamadas: list[str] = []
        self.mensaje: EmailMessage | None = None
        self.credenciales: tuple[str, str] | None = None
        self._falla_en = falla_en

    def _apuntar(self, nombre: str) -> None:
        self.llamadas.append(nombre)
        if nombre == self._falla_en:
            raise smtplib.SMTPAuthenticationError(535, f"rechazada la clave {CLAVE}".encode())

    def starttls(self, **_: Any) -> None:
        self._apuntar("starttls")

    def login(self, usuario: str, clave: str) -> None:
        self.credenciales = (usuario, clave)
        self._apuntar("login")

    def send_message(self, mensaje: EmailMessage) -> None:
        self.mensaje = mensaje
        self._apuntar("send_message")

    def quit(self) -> None:
        self._apuntar("quit")


def test_email_cifra_antes_de_identificarse_y_envia() -> None:
    servidor = SMTPFalso()
    pedido: list[ConfigSMTP] = []

    def conectar(config: ConfigSMTP) -> SMTPFalso:
        pedido.append(config)
        return servidor

    asyncio.run(CanalEmail(SMTP, conectar=conectar).enviar(AVISO))

    assert pedido == [SMTP]
    assert servidor.llamadas == ["starttls", "login", "send_message", "quit"]
    assert servidor.credenciales == ("precia@example.org", CLAVE)
    mensaje = servidor.mensaje
    assert mensaje is not None
    assert mensaje["To"] == "julio@example.org"
    assert mensaje["From"] == "precia@example.org"
    assert mensaje["Subject"] == asunto(AVISO)
    assert mensaje.get_content_type() == "text/plain"
    assert "27,50 €" in mensaje.get_content()


def test_email_con_ssl_no_hace_starttls() -> None:
    servidor = SMTPFalso()
    config = ConfigSMTP(
        "smtp.example", 465, "u@example.org", CLAVE, "u@example.org", "j@example.org", "ssl"
    )
    asyncio.run(CanalEmail(config, conectar=lambda _: servidor).enviar(AVISO))
    assert servidor.llamadas == ["login", "send_message", "quit"]


def test_email_con_nombre_hostil_no_inyecta_cabeceras() -> None:
    servidor = SMTPFalso()
    hostil = Aviso("Faro\r\nBcc: otro@example.org", Decimal(5), Decimal(4), "t.example", "")
    asyncio.run(CanalEmail(SMTP, conectar=lambda _: servidor).enviar(hostil))
    mensaje = servidor.mensaje
    assert mensaje is not None
    assert mensaje["Bcc"] is None
    assert list(mensaje.keys()).count("To") == 1


def test_email_un_fallo_del_servidor_no_ensena_la_clave() -> None:
    servidor = SMTPFalso(falla_en="login")
    with pytest.raises(ErrorAviso) as error:
        asyncio.run(CanalEmail(SMTP, conectar=lambda _: servidor).enviar(AVISO))
    assert CLAVE not in str(error.value)
    assert "quit" in servidor.llamadas  # la conexión se cierra también si falla


def test_email_un_fallo_de_conexion_es_un_error_de_aviso() -> None:
    def conectar(_: ConfigSMTP) -> SMTPFalso:
        raise OSError(f"no se pudo conectar con la clave {CLAVE}")

    with pytest.raises(ErrorAviso) as error:
        asyncio.run(CanalEmail(SMTP, conectar=conectar).enviar(AVISO))
    assert CLAVE not in str(error.value)


def test_la_config_smtp_no_ensena_la_clave_al_imprimirse() -> None:
    assert CLAVE not in repr(SMTP)


@pytest.mark.parametrize(
    "cambios",
    [
        {"servidor": ""},
        {"servidor": "smtp.example\r\nX: y"},
        {"puerto": 0},
        {"puerto": 70000},
        {"destinatario": "sin-arroba"},
        {"destinatario": "a@example.org\r\nBcc: b@example.org"},
        {"remitente": "dos@example.org, tres@example.org"},
        {"seguridad": "ninguna"},
        {"clave": ""},
    ],
)
def test_config_smtp_rechaza_valores_raros(cambios: dict[str, Any]) -> None:
    valores: dict[str, Any] = {
        "servidor": "smtp.example",
        "puerto": 587,
        "usuario": "u@example.org",
        "clave": CLAVE,
        "remitente": "u@example.org",
        "destinatario": "j@example.org",
        "seguridad": "starttls",
    }
    with pytest.raises(CanalNoConfigurado) as error:
        ConfigSMTP(**{**valores, **cambios})
    assert CLAVE not in str(error.value)


# --- Telegram ---------------------------------------------------------------------------------


def test_telegram_envia_texto_plano_al_chat() -> None:
    peticiones: list[tuple[str, bytes]] = []

    def enviar(url: str, datos: bytes) -> tuple[int, bytes]:
        peticiones.append((url, datos))
        return 200, b'{"ok": true}'

    asyncio.run(CanalTelegram(TOKEN, "-100123", enviar=enviar).enviar(AVISO))

    [(url, datos)] = peticiones
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    cuerpo_json = json.loads(datos)
    assert cuerpo_json["chat_id"] == "-100123"
    assert "27,50 €" in cuerpo_json["text"]
    # Sin `parse_mode`: el nombre del producto no puede meter formato ni enlaces.
    assert "parse_mode" not in cuerpo_json
    assert cuerpo_json["disable_web_page_preview"] is True


@pytest.mark.parametrize(
    ("estado", "respuesta"),
    [
        (401, b'{"ok": false, "description": "Unauthorized"}'),
        (200, b'{"ok": false}'),
        (200, b"<html>"),
    ],
)
def test_telegram_una_respuesta_mala_es_un_error_sin_el_token(
    estado: int, respuesta: bytes
) -> None:
    canal = CanalTelegram(TOKEN, "42", enviar=lambda *_: (estado, respuesta))
    with pytest.raises(ErrorAviso) as error:
        asyncio.run(canal.enviar(AVISO))
    assert TOKEN not in str(error.value)


def test_telegram_un_fallo_de_red_no_ensena_el_token() -> None:
    def enviar(url: str, _: bytes) -> tuple[int, bytes]:
        raise OSError(f"fallo al abrir {url}")

    with pytest.raises(ErrorAviso) as error:
        asyncio.run(CanalTelegram(TOKEN, "42", enviar=enviar).enviar(AVISO))
    assert TOKEN not in str(error.value)
    assert error.value.__cause__ is None  # la causa llevaría la URL con el token


@pytest.mark.parametrize(
    ("token", "chat"),
    [
        ("", "42"),
        ("sin-formato", "42"),
        ("123:corto", "42"),
        (TOKEN + "/../getMe?x=", "42"),
        (TOKEN, ""),
        (TOKEN, "42; rm"),
        (TOKEN, "@a"),
    ],
)
def test_telegram_rechaza_token_o_chat_con_mal_formato(token: str, chat: str) -> None:
    with pytest.raises(CanalNoConfigurado) as error:
        CanalTelegram(token, chat)
    assert token == "" or token not in str(error.value)


def test_telegram_recorta_el_texto_al_limite_de_la_api() -> None:
    vistos: list[bytes] = []

    def enviar(_: str, datos: bytes) -> tuple[int, bytes]:
        vistos.append(datos)
        return 200, b'{"ok": true}'

    largo = Aviso("x" * 9000, Decimal(5), Decimal(4), "t.example", "")
    asyncio.run(CanalTelegram(TOKEN, "@canal_precia", enviar=enviar).enviar(largo))
    assert len(json.loads(vistos[0])["text"]) <= 4096


# --- Canales según la configuración -----------------------------------------------------------


def test_canales_desde_el_entorno() -> None:
    entorno = {
        "PRECIA_SMTP_SERVIDOR": "smtp.example",
        "PRECIA_SMTP_USUARIO": "u@example.org",
        "PRECIA_SMTP_CLAVE": CLAVE,
        "PRECIA_ALERTAS_EMAIL": "j@example.org",
        "PRECIA_TELEGRAM_TOKEN": TOKEN,
        "PRECIA_TELEGRAM_CHAT": "42",
    }
    canales, motivos = canales_desde(entorno.get)
    assert set(canales) == {"email", "telegram"}
    assert motivos == {}


def test_sin_configurar_no_hay_canales_y_se_dice_que_falta() -> None:
    canales, motivos = canales_desde(lambda _: None)
    assert canales == {}
    assert "PRECIA_SMTP_SERVIDOR" in motivos["email"]
    assert "PRECIA_TELEGRAM_TOKEN" in motivos["telegram"]


def test_una_configuracion_mala_no_ensena_el_secreto() -> None:
    entorno = {
        "PRECIA_SMTP_SERVIDOR": "smtp.example",
        "PRECIA_SMTP_USUARIO": "u@example.org",
        "PRECIA_SMTP_CLAVE": CLAVE,
        "PRECIA_ALERTAS_EMAIL": "sin-arroba",
        "PRECIA_TELEGRAM_TOKEN": "mal-token-secreto",
        "PRECIA_TELEGRAM_CHAT": "42",
    }
    canales, motivos = canales_desde(entorno.get)
    assert canales == {}
    assert CLAVE not in motivos["email"] and "mal-token-secreto" not in motivos["telegram"]


def test_el_puerto_y_la_seguridad_se_leen_del_entorno() -> None:
    entorno = {
        "PRECIA_SMTP_SERVIDOR": "smtp.example",
        "PRECIA_SMTP_PUERTO": "465",
        "PRECIA_SMTP_SEGURIDAD": "SSL",
        "PRECIA_SMTP_USUARIO": "u@example.org",
        "PRECIA_SMTP_CLAVE": CLAVE,
        "PRECIA_SMTP_REMITENTE": "avisos@example.org",
        "PRECIA_ALERTAS_EMAIL": "j@example.org",
    }
    canales, _ = canales_desde(entorno.get)
    email = canales["email"]
    assert isinstance(email, CanalEmail)
    assert (email.config.puerto, email.config.seguridad) == (465, "ssl")
    assert email.config.remitente == "avisos@example.org"
