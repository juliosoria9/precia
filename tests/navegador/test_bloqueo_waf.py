"""Firmas de WAF y rechazos «secos» (nota 15, sección A).

Solo se reconocen para **parar** y explicar el motivo: nunca se intenta superar el bloqueo.
Las fixtures están escritas a mano imitando las páginas reales de cada proveedor; los casos
negativos son igual de importantes, porque un falso bloqueo deja una tienda sin comparar.
"""

import time
from pathlib import Path

import pytest

from precia.navegador.bloqueo import detectar_bloqueo
from tests.navegador.falsos import pagina

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
URL = "https://www.recambios-ejemplo.example/buscar?q=1K0698151"
ROBOTS = "https://www.recambios-ejemplo.example/robots.txt"


def fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


# --- Firmas de WAF y anti-bot -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nombre", "estado", "esperado"),
    [
        ("akamai_access_denied.html", 403, "Akamai"),
        ("cloudfront_request_blocked.html", 403, "CloudFront"),
        ("cloudfront_pais_bloqueado.html", 403, "CloudFront"),
        # La acción CAPTCHA de AWS WAF responde 405; la de desafío, 202.
        ("aws_waf_captcha.html", 405, "AWS WAF"),
        ("aws_waf_captcha.html", 202, "AWS WAF"),
        ("incapsula_incidente.html", 403, "Incapsula"),
        ("incapsula_incidente.html", 200, "Incapsula"),
        ("incapsula_interstitial.html", 200, "Incapsula"),
        ("perimeterx_press_hold.html", 403, "PerimeterX"),
        ("datadome.html", 403, "DataDome"),
        ("sucuri_firewall.html", 403, "Sucuri"),
        # F5 BIG-IP ASM responde por defecto con un 200.
        ("f5_rechazada.html", 200, "F5"),
        ("kasada_429.html", 429, "Kasada"),
        ("kasada_429.html", 403, "Kasada"),
    ],
)
def test_detecta_firmas_de_waf(nombre: str, estado: int, esperado: str) -> None:
    motivo = detectar_bloqueo(pagina(URL, fixture(nombre), estado=estado))
    assert motivo is not None
    assert esperado in motivo


def test_error_de_origen_de_cloudfront_no_es_bloqueo() -> None:
    # Misma plantilla («The request could not be satisfied»), pero es la tienda caída (504):
    # se trata como un error normal, no como un muro anti-bot.
    assert detectar_bloqueo(pagina(URL, fixture("cloudfront_504_origen.html"), estado=504)) is None


def test_sensores_anti_bot_en_una_ficha_normal_no_son_bloqueo() -> None:
    # Cloudflare (jsd), Incapsula, PerimeterX, DataDome, Kasada y AWS WAF inyectan su JavaScript
    # en todas las páginas de la tienda: su presencia sola no significa que nos bloqueen.
    html = fixture("ficha_con_sensores_antibot.html")
    assert detectar_bloqueo(pagina(URL, html, estado=200)) is None


# --- Rechazos secos -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nombre", "estado"),
    [
        ("ebay_403_seco.html", 403),
        ("nextjs_405_cloudfront.html", 405),
        ("pagina_404.html", 401),
        ("pagina_404.html", 451),
    ],
)
def test_rechazo_seco(nombre: str, estado: int) -> None:
    motivo = detectar_bloqueo(pagina(URL, fixture(nombre), estado=estado))
    assert motivo == f"La tienda rechaza el acceso automático (HTTP {estado})."


def test_403_vacio_es_rechazo_seco() -> None:
    motivo = detectar_bloqueo(pagina(URL, "", estado=403))
    assert motivo is not None and "HTTP 403" in motivo


def test_un_404_nunca_es_rechazo_seco() -> None:
    assert detectar_bloqueo(pagina(URL, fixture("pagina_404.html"), estado=404)) is None


def test_403_con_json_ld_product_no_es_bloqueo() -> None:
    # «No disponible en tu país» con la ficha completa: la tienda responde, solo que con 403.
    html = fixture("geobloqueo_403_producto.html")
    assert detectar_bloqueo(pagina(URL, html, estado=403)) is None


def test_403_con_frase_de_desafio_y_json_ld_product_no_es_bloqueo() -> None:
    html = fixture("geobloqueo_403_producto.html").replace(
        "Este producto", "Acceso denegado: este producto"
    )
    assert detectar_bloqueo(pagina(URL, html, estado=403)) is None


