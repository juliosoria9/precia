"""Lectura de precios escritos en formato español o internacional.

`leer_precio` es conservador: prefiere devolver `None` a confundir un teléfono, una referencia o
un descuento con un precio. Un número cuenta como precio si lleva moneda al lado; sin moneda,
solo si tiene céntimos («38,90») o si es lo único que hay en el texto («38»).
"""

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from precia.extraccion.tipos import Precio

MAX_TEXTO = 2_000
"""`leer_precio` recibe el texto de un elemento; más allá de esto no busca."""
MAX_IMPORTE = Decimal(1_000_000_000)

_MONEDA = re.compile(
    r"€|(?<![a-z])(?:euros?|eur)(?![a-z])|(?<![a-z])(?:us)?\$|(?<![a-z])usd(?![a-z])|£"
    r"|(?<![a-z])gbp(?![a-z])",
    re.IGNORECASE,
)
_CODIGOS = {"€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "$": "USD", "us$": "USD"}
_CODIGOS |= {"usd": "USD", "£": "GBP", "gbp": "GBP"}

_SEP_ESPACIO = "[ \u00a0\u202f]"
# Miles con espacio («1 234,56 €»): solo si va seguido de moneda; si no, «912 345 678» sería un
# importe en vez de un teléfono. Como mucho tres grupos (más ya pasa de `MAX_IMPORTE`): con `+`,
# una tira de «123 123 123...» sin moneda retrocedía desde cada grupo y el coste era cuadrático.
_NUMERO_ESPACIADO = re.compile(
    rf"(?<![\d.,])\d{{1,3}}(?:{_SEP_ESPACIO}\d{{3}}){{1,3}}(?:[.,]\d{{1,2}})?(?![\d]|[.,]\d)"
    rf"(?=\s*(?:€|eur|\$|usd|£|gbp))",
    re.IGNORECASE,
)
_NUMERO = re.compile(
    r"(?<![\d.,])"
    r"(?:\d{1,3}(?P<miles>[.,])\d{3}(?:(?P=miles)\d{3})*(?:(?!(?P=miles))[.,]\d{1,2})?"
    r"|\d+(?:[.,]\d{1,2})?)"
    r"(?![\d]|[.,]\d)"
)
_RANGO = re.compile(r"\s*(?:-|\u2013|\u2014|a|al|hasta|to)\s*", re.IGNORECASE)
_MULTIPLICADOR = re.compile(r"\s*[x\u00d7]\s*\d", re.IGNORECASE)
_PORCENTAJE = re.compile(r"\s*%")
_CLAVE_NO_PRECIO = re.compile(
    r"\b(?:ref(?:erencia)?|tel[eé]?f?(?:ono)?|m[oó]vil|sku|ean|oem|c[oó]d(?:igo)?|cp|n[º°o]"
    r"|art(?:[ií]culo)?|pedido|modelo|a[nñ]o)\.?\s*[:#]?\s*$",
    re.IGNORECASE,
)
_ESPACIOS = re.compile(r"\s+")


@dataclass(slots=True)
class _Candidato:
    inicio: int
    fin: int
    importe: Decimal
    decimales: int
    moneda: str | None = None
    texto_inicio: int = 0
    texto_fin: int = 0


def leer_precio(texto: str) -> Precio | None:
    """Primer precio de `texto` (en rangos y «desde», el primero) o `None` si no hay importe."""
    if not isinstance(texto, str):
        return None
    texto = texto[:MAX_TEXTO]
    candidatos = _candidatos(texto)
    for candidato in candidatos:
        if candidato.moneda is not None:
            return _precio(texto, candidato)
    if len(candidatos) != 1:
        return None
    unico = candidatos[0]
    if unico.decimales == 2 and not _precedido_de_clave(texto, unico):
        return _precio(texto, unico)
    if unico.decimales == 0 and texto.strip() == texto[unico.inicio : unico.fin]:
        return _precio(texto, unico)
    return None


def buscar_precios(texto: str) -> Iterator[Precio]:
    """Todo lo que está escrito como precio en `texto` (con moneda o con céntimos).

    Pensado para el texto de una página entera (anclaje), así que no lo recorta.
    """
    for candidato in _candidatos(texto):
        if candidato.moneda is not None or (
            candidato.decimales == 2 and not _precedido_de_clave(texto, candidato)
        ):
            yield _precio(texto, candidato)


def _precio(texto: str, candidato: _Candidato) -> Precio:
    original = texto[candidato.texto_inicio : candidato.texto_fin]
    return Precio(
        importe=candidato.importe,
        moneda=candidato.moneda or "EUR",
        texto=_ESPACIOS.sub(" ", original).strip(),
    )


def _candidatos(texto: str) -> list[_Candidato]:
    monedas = [(m.start(), m.end(), _CODIGOS[m.group().lower()]) for m in _MONEDA.finditer(texto)]
    inicios_moneda = [m[0] for m in monedas]
    fines_moneda = [m[1] for m in monedas]
    espaciados = [m.span() for m in _NUMERO_ESPACIADO.finditer(texto)]
    inicios_espaciados = [e[0] for e in espaciados]
    numeros = list(espaciados)
    for m in _NUMERO.finditer(texto):
        # Los espaciados no se solapan entre sí: basta mirar los vecinos.
        i = bisect_right(inicios_espaciados, m.start()) - 1
        solapa = i >= 0 and espaciados[i][1] > m.start()
        if not solapa and not (i + 1 < len(espaciados) and espaciados[i + 1][0] < m.end()):
            numeros.append(m.span())
    numeros.sort()

    candidatos: list[_Candidato] = []
    usadas: set[int] = set()
    for inicio, fin in numeros:
        candidato = _leer_numero(texto[inicio:fin], inicio, fin)
        if candidato is None:
            continue
        delante = _moneda_delante(texto, inicio, fines_moneda)
        detras = _moneda_detras(texto, fin, inicios_moneda)
        if not _contexto_valido(texto, candidato, delante, detras):
            continue
        if detras is not None:
            candidato.moneda = monedas[detras][2]
            candidato.texto_fin = monedas[detras][1]
            usadas.add(detras)
        elif delante is not None and delante not in usadas:
            candidato.moneda = monedas[delante][2]
            candidato.texto_inicio = monedas[delante][0]
            usadas.add(delante)
        candidatos.append(candidato)

    # «De 10 a 20 €», «10-20 €»: el primero del rango hereda la moneda del segundo.
    for actual, siguiente in pairwise(candidatos):
        entre = texto[actual.fin : siguiente.inicio]
        if actual.moneda is None and siguiente.moneda and _RANGO.fullmatch(entre):
            actual.moneda = siguiente.moneda
    return candidatos


def _leer_numero(numero: str, inicio: int, fin: int) -> _Candidato | None:
    limpio = re.sub(_SEP_ESPACIO, "", numero)
    ultimo = max(limpio.rfind(","), limpio.rfind("."))
    decimales = 0
    # Un único separador seguido de tres cifras es de miles («1.500 €»), no decimal.
    if ultimo != -1 and len(limpio) - ultimo - 1 in (1, 2):
        decimales = len(limpio) - ultimo - 1
        entero, fraccion = limpio[:ultimo], limpio[ultimo + 1 :]
    else:
        entero, fraccion = limpio, ""
    entero = entero.replace(".", "").replace(",", "")
    try:
        importe = Decimal(f"{entero}.{fraccion}" if fraccion else entero)
    except InvalidOperation:
        return None
    if importe >= MAX_IMPORTE:
        return None
    return _Candidato(inicio, fin, importe, decimales, texto_inicio=inicio, texto_fin=fin)


def _moneda_delante(texto: str, inicio: int, fines: list[int]) -> int | None:
    """Índice de la moneda que acaba justo antes de `inicio` (con espacios en medio o no)."""
    i = bisect_right(fines, inicio) - 1
    if i >= 0 and not texto[fines[i] : inicio].strip():
        return i
    return None


def _moneda_detras(texto: str, fin: int, inicios: list[int]) -> int | None:
    i = bisect_left(inicios, fin)
    if i < len(inicios) and not texto[fin : inicios[i]].strip():
        return i
    return None


def _contexto_valido(
    texto: str, candidato: _Candidato, delante: int | None, detras: int | None
) -> bool:
    inicio, fin = candidato.inicio, candidato.fin
    previo = texto[inicio - 1] if inicio > 0 else ""
    anterior = texto[inicio - 2] if inicio > 1 else ""
    siguiente = texto[fin] if fin < len(texto) else ""
    if _PORCENTAJE.match(texto, fin) or _MULTIPLICADOR.match(texto, fin):
        return False
    if previo and previo in "-\u2212" and (not anterior or anterior.isspace() or anterior == "("):
        return False  # «-5,00 €» es un descuento, no un precio.
    # «3x12,00 €» sí; «1K0615301AA» no.
    multiplicado = previo in "xX" and (not anterior or anterior.isdigit() or anterior.isspace())
    if (previo.isalpha() or previo == "_") and delante is None and not multiplicado:
        return False
    return not ((siguiente.isalpha() or siguiente == "_") and detras is None)


def _precedido_de_clave(texto: str, candidato: _Candidato) -> bool:
    return (
        _CLAVE_NO_PRECIO.search(texto, max(0, candidato.inicio - 24), candidato.inicio) is not None
    )
