from decimal import Decimal

import pytest

from precia.nucleo.modelos import Modo
from precia.web.formularios import (
    ErrorFormulario,
    consulta_desde_parametros,
    datos_de_alta,
    leer_formulario,
    leer_importe,
    leer_version,
)


def test_leer_formulario_admite_valores_repetidos() -> None:
    datos = leer_formulario(b"campos=precio&campos=marca&texto=pastillas+freno")
    assert datos == {"campos": ["precio", "marca"], "texto": ["pastillas freno"]}


def test_leer_formulario_rechaza_utf8_invalido() -> None:
    with pytest.raises(ErrorFormulario):
        leer_formulario(b"texto=%FF%FE")


def test_consulta_desde_parametros() -> None:
    consulta = consulta_desde_parametros(
        {
            "texto": ["  1K0698151 "],
            "tiendas": ["a.example", "b.example"],
            "campos": ["precio", "inventado"],
            "modo": ["ia"],
            "descubrir": ["on"],
            "filtro": ["sin kits"],
        }
    )
    assert consulta.texto == "1K0698151"
    assert consulta.tiendas == ("a.example", "b.example")
    assert consulta.campos == ("precio",)
    assert consulta.modo is Modo.IA
    assert consulta.descubrir
    assert consulta.filtro == "sin kits"


def test_consulta_por_defecto() -> None:
    consulta = consulta_desde_parametros({"texto": ["x"]})
    assert consulta.modo is Modo.AUTO
    assert "precio" in consulta.campos
    assert not consulta.descubrir


def test_consulta_sin_texto_falla() -> None:
    with pytest.raises(ErrorFormulario):
        consulta_desde_parametros({"texto": ["   "]})


def test_consulta_con_modo_invalido_usa_auto() -> None:
    assert consulta_desde_parametros({"texto": ["x"], "modo": ["hack"]}).modo is Modo.AUTO


def test_texto_demasiado_largo_falla() -> None:
    with pytest.raises(ErrorFormulario):
        consulta_desde_parametros({"texto": ["x" * 201]})


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [("30", "30"), ("30,50", "30.50"), ("30.5", "30.5"), (" 1.234,56 ", "1234.56")],
)
def test_leer_importe(entrada: str, esperado: str) -> None:
    assert leer_importe(entrada) == Decimal(esperado)


@pytest.mark.parametrize("entrada", ["", "gratis", "-5", "0", "NaN", "Infinity", "1e9"])
def test_leer_importe_invalido(entrada: str) -> None:
    with pytest.raises(ErrorFormulario):
        leer_importe(entrada)


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [("1.234", "1234"), ("1.234,5", "1234.5"), ("12.345,60", "12345.60")],
)
def test_importe_con_puntos_de_miles(entrada: str, esperado: str) -> None:
    assert leer_importe(entrada) == Decimal(esperado)


@pytest.mark.parametrize("entrada", ["1.2.3,4", "1.23,4", "12.345.678", "٣٠", "³", "30\u00a0€"])
def test_importe_mal_formado_o_con_digitos_no_ascii_falla(entrada: str) -> None:
    with pytest.raises(ErrorFormulario):
        leer_importe(entrada)


def test_datos_de_alta_recorta_espacios() -> None:
    assert datos_de_alta({"dominio": [" tienda.es "], "nombre": [" Mi tienda "]}) == (
        "tienda.es",
        "Mi tienda",
    )
    assert datos_de_alta({"dominio": ["tienda.es"]}) == ("tienda.es", "")


@pytest.mark.parametrize(
    "formulario",
    [
        {},
        {"dominio": ["  "]},
        {"dominio": ["a" * 254]},
        {"dominio": ["t.es"], "nombre": ["x\x00y"]},
        {"dominio": ["t.es"], "nombre": ["n" * 101]},
    ],
)
def test_datos_de_alta_no_validos(formulario: dict[str, list[str]]) -> None:
    with pytest.raises(ErrorFormulario):
        datos_de_alta(formulario)


@pytest.mark.parametrize(("texto", "version"), [("1", 1), (" 12 ", 12), ("007", 7)])
def test_leer_version(texto: str, version: int) -> None:
    assert leer_version(texto) == version


@pytest.mark.parametrize("texto", ["", "0", "-1", "1.5", "²", "٣", "1" * 10, "v1"])
def test_leer_version_no_valida(texto: str) -> None:
    with pytest.raises(ErrorFormulario):
        leer_version(texto)
