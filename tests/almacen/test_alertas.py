"""Estado de las alertas (H7): avisada, último error, última comprobación, pausa y borrado."""

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.almacen.sqlite import MIGRACIONES, AlertaNoEncontrada, Almacen, DatoNoValido
from precia.nucleo.modelos import Alerta

PRODUCTO = "oem-1k0698151"
AHORA = datetime(2026, 11, 24, 9, 30)


@pytest.fixture
def alerta(almacen: Almacen) -> Alerta:
    almacen.guardar_precio(
        PRODUCTO, "Pastillas", "1K0698151", "a.example", "https://a.example/p", Decimal(40), AHORA
    )
    return almacen.crear_alerta(PRODUCTO, Decimal(35), "email")


def test_una_alerta_nueva_no_esta_avisada_ni_comprobada(alerta: Alerta) -> None:
    assert alerta.avisada is False
    assert alerta.ultimo_error == ""
    assert alerta.ultima_comprobacion is None


def test_anotar_un_aviso_enviado(almacen: Almacen, alerta: Alerta) -> None:
    almacen.anotar_alerta(alerta.id, comprobada=AHORA, avisada=True)
    [guardada] = almacen.alertas()
    assert guardada.avisada is True
    assert guardada.ultima_activacion == AHORA
    assert guardada.ultima_comprobacion == AHORA
    assert guardada.ultimo_error == ""


def test_anotar_sin_decir_avisada_conserva_el_estado(almacen: Almacen, alerta: Alerta) -> None:
    almacen.anotar_alerta(alerta.id, comprobada=AHORA, avisada=True)
    despues = datetime(2026, 11, 25, 9, 30)
    almacen.anotar_alerta(alerta.id, comprobada=despues, error="El servidor rechazó el envío.")
    [guardada] = almacen.alertas()
    assert guardada.avisada is True
    assert guardada.ultima_activacion == AHORA
    assert guardada.ultima_comprobacion == despues
    assert guardada.ultimo_error == "El servidor rechazó el envío."


def test_rearmar_conserva_cuando_salto(almacen: Almacen, alerta: Alerta) -> None:
    almacen.anotar_alerta(alerta.id, comprobada=AHORA, avisada=True)
    almacen.anotar_alerta(alerta.id, comprobada=datetime(2026, 11, 26), avisada=False)
    [guardada] = almacen.alertas()
    assert guardada.avisada is False
    assert guardada.ultima_activacion == AHORA


def test_el_error_se_recorta_y_tiene_que_ser_texto(almacen: Almacen, alerta: Alerta) -> None:
    almacen.anotar_alerta(alerta.id, comprobada=AHORA, error="x" * 5000)
    assert len(almacen.alertas()[0].ultimo_error) == 500
    with pytest.raises(DatoNoValido):
        almacen.anotar_alerta(alerta.id, comprobada=AHORA, error=b"bytes")  # type: ignore[arg-type]
    with pytest.raises(DatoNoValido):
        almacen.anotar_alerta(alerta.id, comprobada="hoy")  # type: ignore[arg-type]


def test_pausar_y_reanudar(almacen: Almacen, alerta: Alerta) -> None:
    almacen.cambiar_alerta(alerta.id, activa=False)
    assert almacen.alertas()[0].activa is False
    almacen.cambiar_alerta(alerta.id, activa=True)
    assert almacen.alertas()[0].activa is True


def test_reanudar_rearma_la_alerta(almacen: Almacen, alerta: Alerta) -> None:
    # Quien la reanuda quiere que vuelva a avisar aunque el precio siga bajo.
    almacen.anotar_alerta(alerta.id, comprobada=AHORA, avisada=True, error="algo")
    almacen.cambiar_alerta(alerta.id, activa=False)
    almacen.cambiar_alerta(alerta.id, activa=True)
    [guardada] = almacen.alertas()
    assert guardada.avisada is False and guardada.ultimo_error == ""


def test_borrar(almacen: Almacen, alerta: Alerta) -> None:
    almacen.borrar_alerta(alerta.id)
    assert almacen.alertas() == []


@pytest.mark.parametrize("identificador", [0, -1, 9999, 2**70])
def test_una_alerta_que_no_existe(almacen: Almacen, alerta: Alerta, identificador: int) -> None:
    with pytest.raises(AlertaNoEncontrada):
        almacen.cambiar_alerta(identificador, activa=False)
    with pytest.raises(AlertaNoEncontrada):
        almacen.borrar_alerta(identificador)
    with pytest.raises(AlertaNoEncontrada):
        almacen.anotar_alerta(identificador, comprobada=AHORA)
    assert len(almacen.alertas()) == 1


def test_la_migracion_13_conserva_las_alertas_guardadas(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.sqlite3"
    with Almacen(ruta, migraciones=MIGRACIONES[:12]) as viejo:
        viejo.guardar_precio(
            PRODUCTO, "Pastillas", None, "a.example", "https://a.example/p", Decimal(40), AHORA
        )
    con = sqlite3.connect(ruta)
    con.execute(
        "INSERT INTO alertas (producto_id, umbral, canal, activa, ultima_activacion)"
        " VALUES (?, '35', 'telegram', 0, '2026-10-17T09:12:00')",
        (PRODUCTO,),
    )
    con.commit()
    con.close()
    with Almacen(ruta) as almacen:
        assert almacen.pragma("user_version") == len(MIGRACIONES) == 13
        [guardada] = almacen.alertas()
    assert guardada.activa is False
    assert guardada.ultima_activacion == datetime(2026, 10, 17, 9, 12)
    assert (guardada.avisada, guardada.ultimo_error) == (False, "")
    assert guardada.ultima_comprobacion is None