@pytest.mark.parametrize(
    "senal",
    [
        "<p class='precio'>29,95 €</p>",
        "<p>Precio: € 29.95</p>",
        "<p>29.95 EUR</p>",
        '<span itemprop="price" content="29.95">29,95</span>',
        '<meta property="product:price:amount" content="29.95">',
        '<div itemscope itemtype="https://schema.org/Product"></div>',
    ],
)
def test_403_pequeno_con_precio_no_es_rechazo_seco(senal: str) -> None:
    html = f"<html><body><h1>No disponible en tu país</h1>{senal}</body></html>"
    assert detectar_bloqueo(pagina(URL, html, estado=403)) is None


def test_403_grande_no_es_rechazo_seco() -> None:
    # 30 KB o más: es una página de verdad (una plantilla de error de la tienda con su menú).
    html = "<html><body><h1>Prohibido</h1>" + "<p>texto de relleno</p>" * 2_000 + "</body></html>"
    assert len(html.encode("utf-8")) >= 30 * 1024
    assert detectar_bloqueo(pagina(URL, html, estado=403)) is None


def test_el_limite_de_tamano_cuenta_bytes() -> None:
    # 20 000 «ñ» son 40 000 bytes en UTF-8: no es un cuerpo pequeño aunque sean pocos caracteres.
    html = "<p>" + "ñ" * 20_000 + "</p>"
    assert len(html) < 30 * 1024 <= len(html.encode("utf-8"))
    assert detectar_bloqueo(pagina(URL, html, estado=403)) is None


def test_403_en_el_robots_txt_no_es_rechazo_seco() -> None:
    # El robots.txt tiene su propia semántica (RFC 9309: un 401/403 es «sin robots.txt»).
    robots = pagina(ROBOTS, "User-agent: *\nDisallow: /\n", estado=403)
    assert detectar_bloqueo(robots) is None


def test_403_seco_en_texto_plano() -> None:
    motivo = detectar_bloqueo(pagina(URL, "Forbidden", estado=403, tipo="text/plain"))
    assert motivo is not None and "HTTP 403" in motivo


# --- JSON y XML: no se analizan como HTML -------------------------------------------------------


def test_json_de_una_api_con_frases_de_desafio_no_es_bloqueo() -> None:
    api = fixture("api_woocommerce_productos.json")
    assert detectar_bloqueo(pagina(URL, api, estado=200, tipo="application/json")) is None


def test_json_403_de_una_api_no_es_rechazo_seco() -> None:
    api = (
        '{"code":"rest_forbidden","message":"Lo siento, no tienes permisos.","data":{"status":403}}'
    )
    assert detectar_bloqueo(pagina(URL, api, estado=403, tipo="application/json")) is None


def test_json_403_de_datadome_es_bloqueo() -> None:
    # DataDome responde así a las llamadas XHR/fetch que bloquea.
    api = fixture("datadome_api_403.json")
    motivo = detectar_bloqueo(pagina(URL, api, estado=403, tipo="application/json"))
    assert motivo is not None and "DataDome" in motivo


def test_json_429_sigue_siendo_limite() -> None:
    motivo = detectar_bloqueo(pagina(URL, "{}", estado=429, tipo="application/json"))
    assert motivo is not None and "429" in motivo


def test_xml_opensearch_no_es_bloqueo() -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        "<ShortName>Access denied</ShortName>"
        '<Url type="text/html" template="https://www.recambios-ejemplo.example/buscar?q={searchTerms}"/>'
        "</OpenSearchDescription>"
    )
    tipo = "application/opensearchdescription+xml"
    assert detectar_bloqueo(pagina(URL, xml, estado=200, tipo=tipo)) is None


# --- Entradas hostiles: lineal y rápido ---------------------------------------------------------


def _cronometrar(html: str, estado: int, tipo: str = "text/html") -> float:
    inicio = time.perf_counter()
    detectar_bloqueo(pagina(URL, html, estado=estado, tipo=tipo))
    return time.perf_counter() - inicio


@pytest.mark.parametrize(
    "hostil",
    [
        # Precios: muchos dígitos y espacios sin «€» detrás.
        "Access denied " + "1" + " " * 2_000_000,
        "Access denied " + "1 " * 1_000_000,
        "Access denied " + "€ " * 1_000_000,
        # Cifras encadenadas con «.» y «,» sin «€» al final: cada cifra se recorre una vez.
        "Access denied " + "1." * 1_000_000,
        "Access denied " + "12," * 700_000 + " eu",
        # JSON-LD: muchos «"@type"» seguidos de espacios sin «Product».
        "Access denied " + '"@type"   :   [  ' * 200_000,
        # Marcas de WAF partidas mil veces.
        "Access denied " + "reference #" * 200_000 + "generated by cloud" * 100_000,
    ],
    ids=[
        "digito-y-espacios",
        "digitos-espaciados",
        "euros-espaciados",
        "cifras-con-punto",
        "cifras-con-coma",
        "type-sin-product",
        "marcas",
    ],
)
def test_html_hostil_no_dispara_el_tiempo(hostil: str) -> None:
    assert _cronometrar(hostil, 403) < 2


