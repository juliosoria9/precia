"""Referencias OEM: normalización y detección de candidatas en títulos y campos.

Una referencia candidata es alfanumérica, tiene al menos 5 caracteres y al menos 2 cifras
una vez quitados espacios y separadores. Con una sola cifra es el nombre de una tecnología
(«DLSS4», «GDDR6X», «PCIe5»), que sale en los títulos de productos distintos (bug 36).
Las tiendas la escriben de muchas formas («1K0 698 151», «1K0-698-151», «REF.1K0698151»),
así que se normaliza antes de comparar.

Una referencia escrita por grupos conserva su sufijo de revisión de una o dos letras
(«1K0 941 005 E» → 1K0941005E): sin él, una revisión pasaría por la referencia exacta.
"""

import re

MAX_TEXTO = 10_000
"""Solo se analiza este prefijo del texto: los datos vienen de webs no confiables."""

LONGITUD_MINIMA = 5
MIN_CIFRAS = 2
"""Cifras que lleva como mínimo una referencia: «DLSS4» o «WiFi6E» no lo son (bug 36)."""
MIN_DIGITOS_REFERENCIA = 8
"""Un número suelto de una URL con menos cifras es un id («/123-faro.html», «/p/123456»);
con 8 o más se conserva, porque puede ser una referencia numérica (Bosch «0986494104») o un
EAN. Lo usan el catálogo por sitemap y la extracción de la ficha (R4-H)."""

_SEPARADORES = re.compile(r"[\s\-./_]+")
_TOKEN = re.compile(r"[0-9A-Za-z]+(?:[-./_][0-9A-Za-z]+)*")
# Los precios contienen números largos («12.345,67 €») que no son referencias. El número solo
# puede empezar al principio de una tira de cifras: sin el lookbehind, un título hostil hecho
# de miles de cifras volvía cuadrática la búsqueda (segundos por llamada).
_PRECIO = re.compile(
    r"(?:€|\$|\bEUR\b|\bUSD\b)\s*\d[\d.,]*"
    r"|(?<![\d.,])\d[\d.,]*\s*(?:€|\$|\bEUR\b|\bUSD\b|\beuros?\b)",
    re.IGNORECASE,
)
# Cilindrada pegada a la familia del motor («1.9TDI», «1,6HDI», «2.0TFSI») y normas de
# emisiones («Euro5»): salen en piezas muy distintas del mismo coche y no identifican ninguna.
_MOTOR_O_NORMA = re.compile(r"\b\d[.,]\d[A-Za-z]{2,6}\b|\beuro\s?\d\b", re.IGNORECASE)
_UNIDADES = frozenset(
    {"KM", "KMS", "MM", "CM", "CV", "KW", "HP", "W", "V", "AH", "ML", "L", "KG", "G", "NM", "CC"}
)
_CON_UNIDAD = re.compile(r"^\d+(?:" + "|".join(sorted(_UNIDADES, key=len, reverse=True)) + r")$")
_RANGO_AÑOS = re.compile(r"^(?:19|20)\d{2}(?:19|20)\d{2}$")
_AÑO = re.compile(r"^(?:19|20)\d{2}$")
_MILES = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
MAX_SUFIJO = 2
"""Letras de revisión que un fabricante añade a la referencia («1K0941005E», «1K0615301AA»)."""
_SUFIJO = re.compile(rf"^[A-Z]{{1,{MAX_SUFIJO}}}$")
# Tras una referencia por grupos, una o dos letras en MAYÚSCULAS son su sufijo de revisión
# («1K0 941 005 E»), salvo conjunciones, artículos y preposiciones («… 151 Y 1K0 …», «… DE …»),
# la marca o el «OE» que escriben algunas tiendas detrás y las unidades («… 151 KW»). La «E»
# también es conjunción, pero es la letra de revisión más común: se acepta. Tampoco cuenta si no
# va a un solo espacio (entre medias había un precio, ya quitado) o si la sigue «.» o «:», que
# hacen de ella una abreviatura («PT.», puertas) o una etiqueta («ID: 35952»). El lado de la
# pieza («… 507 LH», «IZ», «DX») tampoco. Una «A» puede ser revisión o preposición («… 151 A
# ESTRENAR»): salen las dos referencias, con y sin ella.
_FIN_DE_ABREVIATURA = frozenset({".", ":"})
_NO_SUFIJOS = frozenset(
    {"Y", "O", "U", "DE", "EN", "EL", "LA", "AL", "UN", "ES", "SE", "SU", "VW", "OE"}
    | {"LH", "RH", "IZ", "DR", "DX", "SX", "LI", "LD"}
)
_SUFIJOS_DUDOSOS = frozenset({"A"})


def es_revision(referencia: str, buscada: str) -> bool:
    """`referencia` es `buscada` con un sufijo de revisión de 1 a `MAX_SUFIJO` letras.

    Las dos llegan ya normalizadas. Solo en esta dirección (la de la ficha es más larga): buscar
    1K0941005E y aceptar 1K0941005, o una revisión hermana (E frente a F), es otra decisión.
    """
    sufijo = referencia[len(buscada) :]
    return referencia.startswith(buscada) and 1 <= len(sufijo) <= MAX_SUFIJO and sufijo.isalpha()


