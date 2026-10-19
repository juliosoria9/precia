"""Proveedor local con Ollama (`/api/chat`), con un transporte falso."""

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.ollama import ProveedorOllama
from precia.ia.salida import SalidaNoValidaConConsumo
from precia.ia.tipos import ErrorIA, IANoDisponible, PeticionIA, SalidaNoValida
from precia.ia.transporte import ErrorTransporte
from tests.ia.falsos import RelojFalso, TransporteFalso, respuesta_json

MODELO = "qwen2.5:7b-instruct-q4_K_M"
ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"marca": {"anyOf": [{"type": "string", "maxLength": 30}, {"type": "null"}]}},
    "required": ["marca"],
    "additionalProperties": False,
}
PETICION = PeticionIA(
    componente="extractor",
    instrucciones="Extrae la marca.",
    datos="Filtro de aceite MANN W 712/75",
    esquema=ESQUEMA,
    max_tokens=300,
)


def chat(contenido: str, *, done_reason: str = "stop") -> tuple[int, dict[str, str], bytes]:
    return respuesta_json(
        200,
        {
            "model": MODELO,
            "message": {"role": "assistant", "content": contenido},
            "done": True,
            "done_reason": done_reason,
            "prompt_eval_count": 850,
            "eval_count": 12,
        },
    )


def proveedor(transporte: TransporteFalso, url: str = "http://127.0.0.1:11434") -> ProveedorOllama:
    return ProveedorOllama(url=url, modelo=MODELO, transporte=transporte, reloj=RelojFalso())


def completar(p: ProveedorOllama) -> Any:
    return asyncio.run(p.completar(PETICION))


def test_peticion_bien_formada() -> None:
    transporte = TransporteFalso([chat('{"marca": "MANN"}')])
    completar(proveedor(transporte, "http://127.0.0.1:11434/"))
    (llamada,) = transporte.llamadas
    assert llamada.url == "http://127.0.0.1:11434/api/chat"
    assert {k.lower() for k in llamada.cabeceras} == {"content-type"}
    cuerpo = llamada.json
    assert cuerpo["model"] == MODELO
    assert cuerpo["stream"] is False
    assert cuerpo["options"] == {"temperature": 0, "num_predict": 300}
    assert cuerpo["format"]["properties"]["marca"]["anyOf"][0] == {"type": "string"}
    sistema, usuario = cuerpo["messages"]
    assert sistema["role"] == "system" and "Extrae la marca." in sistema["content"]
    assert usuario["role"] == "user" and "MANN W 712/75" in usuario["content"]


def test_respuesta_correcta_y_gratis() -> None:
    respuesta = completar(proveedor(TransporteFalso([chat('{"marca": "MANN"}')])))
    assert respuesta.datos == {"marca": "MANN"}
    assert (respuesta.tokens_entrada, respuesta.tokens_salida) == (850, 12)
    assert respuesta.coste_usd == Decimal(0)
    assert respuesta.proveedor == "ollama"
    assert respuesta.modelo == MODELO
    assert respuesta.duracion_ms == 250


def test_salida_cortada_por_longitud() -> None:
    with pytest.raises(SalidaNoValidaConConsumo, match="incompleta"):
        completar(proveedor(TransporteFalso([chat('{"marca": "MA', done_reason="length")])))


def test_salida_que_no_cumple() -> None:
    texto = json.dumps({"marca": "M" * 31})
    with pytest.raises(SalidaNoValida):
        completar(proveedor(TransporteFalso([chat(texto)])))


def test_sin_servidor() -> None:
    with pytest.raises(IANoDisponible, match="Ollama"):
        completar(proveedor(TransporteFalso([ErrorTransporte("Connection refused")])))


def test_modelo_no_instalado() -> None:
    transporte = TransporteFalso([respuesta_json(404, {"error": "model not found"})])
    with pytest.raises(IANoDisponible, match="ollama pull"):
        completar(proveedor(transporte))


def test_error_del_servidor() -> None:
    transporte = TransporteFalso([respuesta_json(500, {"error": "out of memory"})])
    with pytest.raises(ErrorIA) as error:
        completar(proveedor(transporte))
    assert not isinstance(error.value, IANoDisponible)


def test_respuesta_ilegible() -> None:
    with pytest.raises(ErrorIA):
        completar(proveedor(TransporteFalso([(200, {}, b"no json")])))


@pytest.mark.parametrize(
    "url", ["file:///tmp/x", "127.0.0.1:11434", "http://", "http://u:p@127.0.0.1:11434"]
)
def test_url_no_valida(url: str) -> None:
    with pytest.raises(ValueError):
        ProveedorOllama(url=url, modelo=MODELO)


@pytest.mark.parametrize("url", ["http://127.0.0.1:99999", "http://127.0.0.1:abc"])
def test_puerto_no_valido(url: str) -> None:
    with pytest.raises(ValueError, match="puerto"):
        ProveedorOllama(url=url, modelo=MODELO)


def test_respuesta_demasiado_anidada() -> None:
    profunda: tuple[int, dict[str, str], bytes] = (200, {}, b"[" * 100_000 + b"]" * 100_000)
    with pytest.raises(ErrorIA, match="ilegible"):
        completar(proveedor(TransporteFalso([profunda])))
