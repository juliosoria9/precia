"""Planificador (H7): repite una tarea cada cierto tiempo, sin relojes reales."""

import asyncio

import pytest

from precia.seguimiento.planificador import Planificador


class Dormir:
    """`asyncio.sleep` de mentira: apunta lo pedido y cede el turno; tras `vueltas`, se queda
    esperando para siempre (como un sueño muy largo)."""

    def __init__(self, vueltas: int) -> None:
        self.pedidos: list[float] = []
        self._vueltas = vueltas

    async def __call__(self, segundos: float) -> None:
        self.pedidos.append(segundos)
        if len(self.pedidos) > self._vueltas:
            await asyncio.Event().wait()
        await asyncio.sleep(0)


async def ceder(veces: int = 20) -> None:
    for _ in range(veces):
        await asyncio.sleep(0)


def test_ejecuta_la_tarea_tras_cada_espera_y_no_al_arrancar() -> None:
    hechas: list[int] = []

    async def tarea() -> None:
        hechas.append(len(hechas))

    async def prueba() -> None:
        dormir = Dormir(2)
        planificador = Planificador(tarea, 3600.0, dormir=dormir)
        planificador.arrancar()
        await asyncio.sleep(0)
        assert hechas == []  # primero espera: arrancar la web no visita ninguna tienda
        await ceder()
        await planificador.cerrar()
        assert hechas == [0, 1]
        assert dormir.pedidos == [3600.0, 3600.0, 3600.0]

    asyncio.run(prueba())


def test_un_fallo_de_la_tarea_no_para_el_planificador() -> None:
    veces = 0

    async def tarea() -> None:
        nonlocal veces
        veces += 1
        if veces == 1:
            raise RuntimeError("fallo de la primera vuelta")

    async def prueba() -> None:
        planificador = Planificador(tarea, 60.0, dormir=Dormir(2))
        planificador.arrancar()
        await ceder()
        await planificador.cerrar()

    asyncio.run(prueba())
    assert veces == 2


def test_con_intervalo_cero_no_hay_bucle_pero_se_puede_lanzar_a_mano() -> None:
    hechas: list[str] = []

    async def tarea() -> str:
        hechas.append("ya")
        return "informe"

    async def prueba() -> None:
        dormir = Dormir(5)
        planificador = Planificador(tarea, 0.0, dormir=dormir)
        planificador.arrancar()
        await ceder()
        assert hechas == [] and dormir.pedidos == []
        assert planificador.lanzar() is True
        await ceder()
        assert hechas == ["ya"]
        assert planificador.ultimo == "informe"
        await planificador.cerrar()

    asyncio.run(prueba())


def test_no_se_lanza_dos_veces_a_la_vez() -> None:
    empezadas = 0

    async def prueba() -> None:
        suelta = asyncio.Event()

        async def tarea() -> None:
            nonlocal empezadas
            empezadas += 1
            await suelta.wait()

        planificador = Planificador(tarea, 0.0)
        assert planificador.en_curso is False
        assert planificador.lanzar() is True
        await asyncio.sleep(0)
        assert planificador.en_curso is True
        assert planificador.lanzar() is False
        suelta.set()
        await ceder()
        assert planificador.en_curso is False
        assert planificador.lanzar() is True
        await ceder()
        await planificador.cerrar()

    asyncio.run(prueba())
    assert empezadas == 2


def test_cerrar_cancela_la_tarea_en_marcha_y_el_bucle() -> None:
    cancelada = False

    async def prueba() -> None:
        async def tarea() -> None:
            nonlocal cancelada
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelada = True
                raise

        planificador = Planificador(tarea, 10.0, dormir=Dormir(1))
        planificador.arrancar()
        await ceder()
        assert planificador.en_curso is True
        await planificador.cerrar()
        assert planificador.en_curso is False
        assert planificador.lanzar() is False  # cerrado: ya no lanza nada

    asyncio.run(prueba())
    assert cancelada is True


def test_el_intervalo_no_puede_ser_negativo() -> None:
    async def tarea() -> None:
        return None

    with pytest.raises(ValueError):
        Planificador(tarea, -1.0)
