"""Detección y normalización de referencias OEM en textos de tiendas."""

import time

import pytest

from precia.emparejado.referencias import (
    MAX_TEXTO,
    es_referencia,
    normalizar_referencia,
    referencias_en,
)


@pytest.mark.parametrize(
    "texto",
    [" 1k0-698 151 ", "1K0.698.151", "1k0/698/151", "1K0698151", "1K0_698_151"],
)
def test_normalizar_referencia_quita_separadores_y_pasa_a_mayusculas(texto: str) -> None:
    assert normalizar_referencia(texto) == "1K0698151"


def test_es_referencia_exige_cinco_caracteres_y_un_digito() -> None:
    assert es_referencia("1K0698151")
    assert es_referencia("0986494104")
    assert not es_referencia("A4B6")
    assert not es_referencia("DELANTERAS")


def test_referencias_en_titulo_de_tienda() -> None:
    titulo = "Pastillas de freno delanteras VW Golf VI 1K0698151 Brembo P85020"
    assert referencias_en(titulo) == {"1K0698151", "P85020"}


@pytest.mark.parametrize(
    "texto",
    [
        "Ref. OEM: 1K0 698 151",
        "Juego pastillas (1K0-698-151) eje delantero",
        "REF.1K0698151 - envío 24 h",
        "pastillas 1k0698151 originales",
    ],
)
def test_referencias_escritas_de_varias_formas(texto: str) -> None:
    assert "1K0698151" in referencias_en(texto)


def test_referencia_bosch_escrita_por_grupos() -> None:
    assert referencias_en("Pastillas Bosch 0 986 494 104") == {"0986494104"}


@pytest.mark.parametrize(
    "texto",
    [
        "Pastillas delanteras Golf VI 2008-2012",
        "Motor usado 120000 km",
        "Batería 60Ah 540A",
        "Precio 1.234,56 € IVA incl.",
        "Disco ventilado 288 mm",
        "Golf 6 2010 pastillas",
        "Audi A4 B8 delanteras",
        "",
    ],
)
def test_no_confunde_años_medidas_ni_precios_con_referencias(texto: str) -> None:
    assert referencias_en(texto) == set()


def test_texto_enorme_se_recorta() -> None:
    texto = "x " * MAX_TEXTO + "1K0698151"
    assert referencias_en(texto) == set()


@pytest.mark.parametrize(
    "texto",
    [
        "Turbo Garrett Golf IV 1.9TDI 105cv",
        "Inyector Peugeot 308 1.6HDI",
        "Centralita motor A3 2.0TFSI Euro5",
        "Caudalímetro Focus 1,8TDCI",
    ],
)
def test_cilindrada_con_motor_y_norma_euro_no_son_referencias(texto: str) -> None:
    # «1.9TDI» sale en el título de piezas muy distintas del mismo coche: si contara como
    # referencia, un turbo y un inyector acabarían en el mismo grupo «Referencia OEM exacta».
    assert referencias_en(texto) == set()


@pytest.mark.parametrize("texto", ["1" * MAX_TEXTO, "1." * (MAX_TEXTO // 2), "9," * 5000])
def test_cadenas_largas_de_cifras_no_disparan_el_coste(texto: str) -> None:
    # Un título hostil hecho solo de cifras hacía que la expresión de precios fuese
    # cuadrática (~3 s por llamada con 10 000 caracteres) y bloqueaba el bucle de eventos.
    inicio = time.perf_counter()
    referencias_en(texto)
    assert time.perf_counter() - inicio < 0.2
