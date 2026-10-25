"""Detección y normalización de referencias OEM en textos de tiendas."""

import pytest

from precia.emparejado.referencias import (
    MAX_TEXTO,
    es_referencia,
    es_revision,
    normalizar_referencia,
    referencias_de_consulta,
    referencias_en,
)
from tests.cronometro import segundos_de_cpu


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
    assert segundos_de_cpu(lambda: referencias_en(texto), 0.2) < 0.2


# --- Sufijo de revisión en una referencia por grupos (bug 7) -----------------------------------


@pytest.mark.parametrize(
    ("texto", "esperadas"),
    [
        ("1K0 941 005 E", {"1K0941005E"}),
        ("Faro 1K0 941 005 E Golf", {"1K0941005E"}),
        ("1K0 615 301 AA", {"1K0615301AA"}),
    ],
)
def test_sufijo_de_revision_escrito_por_grupos_no_se_pierde(
    texto: str, esperadas: set[str]
) -> None:
    # Antes, la unión por grupos se paraba en la letra: «1K0 941 005 E» daba 1K0941005 y una
    # revisión pasaba por la referencia exacta.
    assert referencias_en(texto) == esperadas


@pytest.mark.parametrize(
    ("texto", "esperadas"),
    [
        ("1K0 698 151 Y 1K0 698 151 A", {"1K0698151", "1K0698151A"}),
        ("PASTILLAS 1K0 698 151 VW", {"1K0698151"}),
        ("1K0 698 151 de Golf", {"1K0698151"}),
        ("1K0 941 005 e", {"1K0941005"}),
        # Casos reales de las páginas guardadas (medida de B5): una etiqueta («ID:») tras dos
        # precios quitados y una abreviatura («PT.», puertas) no son sufijos de revisión.
        ("RENAULT 7700 846 640 80,59 € 89,54 € ID: 35952", {"7700846640", "35952"}),
        ("306 35 PT. S225 306 35 PT. S101", {"S22530635"}),
        # Revisión: el lado de la pieza tampoco es un sufijo de revisión.
        ("Retrovisor 1K0 857 507 LH", {"1K0857507"}),
        ("Faro 1K0 941 005 IZ Valeo", {"1K0941005"}),
        ("FARO 1K0 941 005 RH", {"1K0941005"}),
        ("PILOTO 1K0 945 095 DX", {"1K0945095"}),
    ],
)
def test_letras_que_no_son_sufijo_no_se_pegan(texto: str, esperadas: set[str]) -> None:
    assert referencias_en(texto) == esperadas


@pytest.mark.parametrize(
    "consulta", ["1k0 941 005 e", "1K0 941 005 E", "1k0941005e", "faro 1k0 941 005 e golf"]
)
def test_en_la_consulta_el_sufijo_cuenta_en_minusculas(consulta: str) -> None:
    # Revisión: la consulta la escribe el usuario, no la web; «1k0 941 005 e» perdía la «e» y
    # la buscada pasaba a ser la referencia base.
    assert referencias_de_consulta(consulta) == {"1K0941005E"}


def test_una_a_suelta_da_la_referencia_con_y_sin_sufijo() -> None:
    # «A» es la revisión de muchas referencias y también una preposición («… A ESTRENAR»): con
    # las dos, la ficha con la referencia exacta no pasa por revisión.
    assert referencias_en("PASTILLAS 1K0 698 151 A ESTRENAR") == {"1K0698151", "1K0698151A"}


@pytest.mark.parametrize(
    ("referencia", "buscada", "esperado"),
    [
        ("1K0941005E", "1K0941005", True),
        ("1K0615301AA", "1K0615301", True),
        ("1K0941005", "1K0941005", False),  # Es la exacta, no una revisión.
        ("1K0941005EAB", "1K0941005", False),
        ("1K09410051", "1K0941005", False),  # El sufijo es una cifra: otra referencia.
        ("1K0941005", "1K0941005E", False),  # Dirección inversa: fuera de la decisión de Julio.
        ("X1K0941005E", "1K0941005", False),
    ],
)
def test_es_revision(referencia: str, buscada: str, esperado: bool) -> None:
    assert es_revision(referencia, buscada) is esperado
