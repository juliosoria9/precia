"""🤖 Emparejado semántico: clasifica cada oferta frente a la consulta y aplica el filtro.

La IA decide si una oferta es la pieza buscada o un accesorio, una variante (otra posición,
medida o motor), un kit u otra cosa, y si cumple el filtro en lenguaje natural del usuario.
Los títulos y campos de las tiendas son datos no confiables: van solo en `datos` (la capa
`ia` los delimita) y la respuesta se interpreta con desconfianza, porque una prompt injection
en una ficha puede intentar que se descarten las ofertas de la competencia.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from precia.emparejado.texto import limpiar_titulo
from precia.emparejado.tipos import SesionIA
from precia.ia.tipos import PeticionIA
from precia.nucleo.modelos import Oferta

LOTE = 40
"""Ofertas por petición: acota el tamaño de la petición y de la respuesta."""
MAX_MOTIVO = 140
MAX_CONSULTA = 200
MAX_FILTRO = 300
_MAX_CAMPO = {"titulo": 200, "descripcion": 300}
_MAX_CAMPO_POR_DEFECTO = 80
_CAMPOS_ENVIADOS = ("referencia_oem", "marca", "estado", "descripcion")


class ClaseOferta(StrEnum):
    MISMA_PIEZA = "misma_pieza"
    ACCESORIO = "accesorio"
    VARIANTE = "variante"
    KIT = "kit"
    OTRA = "otra"


_CLASES = frozenset(c.value for c in ClaseOferta)
_ETIQUETAS = {
    ClaseOferta.ACCESORIO: "Accesorio",
    ClaseOferta.VARIANTE: "Variante",
    ClaseOferta.KIT: "Kit",
    ClaseOferta.OTRA: "Otra pieza",
}


@dataclass(frozen=True, slots=True)
class Clasificacion:
    """Veredicto de la IA sobre una oferta. Por defecto, nada que objetar."""

    clase: ClaseOferta = ClaseOferta.MISMA_PIEZA
    motivo: str = ""
    cumple_filtro: bool = True


def motivo_descarte(clasificacion: Clasificacion, filtro: str) -> str | None:
    """Motivo para la lista de descartados, o `None` si la oferta se queda."""
    if clasificacion.clase is not ClaseOferta.MISMA_PIEZA:
        etiqueta = _ETIQUETAS[clasificacion.clase]
        motivo = clasificacion.motivo
        return f"🤖 {etiqueta}: {motivo}" if motivo else f"🤖 {etiqueta}"
    if not clasificacion.cumple_filtro:
        return f"🤖 Filtro: «{filtro}»"
    return None


async def clasificar(
    sesion: SesionIA,
    consulta: str,
    ofertas: Sequence[Oferta],
    *,
    piezas: bool = True,
    filtro: str = "",
) -> list[Clasificacion]:
    """Clasifica las ofertas (si `piezas`) y comprueba el filtro (si lo hay), en orden.

    Los errores de la IA (`ErrorIA`: presupuesto, salida no válida...) se propagan: quien
    llama decide si sigue sin IA.
    """
    filtro = limpiar_titulo(filtro, MAX_FILTRO)
    if not ofertas or not (piezas or filtro):
        return [Clasificacion() for _ in ofertas]
    instrucciones = _instrucciones(limpiar_titulo(consulta, MAX_CONSULTA), piezas, filtro)
    esquema = _esquema(piezas, bool(filtro))
    resultado: list[Clasificacion] = []
    for inicio in range(0, len(ofertas), LOTE):
        lote = ofertas[inicio : inicio + LOTE]
        respuesta = await sesion.pedir(
            PeticionIA(
                componente="emparejado",
                instrucciones=instrucciones,
                datos=_datos(lote),
                esquema=esquema,
                max_tokens=256 + 64 * len(lote),
            )
        )
        resultado.extend(_interpretar(respuesta.datos, len(lote)))
    return resultado


def _instrucciones(consulta: str, piezas: bool, filtro: str) -> str:
    partes = [
        "Eres el emparejador de PrecIA, un comparador de precios de recambios.",
        f"El usuario busca: «{consulta}».",
        "En los datos recibirás una lista JSON de ofertas extraídas de tiendas web. "
        "Devuelve una entrada por oferta con su «indice».",
    ]
    if piezas:
        partes.append(
            "Para cada oferta indica «clase»: «misma_pieza» si es la pieza buscada (aunque sea "
            "de otra marca o usada); «accesorio» si es un complemento de esa pieza (sensor, "
            "tornillería, soporte...); «variante» si es el mismo tipo de pieza pero para otra "
            "posición, medida, motor o modelo; «kit» si incluye la pieza junto con otras; "
            "«otra» si no tiene relación."
        )
    if filtro:
        partes.append(
            "Indica «cumple_filtro»: true si la oferta cumple este requisito del usuario y "
            f"false si no lo cumple o los datos no permiten saberlo: «{filtro}»."
        )
    partes.append("En «motivo», una frase breve en español que justifique la respuesta.")
    return "\n".join(partes)


def _esquema(piezas: bool, filtro: bool) -> dict[str, Any]:
    propiedades: dict[str, Any] = {"indice": {"type": "integer"}}
    if piezas:
        propiedades["clase"] = {"type": "string", "enum": [c.value for c in ClaseOferta]}
    if filtro:
        propiedades["cumple_filtro"] = {"type": "boolean"}
    propiedades["motivo"] = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "clasificaciones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": propiedades,
                    "required": list(propiedades),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["clasificaciones"],
        "additionalProperties": False,
    }


def _recortar(clave: str, valor: str) -> str:
    return valor[: _MAX_CAMPO.get(clave, _MAX_CAMPO_POR_DEFECTO)]


def _datos(lote: Sequence[Oferta]) -> str:
    ofertas = []
    for i, oferta in enumerate(lote):
        datos: dict[str, Any] = {
            "indice": i,
            "tienda": _recortar("tienda", oferta.tienda),
            "titulo": _recortar("titulo", oferta.titulo),
            "precio": str(oferta.precio),
        }
        for clave in _CAMPOS_ENVIADOS:
            if clave in oferta.campos:
                datos[clave] = _recortar(clave, oferta.campos[clave].valor)
        ofertas.append(datos)
    return json.dumps({"ofertas": ofertas}, ensure_ascii=False)


def _interpretar(datos: dict[str, Any], total: int) -> list[Clasificacion]:
    # La salida ya viene validada contra el esquema, pero su contenido no es fiable: índices
    # repetidos o fuera de rango se ignoran y lo que no se clasifica se conserva.
    resultado = [Clasificacion() for _ in range(total)]
    vistos: set[int] = set()
    entradas = datos.get("clasificaciones")
    for entrada in entradas if isinstance(entradas, list) else []:
        if not isinstance(entrada, dict):
            continue
        indice = entrada.get("indice")
        if type(indice) is not int or not 0 <= indice < total or indice in vistos:
            continue
        clase_cruda = entrada.get("clase", ClaseOferta.MISMA_PIEZA.value)
        if not isinstance(clase_cruda, str) or clase_cruda not in _CLASES:
            continue
        vistos.add(indice)
        motivo = entrada.get("motivo")
        cumple = entrada.get("cumple_filtro", True)
        resultado[indice] = Clasificacion(
            clase=ClaseOferta(clase_cruda),
            motivo=limpiar_titulo(motivo, MAX_MOTIVO) if isinstance(motivo, str) else "",
            cumple_filtro=cumple if isinstance(cumple, bool) else True,
        )
    return resultado
