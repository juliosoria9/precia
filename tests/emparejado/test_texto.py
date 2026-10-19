"""Agrupado por similitud de títulos con difflib (modo clásico)."""

import random
import time

import pytest

from precia.emparejado.texto import (
    UMBRAL_POR_DEFECTO,
    agrupar_por_texto,
    limpiar_titulo,
    normalizar_titulo,
    similitud,
)


def test_normalizar_titulo() -> None:
    texto = "  Pastillas  de FRENO " + chr(0x2013) + " Golf VI (Delanteras)! "
    assert normalizar_titulo(texto) == "pastillas de freno golf vi delanteras"
    assert normalizar_titulo("Batería AGM 70Ah") == "bateria agm 70ah"


def test_limpiar_titulo_quita_ruido_y_recorta() -> None:
    assert limpiar_titulo("  Bomba de agua\t\n Golf V  | ") == "Bomba de agua Golf V"
    assert limpiar_titulo("Pastillas\x00\x1b[31m freno") == "Pastillas[31m freno"
    largo = limpiar_titulo("Disco " * 100)
    assert len(largo) <= 120 and largo.endswith("…")


def test_similitud() -> None:
    assert similitud("Disco de freno", "disco de FRENO") == 1.0
    assert (
        similitud("Pastillas freno delanteras Golf VI", "Golf VI pastillas freno delanteras") > 0.95
    )
    assert similitud("Pastillas de freno Golf VI", "pastillas freno golf vi") > 0.95
    assert similitud("Disco de freno ventilado", "Bomba de agua Golf V") < 0.5
    assert similitud("", "") == 0.0


def test_agrupa_titulos_parecidos_de_distintas_tiendas() -> None:
    titulos = [
        "Pastillas de freno delanteras VW Golf VI",
        "Disco de freno delantero ventilado 288 mm",
        "Golf VI pastillas freno delanteras (VW)",
        "Disco freno delantero ventilado 288mm",
        "Bomba de agua Golf V 1.9 TDI",
    ]
    assert agrupar_por_texto(titulos) == [[0, 2], [1, 3], [4]]


def test_umbral_configurable() -> None:
    titulos = ["Pastillas freno delanteras Golf VI", "Pastillas freno traseras Golf VI"]
    assert agrupar_por_texto(titulos, umbral=UMBRAL_POR_DEFECTO) == [[0, 1]]
    assert agrupar_por_texto(titulos, umbral=0.95) == [[0], [1]]


def test_no_encadena_titulos_que_solo_se_parecen_por_un_intermedio() -> None:
    titulos = [
        "Pastillas freno delanteras Golf VI",
        "Pastillas freno traseras Golf VI",
        "Pastillas freno traseras Polo IV",
    ]
    assert agrupar_por_texto(titulos) == [[0, 1], [2]]


def test_sin_titulos() -> None:
    assert agrupar_por_texto([]) == []


@pytest.mark.parametrize("umbral", [0.0, -0.1, 1.5])
def test_umbral_fuera_de_rango(umbral: float) -> None:
    with pytest.raises(ValueError, match="umbral"):
        agrupar_por_texto(["a"], umbral=umbral)


def test_titulos_enormes_no_disparan_el_coste() -> None:
    a = "pastillas " * 10_000
    b = "pastillas " * 10_000 + "x"
    assert similitud(a, b) == 1.0


def test_muchos_titulos_no_bloquean_el_bucle_de_eventos() -> None:
    # agrupar() corre en el bucle de eventos. Con 150 títulos de tienda (10 tiendas x 15
    # fichas) tardaba ~2,5 s, porque cada par se normalizaba de nuevo y se comparaba entero
    # aunque las cotas baratas de difflib ya dijeran que no llegaba al umbral.
    azar = random.Random(7)
    palabras = [
        "pastillas",
        "freno",
        "delanteras",
        "traseras",
        "disco",
        "ventilado",
        "golf",
        "vi",
        "leon",
        "ibiza",
        "bosch",
        "brembo",
        "ate",
        "textar",
        "juego",
        "eje",
        "1.6",
        "tdi",
        "2.0",
        "bomba",
        "agua",
        "filtro",
        "aceite",
    ]
    titulos = [" ".join(azar.choices(palabras, k=9)) for _ in range(150)]
    inicio = time.perf_counter()
    grupos = agrupar_por_texto(titulos)
    assert time.perf_counter() - inicio < 1.2
    assert sum(len(g) for g in grupos) == 150
