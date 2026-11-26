"""Lectura de precios escritos en formato español o internacional.

`leer_precio` es conservador: prefiere devolver `None` a confundir un teléfono, una referencia o
un descuento con un precio. Un número cuenta como precio si lleva moneda al lado; sin moneda,
solo si tiene céntimos («38,90») o si es lo único que hay en el texto («38»). En una oferta se
queda con el precio vigente: `leer_precio` salta el importe anunciado como antiguo («Antes»,
«Ancien prix») y `texto_de_precio` lee el selector de un perfil sin lo tachado (`dom.tachado`).
"""

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from precia.extraccion.dom import Nodo, tachado
from precia.extraccion.selectores import dividir_selector, extraer_valor, seleccionar
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
_CLASE_SUFIJO = re.compile(r"(?:^|[-_])price[-_]suffix$", re.IGNORECASE)
"""Clase del sufijo de un precio: `woocommerce-price-suffix`, donde WooCommerce suele poner el
precio sin IVA al lado del precio principal."""
MAX_SUFIJOS = 50
"""Sufijos de precio que se miran como mucho en una página (cota ante un HTML hostil)."""
# Etiqueta que anuncia el precio antiguo de una oferta, justo delante del importe o detrás.
_ANTIGUO_DELANTE = re.compile(
    r"(?<![a-z])(?:antes|precio\s+(?:anterior|antiguo|original|habitual|de\s+referencia"
    r"|recomendado)|p\.?v\.?p\.?\s+recomendado|pvr|ancien\s+prix|prix\s+de\s+r[eé]f[eé]rence"
    r"|was|old\s+price|regular\s+price|rrp|ahorr[ae]s?|ahorro)\s*[:.]?\s*$",
    re.IGNORECASE,
)
_ANTIGUO_DETRAS = re.compile(
    r"\s*\*?\s*(?:precio\s+recomendado|p\.?v\.?p\.?\s+recomendado)", re.IGNORECASE
)
MAX_ETIQUETA = 32
"""Caracteres que se miran delante de un importe buscando «Antes:», «Ancien prix :»..."""
NIVELES_JUNTO_AL_TACHADO = 2
"""Hasta dónde se sube desde lo tachado para buscar al lado el precio vigente (`texto_de_precio`):
el `<ins>` de WooCommerce es hermano del `<del>`; el precio actual de PrestaShop, primo de su
`regular-price`."""
NIVELES_PRECIO_PARTIDO = 3
"""Hasta dónde se sube desde un trozo de precio («22,»: el entero con el separador colgando, sin
céntimos ni moneda) buscando el precio completo (`texto_de_precio`, causa R9-P)."""
_ENTERO_COLGANDO = re.compile(rf"\d{{1,3}}(?:(?:[.]|{_SEP_ESPACIO})\d{{3}})+[.,]|\d+[.,]")
# Espacio entre el entero colgando y los céntimos cuando estos van en un bloque aparte: `Nodo.texto`
# separa los bloques con un espacio y `<span>29,</span><div>99</div><span>€</span>` da «29, 99 €».
_CENTIMOS_EN_BLOQUE = re.compile(rf"{_SEP_ESPACIO}+(?=\d{{2}}(?!\d))")
_TRAS_ENTERO_COLGANDO = re.compile(rf"\d[.,]{_SEP_ESPACIO}+\Z")


@dataclass(slots=True)
class _Candidato:
    inicio: int
    fin: int
    importe: Decimal
    decimales: int
    moneda: str | None = None
    texto_inicio: int = 0
    texto_fin: int = 0
    pareja: "_Candidato | None" = field(default=None, compare=False, repr=False)
    """El otro extremo, si es parte de un rango («30 - 40 €»): sin compararlo ni enseñarlo,
    que se apuntan el uno al otro."""


def leer_precio(texto: str) -> Precio | None:
    """Primer precio de `texto` (en rangos y «desde», el primero) o `None` si no hay importe.

    Con moneda, se salta el que se anuncia como antiguo («Antes: 59,90 €», «Ancien prix :
    17,06 €», «31,93 € precio recomendado») si queda otro; en un rango, la marca vale para los
    dos extremos («Antes 30 - 40 €»). Si todos quedan marcados, el primero que solo lo está por
    detrás («Antes: 59,90 € 49,90 € PVP recomendado: 69,90 €»: la etiqueta es de 69,90 €) y, si
    no hay, el primero.
    """
    if not isinstance(texto, str):
        return None
    texto = texto[:MAX_TEXTO]
    candidatos = _candidatos(texto)
    con_moneda = [c for c in candidatos if c.moneda is not None]
    if con_moneda:
        marcas = [(c, *_marcas(texto, c)) for c in con_moneda]
        vigente = next((c for c, delante, detras in marcas if not delante and not detras), None)
        if vigente is None:
            vigente = next((c for c, delante, _ in marcas if not delante), con_moneda[0])
        return _precio(texto, vigente)
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
        if _es_precio(texto, candidato):
            yield _precio(texto, candidato)


