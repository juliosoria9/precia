"""Últimas ofertas de un producto (para la ficha del producto en la web) y registro de búsquedas.

La migración 2 amplía `precios` con el título, los campos extraídos y la disponibilidad de cada
oferta, y `productos` con el criterio de emparejado. Las filas antiguas (de antes de la
migración) se siguen leyendo con valores vacíos.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.almacen.sqlite import MIGRACIONES, Almacen, DatoNoValido, ErrorAlmacen
from precia.nucleo.modelos import GrupoProducto, Modo, Oferta, OrigenDato, ValorCampo

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
