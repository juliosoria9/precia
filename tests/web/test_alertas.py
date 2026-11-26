"""Pantalla de Alertas (H7): estado, canales, comprobar ahora, pausar, reanudar y borrar."""

import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import Alerta, InformeAlertas
from precia.web.app import crear_app
from tests.asgi import ClienteASGI

ORIGEN = {"Origin": "http://127.0.0.1:8000"}
DATOS = {"ok": "1"}
"""Un navegador envía siempre la longitud del cuerpo; el cliente de tests, solo si hay cuerpo."""


class FuenteContada(FuenteDemo):
    """Demo que cuenta las comprobaciones y deja elegir el estado de los canales."""

    def __init__(self) -> None:
        super().__init__()
        self.comprobaciones = 0
        self.canales = {"email": "", "telegram": "Falta configurar PRECIA_TELEGRAM_TOKEN en .env."}
        self.informe = InformeAlertas(datetime(2026, 11, 24, 9, 30), 2, 1, ("Faro: sin canal",))

    async def comprobar_alertas(self) -> InformeAlertas:
        self.comprobaciones += 1
        return self.informe

    def canales_aviso(self) -> dict[str, str]:
        return dict(self.canales)


@pytest.fixture
def fuente() -> FuenteContada:
    return FuenteContada()


@pytest.fixture
def cliente(fuente: FuenteContada) -> ClienteASGI:
    return ClienteASGI(crear_app(fuente))


def test_la_pantalla_ya_no_dice_que_los_avisos_estan_por_hacer(cliente: ClienteASGI) -> None:
    html = cliente.get("/alertas").texto
    assert "llegan en el hito H7" not in html
    assert "todavía no se envía ningún aviso" not in html
    assert "Comprobar ahora" in html


def test_dice_que_canales_estan_configurados_y_cuales_no(cliente: ClienteASGI) -> None:
    html = cliente.get("/alertas").texto
    assert "PRECIA_TELEGRAM_TOKEN" in html
    assert "Configurado" in html and "Sin configurar" in html


def test_ensena_el_estado_de_cada_alerta(fuente: FuenteContada, cliente: ClienteASGI) -> None:
    primera: Alerta = fuente.alertas()[0]
    fuente._alertas[0] = replace(
        primera,
        avisada=True,
        ultimo_error="El servidor de correo rechazó el envío (SMTPDataError).",
        ultima_comprobacion=datetime(2026, 11, 24, 9, 30),
    )
    html = cliente.get("/alertas").texto
    assert "Avisada" in html
    assert "rechazó el envío" in html
    assert "24 nov 09:30" in html


def test_el_error_de_una_alerta_se_escapa(fuente: FuenteContada, cliente: ClienteASGI) -> None:
    fuente._alertas[0] = replace(fuente.alertas()[0], ultimo_error="<script>alert(1)</script>")
    html = cliente.get("/alertas").texto
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_pausar_y_reanudar(fuente: FuenteContada, cliente: ClienteASGI) -> None:
    id_alerta = fuente.alertas()[0].id
    respuesta = cliente.post(f"/alertas/{id_alerta}/pausar", DATOS, cabeceras=ORIGEN)
    assert respuesta.estado == 303 and respuesta.cabeceras["location"] == "/alertas"
    assert fuente.alertas()[0].activa is False
    assert "Pausada" in cliente.get("/alertas").texto
    assert cliente.post(f"/alertas/{id_alerta}/reanudar", DATOS, cabeceras=ORIGEN).estado == 303
    assert fuente.alertas()[0].activa is True


def test_borrar(fuente: FuenteContada, cliente: ClienteASGI) -> None:
    id_alerta = fuente.alertas()[0].id
    assert cliente.post(f"/alertas/{id_alerta}/borrar", DATOS, cabeceras=ORIGEN).estado == 303
    assert id_alerta not in [a.id for a in fuente.alertas()]


