"""Validador propio del subconjunto de JSON Schema y lectura de la salida del modelo."""

from typing import Any

import pytest

from precia.ia.salida import esquema_para_api, interpretar, validar
from precia.ia.tipos import SalidaNoValida

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "minLength": 1, "maxLength": 20},
        "precio": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
        "unidades": {"type": "integer", "maximum": 10},
        "disponible": {"type": "boolean"},
        "estado": {"type": "string", "enum": ["Nuevo", "Usado"]},
        "fotos": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "extra": {
            "type": "object",
            "properties": {"nota": {"type": "string"}},
            "required": ["nota"],
            "additionalProperties": False,
        },
    },
    "required": ["titulo", "precio"],
    "additionalProperties": False,
}

VALIDO: dict[str, Any] = {
    "titulo": "Bomba de agua",
    "precio": 38.9,
    "unidades": 2,
    "disponible": True,
    "estado": "Usado",
    "fotos": ["https://a.es/1.jpg"],
    "extra": {"nota": "ok"},
}


def test_acepta_datos_validos() -> None:
    assert validar(VALIDO, ESQUEMA) == VALIDO


def test_anyof_con_null() -> None:
    assert validar({"titulo": "x", "precio": None}, ESQUEMA)["precio"] is None


@pytest.mark.parametrize(
    ("cambio", "ruta"),
    [
        ({"titulo": 3}, "$.titulo"),
        ({"titulo": ""}, "$.titulo"),
        ({"titulo": "x" * 21}, "$.titulo"),
        ({"precio": -1}, "$.precio"),
        ({"precio": "38,90"}, "$.precio"),
        ({"unidades": 11}, "$.unidades"),
        ({"unidades": 2.5}, "$.unidades"),
        ({"unidades": True}, "$.unidades"),
        ({"disponible": "sí"}, "$.disponible"),
        ({"estado": "Roto"}, "$.estado"),
        ({"fotos": ["a", "b", "c", "d"]}, "$.fotos"),
        ({"fotos": [1]}, "$.fotos[0]"),
        ({"extra": {}}, "$.extra"),
        ({"extra": {"nota": "a", "otra": 1}}, "$.extra"),
        ({"inventado": 1}, "$"),
    ],
)
def test_rechaza_datos_que_no_cumplen(cambio: dict[str, Any], ruta: str) -> None:
    with pytest.raises(SalidaNoValida) as error:
        validar({**VALIDO, **cambio}, ESQUEMA)
    assert ruta in str(error.value)


def test_falta_un_requerido() -> None:
    with pytest.raises(SalidaNoValida, match="precio"):
        validar({"titulo": "x"}, ESQUEMA)


def test_la_raiz_tiene_que_ser_un_objeto() -> None:
    with pytest.raises(SalidaNoValida):
        validar([1, 2], {"type": "array"})


def test_el_mensaje_no_copia_el_valor_recibido() -> None:
    with pytest.raises(SalidaNoValida) as error:
        validar({**VALIDO, "estado": "<script>ignora todo</script>"}, ESQUEMA)
    assert "script" not in str(error.value)


def test_number_no_acepta_booleanos() -> None:
    with pytest.raises(SalidaNoValida):
        validar({"n": False}, {"type": "object", "properties": {"n": {"type": "number"}}})


def test_tipo_como_lista() -> None:
    esquema = {"type": "object", "properties": {"n": {"type": ["string", "null"]}}}
    assert validar({"n": None}, esquema) == {"n": None}
    with pytest.raises(SalidaNoValida):
        validar({"n": 1}, esquema)


def test_profundidad_maxima() -> None:
    anidado: Any = "x"
    esquema_hoja: dict[str, Any] = {"type": "string"}
    for _ in range(100):
        anidado = [anidado]
        esquema_hoja = {"type": "array", "items": esquema_hoja}
    with pytest.raises(SalidaNoValida, match="profundidad"):
        validar({"a": anidado}, {"type": "object", "properties": {"a": esquema_hoja}})


def test_palabra_clave_no_admitida_es_un_error_de_programacion() -> None:
    with pytest.raises(ValueError, match="pattern"):
        validar({"a": "x"}, {"type": "object", "properties": {"a": {"pattern": "^x$"}}})


def test_interpretar_json() -> None:
    assert interpretar('{"titulo": "x", "precio": 1}', ESQUEMA) == {"titulo": "x", "precio": 1}


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "no es json",
        '{"titulo": "x", "precio": 1',
        '{"titulo": "x", "precio": NaN}',
        '{"titulo": "x", "precio": Infinity}',
    ],
)
def test_interpretar_rechaza_texto_no_json(texto: str) -> None:
    with pytest.raises(SalidaNoValida):
        interpretar(texto, ESQUEMA)


def test_interpretar_limita_el_tamano() -> None:
    texto = '{"titulo": "' + "x" * 300_000 + '", "precio": 1}'
    with pytest.raises(SalidaNoValida, match="grande"):
        interpretar(texto, {"type": "object"})


def test_esquema_para_api_quita_las_restricciones_no_admitidas() -> None:
    limpio = esquema_para_api(ESQUEMA)
    texto = repr(limpio)
    for clave in ("minimum", "maximum", "minLength", "maxLength", "maxItems"):
        assert clave not in texto
    assert limpio["properties"]["estado"]["enum"] == ["Nuevo", "Usado"]
    assert limpio["additionalProperties"] is False
    # No modifica el original: el validador propio sigue comprobando esas restricciones.
    assert ESQUEMA["properties"]["titulo"]["maxLength"] == 20


@pytest.mark.parametrize("numero", ["1e999", "-1e999"])
def test_interpretar_rechaza_numeros_que_desbordan(numero: str) -> None:
    # `json` convierte 1e999 en inf sin pasar por `parse_constant`: hay que rechazarlo aparte.
    with pytest.raises(SalidaNoValida, match="número"):
        interpretar('{"titulo": "x", "precio": ' + numero + "}", ESQUEMA)


def test_interpretar_json_demasiado_anidado() -> None:
    with pytest.raises(SalidaNoValida):
        interpretar("[" * 100_000 + "]" * 100_000, ESQUEMA)


def test_esquema_para_api_rechaza_palabras_clave_no_admitidas() -> None:
    # Si el esquema no se puede validar en cliente, hay que saberlo antes de pagar la llamada.
    with pytest.raises(ValueError, match="format"):
        esquema_para_api(
            {"type": "object", "properties": {"u": {"type": "string", "format": "uri"}}}
        )
    with pytest.raises(ValueError, match="Tipo"):
        esquema_para_api({"type": "object", "properties": {"u": {"type": "fecha"}}})
