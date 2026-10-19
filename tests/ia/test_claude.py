"""Cliente propio de la API de Anthropic (Messages) con un transporte falso: nunca hay red."""

import asyncio
import json
import logging
import threading
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.claude import URL_API, ProveedorClaude
from precia.ia.salida import RechazoDelModelo, SalidaNoValidaConConsumo
from precia.ia.tipos import ErrorIA, IANoDisponible, PeticionIA, SalidaNoValida
from precia.ia.transporte import ErrorTransporte
from tests.ia.falsos import EsperaFalsa, RelojFalso, TransporteFalso, respuesta_json

CLAVE = "sk-ant-api03-CLAVE-DE-PRUEBA-que-nunca-debe-aparecer"

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"precio": {"type": "string", "maxLength": 40}},
    "required": ["precio"],
    "additionalProperties": False,
}

PETICION = PeticionIA(
    componente="extractor",
    instrucciones="Extrae el precio de la ficha.",
    datos="Bomba de agua Bosch 38,90 € IVA incluido",
    esquema=ESQUEMA,
    max_tokens=512,
)


def mensaje_ok(
    texto: str = '{"precio": "38,90 €"}',
    *,
    stop_reason: str = "end_turn",
    entrada: int = 1200,
    salida: int = 30,
) -> tuple[int, dict[str, str], bytes]:
    return respuesta_json(
        200,
        {
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": texto}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": entrada, "output_tokens": salida},
        },
    )


def error_api(estado: int, tipo: str, mensaje: str = "fallo", **cab: str) -> Any:
    return respuesta_json(
        estado, {"type": "error", "error": {"type": tipo, "message": mensaje}}, dict(cab)
    )


def proveedor(transporte: TransporteFalso, espera: EsperaFalsa | None = None) -> ProveedorClaude:
    return ProveedorClaude(
        CLAVE,
        transporte=transporte,
        dormir=espera or EsperaFalsa(),
        azar=lambda: 0.5,
        reloj=RelojFalso(),
    )


def completar(p: ProveedorClaude, peticion: PeticionIA = PETICION) -> Any:
    return asyncio.run(p.completar(peticion))


def test_peticion_bien_formada() -> None:
    transporte = TransporteFalso([mensaje_ok()])
    completar(proveedor(transporte))
    (llamada,) = transporte.llamadas
    assert llamada.url == URL_API == "https://api.anthropic.com/v1/messages"
    cab = {k.lower(): v for k, v in llamada.cabeceras.items()}
    assert cab["x-api-key"] == CLAVE
    assert cab["anthropic-version"] == "2023-06-01"
    assert cab["content-type"] == "application/json"
    cuerpo = llamada.json
    assert cuerpo["model"] == "claude-haiku-5-5"
    assert cuerpo["max_tokens"] == 512
    assert "Extrae el precio de la ficha." in cuerpo["system"]
    (mensaje,) = cuerpo["messages"]
    assert mensaje["role"] == "user"
    assert "Bomba de agua Bosch" in mensaje["content"]
    assert "<<<DATOS-" in mensaje["content"]
    formato = cuerpo["output_config"]["format"]
    assert formato["type"] == "json_schema"
    # La API no admite maxLength: se quita del esquema enviado (el cliente lo valida).
    assert formato["schema"]["properties"]["precio"] == {"type": "string"}
    assert cuerpo["output_config"]["effort"] == "low"


def test_respuesta_correcta() -> None:
    respuesta = completar(proveedor(TransporteFalso([mensaje_ok()])))
    assert respuesta.datos == {"precio": "38,90 €"}
    assert respuesta.tokens_entrada == 1200
    assert respuesta.tokens_salida == 30
    assert respuesta.coste_usd == Decimal("0.10") * 1200 / 10**6 + Decimal("0.50") * 30 / 10**6
    assert respuesta.proveedor == "api"
    assert respuesta.modelo == "claude-haiku-5-5"
    assert respuesta.duracion_ms == 250


