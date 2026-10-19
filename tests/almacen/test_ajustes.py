"""Ajustes de la aplicación (clave-valor JSON)."""

import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen, DatoNoValido, ErrorAlmacen
from precia.nucleo.modelos import Ajustes


def test_por_defecto_si_no_hay_nada(almacen: Almacen) -> None:
    assert almacen.leer_ajustes() == Ajustes()


def test_guardar_y_leer(almacen: Almacen) -> None:
    ajustes = Ajustes(
        proveedor="ollama",
        modelo_local="llama3.1:8b",
        presupuesto_mensual_usd=Decimal("2.50"),
        tope_llamadas_busqueda=5,
        google_activado=True,
        retardo_dominio_s=3.5,
        user_agent="PrecIA/0.2",
    )
    almacen.guardar_ajustes(ajustes)
    leidos = almacen.leer_ajustes()
    assert leidos == ajustes
    assert isinstance(leidos.presupuesto_mensual_usd, Decimal)
    assert str(leidos.presupuesto_mensual_usd) == "2.50"
    # Guardar otra vez reemplaza, no duplica.
    almacen.guardar_ajustes(Ajustes(tope_llamadas_busqueda=7))
    assert almacen.leer_ajustes() == Ajustes(tope_llamadas_busqueda=7)


def test_el_presupuesto_se_guarda_como_texto(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    with Almacen(ruta) as a:
        a.guardar_ajustes(Ajustes(presupuesto_mensual_usd=Decimal("0.10")))
    con = sqlite3.connect(ruta)
    try:
        fila = con.execute(
            "SELECT valor FROM ajustes WHERE clave = 'presupuesto_mensual_usd'"
        ).fetchone()
    finally:
        con.close()
    assert fila == ('"0.10"',)


def test_claves_desconocidas_se_ignoran_y_las_que_faltan_toman_el_defecto(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta).cerrar()
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO ajustes (clave, valor) VALUES ('obsoleta', '1')")
    con.execute("INSERT INTO ajustes (clave, valor) VALUES ('tope_llamadas_busqueda', '3')")
    con.commit()
    con.close()
    with Almacen(ruta) as a:
        assert a.leer_ajustes() == Ajustes(tope_llamadas_busqueda=3)


@pytest.mark.parametrize(
    ("clave", "valor"),
    [
        ("tope_llamadas_busqueda", '"veinte"'),
        ("tope_llamadas_busqueda", "true"),
        ("google_activado", "1"),
        ("presupuesto_mensual_usd", "10.5"),
        ("presupuesto_mensual_usd", '"no"'),
        ("retardo_dominio_s", '"2"'),
        ("proveedor", "{}"),
        ("proveedor", "no es json"),
    ],
)
def test_valor_corrupto_es_un_error_explicito(tmp_path: Path, clave: str, valor: str) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta).cerrar()
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?)", (clave, valor))
    con.commit()
    con.close()
    with Almacen(ruta) as a, pytest.raises(ErrorAlmacen, match=clave):
        a.leer_ajustes()


@pytest.mark.parametrize(
    "ajustes",
    [
        Ajustes(presupuesto_mensual_usd=Decimal("-1")),
        Ajustes(presupuesto_mensual_usd=Decimal("NaN")),
        Ajustes(tope_llamadas_busqueda=-1),
        Ajustes(retardo_dominio_s=float("nan")),
        Ajustes(user_agent="x" * 501),
    ],
)
def test_guardar_valida(almacen: Almacen, ajustes: Ajustes) -> None:
    with pytest.raises(DatoNoValido):
        almacen.guardar_ajustes(ajustes)
    assert almacen.leer_ajustes() == Ajustes()


@pytest.mark.parametrize(
    "cambios",
    [
        {"tope_llamadas_busqueda": 5.0},
        {"tope_llamadas_busqueda": True},
        {"google_activado": 1},
        {"presupuesto_mensual_usd": 10.0},
        {"presupuesto_mensual_usd": "10"},
        {"retardo_dominio_s": Decimal("2")},
        {"retardo_dominio_s": True},
    ],
)
def test_guardar_rechaza_tipos_que_luego_no_se_podrian_leer(
    almacen: Almacen, cambios: dict[str, Any]
) -> None:
    # Sin esto, un formulario que pasa un tipo equivocado deja los ajustes ilegibles para siempre.
    with pytest.raises(DatoNoValido):
        almacen.guardar_ajustes(replace(Ajustes(), **cambios))
    assert almacen.leer_ajustes() == Ajustes()


def test_un_entero_vale_como_retardo(almacen: Almacen) -> None:
    almacen.guardar_ajustes(Ajustes(retardo_dominio_s=3))
    assert almacen.leer_ajustes().retardo_dominio_s == 3.0
