"""Resultados de la página de búsqueda de una tienda, con los selectores de su perfil.

Los enlaces vienen de una web no confiable: solo se aceptan URL `http`/`https` absolutas del
mismo dominio que el perfil (o su `www.`), sin usuario ni contraseña y en el puerto por defecto.
Así un anuncio, un enlace a otra tienda o un `https://tienda.es@otra.example/` no se cuelan en
los resultados ni hacen que el navegador visite otro sitio. La validación anti-SSRF de
`navegador.validar_url` se aplica después, al descargar.
"""

import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from precia.extraccion.dom import analizar
from precia.extraccion.precios import leer_precio
from precia.extraccion.selectores import extraer_valor, seleccionar
from precia.extraccion.tipos import Precio, ResultadoListado
from precia.navegador.tipos import Pagina
from precia.perfiles.tipos import Perfil

MAX_TITULO = 300
MAX_URL = 2_000
_PUERTOS = {"http": 80, "https": 443}
_SEGUIMIENTO = re.compile(r"utm_.*|gclid|fbclid|msclkid|_ga", re.IGNORECASE)
_ESPACIOS = re.compile(r"\s+")


def extraer_listado(pagina: Pagina, perfil: Perfil, *, maximo: int = 10) -> list[ResultadoListado]:
    """Hasta `maximo` resultados con título y enlace válidos, sin repetir URL.

    Lanza `SelectorNoValido` si algún selector del perfil no pertenece al subconjunto admitido.
    """
    if maximo <= 0:
        return []
    raiz = analizar(pagina.html)
    base = pagina.url_final or pagina.url
    dominio = dominio_normalizado(perfil.dominio)
    # `href="#"` o `href=""` (vista rápida, botones) apuntan a la propia página de búsqueda.
    propia = url_de_la_tienda(base, base, dominio) if dominio else None
    resultados: list[ResultadoListado] = []
    vistas: set[str] = set()
    for nodo in seleccionar(raiz, perfil.sel_resultado):
        titulo = extraer_valor(nodo, perfil.sel_titulo)
        enlace = extraer_valor(nodo, perfil.sel_enlace)
        if not titulo or not enlace or dominio is None:
            continue
        url = url_de_la_tienda(enlace, base, dominio)
        if url is None or url in vistas or url == propia:
            continue
        precio: Precio | None = None
        if perfil.sel_precio_listado:
            texto_precio = extraer_valor(nodo, perfil.sel_precio_listado)
            precio = leer_precio(texto_precio) if texto_precio else None
        vistas.add(url)
        resultados.append(ResultadoListado(_recortar(titulo), url, precio))
        if len(resultados) >= maximo:
            break
    return resultados


def url_de_la_tienda(enlace: str, base: str, dominio: str) -> str | None:
    """`enlace` resuelto contra `base` y normalizado, o `None` si no es de `dominio`.

    `dominio` ya normalizado (minúsculas, IDNA, sin `www.`). Se quitan el fragmento y los
    parámetros de seguimiento (`utm_*`, `gclid`...) para no repetir la misma ficha.
    """
    try:
        partes = urlsplit(urljoin(base, enlace.strip()))
        puerto = partes.port
    except ValueError:
        return None
    esquema = partes.scheme.lower()
    if esquema not in _PUERTOS or partes.username is not None or partes.password is not None:
        return None
    if puerto is not None and puerto != _PUERTOS[esquema]:
        return None
    host = _host_normalizado(partes.hostname or "")
    if host is None:
        return None
    sin_www = host.removeprefix("www.")
    if sin_www != dominio:
        return None
    consulta = partes.query
    parametros = parse_qsl(consulta, keep_blank_values=True)
    if any(_SEGUIMIENTO.fullmatch(clave) for clave, _ in parametros):
        consulta = urlencode([(k, v) for k, v in parametros if not _SEGUIMIENTO.fullmatch(k)])
    ruta = quote(partes.path or "/", safe="/%:@!$&'()*+,;=-._~")
    # Espacios, comillas o `<` en la consulta no deben llegar tal cual a la URL que se descarga.
    consulta = quote(consulta, safe="/?%:@!$&'()*+,;=-._~")
    url = urlunsplit((esquema, host, ruta, consulta, ""))
    return url if len(url) <= MAX_URL else None


def dominio_normalizado(dominio: str) -> str | None:
    """Dominio de un perfil tal y como lo espera `url_de_la_tienda`."""
    host = _host_normalizado(dominio)
    return host.removeprefix("www.") if host else None


def _host_normalizado(host: str) -> str | None:
    """Minúsculas, sin punto final y en IDNA (`españa.es` → `xn--espaa-rta.es`)."""
    host = host.strip().lower().rstrip(".")
    if not host:
        return None
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def _recortar(titulo: str) -> str:
    titulo = _ESPACIOS.sub(" ", titulo).strip()
    return titulo if len(titulo) <= MAX_TITULO else titulo[: MAX_TITULO - 1].rstrip() + "…"
