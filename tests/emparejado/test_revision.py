"""Revisión con IA de los resultados: una llamada por búsqueda, prompt y caché por oferta."""

import asyncio
import functools
from decimal import Decimal
from typing import Any

from precia.emparejado.semantico import (
    LOTE,
    TOKENS_BASE,
    TOKENS_POR_OFERTA,
    ClaseOferta,
    Clasificacion,
    Revision,
    motivo_descarte,
    revisar,
)
from precia.ia.tipos import PeticionIA
from precia.nucleo.modelos import Oferta
from precia.nucleo.orquestador import MAX_OFERTAS
from tests.emparejado.ayudas import SesionFalsa, clasificar_por_palabras, ofertas_de

PUERTA = "puerta delantera derecha"
REGLAS = {"cerradura": ("accesorio", "Es la cerradura, no la puerta")}


def oferta(titulo: str, precio: str = "120.00", tienda: str = "tienda.example") -> Oferta:
    return Oferta(tienda, f"https://{tienda}/p", titulo, Decimal(precio), {})


class CacheFalsa:
    def __init__(self) -> None:
        self.filas: dict[str, tuple[str, str, bool]] = {}

    def leer(self, clave: str) -> tuple[str, str, bool] | None:
        return self.filas.get(clave)

    def guardar(self, clave: str, clase: str, motivo: str, cumple_filtro: bool) -> None:
        self.filas[clave] = (clase, motivo, cumple_filtro)


def ejecutar(sesion: SesionFalsa, ofertas: list[Oferta], **kwargs: Any) -> Revision:
    """Revisa y guarda en la caché, como hace el agrupador si la revisión no es anómala."""
    kwargs.setdefault("componente", "revision")
    revision = asyncio.run(revisar(sesion, PUERTA, ofertas, **kwargs))
    if kwargs.get("cache") is not None:
        revision.guardar(kwargs["cache"])
    return revision


OFERTAS = [oferta("Puerta delantera derecha Seat Ibiza"), oferta(f"Cerradura de la {PUERTA}")]


def test_el_caso_de_julio_la_cerradura_es_pieza_de_lo_buscado() -> None:
    sesion = SesionFalsa(clasificar_por_palabras(REGLAS))
    revision = ejecutar(sesion, OFERTAS)
    assert [c.clase for c in revision.clasificaciones] == [
        ClaseOferta.MISMA_PIEZA,
        ClaseOferta.ACCESORIO,
    ]
    motivo = motivo_descarte(revision.clasificaciones[1], "")
    assert motivo == "IA · Pieza o accesorio de lo buscado: Es la cerradura, no la puerta"
    assert sesion.peticiones[0].componente == "revision"


def test_una_sola_llamada_hasta_el_tope_de_ofertas() -> None:
    assert LOTE >= MAX_OFERTAS
    sesion = SesionFalsa(clasificar_por_palabras(REGLAS))
    ejecutar(sesion, [oferta(f"Puerta {i}") for i in range(MAX_OFERTAS)])
    assert len(sesion.peticiones) == 1
    assert sesion.peticiones[0].max_tokens == TOKENS_BASE + TOKENS_POR_OFERTA * MAX_OFERTAS


def test_el_prompt_explica_pieza_de_y_avisa_de_las_instrucciones_en_los_datos() -> None:
    sesion = SesionFalsa(clasificar_por_palabras(REGLAS))
    ejecutar(sesion, OFERTAS)
    instrucciones = sesion.peticiones[0].instrucciones
    assert "cerradura" in instrucciones  # El ejemplo de «pieza o accesorio de lo buscado».
    assert "solo por sus propios datos" in instrucciones
    assert "ignóralas" in instrucciones
    assert "vacío si es «misma_pieza»" in instrucciones


def test_las_etiquetas_dicen_que_es_cada_descarte() -> None:
    def motivo(clase: ClaseOferta) -> str | None:
        return motivo_descarte(Clasificacion(clase), "")

    assert motivo(ClaseOferta.ACCESORIO) == "IA · Pieza o accesorio de lo buscado"
    assert motivo(ClaseOferta.VARIANTE) == "IA · Variante (otro lado, medida o modelo)"
    assert motivo(ClaseOferta.KIT) == "IA · Kit"
    assert motivo(ClaseOferta.OTRA) == "IA · Otro producto"


