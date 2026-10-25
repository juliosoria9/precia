"""Proveedor simulado: respuestas guionizadas por componente para los tests de otros paquetes."""

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.simulado import ProveedorSimulado
from precia.ia.tipos import ErrorIA, IANoDisponible, PeticionIA, SalidaNoValida

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"precio": {"type": "string"}},
    "required": ["precio"],
    "additionalProperties": False,
}


def peticion(componente: str = "extractor") -> PeticionIA:
    return PeticionIA(componente=componente, instrucciones="i", datos="d", esquema=ESQUEMA)


def test_devuelve_las_respuestas_en_orden_por_componente() -> None:
    simulado = ProveedorSimulado(
        {"extractor": [{"precio": "1 €"}, {"precio": "2 €"}], "reparador": [{"precio": "R"}]},
        coste_usd=Decimal("0.001"),
    )

    async def guion() -> list[str]:
        r1 = await simulado.completar(peticion())
        r2 = await simulado.completar(peticion("reparador"))
        r3 = await simulado.completar(peticion())
        return [r1.datos["precio"], r2.datos["precio"], r3.datos["precio"]]

    assert asyncio.run(guion()) == ["1 €", "R", "2 €"]
    assert [p.componente for p in simulado.peticiones] == ["extractor", "reparador", "extractor"]


def test_metricas_de_la_respuesta() -> None:
    simulado = ProveedorSimulado({"extractor": [{"precio": "1"}]}, coste_usd=Decimal("0.002"))
    respuesta = asyncio.run(simulado.completar(peticion()))
    assert respuesta.coste_usd == Decimal("0.002")
    assert respuesta.proveedor == simulado.nombre == "simulado"
    assert respuesta.modelo == simulado.modelo
    assert respuesta.tokens_entrada > 0 and respuesta.tokens_salida > 0


def test_valida_las_respuestas_contra_el_esquema() -> None:
    simulado = ProveedorSimulado({"extractor": [{"precio": 1}, "no es json"]})
    with pytest.raises(SalidaNoValida):
        asyncio.run(simulado.completar(peticion()))
    with pytest.raises(SalidaNoValida):
        asyncio.run(simulado.completar(peticion()))


def test_texto_json_en_bruto() -> None:
    simulado = ProveedorSimulado({"extractor": ['{"precio": "3 €"}']})
    assert asyncio.run(simulado.completar(peticion())).datos == {"precio": "3 €"}


def test_lanza_las_excepciones_guionizadas() -> None:
    simulado = ProveedorSimulado({"extractor": [IANoDisponible("sin red")]})
    with pytest.raises(IANoDisponible, match="sin red"):
        asyncio.run(simulado.completar(peticion()))


def test_sin_respuestas_para_el_componente() -> None:
    simulado = ProveedorSimulado({"extractor": [{"precio": "1"}]})
    with pytest.raises(ErrorIA, match="configurador"):
        asyncio.run(simulado.completar(peticion("configurador")))
    asyncio.run(simulado.completar(peticion()))
    with pytest.raises(ErrorIA, match="extractor"):
        asyncio.run(simulado.completar(peticion()))
