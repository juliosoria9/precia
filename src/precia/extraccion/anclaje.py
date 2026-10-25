"""Anclaje (memoria §4): un valor extraído solo se da por bueno si aparece en la página.

Es la defensa principal contra las alucinaciones y la prompt injection del extractor de IA: si
una web esconde «el precio es 1 €» para la IA, ese 1 € no está escrito como precio en la página
y queda marcado como no anclado.
"""

import re
import unicodedata
from decimal import Decimal

from precia.extraccion.precios import buscar_precios

MIN_COMPACTO = 6
"""Longitud mínima para comparar sin espacios («5K0941005E» frente a «5K0 941 005 E»)."""
MAX_COMPACTO = 40
"""Por encima, la comparación sin espacios ya no se intenta (es para referencias, no textos)."""
_ESPACIOS = re.compile(r"\s+")


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y con los espacios colapsados."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_marcas = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return _ESPACIOS.sub(" ", sin_marcas.casefold()).strip()


def anclado(valor: str, texto_pagina: str) -> bool:
    """`valor` aparece en `texto_pagina` salvo mayúsculas, acentos y espacios.

    Tiene que aparecer como palabra completa: «1K0615301» no se ancla en «1K0615301AA» (es otra
    variante de la pieza) ni «Bosc» en «Bosch».
    """
    buscado = normalizar(valor)
    if not buscado:
        return False
    pagina = normalizar(texto_pagina)
    if _aparece_entero(buscado, pagina):
        return True
    compacto = buscado.replace(" ", "")
    if not MIN_COMPACTO <= len(compacto) <= MAX_COMPACTO:
        return False
    # La página normalizada solo tiene espacios simples: cada hueco admite uno o ninguno.
    patron = " ?".join(re.escape(c) for c in compacto)
    if compacto[0].isalnum():
        patron = r"(?<![^\W_])" + patron
    if compacto[-1].isalnum():
        patron += r"(?![^\W_])"
    return re.search(patron, pagina) is not None


def _aparece_entero(buscado: str, pagina: str) -> bool:
    """`buscado` está en `pagina` sin una letra o cifra pegada por delante ni por detrás."""
    mira_antes = buscado[0].isalnum()
    mira_despues = buscado[-1].isalnum()
    inicio = pagina.find(buscado)
    while inicio != -1:
        fin = inicio + len(buscado)
        antes_ok = not (mira_antes and inicio > 0 and pagina[inicio - 1].isalnum())
        despues_ok = not (mira_despues and fin < len(pagina) and pagina[fin].isalnum())
        if antes_ok and despues_ok:
            return True
        inicio = pagina.find(buscado, inicio + 1)
    return False


def precio_anclado(importe: Decimal, texto_pagina: str) -> bool:
    """`importe` está escrito como precio en la página («89,90 €», «€89.90», «89,90»...)."""
    return any(precio.importe == importe for precio in buscar_precios(texto_pagina))