def normalizar_referencia(texto: str) -> str:
    """Quita espacios y separadores (`-`, `.`, `/`, `_`) y pasa a mayúsculas."""
    return _SEPARADORES.sub("", texto).upper()


def es_id_numerico(texto: str) -> bool:
    """Si `texto` es un número de menos de `MIN_DIGITOS_REFERENCIA` cifras (un id de URL)."""
    return texto.isdigit() and len(texto) < MIN_DIGITOS_REFERENCIA


def es_referencia(texto: str) -> bool:
    """Indica si un texto ya normalizado tiene forma de referencia OEM."""
    return (
        len(texto) >= LONGITUD_MINIMA
        and texto.isascii()
        and texto.isalnum()
        and sum(c.isdigit() for c in texto) >= MIN_CIFRAS
    )


def _es_numero_o_medida(normalizado: str, crudo: str, siguiente: str | None) -> bool:
    if _CON_UNIDAD.match(normalizado) or _RANGO_AÑOS.match(normalizado) or _MILES.match(crudo):
        return True
    return normalizado.isdigit() and siguiente is not None and siguiente.upper() in _UNIDADES


def _es_trozo(crudo: str) -> bool:
    """Trozo corto de una referencia escrita por grupos («1K0 698 151», «0 986 494 104»)."""
    return (
        crudo.isalnum()
        and len(crudo) <= 4
        and any(c.isdigit() for c in crudo)
        and not _AÑO.match(crudo)
    )


def referencias_en(texto: str) -> set[str]:
    """Devuelve las referencias OEM candidatas (normalizadas) que aparecen en el texto."""
    texto = _MOTOR_O_NORMA.sub(" ", _PRECIO.sub(" ", texto[:MAX_TEXTO]))
    tokens = list(_TOKEN.finditer(texto))
    encontradas: set[str] = set()

    for i, token in enumerate(tokens):
        crudo = token.group()
        siguiente = tokens[i + 1].group() if i + 1 < len(tokens) else None
        normalizado = normalizar_referencia(crudo)
        if _es_numero_o_medida(normalizado, crudo, siguiente):
            continue
        if es_referencia(normalizado):
            encontradas.add(normalizado)
        # «REF.1K0698151»: la referencia va pegada a un prefijo con un separador.
        for parte in _SEPARADORES.split(crudo):
            parte = parte.upper()
            if parte != normalizado and es_referencia(parte):
                encontradas.add(parte)

    encontradas.update(_referencias_por_grupos(texto, tokens))
    return encontradas


def referencias_de_consulta(texto: str) -> set[str]:
    """Las referencias de la consulta del usuario, con el sufijo de revisión en minúsculas.

    En la web, solo una letra en MAYÚSCULAS tras la referencia por grupos es su sufijo (en
    minúsculas suele ser texto: «… 005 e iluminación»). La consulta la escribe el usuario, que
    teclea «1k0 941 005 e» queriendo la revisión E: se pasa a mayúsculas antes de buscarlas.
    """
    return referencias_en(texto.upper())


def _sufijo_suelto(texto: str, grupo: re.Match[str], sufijo: re.Match[str]) -> bool:
    """El posible sufijo va a un solo espacio del último grupo y no lo sigue «.» ni «:»."""
    hueco = texto[grupo.end() : sufijo.start()]
    return (
        len(hueco) == 1
        and hueco.isspace()
        and texto[sufijo.end() : sufijo.end() + 1] not in _FIN_DE_ABREVIATURA
    )


def _referencias_por_grupos(texto: str, tokens: list[re.Match[str]]) -> set[str]:
    # Un primer trozo con algún dígito seguido de al menos dos grupos solo de dígitos,
    # separados únicamente por espacios. Exigirlo así evita unir «Audi A4 B8» o «Golf 6 2010».
    encontradas: set[str] = set()
    i = 0
    while i < len(tokens):
        if not _es_trozo(tokens[i].group()):
            i += 1
            continue
        fin = i + 1
        while (
            fin < len(tokens)
            and _es_trozo(tokens[fin].group())
            and tokens[fin].group().isdigit()
            and texto[tokens[fin - 1].end() : tokens[fin].start()].isspace()
        ):
            fin += 1
        if fin - i >= 3:
            unida = "".join(t.group() for t in tokens[i:fin]).upper()
            siguiente = tokens[fin].group() if fin < len(tokens) else None
            if (
                siguiente is not None
                and _SUFIJO.match(siguiente)
                and siguiente not in _NO_SUFIJOS
                and siguiente not in _UNIDADES
                and _sufijo_suelto(texto, tokens[fin - 1], tokens[fin])
            ):
                if siguiente in _SUFIJOS_DUDOSOS and es_referencia(unida):
                    encontradas.add(unida)
                unida += siguiente
                fin += 1
                siguiente = tokens[fin].group() if fin < len(tokens) else None
            if es_referencia(unida) and not (siguiente and siguiente.upper() in _UNIDADES):
                encontradas.add(unida)
            i = fin
        else:
            i += 1
    return encontradas
