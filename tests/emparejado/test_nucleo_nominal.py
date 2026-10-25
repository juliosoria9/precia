"""Regla sin IA del núcleo nominal: «cerradura de la puerta…» no es «puerta…»."""

import pytest

from precia.emparejado.nucleo_nominal import encabeza, nucleo, pieza_de

PUERTA = "puerta delantera derecha"


@pytest.mark.parametrize(
    ("consulta", "esperado"),
    [
        (PUERTA, "puerta"),
        ("Faros delanteros", "faro"),
        ("la puerta del Ibiza", "puerta"),
        ("kit embrague", "kit"),
        ("1K0941005", None),
        ("1K0941005 faro", "faro"),
        ("", None),
        ("   ", None),
    ],
)
def test_nucleo_es_la_primera_palabra_de_contenido(consulta: str, esperado: str | None) -> None:
    assert nucleo(consulta) == esperado


def test_el_caso_de_julio_la_cerradura_es_pieza_de_la_puerta() -> None:
    motivo = pieza_de("Cerradura de la puerta delantera derecha", PUERTA)
    assert motivo is not None
    assert "«puerta»" in motivo
    assert "cerradura" in motivo


@pytest.mark.parametrize(
    "titulo",
    [
        "Moldura para puerta delantera derecha",
        "Recambio original cerradura de puerta delantera derecha",
        "Bombín eléctrico de la puerta delantera derecha",
        "Cerradura compatible con puerta delantera derecha",
    ],
)
def test_sustantivo_de_o_para_el_nucleo_es_pieza_de(titulo: str) -> None:
    assert pieza_de(titulo, PUERTA) is not None


def test_funciona_con_la_consulta_en_plural() -> None:
    assert pieza_de("Bombilla H7 para faro delantero", "faros delanteros") is not None
    assert pieza_de("Faro delantero izquierdo", "faros delanteros") is None


@pytest.mark.parametrize(
    "titulo",
    [
        "Puerta delantera derecha Seat Ibiza",
        "Puertas delanteras derechas",
        "SEAT Puerta delantera derecha",
        "VW 5K0 Puerta delantera derecha con cerradura",
        "PUERTA DELANTERA DERECHA SEAT IBIZA",
        "Puerta delantera derecha \u2013 cerradura incluida",
        "Recambio puerta delantera derecha nueva",
    ],
)
def test_el_nucleo_en_cabeza_no_objeta(titulo: str) -> None:
    assert pieza_de(titulo, PUERTA) is None
    assert encabeza(titulo, PUERTA)


@pytest.mark.parametrize(
    ("titulo", "consulta"),
    [
        # En recambios, «kit/juego de X» es X: el producto normal, no una pieza de X.
        ("Kit de embrague Valeo Golf IV", "embrague golf"),
        ("Juego de pastillas de freno delanteras TRW", "pastillas de freno"),
        ("Juego de pastillas de freno delanteras TRW", "pastillas freno"),
        ("Set de las bujías NGK", "bujías"),
        ("Kit de puerta delantera derecha", PUERTA),
    ],
)
def test_kit_de_lo_buscado_es_lo_buscado(titulo: str, consulta: str) -> None:
    assert pieza_de(titulo, consulta) is None
    assert encabeza(titulo, consulta)


def test_kit_de_una_pieza_de_lo_buscado_sigue_siendo_pieza_de() -> None:
    motivo = pieza_de("Kit de reparación de la puerta delantera derecha", PUERTA)
    assert motivo is not None
    assert "reparacion" in motivo


def test_kit_para_lo_buscado_es_kit() -> None:
    motivo = pieza_de("Kit para puerta delantera derecha", PUERTA)
    assert motivo is not None
    assert "kit" in motivo.lower()


def test_si_se_busca_un_kit_el_titulo_del_kit_encabeza() -> None:
    assert encabeza("Kit de embrague Valeo", "kit embrague")
    assert pieza_de("Kit de embrague Valeo", "kit embrague") is None


def test_kit_sin_el_nucleo_no_dice_nada() -> None:
    assert pieza_de("Kit de distribución", PUERTA) is None


def test_si_la_consulta_es_la_pieza_de_algo_no_objeta() -> None:
    assert pieza_de("Cerradura de puerta delantera", "cerradura puerta") is None


@pytest.mark.parametrize(
    "titulo",
    [
        "Front right door lock",  # Núcleo al final, en inglés: la regla no lo ve (límite).
        "Elevalunas puerta delantera derecha",  # Sin preposición: conservadora, no objeta.
        "Faro delantero",  # Otra cosa, sin el núcleo: no le toca a esta regla.
        "",
    ],
)
def test_lo_que_la_regla_no_ve_no_lo_marca(titulo: str) -> None:
    assert pieza_de(titulo, PUERTA) is None


def test_sin_nucleo_en_la_consulta_no_objeta() -> None:
    titulo = "Cerradura de la puerta delantera derecha"
    assert pieza_de(titulo, "") is None
    assert pieza_de(titulo, "1K0941005") is None
    assert not encabeza(titulo, "")


def test_encabeza_falla_si_va_detras_de_otro_sustantivo() -> None:
    assert not encabeza("Cerradura de la puerta delantera derecha", PUERTA)
    assert not encabeza("Front right door lock", PUERTA)


def test_titulos_hostiles_largos_no_se_cuelgan() -> None:
    titulo = "de " * 50_000 + "puerta"
    assert pieza_de(titulo, PUERTA) is None
