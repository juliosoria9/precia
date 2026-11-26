"""Módulo propio de Google («mini-SERP», ADR-006): URL de búsqueda, bloqueo y resultados.

Está **desactivado por defecto** (ajuste `google_activado`): las condiciones de servicio de
Google prohíben el acceso automatizado a sus resultados (nota 08). Aquí no se descarga nada: la
página la pide el `navegador` (con su validación, su cortesía por dominio y su caché) y este
módulo solo construye la URL y lee el HTML.

**Medido en S06 (2026-11-24):** Google responde a PrecIA con su página de CAPTCHA (Chromium, en
directo y por el proxy) o con la de «habilita JavaScript» (`curl`). `motivo_bloqueo` reconoce
las dos y el CAPTCHA nunca se intenta resolver: el descubrimiento avisa y la búsqueda sigue con
las tiendas dadas de alta.

Lectura de resultados: no depende de las clases de Google, que cambian. Un resultado es un
enlace `http(s)` a otra web que lleva dentro un `<h3>` con su título. El HTML viene de fuera y
no es de fiar: solo salen URL absolutas `http(s)` con nombre de host (sin credenciales ni IP),
nunca las de Google ni los anuncios.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urldefrag, urlencode, urlsplit

from precia.extraccion.dom import Nodo, analizar
from precia.navegador.tipos import Pagina
from precia.nucleo.dominios import host_estricto

ORGANICO = "organico"
SHOPPING = "shopping"
URL_BUSQUEDA = "https://www.google.com/search"
UDM_SHOPPING = "28"
"""Valor de `udm` de la pestaña Shopping."""
MAX_CONSULTA = 500
MAX_RESULTADOS = 30
MAX_TITULO = 300
MAX_URL = 2048
MAX_ENLACES = 5_000
"""Enlaces que se miran como mucho en una página (cota ante un HTML hostil)."""

_PROPIOS = re.compile(
    r"(^|\.)(google\.[a-z.]{2,6}|googleusercontent\.com|gstatic\.com|googleadservices\.com"
    r"|doubleclick\.net|googlesyndication\.com|g\.co|goo\.gl|withgoogle\.com|blogger\.com)$"
)
"""Hosts de Google: sus enlaces (caché, mapas, cuenta, ayuda, anuncios) no son resultados."""
_ESPACIOS = re.compile(r"\s+")
_ESTADOS_BLOQUEO = frozenset({403, 429, 503})
_FRASES_CAPTCHA = ("tráfico inusual", "unusual traffic", "g-recaptcha", "/sorry/")
_FRASES_JS = ("enablejs", "habilita javascript", "enable javascript")


@dataclass(frozen=True, slots=True)
class ResultadoWeb:
    """Un resultado de la página de Google: a dónde lleva y con qué título."""

    url: str
    titulo: str
    origen: str
    """`ORGANICO` o `SHOPPING`."""


def url_busqueda(consulta: str, *, shopping: bool = False) -> str:
    """La URL de resultados de Google (en español y para España) de `consulta`."""
    texto = consulta.strip()
    if not texto or len(texto) > MAX_CONSULTA:
        raise ValueError(f"La consulta tiene que tener de 1 a {MAX_CONSULTA} caracteres")
    parametros = {"q": texto, "hl": "es", "gl": "es", "num": "20"}
    if shopping:
        parametros["udm"] = UDM_SHOPPING
    return f"{URL_BUSQUEDA}?{urlencode(parametros)}"


def motivo_bloqueo(pagina: Pagina) -> str | None:
    """Por qué la página no es de resultados (para enseñarlo), o `None` si lo parece.

    Nunca se intenta pasar el bloqueo: ni se resuelve el CAPTCHA ni se aceptan las cookies.
    """
    host = (urlsplit(pagina.url_final).hostname or "").lower()
    ruta = urlsplit(pagina.url_final).path
    if host.startswith("consent."):
        return "Google pide aceptar sus cookies antes de enseñar los resultados"
    contenido = pagina.html[:200_000].lower()
    if ruta.startswith("/sorry") or any(frase in contenido for frase in _FRASES_CAPTCHA):
        return "Google pide resolver un CAPTCHA (tráfico inusual)"
    if pagina.estado in _ESTADOS_BLOQUEO:
        return f"Google rechaza la consulta (HTTP {pagina.estado})"
    if any(frase in contenido for frase in _FRASES_JS) and "<h3" not in contenido:
        return "Google exige JavaScript y no ha enseñado ningún resultado"
    return None


def leer_resultados(html: str, *, origen: str) -> list[ResultadoWeb]:
    """Los resultados de la página, en orden y sin repetir la misma URL (sin su fragmento)."""
    resultados: list[ResultadoWeb] = []
    vistas: set[str] = set()
    mirados = 0
    for nodo in analizar(html).descendientes():
        if nodo.etiqueta != "a":
            continue
        mirados += 1
        if mirados > MAX_ENLACES or len(resultados) >= MAX_RESULTADOS:
            break
        url = _destino(nodo.atributo("href") or "")
        if url is None or url in vistas:
            continue
        titulo = _titulo(nodo)
        if not titulo:
            continue
        vistas.add(url)
        resultados.append(ResultadoWeb(url, titulo, origen))
    return resultados


def _titulo(enlace: Nodo) -> str:
    for nodo in enlace.descendientes():
        if nodo.etiqueta == "h3":
            texto = "".join(c if c.isprintable() else " " for c in nodo.texto())
            return _ESPACIOS.sub(" ", texto).strip()[:MAX_TITULO].strip()
    return ""


def _destino(href: str) -> str | None:
    """La URL externa a la que lleva el enlace, sin fragmento, o `None` si no es un resultado."""
    href = href.strip()
    if not href or len(href) > MAX_URL:
        return None
    if href.startswith("/url?"):
        # Enlace de redirección de Google: el destino va en `q` (o en `url`).
        parametros = parse_qs(urlsplit(href).query)
        href = (parametros.get("q") or parametros.get("url") or [""])[0].strip()
    try:
        partes = urlsplit(href)
        host = partes.hostname
        puerto = partes.port
    except ValueError:
        return None
    if partes.scheme not in ("http", "https") or not host:
        return None
    if partes.username is not None or partes.password is not None or puerto is not None:
        return None
    if host_estricto(host) is None or _PROPIOS.search(host.lower()):
        return None
    limpia = urldefrag(href).url
    if any(c.isspace() or not c.isprintable() for c in limpia):
        return None
    return limpia