@pytest.mark.parametrize("accion", ["pausar", "reanudar", "borrar"])
@pytest.mark.parametrize("identificador", ["9999", "0", "abc", "-1", "1e3", "99999999999999999999"])
def test_una_alerta_que_no_existe_da_404(
    cliente: ClienteASGI, accion: str, identificador: str
) -> None:
    assert cliente.post(f"/alertas/{identificador}/{accion}", DATOS, cabeceras=ORIGEN).estado == 404


@pytest.mark.parametrize("ruta", ["/alertas/1/pausar", "/alertas/1/borrar", "/alertas/comprobar"])
def test_las_acciones_exigen_venir_de_la_propia_web(
    fuente: FuenteContada, cliente: ClienteASGI, ruta: str
) -> None:
    antes = fuente.alertas()
    respuesta = cliente.post(ruta, DATOS, cabeceras={"Origin": "https://otra.example"})
    assert respuesta.estado == 403
    assert fuente.alertas() == antes and fuente.comprobaciones == 0


def test_las_acciones_no_se_pueden_lanzar_con_un_get(cliente: ClienteASGI) -> None:
    assert cliente.get("/alertas/1/borrar").estado in (404, 405)
    assert cliente.get("/alertas/comprobar").estado in (404, 405)


def test_comprobar_ahora_lanza_la_comprobacion_en_segundo_plano(fuente: FuenteContada) -> None:
    app = crear_app(fuente)
    cliente = ClienteASGI(app)

    async def prueba() -> tuple[int, str, str]:
        respuesta = await cliente.apost("/alertas/comprobar", DATOS, cabeceras=ORIGEN)
        for _ in range(20):
            await asyncio.sleep(0)
        pagina = await cliente.aget("/alertas")
        return respuesta.estado, respuesta.cabeceras["location"], pagina.texto

    estado, destino, html = asyncio.run(prueba())
    assert (estado, destino) == (303, "/alertas")
    assert fuente.comprobaciones == 1
    # El informe de la última comprobación: 2 alertas, 1 aviso y lo que falló.
    assert "1 aviso" in html and "Faro: sin canal" in html


def test_mientras_comprueba_lo_dice_y_no_lanza_otra(fuente: FuenteContada) -> None:
    suelta: asyncio.Event | None = None

    async def lenta() -> InformeAlertas:
        fuente.comprobaciones += 1
        assert suelta is not None
        await suelta.wait()
        return fuente.informe

    fuente.comprobar_alertas = lenta  # type: ignore[method-assign]
    cliente = ClienteASGI(crear_app(fuente))

    async def prueba() -> str:
        nonlocal suelta
        suelta = asyncio.Event()
        await cliente.apost("/alertas/comprobar", DATOS, cabeceras=ORIGEN)
        await asyncio.sleep(0)
        await cliente.apost("/alertas/comprobar", DATOS, cabeceras=ORIGEN)
        await asyncio.sleep(0)
        html = (await cliente.aget("/alertas")).texto
        suelta.set()
        return html

    html = asyncio.run(prueba())
    assert fuente.comprobaciones == 1
    assert "Comprobando" in html


async def _ciclo_de_vida(app: Any) -> None:
    mensajes = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(0)
        return mensajes.pop(0)

    async def send(_: dict[str, Any]) -> None:
        return None

    await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)


def test_el_planificador_arranca_y_se_cierra_con_la_aplicacion(fuente: FuenteContada) -> None:
    app = crear_app(fuente, cada_alertas_s=3600.0)
    planificador = app.state.planificador
    assert planificador.cada_s == 3600.0
    asyncio.run(_ciclo_de_vida(app))
    assert planificador.lanzar() is False  # cerrado al apagar
    assert fuente.comprobaciones == 0  # arrancar no comprueba nada


def test_la_pantalla_dice_cada_cuanto_se_comprueba(fuente: FuenteContada) -> None:
    html = ClienteASGI(crear_app(fuente, cada_alertas_s=12 * 3600.0)).get("/alertas").texto
    assert "cada 12 horas" in html
    apagado = ClienteASGI(crear_app(fuente, cada_alertas_s=0.0)).get("/alertas").texto
    assert "comprobación automática está desactivada" in apagado