def _es_precio(texto: str, candidato: _Candidato) -> bool:
    """Con moneda, o con céntimos y sin una clave delante («Ref.», «Tel.»...)."""
    return candidato.moneda is not None or (
        candidato.decimales == 2 and not _precedido_de_clave(texto, candidato)
    )


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
            actual.pareja, siguiente.pareja = siguiente, actual
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
    if (
        fin - inicio == 2
        and _TRAS_ENTERO_COLGANDO.search(texto, max(0, inicio - MAX_ETIQUETA), inicio) is not None
    ):
        return False  # «29, 99 €»: los céntimos de un precio partido en bloques, no 99 €.
    # «3x12,00 €» sí; «1K0615301AA» no.
    multiplicado = previo in "xX" and (not anterior or anterior.isdigit() or anterior.isspace())
    if (previo.isalpha() or previo == "_") and delante is None and not multiplicado:
        return False
    return not ((siguiente.isalpha() or siguiente == "_") and detras is None)


def texto_de_precio(raiz: Nodo, selector: str) -> str | None:
    """Como `selectores.extraer_valor`, pero sin el precio tachado cuando queda otro precio.

    Se saltan las coincidencias que están dentro de algo tachado (`dom.tachado`), pero solo a
    favor de una que esté al lado del primer tachado (`NIVELES_JUNTO_AL_TACHADO`): más lejos
    suele ser otro producto (los relacionados), y en Magento 1 `regular-price` es el precio
    vigente cuando no hay oferta. De la que queda se quita el texto tachado si lo que queda se
    lee como precio. Si todo está tachado, no queda precio al lado o no queda precio, el texto
    de siempre: una tienda cuyo precio normal lleve una clase como `price-item--regular` no se
    queda sin precio. Con ` @atributo`, igual que `extraer_valor`. Lanza `SelectorNoValido` si
    el selector no es del subconjunto admitido.
    """
    css, atributo = dividir_selector(selector)
    if atributo is not None:
        return extraer_valor(raiz, selector)
    vistos: dict[int, Nodo | None] = {}
    junto: Nodo | None = None
    for nodo in seleccionar(raiz, css):
        cercano = _tachado_mas_cercano(nodo, raiz, vistos)
        if cercano is not None:
            if junto is None:
                junto = _subir(cercano, raiz, NIVELES_JUNTO_AL_TACHADO)
            continue
        completo = nodo.texto()
        if not completo:
            continue
        if junto is not None and not _dentro_de(nodo, junto):
            break
        vigente = nodo.texto(sin_tachados=True)
        texto = vigente if vigente and leer_precio(vigente) is not None else completo
        return _precio_del_antepasado(nodo, raiz, texto) or texto
    return extraer_valor(raiz, selector)


def _precio_del_antepasado(nodo: Nodo, raiz: Nodo, trozo: str) -> str | None:
    """El precio completo de un antepasado cercano si `trozo` es solo su entero, o `None`.

    Hay tiendas que parten el precio en elementos hermanos (`<span>22<span>,</span></span>
    <span>95</span><span>€</span>`) y un selector puede quedarse con el entero y el separador
    colgando («22,»), que no se lee como precio (causa R9-P). Se sube hasta
    `NIVELES_PRECIO_PARTIDO` niveles, sin pasar de `raiz`, hasta el primer antepasado con algún
    importe. Vale si su texto tiene un único importe, `trozo` sale una sola vez y ese importe
    empieza justo ahí (la moneda puede ir delante: «£22.95»); si no, no se sigue subiendo: más
    arriba solo hay más precios. No vale si el importe se anuncia como antiguo («Antes:», «PVP
    recomendado») dentro del antepasado o justo al lado. Si los céntimos van en un bloque y el
    texto queda «29, 99 €», se juntan. Devuelve solo el texto del precio. El texto partido con
    los céntimos en superíndice ya lo junta `Nodo.texto` (R3-A2); esto es para cuando el
    selector solo coge una parte.
    """
    if _ENTERO_COLGANDO.fullmatch(trozo) is None:
        return None
    actual = nodo
    for _ in range(NIVELES_PRECIO_PARTIDO):
        if actual is raiz or actual.padre is None:
            break
        actual = actual.padre
        original = actual.texto()
        inicio = original.find(trozo)
        unico = inicio != -1 and original.find(trozo, inicio + 1) == -1
        texto = original
        espacio = _CENTIMOS_EN_BLOQUE.match(texto, inicio + len(trozo)) if unico else None
        if espacio is not None:
            texto = texto[: espacio.start()] + texto[espacio.end() :]
        candidatos = [c for c in _candidatos(texto) if _es_precio(texto, c)]
        if not candidatos:
            continue
        mismo = next((c for c in candidatos if c.inicio == inicio), None)
        if (
            not unico
            or mismo is None
            or len({c.importe for c in candidatos}) > 1
            or any(_marcas(texto, mismo))
            or _anunciado_al_lado(actual, original, raiz)
        ):
            return None
        return _precio(texto, mismo).texto
    return None