def test_el_transporte_se_ejecuta_en_otro_hilo() -> None:
    transporte = TransporteFalso([mensaje_ok()])
    completar(proveedor(transporte))
    assert transporte.llamadas[0].hilo != threading.get_ident()


def test_ignora_bloques_que_no_son_texto() -> None:
    estado, cab, _ = mensaje_ok()
    cuerpo = {
        "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": '{"precio": "1"}'},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    respuesta = completar(proveedor(TransporteFalso([(estado, cab, json.dumps(cuerpo).encode())])))
    assert respuesta.datos == {"precio": "1"}


def test_salida_que_no_cumple_el_esquema() -> None:
    transporte = TransporteFalso([mensaje_ok('{"precio": 38.9}')])
    with pytest.raises(SalidaNoValida) as error:
        completar(proveedor(transporte))
    # Se pagó: el error lleva el consumo para registrarlo.
    assert isinstance(error.value, SalidaNoValidaConConsumo)
    assert error.value.consumo.tokens_entrada == 1200
    assert len(transporte.llamadas) == 1


def test_valida_en_cliente_las_restricciones_que_la_api_no_admite() -> None:
    texto = json.dumps({"precio": "x" * 41})
    with pytest.raises(SalidaNoValida):
        completar(proveedor(TransporteFalso([mensaje_ok(texto)])))


def test_texto_que_no_es_json() -> None:
    with pytest.raises(SalidaNoValida):
        completar(proveedor(TransporteFalso([mensaje_ok("El precio es 38,90 €")])))


def test_max_tokens_es_salida_incompleta() -> None:
    transporte = TransporteFalso([mensaje_ok('{"precio": "38,', stop_reason="max_tokens")])
    with pytest.raises(SalidaNoValida, match="incompleta"):
        completar(proveedor(transporte))


def test_refusal_no_se_reintenta() -> None:
    transporte = TransporteFalso([mensaje_ok("", stop_reason="refusal"), mensaje_ok()])
    with pytest.raises(ErrorIA) as error:
        completar(proveedor(transporte))
    assert isinstance(error.value, RechazoDelModelo)
    assert not isinstance(error.value, SalidaNoValida)
    assert len(transporte.llamadas) == 1


def test_429_respeta_retry_after() -> None:
    espera = EsperaFalsa()
    transporte = TransporteFalso(
        [error_api(429, "rate_limit_error", **{"retry-after": "7"}), mensaje_ok()]
    )
    respuesta = completar(proveedor(transporte, espera))
    assert respuesta.datos == {"precio": "38,90 €"}
    assert len(transporte.llamadas) == 2
    assert espera.esperas == [7.0]


def test_500_y_529_se_reintentan_con_espera_exponencial() -> None:
    espera = EsperaFalsa()
    transporte = TransporteFalso(
        [
            error_api(500, "api_error"),
            error_api(529, "overloaded_error"),
            error_api(529, "overloaded_error"),
            mensaje_ok(),
        ]
    )
    completar(proveedor(transporte, espera))
    assert len(transporte.llamadas) == 4
    # Base 1 s, el doble en cada intento y jitter fijo en 0,75 (azar = 0,5).
    assert espera.esperas == [0.75, 1.5, 3.0]


def test_se_rinde_tras_agotar_los_reintentos() -> None:
    transporte = TransporteFalso([error_api(529, "overloaded_error")] * 4)
    with pytest.raises(IANoDisponible, match="sobrecargad"):
        completar(proveedor(transporte))
    assert len(transporte.llamadas) == 4


def test_retry_after_excesivo_no_se_espera() -> None:
    espera = EsperaFalsa()
    transporte = TransporteFalso([error_api(429, "rate_limit_error", **{"retry-after": "3600"})])
    with pytest.raises(IANoDisponible, match="límite"):
        completar(proveedor(transporte, espera))
    assert espera.esperas == []


