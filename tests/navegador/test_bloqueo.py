"""Detección de CAPTCHA y bloqueos anti-bot. Ante cualquiera de ellos se para: nunca se evaden."""

from pathlib import Path

import pytest

from precia.navegador.bloqueo import detectar_bloqueo
from tests.cronometro import segundos_de_cpu
from tests.navegador.falsos import pagina

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
URL = "https://www.recambios-ejemplo.example/buscar?q=1K0698151"


def fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("nombre", "estado", "esperado"),
    [
        ("cloudflare_just_a_moment.html", 403, "Cloudflare"),
        ("cloudflare_just_a_moment.html", 503, "Cloudflare"),
        # Cloudflare a veces sirve el desafío con un 200.
        ("cloudflare_just_a_moment.html", 200, "Cloudflare"),
        ("cloudflare_bloqueado.html", 403, "Cloudflare"),
        ("recaptcha_trafico_inusual.html", 200, "reCAPTCHA"),
        ("recaptcha_trafico_inusual.html", 403, "reCAPTCHA"),
        ("hcaptcha_desafio.html", 403, "hCaptcha"),
        ("turnstile_desafio.html", 200, "Turnstile"),
        ("datadome.html", 403, "DataDome"),
    ],
)
def test_detecta_desafios(nombre: str, estado: int, esperado: str) -> None:
    motivo = detectar_bloqueo(pagina(URL, fixture(nombre), estado=estado))
    assert motivo is not None
    assert esperado in motivo


@pytest.mark.parametrize(
    ("nombre", "estado"),
    [
        # reCAPTCHA v3 en todas las páginas y un formulario de contacto con reCAPTCHA v2:
        # es una ficha normal, no un bloqueo.
        ("ficha_con_recaptcha.html", 200),
        ("listado_desguace.html", 200),
        ("mantenimiento_503.html", 503),
        ("listado_desguace.html", 404),
    ],
)
def test_paginas_normales_no_son_bloqueo(nombre: str, estado: int) -> None:
    assert detectar_bloqueo(pagina(URL, fixture(nombre), estado=estado)) is None


def test_429_siempre_es_bloqueo() -> None:
    motivo = detectar_bloqueo(pagina(URL, "Too Many Requests", estado=429))
    assert motivo is not None and "429" in motivo


def test_403_con_texto_de_desafio() -> None:
    html = (
        "<html><body><h1>Access Denied</h1><p>Reference #18.4f2a3b17.1729339200</p></body></html>"
    )
    assert detectar_bloqueo(pagina(URL, html, estado=403)) is not None


def test_403_corto_sin_desafio_es_rechazo_seco() -> None:
    # Antes no contaba como bloqueo; la primera prueba real (nota 15) mostró que así es como
    # rechazan muchas tiendas a un cliente automático: un 403 pequeño y sin ficha.
    html = "<html><body><h1>403 Prohibido</h1><p>No tienes permiso para ver esto.</p></body></html>"
    motivo = detectar_bloqueo(pagina(URL, html, estado=403))
    assert motivo == "La tienda rechaza el acceso automático (HTTP 403)."


def test_frase_de_desafio_en_una_ficha_normal_no_basta() -> None:
    # Un 200 sin widget de CAPTCHA que menciona robots (un robot de cocina, por ejemplo).
    html = "<html><title>Robot aspirador</title><body>¿Eres un robot? No, un cliente.</body></html>"
    assert detectar_bloqueo(pagina(URL, html)) is None


def test_html_vacio() -> None:
    assert detectar_bloqueo(pagina(URL, "")) is None


# --- HTML hostil: la detección corre en el bucle de eventos y no puede tardar segundos -----------


def _cronometrar(html: str, estado: int, limite: float = 2) -> float:
    """Segundos de CPU de la detección: el mejor de hasta 3 intentos (bug 6)."""
    return segundos_de_cpu(lambda: detectar_bloqueo(pagina(URL, html, estado=estado)), limite)


def test_muchos_atributos_class_sin_cerrar_no_disparan_el_tiempo() -> None:
    # Con un patrón `class=...[^"'>]*\bg-recaptcha` cada «class=» recorría el resto del texto:
    # 200 KiB costaban minutos. Tiene que ser lineal.
    html = "<p>Verify you are human</p><div " + "class=" * 35_000
    assert _cronometrar(html, 403) < 2


def test_muchas_etiquetas_title_sin_cerrar_no_disparan_el_tiempo() -> None:
    html = "<title>" * 9_000
    assert _cronometrar(html, 200) < 2


def test_widget_en_class_sin_comillas() -> None:
    html = "<p>Verify you are human</p><div class=g-recaptcha data-sitekey=x></div>"
    motivo = detectar_bloqueo(pagina(URL, html, estado=200))
    assert motivo is not None and "reCAPTCHA" in motivo


def test_titulo_cloudflare_tras_una_etiqueta_title_rota() -> None:
    html = "<title class='x'>Just a moment...</title><p>cloudflare</p>"
    assert detectar_bloqueo(pagina(URL, html, estado=503)) is not None