def _anunciado_al_lado(nodo: Nodo, texto: str, raiz: Nodo) -> bool:
    """Si en el padre de `nodo` hay una etiqueta de precio antiguo justo delante o detrás de
    `texto` (el de `nodo`): `<div>Antes: <span>39,90 €</span> 29,90 €</div>`."""
    if nodo is raiz or nodo.padre is None:
        return False
    fuera = nodo.padre.texto()
    i = fuera.find(texto)
    if i == -1:
        return False
    delante = _ANTIGUO_DELANTE.search(fuera, max(0, i - MAX_ETIQUETA), i) is not None
    return delante or _ANTIGUO_DETRAS.match(fuera, i + len(texto)) is not None


def _subir(nodo: Nodo, raiz: Nodo, niveles: int) -> Nodo:
    """El ancestro de `nodo` a `niveles` de distancia, sin pasar de `raiz`."""
    for _ in range(niveles):
        if nodo is raiz or nodo.padre is None:
            break
        nodo = nodo.padre
    return nodo


def _dentro_de(nodo: Nodo, contenedor: Nodo) -> bool:
    actual: Nodo | None = nodo
    while actual is not None:
        if actual is contenedor:
            return True
        actual = actual.padre
    return False


def _tachado_mas_cercano(nodo: Nodo, raiz: Nodo, vistos: dict[int, Nodo | None]) -> Nodo | None:
    """El nodo tachado más cercano entre `nodo` y sus ancestros hasta `raiz`, o `None`.

    `vistos` guarda la respuesta de cada nodo por el que se pasa (por `id`; el árbol vive
    mientras dura la llamada): entre todas las coincidencias de una página, cada nodo se mira una
    vez. Subir por todos los ancestros de cada coincidencia era O(coincidencias por profundidad) y
    una ficha hostil paraba el bucle de asyncio durante minutos.
    """
    camino: list[Nodo] = []
    actual: Nodo | None = nodo
    cercano: Nodo | None = None
    while actual is not None and actual is not raiz:
        if id(actual) in vistos:
            cercano = vistos[id(actual)]
            break
        camino.append(actual)
        if tachado(actual):
            cercano = actual
            break
        actual = actual.padre
    for paso in camino:
        vistos[id(paso)] = cercano
    return cercano


def _marcas(texto: str, candidato: _Candidato) -> tuple[bool, bool]:
    """Si se anuncia como antiguo con una etiqueta delante y con una detrás, contando las del
    otro extremo si es parte de un rango."""
    extremos = (candidato,) if candidato.pareja is None else (candidato, candidato.pareja)
    delante = any(
        _ANTIGUO_DELANTE.search(texto, max(0, e.texto_inicio - MAX_ETIQUETA), e.texto_inicio)
        is not None
        for e in extremos
    )
    detras = any(_ANTIGUO_DETRAS.match(texto, e.texto_fin) is not None for e in extremos)
    return delante, detras


def _precedido_de_clave(texto: str, candidato: _Candidato) -> bool:
    return (
        _CLAVE_NO_PRECIO.search(texto, max(0, candidato.inicio - 24), candidato.inicio) is not None
    )


def precio_principal_del_sufijo(raiz: Nodo, importe: Decimal, texto_pagina: str) -> Precio | None:
    """El precio principal si `importe` solo aparece en el sufijo de un precio, o `None`.

    WooCommerce puede enseñar el precio sin IVA en `<small class="woocommerce-price-suffix">`,
    al lado del precio con IVA. En el texto quedan los dos seguidos («544,50 € 450,00 €») y quien
    lee el texto, como la IA, no sabe cuál es el principal. Si `importe` está en un sufijo y en
    ningún otro sitio de `texto_pagina`, se devuelve el único otro importe del elemento que
    contiene ese sufijo. Si aparece también fuera, o al lado hay más de un importe, no se decide.
    """
    sufijos = [n for n in raiz.descendientes() if any(_CLASE_SUFIJO.search(c) for c in n.clases)]
    if not sufijos:
        return None
    en_pagina = sum(1 for p in buscar_precios(texto_pagina) if p.importe == importe)
    en_sufijos = 0
    contenedor: Nodo | None = None
    for sufijo in sufijos[:MAX_SUFIJOS]:
        coincidencias = sum(1 for p in buscar_precios(sufijo.texto()) if p.importe == importe)
        if coincidencias and contenedor is None:
            contenedor = sufijo.padre
        en_sufijos += coincidencias
    if contenedor is None or en_sufijos < en_pagina:
        return None
    otros = {p.importe: p for p in buscar_precios(contenedor.texto()) if p.importe != importe}
    return next(iter(otros.values())) if len(otros) == 1 else None