@pytest.mark.parametrize("estado", [401, 403])
def test_clave_rechazada(estado: int) -> None:
    transporte = TransporteFalso([error_api(estado, "authentication_error", "invalid x-api-key")])
    with pytest.raises(IANoDisponible) as error:
        completar(proveedor(transporte))
    assert "clave" in str(error.value)
    assert CLAVE not in str(error.value)
    assert len(transporte.llamadas) == 1


@pytest.mark.parametrize("estado", [400, 404])
def test_errores_no_reintentables(estado: int) -> None:
    transporte = TransporteFalso([error_api(estado, "invalid_request_error", "schema too complex")])
    with pytest.raises(ErrorIA) as error:
        completar(proveedor(transporte))
    assert not isinstance(error.value, IANoDisponible | SalidaNoValida)
    assert "schema too complex" in str(error.value)
    assert len(transporte.llamadas) == 1


def test_errores_de_red_persistentes() -> None:
    transporte = TransporteFalso([ErrorTransporte("timed out")] * 4)
    with pytest.raises(IANoDisponible, match="conectar"):
        completar(proveedor(transporte))
    assert len(transporte.llamadas) == 4


def test_cuerpo_ilegible() -> None:
    with pytest.raises(ErrorIA):
        completar(proveedor(TransporteFalso([(200, {}, b"<html>proxy</html>")])))


def test_sin_clave() -> None:
    with pytest.raises(IANoDisponible, match="ANTHROPIC_API_KEY"):
        ProveedorClaude("  ")


def test_modelo_sin_tarifa() -> None:
    with pytest.raises(ErrorIA, match="tarifa"):
        ProveedorClaude(CLAVE, modelo="modelo-inventado")


def test_la_clave_nunca_aparece_en_repr_errores_ni_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    p = proveedor(
        TransporteFalso(
            [
                error_api(500, "api_error", f"eco de la cabecera: {CLAVE}"),
                ErrorTransporte(f"fallo con {CLAVE}"),
                error_api(400, "invalid_request_error", f"clave {CLAVE} no válida"),
            ]
        )
    )
    assert CLAVE not in repr(p) and CLAVE not in str(p)
    with pytest.raises(ErrorIA) as error:
        completar(p)
    textos = [str(error.value), repr(error.value), caplog.text]
    causa: BaseException | None = error.value
    while causa is not None:
        textos.append(repr(causa))
        causa = causa.__cause__ or causa.__context__
    assert all(CLAVE not in t for t in textos)


@pytest.mark.parametrize("clave", [f"{CLAVE}\nX-Otra: 1", f"{CLAVE}ñ", "sk ant con espacios"])
def test_clave_con_caracteres_no_validos(clave: str) -> None:
    # http.client copiaría la cabecera entera (la clave) en el mensaje del ValueError.
    with pytest.raises(IANoDisponible, match="ANTHROPIC_API_KEY") as error:
        ProveedorClaude(clave, transporte=TransporteFalso([]))
    assert CLAVE not in str(error.value) and "sk ant" not in str(error.value)


def test_respuesta_demasiado_anidada() -> None:
    profunda: tuple[int, dict[str, str], bytes] = (200, {}, b"[" * 100_000 + b"]" * 100_000)
    with pytest.raises(ErrorIA, match="ilegible"):
        completar(proveedor(TransporteFalso([profunda])))
    error_profundo: tuple[int, dict[str, str], bytes] = (
        400,
        {},
        b'{"error": ' + b"[" * 100_000 + b"]" * 100_000 + b"}",
    )
    with pytest.raises(ErrorIA, match="400"):
        completar(proveedor(TransporteFalso([error_profundo])))


def test_esquema_no_admitido_falla_antes_de_pagar() -> None:
    transporte = TransporteFalso([mensaje_ok()])
    esquema = {"type": "object", "properties": {"precio": {"type": "string", "pattern": "€$"}}}
    malo = PeticionIA(componente="extractor", instrucciones="x", datos="y", esquema=esquema)
    with pytest.raises(ValueError, match="pattern"):
        completar(proveedor(transporte), malo)
    assert transporte.llamadas == []
