"""Detección de CAPTCHA y bloqueos anti-bot.

Solo se detecta para **parar** (el navegador lanza `Bloqueado` y no reintenta): nunca se intenta
resolver ni evadir un desafío (nota 07, scraping ético).

Un widget de CAPTCHA por sí solo no es un bloqueo: muchas tiendas cargan reCAPTCHA v3 en todas las
páginas o lo ponen en el formulario de contacto de la ficha. Cuenta como bloqueo si llega con un
403/503 o si la página habla de comprobar que eres humano.
"""

import html
import re

from precia.navegador.tipos import Pagina

# Proveedores cuyas páginas de desafío se reconocen sin dudas por su propio código.
_PROVEEDORES = (
    ("captcha-delivery.com", "Desafío anti-bot de DataDome."),
    ("px-captcha", "Desafío anti-bot de PerimeterX (HUMAN)."),
    ("_incapsula_resource", "Desafío anti-bot de Imperva (Incapsula)."),
    ("/cdn-cgi/challenge-platform/", "Desafío anti-bot de Cloudflare («Just a moment...»)."),
)
# Clases de los widgets. Se buscan dentro del valor de cada atributo `class`, que se recorre una
# sola vez: con un patrón que empezaba en cada «class=» y buscaba la clase hasta la comilla, una
# página con miles de «class=» sin cerrar tardaba minutos (y corre en el bucle de eventos).
_CLASES_WIDGET = (
    ("g-recaptcha", "reCAPTCHA"),
    ("h-captcha", "hCaptcha"),
    ("cf-turnstile", "Turnstile de Cloudflare"),
)
# `*+` es posesivo: el valor se consume de una vez y no hay retroceso.
_ATRIBUTO_CLASS = re.compile(r"""class\s*+=\s*+["']?([^"'>]*+)""")
_IFRAMES_WIDGET = (
    ("google.com/recaptcha/api2/anchor", "reCAPTCHA"),
    ("google.com/recaptcha/api2/bframe", "reCAPTCHA"),
    ("hcaptcha.com/captcha/", "hCaptcha"),
)
_FRASES_DESAFIO = (
    "verify you are human",
    "verifying you are human",
    "you are human",
    "eres humano",
    "no eres un robot",
    "eres un robot",
    "not a robot",
    "are you a robot",
    "unusual traffic",
    "tráfico inusual",
    "checking your browser",
    "comprobando tu navegador",
    "access denied",
    "acceso denegado",
    "you have been blocked",
    "has sido bloqueado",
    "security check",
    "control de seguridad",
)
_LIMITE_TITULO = 64 * 1024
_ESTADOS_DESAFIO = frozenset({403, 503})


def detectar_bloqueo(pagina: Pagina) -> str | None:
    """Motivo del bloqueo (se puede enseñar al usuario) o `None` si la página es normal."""
    if pagina.estado == 429:
        return "La tienda limita las peticiones (HTTP 429)."
    contenido = html.unescape(pagina.html).lower()
    titulo = _titulo(contenido)

    if "cloudflare" in contenido and ("just a moment" in titulo or "attention required" in titulo):
        return "Desafío anti-bot de Cloudflare («Just a moment...»)."
    for marca, motivo in _PROVEEDORES:
        if marca in contenido:
            return motivo

    habla_de_desafio = any(f in contenido for f in _FRASES_DESAFIO)
    sospechosa = pagina.estado in _ESTADOS_DESAFIO or habla_de_desafio
    nombre = _widget(contenido) if sospechosa else None
    if nombre is not None:
        return f"La tienda pide resolver un CAPTCHA ({nombre})."
    if pagina.estado in _ESTADOS_DESAFIO and habla_de_desafio:
        return f"La tienda bloquea el acceso automático (HTTP {pagina.estado})."
    return None


def _widget(contenido: str) -> str | None:
    for marca, nombre in _IFRAMES_WIDGET:
        if marca in contenido:
            return nombre
    for valor in _ATRIBUTO_CLASS.finditer(contenido):
        clases = valor[1].split()
        for clase, nombre in _CLASES_WIDGET:
            if clase in clases:
                return nombre
    return None


def _titulo(contenido: str) -> str:
    # Con `find` y no con `<title[^>]*>(.*?)</title`: la regex reintentaba desde cada «<title» sin
    # cerrar y miles de ellos costaban segundos. Si el primero no cierra, ninguno posterior lo hace.
    inicio = contenido.find("<title", 0, _LIMITE_TITULO)
    if inicio < 0:
        return ""
    apertura = contenido.find(">", inicio, _LIMITE_TITULO)
    if apertura < 0:
        return ""
    cierre = contenido.find("</title", apertura, _LIMITE_TITULO)
    if cierre < 0:
        return ""
    return " ".join(contenido[apertura + 1 : cierre].split())