# --- Caché por oferta --------------------------------------------------------------------------


def test_la_segunda_revision_igual_sale_de_la_cache_sin_llamar() -> None:
    cache = CacheFalsa()
    primera = SesionFalsa(clasificar_por_palabras(REGLAS))
    r1 = ejecutar(primera, OFERTAS, cache=cache, modelo="api/haiku")
    assert r1.desde_cache == 0
    segunda = SesionFalsa(clasificar_por_palabras(REGLAS))
    r2 = ejecutar(segunda, OFERTAS, cache=cache, modelo="api/haiku")
    assert segunda.peticiones == []
    assert r2.desde_cache == 2
    assert r2.clasificaciones == r1.clasificaciones


def test_el_precio_no_cuenta_para_la_cache() -> None:
    cache = CacheFalsa()
    ejecutar(SesionFalsa(clasificar_por_palabras(REGLAS)), OFERTAS, cache=cache, modelo="m")
    otra = [oferta(o.titulo, "99.00") for o in OFERTAS]
    sesion = SesionFalsa(clasificar_por_palabras(REGLAS))
    assert ejecutar(sesion, otra, cache=cache, modelo="m").desde_cache == 2


def test_cambiar_modelo_consulta_o_filtro_invalida_la_cache() -> None:
    cache = CacheFalsa()
    ejecutar(SesionFalsa(clasificar_por_palabras(REGLAS)), OFERTAS, cache=cache, modelo="m")
    for opciones in ({"modelo": "otro"}, {"modelo": "m", "filtro": "solo usadas"}):
        sesion = SesionFalsa(functools.partial(_todas_misma, filtro="filtro" in opciones))
        assert ejecutar(sesion, OFERTAS, cache=cache, **opciones).desde_cache == 0
        assert len(sesion.peticiones) == 1
    sesion = SesionFalsa(clasificar_por_palabras(REGLAS))
    asyncio.run(revisar(sesion, "faro", OFERTAS, componente="revision", cache=cache, modelo="m"))
    assert len(sesion.peticiones) == 1


def test_si_cambia_el_lote_se_vuelven_a_mandar_todas() -> None:
    # La IA ve el lote entero: el veredicto de una oferta puede depender de las demás.
    cache = CacheFalsa()
    ejecutar(SesionFalsa(clasificar_por_palabras(REGLAS)), OFERTAS[1:], cache=cache, modelo="m")
    sesion = SesionFalsa(clasificar_por_palabras(REGLAS))
    revision = ejecutar(sesion, OFERTAS, cache=cache, modelo="m")
    enviadas = ofertas_de(sesion.peticiones[0])
    assert [o["titulo"] for o in enviadas] == [o.titulo for o in OFERTAS]
    assert revision.desde_cache == 0
    assert [c.clase for c in revision.clasificaciones] == [
        ClaseOferta.MISMA_PIEZA,
        ClaseOferta.ACCESORIO,
    ]


def test_lo_que_la_ia_no_clasifica_no_se_guarda() -> None:
    cache = CacheFalsa()
    sesion = SesionFalsa({"clasificaciones": [{"indice": 1, "clase": "accesorio", "motivo": "x"}]})
    revision = ejecutar(sesion, OFERTAS, cache=cache, modelo="m")
    assert revision.clasificaciones[0] == Clasificacion()
    assert len(cache.filas) == 1


def test_una_fila_danada_de_la_cache_se_ignora() -> None:
    cache = CacheFalsa()
    ejecutar(SesionFalsa(clasificar_por_palabras(REGLAS)), OFERTAS, cache=cache, modelo="m")
    for clave in cache.filas:
        cache.filas[clave] = ("borrar_todo", "x" * 10_000, True)
    sesion = SesionFalsa(clasificar_por_palabras(REGLAS))
    revision = ejecutar(sesion, OFERTAS, cache=cache, modelo="m")
    assert len(sesion.peticiones) == 1
    assert revision.desde_cache == 0


def _todas_misma(peticion: PeticionIA, *, filtro: bool) -> dict[str, Any]:
    salida = []
    for o in ofertas_de(peticion):
        entrada: dict[str, Any] = {"indice": o["indice"], "clase": "misma_pieza", "motivo": ""}
        if filtro:
            entrada["cumple_filtro"] = True
        salida.append(entrada)
    return {"clasificaciones": salida}