def test_json_hostil_no_dispara_el_tiempo() -> None:
    hostil = '{"url":"' + "captcha-delivery" * 300_000 + '"}'
    assert _cronometrar(hostil, 403, "application/json") < 2


# --- Revisión adversarial: páginas buenas que no pueden tomarse por un muro ----------------------


def test_robots_txt_que_excluye_el_recurso_de_incapsula_no_es_bloqueo() -> None:
    # Muchas tiendas detrás de Imperva ponen `Disallow: /_Incapsula_Resource` en su robots.txt:
    # es texto plano, sin título y pequeño, pero no es la página intermedia (no hay ningún script).
    robots = pagina(ROBOTS, fixture("robots_imperva.txt"), estado=200, tipo="text/plain")
    assert detectar_bloqueo(robots) is None


def test_csp_con_los_dominios_del_captcha_en_una_ficha_no_es_bloqueo() -> None:
    # DataDome y HUMAN piden a sus clientes permitir `*.captcha-delivery.com` y `captcha.px-*` en
    # la CSP; si va en un <meta>, el dominio del CAPTCHA aparece en todas las fichas.
    html = fixture("ficha_csp_antibot.html")
    assert detectar_bloqueo(pagina(URL, html, estado=200)) is None


def test_resena_que_cita_un_incidente_de_incapsula_no_es_bloqueo() -> None:
    html = fixture("ficha_resena_denied.html").replace(
        "had to phone them.",
        "had to phone them. The checkout said 'Incapsula incident ID: 1234-5678' twice.",
    )
    assert detectar_bloqueo(pagina(URL, html, estado=200)) is None


def test_instrucciones_press_and_hold_en_una_ficha_no_son_perimeterx() -> None:
    # «Press & Hold» en las instrucciones y «humana»/«Recursos Humanos» en la página.
    html = fixture("ficha_press_hold.html")
    assert detectar_bloqueo(pagina(URL, html, estado=200)) is None


@pytest.mark.parametrize("estado", [200, 403])
def test_ficha_con_recaptcha_y_frase_de_seguridad_no_es_captcha(estado: int) -> None:
    # Formulario de contacto con reCAPTCHA v2 y una descripción que dice «control de seguridad»:
    # es una ficha (con JSON-LD y precio), no un CAPTCHA. Con 403 es el caso del geobloqueo.
    html = fixture("ficha_con_recaptcha.html").replace(
        "Compatible con VW Golf V",
        "Supera el control de seguridad de la ITV. Compatible con VW Golf V",
    )
    assert detectar_bloqueo(pagina(URL, html, estado=estado)) is None


def test_un_desafio_real_sigue_detectandose() -> None:
    # Contrapeso del anterior: una página de desafío real no trae JSON-LD ni microdatos de producto
    # y se sigue parando aunque la página hable de comprobar que eres humano.
    html = fixture("perimeterx_press_hold.html")
    motivo = detectar_bloqueo(pagina(URL, html, estado=403))
    assert motivo is not None and "PerimeterX" in motivo


def test_un_token_opaco_del_desafio_no_cuenta_como_precio() -> None:
    # Los tokens del desafío son base64: al pasarlo a minúsculas puede salir «7eur/» o «4€» pegado
    # a letras. Un precio empieza en un número suelto; si no, el desafío pasaría por una ficha.
    html = fixture("cloudflare_just_a_moment.html").replace(
        "cH: 'Zx9.aBcDeFgHiJkLmNoPqRsTuVwXyZ'", "cH: 'Zx9.aB7EuR/q+Lm3eur=Nk'"
    )
    assert "7EuR/" in html
    motivo = detectar_bloqueo(pagina(URL, html, estado=403))
    assert motivo is not None and "Cloudflare" in motivo


@pytest.mark.parametrize("estado", [200, 403])
def test_imperva_advanced_bot_protection(estado: int) -> None:
    # Antiguo Distil Networks: «Pardon Our Interruption». Llega a menudo con un 200, así que ni el
    # rechazo seco ni la regla de «403 + frase» lo paraban y se analizaba como si fuera la tienda.
    html = fixture("imperva_pardon_interruption.html")
    motivo = detectar_bloqueo(pagina(URL, html, estado=estado))
    assert motivo is not None and "Imperva" in motivo


def test_titulo_pardon_our_interruption_en_una_ficha_no_basta() -> None:
    html = fixture("ficha_resena_denied.html").replace(
        "Brake pads front axle 1K0698151", "Pardon Our Interruption - Brake pads"
    )
    assert detectar_bloqueo(pagina(URL, html, estado=200)) is None
