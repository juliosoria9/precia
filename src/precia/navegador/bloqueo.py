"""Detección de CAPTCHA y bloqueos anti-bot.

Solo se detecta para **parar** (el navegador lanza `Bloqueado` y no reintenta) o, con un proxy
configurado, para repetir la petición **una vez** desde otra IP (`navegador.con_proxy`). Nunca se
intenta resolver un desafío ni un CAPTCHA (nota 07, scraping ético).

Un widget de CAPTCHA por sí solo no es un bloqueo: muchas tiendas cargan reCAPTCHA v3 en todas las
páginas o lo ponen en el formulario de contacto de la ficha. Cuenta como bloqueo si llega con un
403/503 o si la página habla de comprobar que eres humano.

Lo mismo con los WAF y anti-bots (Cloudflare, Imperva, PerimeterX, DataDome, Kasada, AWS WAF):
inyectan su JavaScript en **todas** las páginas de la tienda, así que solo cuentan las marcas que
aparecen en su página de bloqueo o de desafío, nunca las del sensor que acompaña a una ficha normal.

Un **rechazo seco** (nota 15, sección A) es un 401/403/405/429/451 de HTML pequeño y sin señales de
producto: así contestan muchas tiendas a un cliente automático, sin desafío que reconocer.

Una página con señales de producto (JSON-LD `Product`, microdatos, precios) nunca es un bloqueo
salvo con un 429: las páginas de bloqueo no las traen, y una ficha buena sí puede llevar marcas de
un anti-bot (la CSP, una reseña, el reCAPTCHA de su formulario de contacto).

JSON y XML (APIs públicas, descriptor OpenSearch) no se analizan como HTML: la descripción de un
producto puede decir «access denied» y no por eso es un muro.
"""

import html
import re
from urllib.parse import urlsplit

from precia.navegador.tipos import Pagina

_MOTIVO_CLOUDFLARE = "Desafío anti-bot de Cloudflare («Just a moment...»)."
_MOTIVO_DATADOME = "Desafío anti-bot de DataDome."
_MOTIVO_PERIMETERX = "Desafío anti-bot de PerimeterX (HUMAN)."
_MOTIVO_INCAPSULA = "Desafío anti-bot de Imperva (Incapsula)."
_MOTIVO_IMPERVA_ABP = "Bloqueo anti-bot de Imperva (Advanced Bot Protection)."
_MOTIVO_AWS_WAF = "Desafío anti-bot de AWS WAF."
_MOTIVO_SUCURI = "Bloqueo del WAF de Sucuri."
_MOTIVO_AKAMAI = "Bloqueo del WAF de Akamai («Access Denied»)."
_MOTIVO_CLOUDFRONT = "Bloqueo del WAF de AWS (CloudFront «Request blocked»)."
_MOTIVO_F5 = "Bloqueo del WAF de F5 (BIG-IP ASM)."
_MOTIVO_KASADA = "Desafío anti-bot de Kasada."
MOTIVO_LIMITE = "La tienda limita las peticiones (HTTP 429)."

