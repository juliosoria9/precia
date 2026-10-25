"""Esquema, migraciones y configuración de la conexión."""

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from precia.almacen.sqlite import MIGRACIONES, Almacen, ErrorAlmacen
from precia.nucleo.modelos import Coincidencia


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
    esperadas |= {"llamadas_ia", "alertas", "ajustes", "extracciones_ia"}
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


# --- Migración 4 (nota 16): entrada, tipos sin buscador y catálogo --------------------------------


def _base_version_3(ruta: Path) -> None:
    """Una base que dejó la versión anterior de PrecIA, con perfiles, precios y una alerta."""
    Almacen(ruta, migraciones=MIGRACIONES[:3]).cerrar()
    con = sqlite3.connect(ruta)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        for dominio in ("a.example", "b.example"):
            con.execute(
                "INSERT INTO tiendas (dominio, nombre, estado, bloqueos)"
                " VALUES (?, ?, 'activo', 2)",
                (dominio, dominio.upper()),
            )
        perfiles = [
            ("a.example", 1, "html", 1, "Primera."),
            ("a.example", 2, "api_shopify", 0, ""),
            ("b.example", 1, "api_woocommerce", 0, "Nota 'con' comillas"),
        ]
        for dominio, version, tipo, revisado, nota in perfiles:
            con.execute(
                "INSERT INTO perfiles (dominio, version, url_busqueda, sel_resultado, sel_titulo,"
                " sel_enlace, sel_precio_listado, sel_campos, necesita_js, autor, revisado,"
                " creado, nota, tipo) VALUES (?, ?, ?, 'li', 'h2', 'a @href', NULL,"
                " '{\"precio\": \".p\"}', 1, 'manual', ?, '2026-10-06T09:05:00', ?, ?)",
                (dominio, version, f"https://{dominio}/?q={{consulta}}", revisado, nota, tipo),
            )
        con.execute("INSERT INTO productos (id, nombre) VALUES ('oem-1K0698151', 'Pastillas')")
        con.execute(
            "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia)"
            " VALUES ('oem-1K0698151', 'a.example', 'https://a.example/p', '42.50',"
            " '2026-10-06T09:05:00', '2026-10-06')"
        )
        con.execute(
            "INSERT INTO alertas (producto_id, umbral, canal) VALUES ('oem-1K0698151', '40', 'web')"
        )
        con.commit()
    finally:
        con.close()


