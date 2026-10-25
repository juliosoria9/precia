"""Dominio de una tienda: del texto que escribe el usuario a la clave de la tienda en el almacén.

El dominio es la clave de la tienda en el almacén y, con él, el configurador escribe la URL de
la portada que se visita. Por eso no basta con pasarlo a minúsculas: un texto como
`otra.es#tienda.es`, `tienda.es@otra.es` o `tienda.es:8080` cambiaría el servidor al que se
conecta. Se admite solo un nombre de host estricto (etiquetas de letras, cifras y guiones,
al menos dos, en IDNA), nunca una IP, y sin `www.`, como lo guarda el configurador en el perfil.

Al dar de alta una tienda, además, el host pasa por el validador anti-SSRF del navegador
(`validar_url`), que resuelve el DNS y rechaza las direcciones internas.
"""

import re

from precia.navegador.tipos import URLNoPermitida
from precia.navegador.url import Validador, validar_url
from precia.nucleo.fuente_datos import DominioNoValido

MAX_DOMINIO = 253
_ETIQUETA = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
_NUMERO = re.compile(r"(0x[0-9a-f]*|[0-9]+)")


def host_estricto(texto: str) -> str | None:
    """`texto` como nombre de host (minúsculas, IDNA, sin punto final), o `None`."""
    texto = texto.strip().lower().rstrip(".")
    if not texto or len(texto) > MAX_DOMINIO:
        return None
    try:
        host = texto.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    etiquetas = host.split(".")
    if len(host) > MAX_DOMINIO or len(etiquetas) < 2:
        return None
    if not all(_ETIQUETA.fullmatch(e) for e in etiquetas):
        return None
    # Como el estándar URL: si la última etiqueta es un número, el host es una IPv4.
    if _NUMERO.fullmatch(etiquetas[-1]):
        return None
    return host


def normalizar_dominio(texto: str) -> str | None:
    """Clave de la tienda (`tienda.es`) para `texto`, o `None` si no es un dominio válido."""
    host = host_estricto(texto)
    if host is None:
        return None
    dominio = host.removeprefix("www.")
    return dominio if "." in dominio else None


async def dominio_de_tienda(texto: str, *, validador: Validador = validar_url) -> str:
    """Valida el dominio que escribe el usuario y lo devuelve normalizado.

    Lanza `DominioNoValido` con un mensaje para el usuario si no es un nombre de host o si el
    validador anti-SSRF lo rechaza (no resuelve, apunta a una red interna...).
    """
    host = host_estricto(texto)
    dominio = normalizar_dominio(texto)
    if host is None or dominio is None:
        raise DominioNoValido(
            "Escribe solo el dominio de la tienda, por ejemplo «tienda.es»: sin https://, "
            "rutas, puertos ni direcciones IP."
        )
    try:
        # El host tal como lo escribe el usuario: hay tiendas que solo responden en `www.`.
        await validador(f"https://{host}/")
    except URLNoPermitida as error:
        raise DominioNoValido(f"No se puede usar «{dominio}»: {error}") from error
    return dominio
