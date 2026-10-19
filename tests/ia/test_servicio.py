"""Servicio de IA: tope de llamadas por búsqueda, presupuesto mensual, reintento y registro."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.salida import Consumo, RechazoDelModelo
from precia.ia.servicio import ServicioIA, SesionIA
from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import (
    ErrorIA,
    IANoDisponible,
    PeticionIA,
    PresupuestoAgotado,
    RespuestaIA,
    SalidaNoValida,
)
from precia.nucleo.modelos import LlamadaIA
from tests.ia.falsos import RegistroFalso

AHORA = datetime(2026, 10, 19, 12, 30, tzinfo=UTC)
COSTE = Decimal("0.001")
ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"precio": {"type": "string"}},
    "required": ["precio"],
    "additionalProperties": False,
}
PETICION = PeticionIA(
    componente="extractor", instrucciones="Extrae.", datos="Precio 9 €", esquema=ESQUEMA
)
BUENA: dict[str, Any] = {"precio": "9 €"}
MALA: dict[str, Any] = {"precio": 9}


def montar(
    guion: list[Guion],
    *,
    tope: int = 10,
    presupuesto: Decimal = Decimal("10"),
    registro: RegistroFalso | None = None,
) -> tuple[ServicioIA, ProveedorSimulado, RegistroFalso]:
    proveedor = ProveedorSimulado({"extractor": guion}, nombre="api", modelo="m", coste_usd=COSTE)
    registro = registro or RegistroFalso()
    servicio = ServicioIA(
        proveedor,
        registro,
        presupuesto_mes_usd=presupuesto,
        tope_llamadas=tope,
        reloj=lambda: AHORA,
    )
    return servicio, proveedor, registro


def pedir(sesion: SesionIA, peticion: PeticionIA = PETICION) -> RespuestaIA:
    return asyncio.run(sesion.pedir(peticion))


def test_llamada_correcta_se_registra_y_se_suma() -> None:
    servicio, _, registro = montar([BUENA])
    sesion = servicio.nueva_busqueda()
    respuesta = pedir(sesion)
    assert respuesta.datos == BUENA
    assert (sesion.llamadas, sesion.coste_usd) == (1, COSTE)
    (llamada,) = registro.llamadas
    assert llamada == LlamadaIA(
        fecha=AHORA,
        componente="extractor",
        proveedor="api",
        modelo="m",
        tokens_entrada=100,
        tokens_salida=20,
        coste_usd=COSTE,
        resultado="ok",
        duracion_ms=5,
    )


def test_tope_de_llamadas_por_busqueda() -> None:
    servicio, proveedor, _ = montar([BUENA] * 5, tope=2)
    sesion = servicio.nueva_busqueda()
    pedir(sesion)
    pedir(sesion)
    with pytest.raises(PresupuestoAgotado, match="2"):
        pedir(sesion)
    assert len(proveedor.peticiones) == 2
    # Cada búsqueda tiene su propio contador.
    otra = servicio.nueva_busqueda()
    pedir(otra)
    assert (otra.llamadas, sesion.llamadas) == (1, 2)


def test_presupuesto_mensual_agotado_antes_de_empezar() -> None:
    servicio, proveedor, registro = montar([BUENA], registro=RegistroFalso(Decimal("10")))
    with pytest.raises(PresupuestoAgotado, match="presupuesto"):
        pedir(servicio.nueva_busqueda())
    assert proveedor.peticiones == [] and registro.llamadas == []


def test_presupuesto_que_se_agota_durante_la_busqueda() -> None:
    servicio, proveedor, _ = montar([BUENA] * 5, presupuesto=Decimal("0.0015"))
    sesion = servicio.nueva_busqueda()
    pedir(sesion)
    pedir(sesion)
    with pytest.raises(PresupuestoAgotado):
        pedir(sesion)
    assert len(proveedor.peticiones) == 2


def test_reintenta_una_vez_si_la_salida_no_es_valida() -> None:
    servicio, proveedor, registro = montar([MALA, BUENA])
    sesion = servicio.nueva_busqueda()
    assert pedir(sesion).datos == BUENA
    assert len(proveedor.peticiones) == 2
    assert [ll.resultado for ll in registro.llamadas] == ["salida_no_valida", "ok"]
    # El intento fallido también se pagó y cuenta.
    assert (sesion.llamadas, sesion.coste_usd) == (2, 2 * COSTE)
    assert registro.llamadas[0].coste_usd == COSTE


def test_solo_un_reintento() -> None:
    servicio, proveedor, registro = montar([MALA, "no es json", BUENA])
    with pytest.raises(SalidaNoValida):
        pedir(servicio.nueva_busqueda())
    assert len(proveedor.peticiones) == 2
    assert len(registro.llamadas) == 2


def test_el_reintento_respeta_el_tope() -> None:
    servicio, proveedor, _ = montar([MALA, BUENA], tope=1)
    with pytest.raises(SalidaNoValida):
        pedir(servicio.nueva_busqueda())
    assert len(proveedor.peticiones) == 1


def test_otros_errores_se_registran_y_no_se_reintentan() -> None:
    servicio, proveedor, registro = montar([IANoDisponible("sin red"), BUENA])
    sesion = servicio.nueva_busqueda()
    with pytest.raises(IANoDisponible):
        pedir(sesion)
    assert len(proveedor.peticiones) == 1
    (llamada,) = registro.llamadas
    assert (llamada.resultado, llamada.coste_usd, llamada.tokens_entrada) == (
        "no_disponible",
        Decimal(0),
        0,
    )
    assert sesion.llamadas == 1


def test_rechazo_del_modelo_se_registra_con_su_coste() -> None:
    rechazo = RechazoDelModelo("rechazo", Consumo(50, 3, Decimal("0.0002")))
    servicio, _, registro = montar([rechazo])
    sesion = servicio.nueva_busqueda()
    with pytest.raises(ErrorIA):
        pedir(sesion)
    (llamada,) = registro.llamadas
    assert (llamada.resultado, llamada.coste_usd, llamada.tokens_entrada) == (
        "rechazo",
        Decimal("0.0002"),
        50,
    )
    assert sesion.coste_usd == Decimal("0.0002")


def test_las_llamadas_concurrentes_no_superan_el_tope() -> None:
    servicio, proveedor, _ = montar([BUENA] * 5, tope=2)
    sesion = servicio.nueva_busqueda()

    async def tres_a_la_vez() -> list[RespuestaIA | BaseException]:
        return await asyncio.gather(
            *(sesion.pedir(PETICION) for _ in range(3)), return_exceptions=True
        )

    resultados = asyncio.run(tres_a_la_vez())
    assert sum(isinstance(r, PresupuestoAgotado) for r in resultados) == 1
    assert len(proveedor.peticiones) == 2


def test_el_registro_no_guarda_contenido() -> None:
    servicio, _, registro = montar([BUENA])
    pedir(servicio.nueva_busqueda())
    texto = repr(registro.llamadas)
    assert "Precio 9 €" not in texto and "Extrae." not in texto and "9 €" not in texto


@pytest.mark.parametrize(("tope", "presupuesto"), [(-1, Decimal(1)), (5, Decimal(-1))])
def test_parametros_no_validos(tope: int, presupuesto: Decimal) -> None:
    with pytest.raises(ValueError):
        ServicioIA(
            ProveedorSimulado({}),
            RegistroFalso(),
            presupuesto_mes_usd=presupuesto,
            tope_llamadas=tope,
        )
