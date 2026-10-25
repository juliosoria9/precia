"""Reserva de llamadas: la revisión de los resultados siempre tiene su llamada en la búsqueda."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.servicio import ServicioIA
from precia.ia.simulado import ProveedorSimulado
from precia.ia.tipos import PeticionIA, PresupuestoAgotado
from tests.ia.falsos import RegistroFalso

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
EXTRACTOR = PeticionIA(componente="extractor", instrucciones="x", datos="y", esquema=ESQUEMA)
REVISION = replace(EXTRACTOR, componente="revision")


def montar(tope: int, reservadas: tuple[str, ...]) -> ServicioIA:
    respuestas = [{"ok": True}] * 10
    proveedor = ProveedorSimulado({"extractor": respuestas, "revision": respuestas})
    return ServicioIA(
        proveedor,
        RegistroFalso(),
        presupuesto_mes_usd=Decimal(10),
        tope_llamadas=tope,
        reservadas=reservadas,
    )


def test_los_demas_no_pueden_usar_la_llamada_reservada() -> None:
    sesion = montar(3, ("revision",)).nueva_busqueda()
    asyncio.run(sesion.pedir(EXTRACTOR))
    asyncio.run(sesion.pedir(EXTRACTOR))
    with pytest.raises(PresupuestoAgotado):
        asyncio.run(sesion.pedir(EXTRACTOR))
    asyncio.run(sesion.pedir(REVISION))
    assert sesion.por_componente == {"extractor": 2, "revision": 1}


def test_despues_de_usarla_ya_no_se_reserva_nada() -> None:
    sesion = montar(3, ("revision",)).nueva_busqueda()
    asyncio.run(sesion.pedir(REVISION))
    asyncio.run(sesion.pedir(EXTRACTOR))
    asyncio.run(sesion.pedir(EXTRACTOR))
    with pytest.raises(PresupuestoAgotado):
        asyncio.run(sesion.pedir(REVISION))


def test_sin_reservas_como_siempre() -> None:
    sesion = montar(2, ()).nueva_busqueda()
    asyncio.run(sesion.pedir(EXTRACTOR))
    asyncio.run(sesion.pedir(EXTRACTOR))
    assert sesion.llamadas == 2
