"""Esquema, migraciones y configuración de la conexión."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from precia.almacen.sqlite import MIGRACIONES, Almacen, ErrorAlmacen


def test_aplica_todas_las_migraciones(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta).cerrar()
    con = sqlite3.connect(ruta)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES)
        tablas = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    esperadas = {"tiendas", "perfiles", "busquedas", "productos", "precios"}
    esperadas |= {"llamadas_ia", "alertas", "ajustes"}
    assert esperadas <= tablas


def test_reabrir_no_repite_migraciones_ni_pierde_datos(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    with Almacen(ruta) as a:
        a.alta_tienda("tienda.example", "Tienda")
    with Almacen(str(ruta)) as a:
        assert [t.dominio for t in a.tiendas()] == ["tienda.example"]


def test_usa_wal_y_claves_foraneas(tmp_path: Path) -> None:
    with Almacen(tmp_path / "precia.db") as a:
        assert a.pragma("journal_mode") == "wal"
        assert a.pragma("foreign_keys") == 1


def test_claves_foraneas_activas_en_memoria(almacen: Almacen) -> None:
    assert almacen.pragma("foreign_keys") == 1


def test_pragma_solo_admite_los_conocidos(almacen: Almacen) -> None:
    with pytest.raises(ErrorAlmacen):
        almacen.pragma("user_version; DROP TABLE tiendas")


def test_rechaza_una_base_de_datos_de_una_version_futura(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    con = sqlite3.connect(ruta)
    con.execute(f"PRAGMA user_version = {len(MIGRACIONES) + 1}")
    con.close()
    with pytest.raises(ErrorAlmacen, match="versión"):
        Almacen(ruta)


def test_una_migracion_fallida_no_deja_el_esquema_a_medias(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    rotas = (*MIGRACIONES, "CREATE TABLE nueva (x INTEGER); CREATE TABLE tiendas (y INTEGER);")
    with pytest.raises(sqlite3.Error):
        Almacen(ruta, migraciones=rotas)
    con = sqlite3.connect(ruta)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES)
        nueva = con.execute("SELECT name FROM sqlite_master WHERE name = 'nueva'").fetchone()
        assert nueva is None
    finally:
        con.close()


def test_dos_procesos_que_crean_la_base_a_la_vez(tmp_path: Path) -> None:
    # Otro proceso (p. ej. la web y un script de evaluación arrancados a la vez) está migrando
    # el mismo fichero: el almacén tiene que esperar y ver lo que dejó hecho, no repetirlo.
    ruta = tmp_path / "precia.db"
    otro = sqlite3.connect(ruta, autocommit=True)
    otro.execute("PRAGMA journal_mode = WAL")
    otro.execute("BEGIN IMMEDIATE")
    for numero, script in enumerate(MIGRACIONES, start=1):
        otro.executescript(f"{script}\nPRAGMA user_version = {numero:d};")
    errores: list[BaseException] = []

    def abrir() -> None:
        try:
            Almacen(ruta).cerrar()
        except BaseException as e:
            errores.append(e)

    hilo = threading.Thread(target=abrir)
    hilo.start()
    time.sleep(0.3)  # deja que el almacén llegue a esperar el cerrojo de escritura
    otro.execute("COMMIT")
    otro.close()
    hilo.join()
    assert errores == []
