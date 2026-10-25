"""Servicio de IA: tope de llamadas por búsqueda, presupuesto mensual, reintento y registro."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.salida import Consumo, RechazoDelModelo, SalidaCortada
from precia.ia.servicio import TECHO_MAX_TOKENS, LlamadasEnVuelo, ServicioIA, SesionIA
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


def test_el_reintento_por_salida_mal_formada_repite_la_misma_peticion() -> None:
    servicio, proveedor, _ = montar([MALA, BUENA])
    pedir(servicio.nueva_busqueda())
    assert proveedor.peticiones == [PETICION, PETICION]


# --- R2-TOK: salida cortada por el tope de tokens ----------------------------------------------


def cortada() -> SalidaCortada:
    return SalidaCortada(
        "Salida incompleta: se ha alcanzado el máximo de tokens", Consumo(16_579, 768, COSTE)
    )


def test_salida_cortada_se_repite_con_el_doble_de_tokens() -> None:
    """Repetirla igual se volvería a cortar: en la ronda 2 se pagaron 2 x 16 579 en balde."""
    servicio, proveedor, registro = montar([cortada(), BUENA])
    sesion = servicio.nueva_busqueda()
    assert pedir(sesion, replace(PETICION, max_tokens=768)).datos == BUENA
    assert [p.max_tokens for p in proveedor.peticiones] == [768, 1536]
    assert proveedor.peticiones[1] == replace(PETICION, max_tokens=1536)
    assert [ll.resultado for ll in registro.llamadas] == ["salida_no_valida", "ok"]
    assert registro.llamadas[0].tokens_entrada == 16_579
    assert (sesion.llamadas, sesion.coste_usd) == (2, 2 * COSTE)


def test_el_doble_no_pasa_del_techo() -> None:
    servicio, proveedor, _ = montar([cortada(), BUENA])
    pedir(servicio.nueva_busqueda(), replace(PETICION, max_tokens=TECHO_MAX_TOKENS - 100))
    assert [p.max_tokens for p in proveedor.peticiones] == [
        TECHO_MAX_TOKENS - 100,
        TECHO_MAX_TOKENS,
    ]


def test_salida_cortada_en_el_techo_no_se_repite() -> None:
    servicio, proveedor, registro = montar([cortada(), BUENA])
    with pytest.raises(SalidaCortada):
        pedir(servicio.nueva_busqueda(), replace(PETICION, max_tokens=TECHO_MAX_TOKENS))
    assert len(proveedor.peticiones) == 1
    assert [ll.resultado for ll in registro.llamadas] == ["salida_no_valida"]


def test_salida_cortada_dos_veces_no_se_repite_mas() -> None:
    servicio, proveedor, _ = montar([cortada(), cortada(), BUENA])
    with pytest.raises(SalidaCortada):
        pedir(servicio.nueva_busqueda(), replace(PETICION, max_tokens=512))
    assert [p.max_tokens for p in proveedor.peticiones] == [512, 1024]


def test_el_reintento_de_una_salida_cortada_respeta_el_tope() -> None:
    servicio, proveedor, _ = montar([cortada(), BUENA], tope=1)
    with pytest.raises(SalidaCortada):
        pedir(servicio.nueva_busqueda(), replace(PETICION, max_tokens=512))
    assert len(proveedor.peticiones) == 1


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


def test_cuenta_las_llamadas_por_componente_y_expone_el_modelo() -> None:
    # A3: el desglose por componente dice en los resultados para qué se usó la IA.
    proveedor = ProveedorSimulado(
        {"configurador": [BUENA], "extractor": [MALA, BUENA]}, nombre="simulado", modelo="m1"
    )
    servicio = ServicioIA(
        proveedor, RegistroFalso(), presupuesto_mes_usd=Decimal(10), tope_llamadas=10
    )
    sesion = servicio.nueva_busqueda()
    pedir(sesion, PeticionIA("configurador", "Configura.", "<html>", ESQUEMA))
    pedir(sesion)
    assert sesion.por_componente == {"configurador": 1, "extractor": 2}
    # El reintento también cuenta: la suma es la de `llamadas`.
    assert sum(sesion.por_componente.values()) == sesion.llamadas == 3
    assert sesion.modelo == "simulado/m1"


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


# --- C1: una llamada cuya búsqueda se cancela se cobra igual ----------------------------------


class ProveedorLento(ProveedorSimulado):
    """Responde cuando se suelta `soltar` (la llamada a la API sigue en su hilo, C1)."""

    def __init__(self, soltar: asyncio.Event) -> None:
        super().__init__({"extractor": [BUENA]}, nombre="api", modelo="m", coste_usd=COSTE)
        self.soltar = soltar

    async def completar(self, peticion: PeticionIA) -> RespuestaIA:
        await self.soltar.wait()
        return await super().completar(peticion)


def test_llamada_cancelada_se_registra_al_terminar() -> None:
    registro = RegistroFalso()

    async def escenario() -> None:
        soltar = asyncio.Event()
        servicio = ServicioIA(
            ProveedorLento(soltar),
            registro,
            presupuesto_mes_usd=Decimal(10),
            tope_llamadas=10,
            reloj=lambda: AHORA,
        )
        sesion = servicio.nueva_busqueda()
        tarea = asyncio.create_task(sesion.pedir(PETICION))
        await asyncio.sleep(0)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert registro.llamadas == []
        soltar.set()
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(escenario())
    (llamada,) = registro.llamadas
    assert (llamada.coste_usd, llamada.resultado) == (COSTE, "ok")
    assert registro.gasto_mes_usd() == COSTE


def test_las_llamadas_canceladas_quedan_en_vuelo_hasta_registrarse() -> None:
    # Revisión: al apagar, la llamada protegida seguía en su hilo y nadie la esperaba: al
    # cerrarse el bucle (o la base de datos) no se registraba, aunque el proveedor la cobrara.
    registro = RegistroFalso()
    en_vuelo = LlamadasEnVuelo()

    async def escenario() -> None:
        soltar = asyncio.Event()
        servicio = ServicioIA(
            ProveedorLento(soltar),
            registro,
            presupuesto_mes_usd=Decimal(10),
            tope_llamadas=10,
            reloj=lambda: AHORA,
            en_vuelo=en_vuelo,
        )
        tarea = asyncio.create_task(servicio.nueva_busqueda().pedir(PETICION))
        await asyncio.sleep(0)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert len(en_vuelo) == 1
        asyncio.get_running_loop().call_later(0.01, soltar.set)
        await en_vuelo.esperar(5)
        assert len(en_vuelo) == 0
        assert [ll.coste_usd for ll in registro.llamadas] == [COSTE]

    asyncio.run(escenario())


def test_esperar_las_llamadas_en_vuelo_tiene_un_plazo() -> None:
    async def escenario() -> None:
        en_vuelo = LlamadasEnVuelo()
        nunca: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        en_vuelo.anotar(nunca)
        await asyncio.wait_for(en_vuelo.esperar(0.01), 1)
        assert len(en_vuelo) == 1

    asyncio.run(escenario())


def test_llamada_cancelada_que_falla_se_registra_con_su_error() -> None:
    registro = RegistroFalso()

    async def escenario() -> None:
        soltar = asyncio.Event()
        proveedor = ProveedorLento(soltar)
        proveedor._colas["extractor"].clear()  # Sin respuestas: el simulado lanza ErrorIA.
        servicio = ServicioIA(
            proveedor, registro, presupuesto_mes_usd=Decimal(10), tope_llamadas=10
        )
        tarea = asyncio.create_task(servicio.nueva_busqueda().pedir(PETICION))
        await asyncio.sleep(0)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        soltar.set()
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(escenario())
    assert [ll.resultado for ll in registro.llamadas] == ["error"]