# Marcas que solo están en la página de desafío o de bloqueo de cada proveedor, no en su sensor.
_PROVEEDORES = (
    ("captcha-delivery.com", _MOTIVO_DATADOME),
    ("px-captcha", _MOTIVO_PERIMETERX),
    ("captcha.px-cdn.net", _MOTIVO_PERIMETERX),
    ("captcha.px-cloud.net", _MOTIVO_PERIMETERX),
    ("incapsula incident id", _MOTIVO_INCAPSULA),
    ("_incapsula_resource?cwudnsai", _MOTIVO_INCAPSULA),
    # Desafío de Cloudflare. No vale «/cdn-cgi/challenge-platform/» sin más: Cloudflare inyecta
    # `/cdn-cgi/challenge-platform/scripts/jsd/main.js` en las páginas normales de sus clientes.
    ("_cf_chl_opt", _MOTIVO_CLOUDFLARE),
    # `window.gokuProps` solo está en las páginas de CAPTCHA o desafío de AWS WAF: su
    # `challenge.js` y `AwsWafIntegration` también los cargan las páginas normales.
    ("gokuprops", _MOTIVO_AWS_WAF),
    ("sucuri_cloudproxy_js", _MOTIVO_SUCURI),
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
_ESTADOS_RECHAZO = frozenset({401, 403, 405, 429, 451})
_MAXIMO_SECO = 30 * 1024
"""Bytes: por debajo, un error sin ficha es un rechazo; por encima, una plantilla de la tienda."""
_LIMITE_DATOS = 64 * 1024
"""En JSON/XML solo se mira el principio: la respuesta de bloqueo de DataDome es diminuta."""
_TIPOS_HTML = frozenset({"text/html", "application/xhtml+xml", "text/plain", ""})
"""Lo que se analiza como página. El resto (JSON, XML) va por `_bloqueo_en_datos`."""
# Señales de que la respuesta es una ficha o un listado aunque llegue con un 4xx. Los patrones
# empiezan por un literal y sus cuantificadores son posesivos: lineales con cualquier entrada.
_PRODUCTO_JSONLD = re.compile(
    r'"@type"\s*+:\s*+\[?\s*+"(?:product|productgroup|individualproduct|offer|aggregateoffer)"'
)
# Un precio empieza en un número suelto: «7eur/» pegado a letras es un trozo de un token base64 (el
# del desafío de Cloudflare, por ejemplo), y tomarlo por precio dejaría pasar el desafío. El número
# no puede ir tras letra, dígito, «.» ni «,»: así cada cifra se recorre una sola vez (lineal).
_PRECIO = re.compile(r"(?<![\w.,])\d++(?:[.,]\d++)*+\s*+(?:€|£|eur\b|euros?\b)|[€£]\s*+\d")
_MARCAS_PRODUCTO = (
    "schema.org/product",
    "schema.org/offer",
    'itemprop="price"',
    "itemprop='price'",
    "itemprop=price",
    "product:price:amount",
    "og:price:amount",
)


def detectar_bloqueo(pagina: Pagina) -> str | None:
    """Motivo del bloqueo (se puede enseñar al usuario) o `None` si la página es normal."""
    if pagina.tipo_contenido not in _TIPOS_HTML:
        return _bloqueo_en_datos(pagina)
    contenido = html.unescape(pagina.html).lower()
    if _es_producto(contenido):
        # Una ficha o un listado con precios no es un muro, llegue con el código que llegue: las
        # páginas de bloqueo de los proveedores no traen JSON-LD de producto ni precios. Las marcas
        # sí pueden estar en una ficha buena: la CSP de un <meta> que permite
        # `*.captcha-delivery.com`, una reseña que cita un «Incapsula incident ID», unas
        # instrucciones «press & hold» o el reCAPTCHA del formulario de contacto junto a una
        # descripción que dice «control de seguridad». Solo el 429 se respeta siempre.
        return MOTIVO_LIMITE if pagina.estado == 429 else None
    titulo = _titulo(contenido)

    # Primero el proveedor: un 429 de Kasada dice más con su nombre que con el código.
    motivo = _proveedor(contenido, titulo, pagina.estado)
    if motivo is not None:
        return motivo
    if pagina.estado == 429:
        return MOTIVO_LIMITE

    habla_de_desafio = any(f in contenido for f in _FRASES_DESAFIO)
    sospechosa = pagina.estado in _ESTADOS_DESAFIO or habla_de_desafio
    nombre = _widget(contenido) if sospechosa else None
    if nombre is not None:
        return f"La tienda pide resolver un CAPTCHA ({nombre})."
    if pagina.estado in _ESTADOS_DESAFIO and habla_de_desafio:
        return f"La tienda bloquea el acceso automático (HTTP {pagina.estado})."
    if _rechazo_seco(pagina):
        return f"La tienda rechaza el acceso automático (HTTP {pagina.estado})."
    return None


def _proveedor(contenido: str, titulo: str, estado: int) -> str | None:
    """Firma de un WAF o anti-bot concreto, sea cual sea el código de estado."""
    if "cloudflare" in contenido and ("just a moment" in titulo or "attention required" in titulo):
        return _MOTIVO_CLOUDFLARE
    if "/cdn-cgi/challenge-platform/" in contenido and "/orchestrate/" in contenido:
        return _MOTIVO_CLOUDFLARE
    for marca, motivo in _PROVEEDORES:
        if marca in contenido:
            return motivo
    if "access denied" in titulo and "reference #" in contenido:
        return _MOTIVO_AKAMAI
    # La misma plantilla de CloudFront sirve para los fallos del origen (502, 504): solo es un
    # bloqueo si es un 403 o si dice «Request blocked».
    if "generated by cloudfront" in contenido and (estado == 403 or "request blocked" in contenido):
        return _MOTIVO_CLOUDFRONT
    # Imperva Advanced Bot Protection (antes Distil Networks). Suele responder con un 200.
    if "pardon our interruption" in titulo and "you were a bot" in contenido:
        return _MOTIVO_IMPERVA_ABP
    if "sucuri website firewall" in titulo:
        return _MOTIVO_SUCURI
    if "the requested url was rejected" in contenido and "support id" in contenido:
        return _MOTIVO_F5
    # El sensor de Kasada (`KPSDK.configure`, `p.js`) va en todas las páginas; `ips.js` es el
    # script de su página de desafío.
    if "kpsdk" in contenido and "/ips.js" in contenido:
        return _MOTIVO_KASADA
    if "press & hold" in contenido and "human" in contenido:
        return _MOTIVO_PERIMETERX
    # Página intermedia de Imperva: sin título, diminuta y solo con su script. Las páginas normales
    # también cargan `/_Incapsula_Resource?SWJIYLWA=...`, pero llevan su título y su contenido. Sin
    # `<script` no es la página intermedia: un robots.txt con `Disallow: /_Incapsula_Resource` es
    # texto plano, sin título y pequeño.
    if (
        "_incapsula_resource" in contenido
        and "<script" in contenido
        and not titulo
        and len(contenido) < _MAXIMO_SECO
    ):
        return _MOTIVO_INCAPSULA
    return None


def _bloqueo_en_datos(pagina: Pagina) -> str | None:
    """JSON o XML: solo el límite de peticiones y el bloqueo de DataDome a una llamada de API."""
    if pagina.estado == 429:
        return MOTIVO_LIMITE
    if pagina.estado >= 400 and "captcha-delivery.com" in pagina.html[:_LIMITE_DATOS].lower():
        return _MOTIVO_DATADOME
    return None


def _rechazo_seco(pagina: Pagina) -> bool:
    """Un 401/403/405/429/451 pequeño y sin ficha: la tienda no deja pasar al cliente automático."""
    if pagina.estado not in _ESTADOS_RECHAZO:
        return False
    # El robots.txt tiene su propia semántica (RFC 9309): un 401/403 ahí es «no hay robots.txt».
    if urlsplit(pagina.url).path == "/robots.txt":
        return False
    # Las señales de producto ya se han mirado en `detectar_bloqueo`. El tamaño se cuenta en bytes,
    # como dice la nota 15; `len(str)` nunca supera a sus bytes en UTF-8: si ya es grande, no hace
    # falta codificar nada.
    return len(pagina.html) < _MAXIMO_SECO and len(pagina.html.encode("utf-8")) < _MAXIMO_SECO


def _es_producto(contenido: str) -> bool:
    """La página trae una ficha o precios (JSON-LD `Product`, microdatos, OpenGraph o «€»)."""
    if any(marca in contenido for marca in _MARCAS_PRODUCTO):
        return True
    return _PRODUCTO_JSONLD.search(contenido) is not None or _PRECIO.search(contenido) is not None


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
