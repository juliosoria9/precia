"""Últimas ofertas de un producto (para la ficha del producto en la web) y registro de búsquedas.

La migración 2 amplía `precios` con el título, los campos extraídos y la disponibilidad de cada
oferta, y `productos` con el criterio de emparejado. Las filas antiguas (de antes de la
migración) se siguen leyendo con valores vacíos.
"""

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from precia.almacen.sqlite import MIGRACIONES, Almacen, DatoNoValido, ErrorAlmacen
from precia.nucleo.modelos import (
    Coincidencia,
    GrupoProducto,
    Modo,
    Oferta,
    OrigenDato,
    ValorCampo,
)

PRODUCTO = "oem-1k0698151"
NOMBRE = "Pastillas de freno delanteras"
URL_A = "https://recambiosnorte.example/p/pastillas"
URL_B = "https://piezasya.example/p/9981"
CAMPOS = {
    "precio": ValorCampo("38,90 €", OrigenDato.JSONLD),
    "marca": ValorCampo("TRW", OrigenDato.PERFIL, True, 0.9),
    "estado": ValorCampo("Nuevo", OrigenDato.IA, False, 0.0),
}


def _guardar(
    a: Almacen,
    tienda: str,
    precio: str,
    fecha: datetime,
    *,
    url: str = URL_A,
    titulo: str = "Pastillas TRW",
    campos: dict[str, ValorCampo] | None = None,
) -> None:
    a.guardar_precio(
        PRODUCTO,
        NOMBRE,
        "1K0698151",
        tienda,
        url,
        Decimal(precio),
        fecha,
        titulo=titulo,
        campos=campos if campos is not None else CAMPOS,
        disponible=True,
        criterio="Referencia OEM exacta",
    )


def test_la_migracion_2_amplia_precios_y_productos(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta).cerrar()
    con = sqlite3.connect(ruta)
    try:
        precios = {f[1] for f in con.execute("PRAGMA table_info(precios)")}
        productos = {f[1] for f in con.execute("PRAGMA table_info(productos)")}
    finally:
        con.close()
    assert {"titulo", "campos", "disponible"} <= precios
    assert "criterio" in productos


