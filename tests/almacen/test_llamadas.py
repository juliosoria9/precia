"""Registro de llamadas de IA: el almacén implementa `ia.tipos.RegistroLlamadas`."""

import threading
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen, DatoNoValido
from precia.ia.tipos import RegistroLlamadas
from precia.nucleo.modelos import LlamadaIA
from tests.almacen.conftest import RelojFalso


def _llamada(fecha: datetime, coste: str = "0.0012", componente: str = "extractor") -> LlamadaIA:
    return LlamadaIA(
        fecha=fecha,
        componente=componente,
        proveedor="api",
        modelo="claude-haiku-5-5",
        tokens_entrada=12_400,
        tokens_salida=310,
        coste_usd=Decimal(coste),
        resultado="ok",
        duracion_ms=1840,
    )


def test_cumple_el_protocolo(almacen: Almacen) -> None:
    registro: RegistroLlamadas = almacen
    assert registro.gasto_mes_usd() == Decimal("0")


def test_registrar_y_listar_de_mas_reciente_a_mas_antigua(almacen: Almacen) -> None:
    vieja = _llamada(datetime(2026, 10, 1, 9, 0))
    nueva = _llamada(datetime(2026, 10, 18, 20, 14), componente="configurador")
    almacen.registrar_llamada(vieja)
    almacen.registrar_llamada(nueva)
    assert almacen.llamadas(10) == [nueva, vieja]
    assert almacen.llamadas(1) == [nueva]


def test_llamadas_valida_el_limite(almacen: Almacen) -> None:
    with pytest.raises(DatoNoValido):
        almacen.llamadas(0)
    with pytest.raises(DatoNoValido):
        almacen.llamadas(10_001)


def test_gasto_del_mes_en_curso_segun_el_reloj(almacen: Almacen, reloj: RelojFalso) -> None:
    almacen.registrar_llamada(_llamada(datetime(2026, 9, 30, 23, 59), "5"))
    almacen.registrar_llamada(_llamada(datetime(2026, 10, 1, 0, 0), "0.10"))
    almacen.registrar_llamada(_llamada(datetime(2026, 10, 19, 8, 0), "0.20"))
    almacen.registrar_llamada(_llamada(datetime(2025, 10, 5), "100"))
    # Sumado con Decimal: en coma flotante 0.1 + 0.2 daría 0.30000000000000004.
    assert almacen.gasto_mes_usd() == Decimal("0.30")
    reloj.ahora = datetime(2026, 9, 15)
    assert almacen.gasto_mes_usd() == Decimal("5")
    reloj.ahora = datetime(2026, 11, 1)
    assert almacen.gasto_mes_usd() == Decimal("0")


def test_fechas_con_zona_horaria(reloj: RelojFalso) -> None:
    reloj.ahora = datetime(2026, 10, 19, tzinfo=UTC)
    with Almacen(":memory:", reloj=reloj) as a:
        llamada = _llamada(datetime(2026, 10, 2, 7, 30, tzinfo=UTC), "0.5")
        a.registrar_llamada(llamada)
        assert a.llamadas(5) == [llamada]
        assert a.gasto_mes_usd() == Decimal("0.5")


@pytest.mark.parametrize(
    "cambios",
    [
        {"coste_usd": Decimal("-0.01")},
        {"coste_usd": Decimal("NaN")},
        {"tokens_entrada": -1},
        {"tokens_salida": 2**63},
        {"coste_usd": Decimal("1" * 51)},
        {"modelo": "claude-\udc80"},
        {"componente": "x" * 101},
        {"resultado": "x" * 501},
    ],
)
def test_registrar_llamada_valida(almacen: Almacen, cambios: dict[str, Any]) -> None:
    with pytest.raises(DatoNoValido):
        almacen.registrar_llamada(replace(_llamada(datetime(2026, 10, 1)), **cambios))
    assert almacen.llamadas(5) == []


def test_uso_desde_varios_hilos(almacen: Almacen) -> None:
    # La web y el orquestador llaman al almacén desde `asyncio.to_thread`.
    def registrar() -> None:
        for _ in range(50):
            almacen.registrar_llamada(_llamada(datetime(2026, 10, 19), "0.01"))

    hilos = [threading.Thread(target=registrar) for _ in range(4)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert len(almacen.llamadas(1000)) == 200
    assert almacen.gasto_mes_usd() == Decimal("2.00")
