"""Configuraciones fallidas de las tiendas sin perfil (A1): se guardan, se leen y se levantan.

La política (cuánto se espera) es del orquestador: el almacén solo guarda lo que le pasan.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from precia.almacen.sqlite import (
    MAX_MOTIVO_FALLO,
    MIGRACIONES,
    Almacen,
    DatoNoValido,
    TiendaNoEncontrada,
)
from precia.nucleo.modelos import FalloConfiguracion

DOMINIO = "tienda.example"
T = datetime(2026, 10, 22, 12, 0)
HORA = timedelta(hours=1)


def test_la_migracion_crea_la_tabla(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta).cerrar()
    con = sqlite3.connect(ruta)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES)
        columnas = {f[1] for f in con.execute("PRAGMA table_info(configuraciones_fallidas)")}
    finally:
        con.close()
    assert columnas == {"dominio", "intentos", "fecha", "motivo", "reintentar_desde"}


def test_una_base_anterior_se_migra_sin_fallos(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    # Las configuraciones fallidas son la migración 7: se parte de la 6.
    with Almacen(ruta, migraciones=MIGRACIONES[:6]) as almacen:
        almacen.alta_tienda(DOMINIO, "Tienda")
    with Almacen(ruta) as almacen:
        assert almacen.pragma("user_version") == len(MIGRACIONES)
        tienda = almacen.tienda(DOMINIO)
        assert tienda is not None
        assert tienda.fallo is None


def test_registrar_fallo_lo_ve_la_tienda(almacen: Almacen) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    almacen.alta_tienda("otra.example", "Otra")
    devuelta = almacen.registrar_fallo_configuracion(
        DOMINIO, "motivo", T, intentos=1, reintentar_desde=T + HORA
    )
    esperado = FalloConfiguracion(1, T, "motivo", T + HORA)
    assert devuelta.fallo == esperado
    tienda = almacen.tienda(DOMINIO)
    assert tienda is not None
    assert tienda.fallo == esperado
    assert {t.dominio: t.fallo for t in almacen.tiendas()} == {
        DOMINIO: esperado,
        "otra.example": None,
    }


def test_otro_fallo_sustituye_al_anterior(almacen: Almacen) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    almacen.registrar_fallo_configuracion(DOMINIO, "uno", T, intentos=1, reintentar_desde=T + HORA)
    despues = T + 2 * HORA
    almacen.registrar_fallo_configuracion(
        DOMINIO, "dos", despues, intentos=2, reintentar_desde=despues + timedelta(days=1)
    )
    tienda = almacen.tienda(DOMINIO)
    assert tienda is not None
    assert tienda.fallo == FalloConfiguracion(2, despues, "dos", despues + timedelta(days=1))
    filas = almacen._consultar("SELECT COUNT(*) AS n FROM configuraciones_fallidas")
    assert filas[0]["n"] == 1


def test_borrar_fallo(almacen: Almacen) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    almacen.borrar_fallo_configuracion(DOMINIO)  # Sin fallo: no hace nada.
    almacen.registrar_fallo_configuracion(DOMINIO, "x", T, intentos=1, reintentar_desde=T + HORA)
    almacen.borrar_fallo_configuracion(DOMINIO)
    tienda = almacen.tienda(DOMINIO)
    assert tienda is not None
    assert tienda.fallo is None


def test_levantar_espera_conserva_intentos_y_motivo(almacen: Almacen) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    almacen.registrar_fallo_configuracion(DOMINIO, "x", T, intentos=2, reintentar_desde=T + HORA)
    tienda = almacen.levantar_espera(DOMINIO)
    assert tienda.fallo == FalloConfiguracion(2, T, "x", None)
    assert not tienda.fallo.en_espera(T)


def test_levantar_espera_sin_fallo_no_hace_nada(almacen: Almacen) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    assert almacen.levantar_espera(DOMINIO).fallo is None


def test_borrar_la_tienda_borra_su_fallo(almacen: Almacen) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    almacen.registrar_fallo_configuracion(DOMINIO, "x", T, intentos=1, reintentar_desde=T + HORA)
    with almacen._transaccion() as con:
        con.execute("DELETE FROM tiendas WHERE dominio = ?", (DOMINIO,))
    filas = almacen._consultar("SELECT COUNT(*) AS n FROM configuraciones_fallidas")
    assert filas[0]["n"] == 0


def test_tienda_inexistente(almacen: Almacen) -> None:
    with pytest.raises(TiendaNoEncontrada):
        almacen.registrar_fallo_configuracion(
            "nada.example", "x", T, intentos=1, reintentar_desde=T + HORA
        )
    with pytest.raises(TiendaNoEncontrada):
        almacen.levantar_espera("nada.example")


@pytest.mark.parametrize(
    "cambio",
    [
        {"intentos": 0},
        {"intentos": True},
        {"motivo": ""},
        {"motivo": "x" * (MAX_MOTIVO_FALLO + 1)},
        {"fecha": "2026-10-22"},
        {"reintentar_desde": "mañana"},
    ],
)
def test_datos_no_validos(almacen: Almacen, cambio: dict[str, Any]) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    datos: dict[str, Any] = {
        "motivo": "x",
        "fecha": T,
        "intentos": 1,
        "reintentar_desde": T + HORA,
        **cambio,
    }
    with pytest.raises(DatoNoValido):
        almacen.registrar_fallo_configuracion(
            DOMINIO,
            datos["motivo"],
            datos["fecha"],
            intentos=datos["intentos"],
            reintentar_desde=datos["reintentar_desde"],
        )
    tienda = almacen.tienda(DOMINIO)
    assert tienda is not None
    assert tienda.fallo is None


def test_inyeccion_sql_en_el_motivo(almacen: Almacen) -> None:
    almacen.alta_tienda(DOMINIO, "Tienda")
    malicioso = "x'); DROP TABLE tiendas; --"
    almacen.registrar_fallo_configuracion(
        DOMINIO, malicioso, T, intentos=1, reintentar_desde=T + HORA
    )
    tienda = almacen.tienda(DOMINIO)
    assert tienda is not None
    assert tienda.fallo is not None
    assert tienda.fallo.motivo == malicioso
