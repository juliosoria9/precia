"""Cortesía: intervalo mínimo entre peticiones al mismo dominio y tope global de concurrencia."""

import asyncio
import contextlib

import pytest

from precia.navegador.cortesia import LimitadorDominio, pausa_de_cortesia
from tests.navegador.falsos import Reloj


def limitador(retardo_s: float = 2.0, concurrencia: int = 4) -> tuple[LimitadorDominio, Reloj]:
    reloj = Reloj()
    return (
        LimitadorDominio(retardo_s, concurrencia=concurrencia, reloj=reloj, dormir=reloj.dormir),
        reloj,
    )


def test_la_primera_peticion_no_espera() -> None:
    lim, reloj = limitador()
    asyncio.run(lim.esperar_turno("tienda.example"))
    assert reloj.esperas == []


def test_respeta_el_intervalo_por_dominio() -> None:
    lim, reloj = limitador(2.0)

    async def tres() -> list[float]:
        instantes = []
        for _ in range(3):
            await lim.esperar_turno("tienda.example")
            instantes.append(reloj.ahora)
        return instantes

    assert asyncio.run(tres()) == [1000.0, 1002.0, 1004.0]


def test_dominios_distintos_no_se_esperan() -> None:
    lim, reloj = limitador(2.0)

    async def dos() -> None:
        await lim.esperar_turno("tienda.example")
        await lim.esperar_turno("otra.example")

    asyncio.run(dos())
    assert reloj.esperas == []


def test_el_tiempo_transcurrido_cuenta() -> None:
    lim, reloj = limitador(2.0)
    asyncio.run(lim.esperar_turno("tienda.example"))
    reloj.ahora += 1.5
    asyncio.run(lim.esperar_turno("tienda.example"))
    assert reloj.esperas == [pytest.approx(0.5)]


@pytest.mark.parametrize(("crawl_delay", "espera"), [(5.0, 5.0), (0.5, 2.0), (None, 2.0)])
def test_gana_el_mayor_entre_ajuste_y_crawl_delay(crawl_delay: float | None, espera: float) -> None:
    lim, reloj = limitador(2.0)

    async def dos() -> None:
        await lim.esperar_turno("tienda.example", retardo_minimo=crawl_delay)
        await lim.esperar_turno("tienda.example", retardo_minimo=crawl_delay)

    asyncio.run(dos())
    assert reloj.esperas == [pytest.approx(espera)]


def test_el_intervalo_cuenta_desde_el_final_de_la_peticion() -> None:
    lim, reloj = limitador(2.0)

    async def lenta_y_otra() -> None:
        async with lim.turno("tienda.example"):
            reloj.ahora += 10  # la descarga tarda 10 s
        await lim.esperar_turno("tienda.example")

    asyncio.run(lenta_y_otra())
    assert reloj.esperas == [pytest.approx(2.0)]
    assert reloj.ahora == pytest.approx(1012.0)


def test_simultaneas_al_mismo_dominio_se_ponen_en_fila() -> None:
    lim, reloj = limitador(2.0)
    instantes: list[float] = []

    async def una() -> None:
        async with lim.turno("tienda.example"):
            instantes.append(reloj.ahora)

    async def varias() -> None:
        await asyncio.gather(*(una() for _ in range(4)))

    asyncio.run(varias())
    assert instantes == [1000.0, 1002.0, 1004.0, 1006.0]


def test_semaforo_global_limita_la_concurrencia() -> None:
    lim = LimitadorDominio(0.0, concurrencia=2)
    activas = 0
    maximo = 0

    async def una(dominio: str) -> None:
        nonlocal activas, maximo
        async with lim.turno(dominio):
            activas += 1
            maximo = max(maximo, activas)
            await asyncio.sleep(0.01)
            activas -= 1

    async def varias() -> None:
        await asyncio.gather(*(una(f"t{i}.example") for i in range(6)))

    asyncio.run(varias())
    assert maximo == 2


def test_un_error_en_la_descarga_libera_el_turno() -> None:
    lim, _ = limitador(0.0, concurrencia=1)

    async def falla_y_sigue() -> None:
        with pytest.raises(RuntimeError):
            async with lim.turno("tienda.example"):
                raise RuntimeError("fallo de red")
        await asyncio.wait_for(lim.esperar_turno("tienda.example"), 1)

    asyncio.run(falla_y_sigue())


def test_parametros_no_validos() -> None:
    with pytest.raises(ValueError):
        LimitadorDominio(-1)
    with pytest.raises(ValueError):
        LimitadorDominio(1, concurrencia=0)


def test_pausa_de_cortesia_fuera_de_turno_o_de_otro_dominio_no_espera() -> None:
    reloj = Reloj()
    limitador = LimitadorDominio(2.0, reloj=reloj, dormir=reloj.dormir)

    async def probar() -> tuple[bool, bool, bool]:
        fuera = await pausa_de_cortesia("tienda.example")
        async with limitador.turno("Tienda.Example", retardo_minimo=5.0):
            otro = await pausa_de_cortesia("otra.example")
            propio = await pausa_de_cortesia("tienda.example")
        return fuera, otro, propio

    assert asyncio.run(probar()) == (False, False, True)
    assert reloj.esperas == [5.0]


def test_pausa_de_cortesia_cancelada_no_devuelve_una_plaza_de_mas() -> None:
    async def dormir_eterno(_: float) -> None:
        await asyncio.Event().wait()

    limitador = LimitadorDominio(1.0, concurrencia=1, dormir=dormir_eterno)

    async def probar() -> bool:
        async def con_pausa() -> None:
            async with limitador.turno("tienda.example"):
                await pausa_de_cortesia("tienda.example")

        tarea = asyncio.create_task(con_pausa())
        await asyncio.sleep(0.01)
        tarea.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarea
        # Una sola plaza: si la cancelación la hubiera devuelto dos veces, entrarían dos a la vez.
        dentro = 0
        maximo = 0

        async def ocupar(dominio: str) -> None:
            nonlocal dentro, maximo
            async with limitador.turno(dominio):
                dentro += 1
                maximo = max(maximo, dentro)
                await asyncio.sleep(0.01)
                dentro -= 1

        await asyncio.gather(ocupar("a.example"), ocupar("b.example"))
        return maximo == 1

    assert asyncio.run(probar())