def _volcado(ruta: Path, sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_la_migracion_4_conserva_perfiles_precios_y_alertas(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    _base_version_3(ruta)
    consultas = (
        "SELECT * FROM perfiles ORDER BY dominio, version",
        "SELECT * FROM precios ORDER BY id",
        "SELECT * FROM alertas ORDER BY id",
        "SELECT * FROM productos ORDER BY id",
    )
    antes = [_volcado(ruta, sql) for sql in consultas]
    # Solo hasta la 4: la 5 añade columnas y cambiaría el volcado `SELECT *` (tiene su test).
    with Almacen(ruta, migraciones=MIGRACIONES[:4]) as almacen:
        assert almacen.pragma("user_version") == 4
        assert [p.version for p in almacen.versiones("a.example")] == [1, 2]
        tienda = almacen.tienda("a.example")
        assert tienda is not None and tienda.entrada is None and tienda.bloqueos == 2
        assert len(tienda.versiones) == 2
    # Las columnas antiguas siguen en su orden y con los mismos datos.
    assert [_volcado(ruta, sql) for sql in consultas] == antes
    assert _volcado(ruta, "PRAGMA foreign_key_check") == []
    assert _volcado(ruta, "PRAGMA integrity_check") == [("ok",)]
    tablas = {f[0] for f in _volcado(ruta, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"catalogo", "catalogos", "perfiles"} <= tablas
    assert not any(t.startswith("perfiles_") for t in tablas)


def _clave_primaria(ruta: Path, tabla: str) -> list[str]:
    # En `table_info`, la columna 5 es la posición en la clave primaria (0 si no está).
    columnas = [f for f in _volcado(ruta, f"PRAGMA table_info({tabla})") if f[5]]
    return [f[1] for f in sorted(columnas, key=lambda f: int(f[5]))]


def test_la_migracion_4_mantiene_strict_clave_primaria_y_foranea(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    _base_version_3(ruta)
    Almacen(ruta).cerrar()
    estrictas = {f[1]: f[5] for f in _volcado(ruta, "PRAGMA table_list")}
    assert estrictas["perfiles"] == estrictas["catalogo"] == estrictas["catalogos"] == 1
    assert estrictas["tiendas"] == 1
    for tabla in ("perfiles", "catalogo", "catalogos"):
        (foranea,) = _volcado(ruta, f"PRAGMA foreign_key_list({tabla})")
        assert foranea[2:4] == ("tiendas", "dominio") and foranea[6] == "CASCADE"
    assert _clave_primaria(ruta, "perfiles") == ["dominio", "version"]
    assert _clave_primaria(ruta, "catalogo") == ["dominio", "url"]
    con = sqlite3.connect(ruta)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            # PK (dominio, version) sigue siendo única.
            con.execute(
                "INSERT INTO perfiles SELECT * FROM perfiles WHERE dominio = 'a.example' LIMIT 1"
            )
        with pytest.raises(sqlite3.IntegrityError):
            # La clave foránea sigue activa: no hay perfiles de tiendas que no existen.
            con.execute(
                "INSERT INTO perfiles (dominio, version, url_busqueda, sel_resultado, sel_titulo,"
                " sel_enlace, sel_campos, necesita_js, autor, creado)"
                " VALUES ('nada.example', 1, 'u', 'li', 'h2', 'a', '{}', 0, 'm', 'c')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            # STRICT: un BLOB no cabe en una columna TEXT (sin STRICT, se guardaría).
            con.execute("UPDATE perfiles SET sel_campos = x'00'")
        # ON DELETE CASCADE sigue borrando los perfiles de la tienda.
        con.execute("DELETE FROM tiendas WHERE dominio = 'b.example'")
        assert con.execute("SELECT COUNT(*) FROM perfiles").fetchone() == (2,)
        con.rollback()
    finally:
        con.close()


@pytest.mark.parametrize("tipo", ["listado", "sitemap", "html", "api_shopify", "api_woocommerce"])
def test_la_migracion_4_admite_los_cinco_tipos(tmp_path: Path, tipo: str) -> None:
    ruta = tmp_path / "precia.db"
    _base_version_3(ruta)
    Almacen(ruta).cerrar()
    con = sqlite3.connect(ruta)
    try:
        con.execute("UPDATE perfiles SET tipo = ? WHERE dominio = 'b.example'", (tipo,))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE perfiles SET tipo = 'api_magento'")
    finally:
        con.close()


def test_la_migracion_4_tambien_sirve_en_una_base_nueva(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta).cerrar()
    assert _volcado(ruta, "PRAGMA foreign_key_check") == []
    columnas = [f[1] for f in _volcado(ruta, "PRAGMA table_info(tiendas)")]
    assert columnas[-1] == "entrada"


# --- Migración 5 (B5): coincidencia de cada oferta y alertas solo de exactas ------------------


def test_la_migracion_5_anade_coincidencia_y_solo_exactas(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta, migraciones=MIGRACIONES[:4]).cerrar()
    con = sqlite3.connect(ruta)
    try:
        con.execute("INSERT INTO productos (id, nombre) VALUES ('oem-1k0698151', 'Pastillas')")
        con.execute(
            "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia)"
            " VALUES ('oem-1k0698151', 'a.example', 'https://a.example/p', '42.50',"
            " '2026-10-06T09:05:00', '2026-10-06')"
        )
        con.execute(
            "INSERT INTO alertas (producto_id, umbral, canal) VALUES ('oem-1k0698151', '40', 'web')"
        )
        con.commit()
    finally:
        con.close()
    with Almacen(ruta) as almacen:
        assert almacen.pragma("user_version") == len(MIGRACIONES)
        # Antes de B5 solo entraban exactas (o grupos por texto, que no relajan nada).
        assert almacen.ultimas_ofertas("oem-1k0698151")[0].coincidencia is Coincidencia.EXACTA
        assert almacen.alertas()[0].solo_exactas is True
    con = sqlite3.connect(ruta)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia, coincidencia)"
                " VALUES ('oem-1k0698151', 'a.example', 'https://a.example/p', '1',"
                " '2026-10-07T09:05:00', '2026-10-07', 'otra')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO alertas (producto_id, umbral, canal, solo_exactas)"
                " VALUES ('oem-1k0698151', '40', 'web', 2)"
            )
    finally:
        con.close()


# --- Caché del extractor de IA (A3) -----------------------------------------------------------


def test_migra_la_cache_del_extractor_desde_la_version_anterior(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    # La caché es la migración 6: se parte de la 5 (con `[:-1]`, A1 la habría dado ya hecha).
    with Almacen(ruta, migraciones=MIGRACIONES[:5]) as almacen:
        almacen.alta_tienda("tienda.example", "Tienda")
    with Almacen(ruta) as almacen:
        assert almacen.pragma("user_version") == len(MIGRACIONES)
        assert [t.dominio for t in almacen.tiendas()] == ["tienda.example"]
        almacen.guardar_extraccion_ia("0" * 64, ("estado",), "api/m#v1", {})
        assert almacen.leer_extraccion_ia("0" * 64, ("estado",), "api/m#v1", max_edad_s=60) == {}


# --- Hijos del índice sin leer (R2-C14b) ---------------------------------------------------------


def test_migra_los_hijos_pendientes_del_catalogo_desde_la_version_anterior(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    # La lista de hijos sin leer es la migración 9: se parte de la 8.
    with Almacen(ruta, migraciones=MIGRACIONES[:8]) as almacen:
        almacen.alta_tienda("tienda.example", "Tienda")
    with Almacen(ruta) as almacen:
        assert almacen.pragma("user_version") == len(MIGRACIONES) == 12
        hijo = "https://tienda.example/sitemap-faros.xml"
        almacen.guardar_catalogo(
            "tienda.example", "https://tienda.example/s.xml", [], parcial=True, pendientes=[hijo]
        )
        assert almacen.hijos_pendientes("tienda.example") == [hijo]
    estrictas = {f[1]: f[5] for f in _volcado(ruta, "PRAGMA table_list")}
    assert estrictas["catalogo_pendientes"] == 1
    (foranea,) = _volcado(ruta, "PRAGMA foreign_key_list(catalogo_pendientes)")
    assert foranea[2:4] == ("tiendas", "dominio") and foranea[6] == "CASCADE"
    assert _clave_primaria(ruta, "catalogo_pendientes") == ["dominio", "url"]


# --- Prefijo de idioma del catálogo (R5-C) ------------------------------------------------------


def test_migra_el_prefijo_de_idioma_del_catalogo_desde_la_version_anterior(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    # El prefijo de idioma es la migración 10: se parte de la 9 con un catálogo guardado, que
    # queda sin prefijo (como se leía antes).
    with Almacen(ruta, migraciones=MIGRACIONES[:9]) as almacen:
        almacen.alta_tienda("tienda.example", "Tienda")
    con = sqlite3.connect(ruta)
    try:
        con.execute(
            "INSERT INTO catalogos (dominio, sitemap, actualizado, total, parcial)"
            " VALUES ('tienda.example', 'https://tienda.example/s.xml',"
            " '2026-10-19T12:00:00+00:00', 0, 0)"
        )
        con.commit()
    finally:
        con.close()
    with Almacen(ruta) as almacen:
        assert almacen.pragma("user_version") == len(MIGRACIONES) == 12
        info = almacen.catalogo("tienda.example")
        assert info is not None and info.idioma == ()
