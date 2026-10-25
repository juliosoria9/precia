"""Búsquedas en segundo plano (C1): lanzar, seguir, cancelar, limpiar y cerrar, sin red.

Cada escenario corre en un solo bucle de asyncio (los `Event` se crean dentro): `asyncio.run`
cancela al salir las tareas que queden pendientes.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from decimal import Decimal

import pytest

from precia.nucleo.fuente_datos import ErrorFuente
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    EstadoTienda,
    Modo,
    ResultadoBusqueda,
    Seguimiento,
)
from precia.nucleo.tareas import (
    FALLIDA,
    MAX_BUSQUEDAS_SIMULTANEAS,
    DemasiadasBusquedas,
    EstadoBusqueda,
    GestorBusquedas,
    TareaBusqueda,
)

TIENDAS = ("a.example", "b.example")


def consulta(texto: str = "faro golf") -> ConsultaBusqueda:
    return ConsultaBusqueda(texto, tiendas=TIENDAS, modo=Modo.CLASICO)


def resultado(c: ConsultaBusqueda, *, provisional: bool = False) -> ResultadoBusqueda:
    return ResultadoBusqueda(c, (), (), (), 0, Decimal(0), 0.0, provisional=provisional)


class FuenteFalsa(FuenteDemo):
    """Avisa DESCARGANDO, termina la primera tienda con un parcial y espera a `soltar`."""

    def __init__(self, *, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error
        self.consultas: list[ConsultaBusqueda] = []
        self.canceladas = 0
        self.soltar: asyncio.Event | None = None

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        assert seguimiento is not None
        self.consultas.append(consulta)
        for dominio in consulta.tiendas:
            seguimiento.tienda(dominio, EstadoTienda.DESCARGANDO)
        seguimiento.tienda(consulta.tiendas[0], EstadoTienda.HECHA)
        seguimiento.parcial(resultado(consulta, provisional=True))
        if self.soltar is None:
            self.soltar = asyncio.Event()
        try:
            await self.soltar.wait()
        except asyncio.CancelledError:
            self.canceladas += 1
            raise
        if self.error is not None:
            raise self.error
        for dominio in consulta.tiendas[1:]:
            seguimiento.tienda(dominio, EstadoTienda.HECHA)
        return resultado(consulta)


async def _hasta(condicion: Callable[[], bool], vueltas: int = 10_000) -> None:
    for _ in range(vueltas):
        if condicion():
            return
        await asyncio.sleep(0)
    raise AssertionError("La condición no se ha cumplido a tiempo")


def _en_curso(tarea: TareaBusqueda) -> Callable[[], bool]:
    return lambda: tarea.estado is EstadoBusqueda.EN_CURSO and tarea.resultado is not None


def test_lanzar_crea_una_tarea_en_cola_con_sus_tiendas() -> None:
    async def escenario() -> None:
        gestor = GestorBusquedas(FuenteFalsa())
        tarea = gestor.lanzar(consulta(), TIENDAS)
        assert re.fullmatch(r"[0-9a-f]{16}", tarea.id)
        assert tarea.estado is EstadoBusqueda.EN_COLA and tarea.activa
        assert list(tarea.tiendas.items()) == [(d, EstadoTienda.EN_COLA) for d in TIENDAS]
        assert tarea.resultado is None
        assert gestor.tarea(tarea.id) is tarea
        assert gestor.tarea("0123456789abcdef") is None
        await gestor.cerrar()

    asyncio.run(escenario())


def test_la_tarea_avanza_y_termina_con_el_resultado_final() -> None:
    fuente = FuenteFalsa()

    async def escenario() -> None:
        fuente.soltar = asyncio.Event()
        gestor = GestorBusquedas(fuente)
        tarea = gestor.lanzar(consulta(), TIENDAS)
        version = tarea.version
        await _hasta(_en_curso(tarea))
        assert tarea.version > version
        assert tarea.tiendas == {
            "a.example": EstadoTienda.HECHA,
            "b.example": EstadoTienda.DESCARGANDO,
        }
        assert tarea.resultado is not None and tarea.resultado.provisional
        assert tarea.terminadas == 1
        version = tarea.version
        fuente.soltar.set()
        await gestor.esperar(tarea, 1)
        assert tarea.estado is EstadoBusqueda.TERMINADA and not tarea.activa
        assert tarea.version > version
        assert tarea.resultado is not None and not tarea.resultado.provisional
        assert tarea.terminadas == 2
        assert tarea.error == ""

    asyncio.run(escenario())


def test_misma_consulta_en_curso_reutiliza_la_tarea() -> None:
    fuente = FuenteFalsa()

    async def escenario() -> None:
        fuente.soltar = asyncio.Event()
        gestor = GestorBusquedas(fuente)
        primera = gestor.lanzar(consulta(), TIENDAS)
        assert gestor.lanzar(consulta(), TIENDAS) is primera  # Doble clic.
        fuente.soltar.set()
        await gestor.esperar(primera, 1)
        assert len(fuente.consultas) == 1
        otra = gestor.lanzar(consulta(), TIENDAS)  # «Actualizar precios»: una nueva.
        assert otra.id != primera.id
        await gestor.esperar(otra, 1)
        assert len(fuente.consultas) == 2

    asyncio.run(escenario())


def test_varias_busquedas_a_la_vez_y_la_siguiente_espera_en_cola() -> None:
    # B6 (decisión de Julio): la segunda búsqueda ya no espera a que termine la primera.
    assert MAX_BUSQUEDAS_SIMULTANEAS == 3
    fuente = FuenteFalsa()

    async def escenario() -> None:
        fuente.soltar = asyncio.Event()
        gestor = GestorBusquedas(fuente)
        textos = ["faro", "freno", "filtro", "bujía"]
        tareas = [gestor.lanzar(consulta(t), TIENDAS) for t in textos]
        for tarea in tareas[:3]:
            await _hasta(_en_curso(tarea))
        for _ in range(50):
            await asyncio.sleep(0)
        assert (tareas[3].estado, tareas[3].resultado) == (EstadoBusqueda.EN_COLA, None)
        assert [c.texto for c in fuente.consultas] == textos[:3]
        fuente.soltar.set()
        await gestor.esperar(tareas[3], 1)
        assert tareas[3].estado is EstadoBusqueda.TERMINADA
        assert [c.texto for c in fuente.consultas] == textos

    asyncio.run(escenario())


def test_demasiadas_en_cola_lanza_demasiadas_busquedas() -> None:
    async def escenario() -> None:
        gestor = GestorBusquedas(FuenteFalsa(), simultaneas=1, max_en_cola=1)
        gestor.lanzar(consulta("faro"), TIENDAS)
        gestor.lanzar(consulta("freno"), TIENDAS)
        with pytest.raises(DemasiadasBusquedas) as error:
            gestor.lanzar(consulta("filtro"), TIENDAS)
        assert isinstance(error.value, ErrorFuente)
        assert "espera" in str(error.value)
        # La misma consulta que ya está en marcha no cuenta: se reutiliza.
        gestor.lanzar(consulta("freno"), TIENDAS)
        await gestor.cerrar()

    asyncio.run(escenario())


def test_cancelar_marca_cancelada_conserva_el_parcial_y_las_tiendas_sin_acabar() -> None:
    fuente = FuenteFalsa()

    async def escenario() -> None:
        fuente.soltar = asyncio.Event()
        gestor = GestorBusquedas(fuente)
        tarea = gestor.lanzar(consulta(), TIENDAS)
        await _hasta(_en_curso(tarea))
        assert await gestor.cancelar(tarea.id) is tarea
        assert tarea.estado is EstadoBusqueda.CANCELADA and not tarea.activa
        assert fuente.canceladas == 1
        assert tarea.resultado is not None and tarea.resultado.provisional
        assert tarea.tiendas == {
            "a.example": EstadoTienda.HECHA,
            "b.example": EstadoTienda.CANCELADA,
        }
        assert await gestor.cancelar("0123456789abcdef") is None

    asyncio.run(escenario())


def test_cancelar_una_en_cola_no_llega_a_buscar() -> None:
    fuente = FuenteFalsa()

    async def escenario() -> None:
        fuente.soltar = asyncio.Event()
        gestor = GestorBusquedas(fuente, simultaneas=1)
        primera = gestor.lanzar(consulta("faro"), TIENDAS)
        segunda = gestor.lanzar(consulta("freno"), TIENDAS)
        await _hasta(_en_curso(primera))
        await gestor.cancelar(segunda.id)
        assert segunda.estado is EstadoBusqueda.CANCELADA
        assert set(segunda.tiendas.values()) == {EstadoTienda.CANCELADA}
        fuente.soltar.set()
        await gestor.esperar(primera, 1)
        assert [c.texto for c in fuente.consultas] == ["faro"]

    asyncio.run(escenario())


def test_un_error_de_la_fuente_marca_fallida_y_se_registra(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fuente = FuenteFalsa(error=RuntimeError("detalle interno C:/ruta/secreta"))

    async def escenario() -> TareaBusqueda:
        fuente.soltar = asyncio.Event()
        fuente.soltar.set()
        gestor = GestorBusquedas(fuente)
        tarea = gestor.lanzar(consulta(), TIENDAS)
        await gestor.esperar(tarea, 1)
        return tarea

    with caplog.at_level(logging.ERROR, logger="precia.nucleo.tareas"):
        tarea = asyncio.run(escenario())
    assert tarea.estado is EstadoBusqueda.FALLIDA and not tarea.activa
    assert tarea.error == FALLIDA and "secreta" not in tarea.error
    assert any(r.exc_info for r in caplog.records)


def test_las_terminadas_caducan_tras_el_ttl_y_hay_un_maximo() -> None:
    ahora = [1000.0]
    fuente = FuenteFalsa()

    async def escenario() -> None:
        fuente.soltar = asyncio.Event()
        fuente.soltar.set()
        gestor = GestorBusquedas(fuente, ttl_s=10, maximo=2, reloj=lambda: ahora[0])
        ids = []
        for texto in ("faro", "freno", "filtro"):
            tarea = gestor.lanzar(consulta(texto), TIENDAS)
            await gestor.esperar(tarea, 1)
            ids.append(tarea.id)
            ahora[0] += 1
        # Como mucho 2 terminadas: se va la más antigua.
        assert [gestor.tarea(i) is not None for i in ids] == [False, True, True]
        ahora[0] += 8.5  # La de «freno» terminó hace 10,5 s; la de «filtro», hace 9,5 s.
        assert [gestor.tarea(i) is not None for i in ids] == [False, False, True]
        ahora[0] += 1
        assert gestor.tarea(ids[2]) is None

    asyncio.run(escenario())


def test_cerrar_cancela_y_espera_las_tareas_en_curso() -> None:
    fuente = FuenteFalsa()

    async def escenario() -> None:
        fuente.soltar = asyncio.Event()
        gestor = GestorBusquedas(fuente, simultaneas=1)
        primera = gestor.lanzar(consulta("faro"), TIENDAS)
        segunda = gestor.lanzar(consulta("freno"), TIENDAS)
        await _hasta(_en_curso(primera))
        await gestor.cerrar()
        assert fuente.canceladas == 1
        assert primera.estado is EstadoBusqueda.CANCELADA
        assert segunda.estado is EstadoBusqueda.CANCELADA

    asyncio.run(escenario())


def test_funciona_en_bucles_distintos() -> None:
    # El cliente ASGI de los tests abre un bucle por petición: el semáforo no puede quedarse
    # atado al primero («bound to a different event loop»).
    fuente = FuenteFalsa()
    gestor = GestorBusquedas(fuente)

    async def escenario(texto: str) -> None:
        fuente.soltar = asyncio.Event()
        primera = gestor.lanzar(consulta(texto), TIENDAS)
        segunda = gestor.lanzar(consulta(texto + " delantero"), TIENDAS)
        await _hasta(_en_curso(primera))
        fuente.soltar.set()
        await gestor.esperar(primera, 1)
        await gestor.esperar(segunda, 1)
        assert primera.estado is segunda.estado is EstadoBusqueda.TERMINADA

    asyncio.run(escenario("faro"))
    asyncio.run(escenario("freno"))


@pytest.mark.parametrize(
    "opciones",
    [{"simultaneas": 0}, {"max_en_cola": -1}, {"ttl_s": 0}, {"maximo": 0}],
)
def test_limites_no_validos(opciones: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        GestorBusquedas(FuenteFalsa(), **opciones)  # type: ignore[arg-type]


# --- A10: progreso fino por tienda; A1: lo que retira la revisión del final ---------------------


class Reloj:
    def __init__(self) -> None:
        self.ahora = 100.0

    def __call__(self) -> float:
        return self.ahora


def test_el_detalle_de_cada_tienda_se_guarda_y_sube_la_version_si_cambia() -> None:
    tarea = TareaBusqueda("t1", consulta(), dict.fromkeys(TIENDAS, EstadoTienda.EN_COLA))
    tarea.detalle("a.example", "Ficha 1 de 5")
    assert tarea.detalles == {"a.example": "Ficha 1 de 5"}
    assert tarea.version == 1
    tarea.detalle("a.example", "Ficha 1 de 5")
    assert tarea.version == 1
    tarea.detalle("otra.example", "Ficha 1 de 5")
    assert "otra.example" not in tarea.detalles and tarea.version == 1


def test_los_segundos_de_cada_tienda_cuentan_desde_que_empieza() -> None:
    reloj = Reloj()
    tarea = TareaBusqueda(
        "t1", consulta(), dict.fromkeys(TIENDAS, EstadoTienda.EN_COLA), reloj=reloj
    )
    assert tarea.segundos("a.example") is None
    tarea.tienda("a.example", EstadoTienda.CONFIGURANDO)
    reloj.ahora += 12.7
    tarea.tienda("a.example", EstadoTienda.DESCARGANDO)
    reloj.ahora += 3.0
    assert tarea.segundos("a.example") == 15
    assert tarea.segundos("b.example") is None


def test_el_gestor_da_su_reloj_a_cada_tarea() -> None:
    reloj = Reloj()

    async def escenario() -> None:
        gestor = GestorBusquedas(FuenteFalsa(), reloj=reloj)
        tarea = gestor.lanzar(consulta(), TIENDAS)
        await _hasta(_en_curso(tarea))
        reloj.ahora += 4.0
        assert tarea.segundos("a.example") == 4
        await gestor.cerrar()

    asyncio.run(escenario())


def _con_oferta(c: ConsultaBusqueda, url: str, *, provisional: bool) -> ResultadoBusqueda:
    from precia.nucleo.modelos import GrupoProducto, Oferta

    oferta = Oferta("a.example", url, "Faro", Decimal(10), {})
    grupo = GrupoProducto("g1", "Faro", None, "nombre", (oferta,))
    return ResultadoBusqueda(c, (grupo,), (), (), 0, Decimal(0), 0.0, provisional=provisional)


def test_cuenta_las_ofertas_vistas_que_la_revision_del_final_ha_descartado() -> None:
    from dataclasses import replace

    from precia.nucleo.modelos import Descartado

    c = consulta()
    tarea = TareaBusqueda("t1", c, dict.fromkeys(TIENDAS, EstadoTienda.EN_COLA))
    tarea.parcial(_con_oferta(c, "https://a.example/p/1", provisional=True))
    assert tarea.retiradas == 0
    final = replace(
        resultado(c),
        descartados=(
            Descartado("a.example", "Faro", "accesorio", "https://a.example/p/1", origen="ia"),
            Descartado("a.example", "Otro", "otra pieza", "https://a.example/p/9", origen="ia"),
        ),
    )
    tarea.resultado = final
    # Solo la que se vio en un parcial: la otra nunca estuvo en la tabla.
    assert tarea.retiradas == 1
