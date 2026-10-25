"""B6: el presupuesto del mes se comprueba de forma atómica entre búsquedas simultáneas.

Cada búsqueda abre su `SesionIA`, pero las llamadas que están en vuelo (reservadas y aún sin
registrar) de todas las búsquedas cuentan como gastadas por su coste máximo estimado: dos
búsquedas no pueden colarse a la vez por el último hueco del presupuesto.
"""

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.costes import calcular_coste
from precia.ia.servicio import LlamadasEnVuelo, ServicioIA, coste_maximo
from precia.ia.tipos import ErrorIA, PeticionIA, PresupuestoAgotado, RespuestaIA
from tests.ia.falsos import RegistroFalso

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
PETICION = PeticionIA(
    componente="extractor", instrucciones="x" * 100, datos="y" * 900, esquema=ESQUEMA
)
MODELO = "claude-haiku-5-5"


class ProveedorLento:
    """Responde cuando se abre `puerta`, con un coste fijo (o falla con `error`)."""

    nombre = "api"
    modelo = MODELO

    def __init__(self, coste: Decimal = Decimal("0.0001"), error: ErrorIA | None = None) -> None:
        self.puerta = asyncio.Event()
        self.coste = coste
        self.error = error
        self.llamadas = 0

    async def completar(self, peticion: PeticionIA) -> RespuestaIA:
        self.llamadas += 1
        await self.puerta.wait()
        if self.error is not None:
            raise self.error
        return RespuestaIA({"ok": True}, 10, 2, self.coste, 5, self.nombre, self.modelo)


def _servicio(
    proveedor: ProveedorLento, registro: RegistroFalso, en_vuelo: LlamadasEnVuelo
) -> ServicioIA:
    return ServicioIA(
        proveedor,
        registro,
        presupuesto_mes_usd=Decimal(1),
        tope_llamadas=10,
        en_vuelo=en_vuelo,
    )


def test_coste_maximo_cuenta_la_entrada_a_la_baja_y_todo_el_tope_de_salida() -> None:
    entrada = (100 + 900) // 2  # CARACTERES_POR_TOKEN = 2: estimación por lo alto
    assert coste_maximo("api", MODELO, PETICION) == calcular_coste(
        "api", MODELO, entrada, PETICION.max_tokens
    )
    assert coste_maximo("ollama", "qwen", PETICION) == 0
    assert coste_maximo("simulado", "sin-tarifa", PETICION) == 0


def test_dos_busquedas_no_se_cuelan_a_la_vez_por_el_ultimo_hueco() -> None:
    async def escenario() -> None:
        maximo = coste_maximo("api", MODELO, PETICION)
        registro = RegistroFalso(gasto_previo=Decimal(1) - maximo / 2)
        en_vuelo = LlamadasEnVuelo()
        proveedor = ProveedorLento()
        una = _servicio(proveedor, registro, en_vuelo).nueva_busqueda()
        otra = _servicio(proveedor, registro, en_vuelo).nueva_busqueda()
        primera = asyncio.create_task(una.pedir(PETICION))
        await asyncio.sleep(0)
        assert en_vuelo.apartado_usd == maximo
        with pytest.raises(PresupuestoAgotado, match="presupuesto"):
            await otra.pedir(PETICION)
        proveedor.puerta.set()
        await primera
        # Ya registrada por su coste real: la reserva se suelta y queda hueco otra vez.
        assert en_vuelo.apartado_usd == 0
        await otra.pedir(PETICION)
        assert proveedor.llamadas == 2

    asyncio.run(escenario())


def test_un_error_del_proveedor_tambien_suelta_la_reserva() -> None:
    async def escenario() -> None:
        en_vuelo = LlamadasEnVuelo()
        proveedor = ProveedorLento(error=ErrorIA("caída"))
        sesion = _servicio(proveedor, RegistroFalso(), en_vuelo).nueva_busqueda()
        tarea = asyncio.create_task(sesion.pedir(PETICION))
        await asyncio.sleep(0)
        assert en_vuelo.apartado_usd > 0
        proveedor.puerta.set()
        with pytest.raises(ErrorIA):
            await tarea
        assert en_vuelo.apartado_usd == 0

    asyncio.run(escenario())


def test_cancelada_la_reserva_sigue_hasta_que_la_llamada_se_registra() -> None:
    async def escenario() -> None:
        en_vuelo = LlamadasEnVuelo()
        registro = RegistroFalso()
        proveedor = ProveedorLento()
        sesion = _servicio(proveedor, registro, en_vuelo).nueva_busqueda()
        tarea = asyncio.create_task(sesion.pedir(PETICION))
        await asyncio.sleep(0)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        # La llamada sigue en su hilo y se cobrará: su reserva no se suelta todavía.
        assert en_vuelo.apartado_usd > 0
        proveedor.puerta.set()
        await en_vuelo.esperar(1.0)
        await asyncio.sleep(0)
        assert len(registro.llamadas) == 1
        assert en_vuelo.apartado_usd == 0

    asyncio.run(escenario())