def test_una_base_de_la_version_1_se_migra_sin_perder_el_historico(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    Almacen(ruta, migraciones=MIGRACIONES[:1]).cerrar()
    # Filas escritas por la versión anterior del código, sin las columnas nuevas.
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO productos (id, nombre) VALUES (?, ?)", (PRODUCTO, NOMBRE))
    con.execute(
        "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia)"
        " VALUES (?, 'a.example', ?, '10', '2026-10-01T00:00:00', '2026-10-01')",
        (PRODUCTO, URL_A),
    )
    con.commit()
    con.close()
    with Almacen(ruta) as a:
        assert a.pragma("user_version") == len(MIGRACIONES)
        (oferta,) = a.ultimas_ofertas(PRODUCTO)
        assert oferta == Oferta("a.example", URL_A, NOMBRE, Decimal("10"), {}, True)


def test_ultimas_ofertas_una_por_tienda_con_sus_campos(almacen: Almacen) -> None:
    _guardar(almacen, "recambiosnorte.example", "40.00", datetime(2026, 10, 1, 9))
    _guardar(almacen, "recambiosnorte.example", "38.90", datetime(2026, 10, 2, 9))
    _guardar(
        almacen,
        "piezasya.example",
        "41.50",
        datetime(2026, 10, 1, 9),
        url=URL_B,
        titulo="Pastillas Bosch",
        campos={},
    )
    assert almacen.ultimas_ofertas(PRODUCTO) == [
        Oferta("piezasya.example", URL_B, "Pastillas Bosch", Decimal("41.50"), {}, True),
        Oferta("recambiosnorte.example", URL_A, "Pastillas TRW", Decimal("38.90"), CAMPOS, True),
    ]


def test_mismo_instante_gana_la_ultima_guardada(almacen: Almacen) -> None:
    instante = datetime(2026, 10, 1, 9)
    _guardar(almacen, "a.example", "10", instante, titulo="Primera")
    _guardar(almacen, "a.example", "11", instante, titulo="Segunda")
    (oferta,) = almacen.ultimas_ofertas(PRODUCTO)
    assert (oferta.titulo, oferta.precio) == ("Segunda", Decimal("11"))


def test_dos_ofertas_de_la_misma_tienda_en_la_ultima_visita(almacen: Almacen) -> None:
    # El emparejado puede juntar dos fichas de la misma tienda en un grupo: salen las dos, y
    # no las de la visita anterior.
    antes, ahora = datetime(2026, 10, 1, 9), datetime(2026, 10, 2, 9)
    _guardar(almacen, "a.example", "12", antes, url=URL_B, titulo="Vieja")
    _guardar(almacen, "a.example", "10", ahora, url=URL_A, titulo="Izquierdo")
    _guardar(almacen, "a.example", "11", ahora, url=URL_B, titulo="Derecho")
    assert [(o.titulo, o.precio) for o in almacen.ultimas_ofertas(PRODUCTO)] == [
        ("Izquierdo", Decimal("10")),
        ("Derecho", Decimal("11")),
    ]


def test_producto_reconstruye_el_grupo(almacen: Almacen) -> None:
    _guardar(almacen, "a.example", "10", datetime(2026, 10, 1))
    grupo = almacen.producto(PRODUCTO)
    assert grupo == GrupoProducto(
        PRODUCTO,
        NOMBRE,
        "1K0698151",
        "Referencia OEM exacta",
        tuple(almacen.ultimas_ofertas(PRODUCTO)),
    )
    assert almacen.producto("no-existe") is None
    assert almacen.ultimas_ofertas("no-existe") == []


def test_valor_no_anclado_se_conserva_como_tal(almacen: Almacen) -> None:
    # Un valor de la IA que no está en la página debe seguir saliendo tachado en la web.
    _guardar(almacen, "a.example", "10", datetime(2026, 10, 1))
    (oferta,) = almacen.ultimas_ofertas(PRODUCTO)
    assert oferta.campos["estado"] == ValorCampo("Nuevo", OrigenDato.IA, False, 0.0)


def test_solo_se_guardan_los_campos_declarados(almacen: Almacen) -> None:
    # Ética (nota 08): no se guarda nada que no sea un campo declarado, p. ej. un vendedor.
    with pytest.raises(DatoNoValido):
        _guardar(
            almacen,
            "a.example",
            "10",
            datetime(2026, 10, 1),
            campos={"vendedor": ValorCampo("Pepe", OrigenDato.IA)},
        )
    assert almacen.historico(PRODUCTO) == []


@pytest.mark.parametrize(
    "campos",
    [
        {"marca": ValorCampo("x" * 5_001, OrigenDato.PERFIL)},
        {"marca": ValorCampo("Bosch \ud800", OrigenDato.PERFIL)},
        {"marca": ValorCampo("Bosch", OrigenDato.PERFIL, True, 1.5)},
        {"marca": ValorCampo("Bosch", OrigenDato.PERFIL, True, float("nan"))},
    ],
)
def test_campos_no_validos(almacen: Almacen, campos: dict[str, ValorCampo]) -> None:
    with pytest.raises(DatoNoValido):
        _guardar(almacen, "a.example", "10", datetime(2026, 10, 1), campos=campos)


def test_titulo_demasiado_largo(almacen: Almacen) -> None:
    with pytest.raises(DatoNoValido):
        _guardar(almacen, "a.example", "10", datetime(2026, 10, 1), titulo="t" * 501)


def test_campos_corruptos_en_la_base_son_un_error(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    with Almacen(ruta) as a:
        _guardar(a, "a.example", "10", datetime(2026, 10, 1))
    con = sqlite3.connect(ruta)
    con.execute("UPDATE precios SET campos = '{\"marca\": 3}'")
    con.commit()
    con.close()
    with Almacen(ruta) as a, pytest.raises(ErrorAlmacen):
        a.ultimas_ofertas(PRODUCTO)


def test_registrar_busqueda(almacen: Almacen) -> None:
    almacen.registrar_busqueda(
        "faro golf", Modo.AUTO, datetime(2026, 10, 19, 12), 3, Decimal("0.0123"), 4.5
    )
    assert almacen.busquedas() == [
        ("faro golf", Modo.AUTO, datetime(2026, 10, 19, 12), 3, Decimal("0.0123"), 4.5)
    ]


@pytest.mark.parametrize(
    ("texto", "llamadas", "coste", "duracion"),
    [("", 0, "0", 1.0), ("x", -1, "0", 1.0), ("x", 0, "-1", 1.0), ("x", 0, "0", float("inf"))],
)
def test_registrar_busqueda_valida(
    almacen: Almacen, texto: str, llamadas: int, coste: str, duracion: float
) -> None:
    with pytest.raises(DatoNoValido):
        almacen.registrar_busqueda(
            texto, Modo.CLASICO, datetime(2026, 10, 19), llamadas, Decimal(coste), duracion
        )


# --- Coincidencia con la referencia buscada (B5) -----------------------------------------------


def test_guardar_y_leer_la_coincidencia(almacen: Almacen) -> None:
    almacen.guardar_precio(
        PRODUCTO,
        NOMBRE,
        "1K0698151",
        "oscaro.example",
        URL_A,
        Decimal("31.52"),
        datetime(2026, 10, 22, 13, 39),
        coincidencia=Coincidencia.EQUIVALENTE,
    )
    assert almacen.ultimas_ofertas(PRODUCTO)[0].coincidencia is Coincidencia.EQUIVALENTE
    producto = almacen.producto(PRODUCTO)
    assert producto is not None
    assert producto.ofertas[0].coincidencia is Coincidencia.EQUIVALENTE


def test_coincidencia_no_valida(almacen: Almacen) -> None:
    with pytest.raises(DatoNoValido):
        almacen.guardar_precio(
            PRODUCTO,
            NOMBRE,
            "1K0698151",
            "oscaro.example",
            URL_A,
            Decimal("31.52"),
            datetime(2026, 10, 22),
            coincidencia="otra",  # type: ignore[arg-type]
        )
    assert almacen.ultimas_ofertas(PRODUCTO) == []


# --- La visita que vio cada oferta (A1, con las ofertas de la caché de A3) -----------------------

T1 = datetime(2026, 10, 22, 10, 0)
T2 = T1 + timedelta(hours=2)
URL_C = "https://recambiosnorte.example/p/pastillas-c"


def _visto(a: Almacen, url: str, precio: str, fecha: datetime, visita: datetime) -> None:
    a.guardar_precio(
        PRODUCTO,
        NOMBRE,
        "1K0698151",
        "recambiosnorte.example",
        url,
        Decimal(precio),
        fecha,
        visita=visita,
    )


def test_una_oferta_de_la_cache_sigue_en_las_ultimas_junto_a_las_nuevas(almacen: Almacen) -> None:
    # Visita 1 (T1): A y B. Visita 2 (T2): A sale de la caché (descargada en T1) y C es nueva.
    # Las últimas ofertas son las de la visita 2, A incluida, aunque A conserve su fecha T1.
    _visto(almacen, URL_A, "10", T1, T1)
    _visto(almacen, URL_B, "11", T1, T1)
    _visto(almacen, URL_A, "10", T1, T2)
    _visto(almacen, URL_C, "12", T2, T2)
    assert [o.url for o in almacen.ultimas_ofertas(PRODUCTO)] == [URL_A, URL_C]
    (fila,) = almacen._consultar("SELECT count(*) AS n FROM precios")
    assert fila["n"] == 3


def test_lo_que_no_se_ha_vuelto_a_ver_no_esta_en_las_ultimas(almacen: Almacen) -> None:
    _visto(almacen, URL_A, "10", T1, T1)
    _visto(almacen, URL_B, "11", T1, T1)
    _visto(almacen, URL_A, "10", T1, T2)  # Solo A, desde la caché.
    assert [o.url for o in almacen.ultimas_ofertas(PRODUCTO)] == [URL_A]


def test_sin_visita_cuenta_la_fecha(almacen: Almacen) -> None:
    _guardar(almacen, "recambiosnorte.example", "40.00", T1)
    _guardar(almacen, "recambiosnorte.example", "41.00", T2, url=URL_C)
    assert [o.url for o in almacen.ultimas_ofertas(PRODUCTO)] == [URL_C]


def test_visita_no_valida(almacen: Almacen) -> None:
    with pytest.raises(DatoNoValido):
        almacen.guardar_precio(
            PRODUCTO,
            NOMBRE,
            None,
            "a.example",
            URL_A,
            Decimal("1"),
            T1,
            visita="ayer",  # type: ignore[arg-type]
        )


def test_la_migracion_de_la_visita_conserva_las_ultimas_ofertas(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    # La visita es la migración 8: se parte de la 7 (con `[:-1]`, R2-C14b la daría ya hecha).
    Almacen(ruta, migraciones=MIGRACIONES[:7]).cerrar()
    # Filas escritas por la versión anterior del código, sin la columna `visita`.
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO productos (id, nombre) VALUES (?, ?)", (PRODUCTO, NOMBRE))
    for tienda, url, precio, fecha in [
        ("recambiosnorte.example", URL_A, "40.00", T1),
        ("recambiosnorte.example", URL_C, "41.00", T2),
        ("piezasya.example", URL_B, "39.00", T1),
    ]:
        con.execute(
            "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (PRODUCTO, tienda, url, precio, fecha.isoformat(), fecha.date().isoformat()),
        )
    con.commit()
    con.close()
    with Almacen(ruta) as a:
        assert a.pragma("user_version") == len(MIGRACIONES)
        assert [o.url for o in a.ultimas_ofertas(PRODUCTO)] == [URL_B, URL_C]
        filas = a._consultar("SELECT count(*) AS n FROM precios WHERE visita = fecha")
        assert filas[0]["n"] == 3
