"""La URL de la foto principal en el histórico (migración 11): solo el enlace, nunca la imagen."""

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.almacen.sqlite import MIGRACIONES, Almacen, DatoNoValido

PRODUCTO = "oem-1k0698151"
NOMBRE = "Pastillas de freno delanteras"
URL = "https://recambiosnorte.example/p/pastillas"
FOTO = "https://recambiosnorte.example/img/pastillas.jpg?v=2&w=800"
MIGRACION_FOTO = 11


def _guardar(
    a: Almacen,
    fecha: datetime,
    foto: str | None,
    *,
    precio: str = "38.90",
    visita: datetime | None = None,
) -> None:
    a.guardar_precio(
        PRODUCTO,
        NOMBRE,
        "1K0698151",
        "recambiosnorte.example",
        URL,
        Decimal(precio),
        fecha,
        titulo="Pastillas TRW",
        foto=foto,
        visita=visita,
    )


def test_la_foto_es_la_migracion_11() -> None:
    assert len(MIGRACIONES) >= MIGRACION_FOTO
    assert "ADD COLUMN foto" in MIGRACIONES[MIGRACION_FOTO - 1]


def test_ida_y_vuelta_de_la_foto(almacen: Almacen) -> None:
    _guardar(almacen, datetime(2026, 10, 1, 9), FOTO)
    (oferta,) = almacen.ultimas_ofertas(PRODUCTO)
    assert oferta.foto == FOTO
    grupo = almacen.producto(PRODUCTO)
    assert grupo is not None and grupo.foto == FOTO


def test_sin_foto(almacen: Almacen) -> None:
    _guardar(almacen, datetime(2026, 10, 1, 9), None)
    (oferta,) = almacen.ultimas_ofertas(PRODUCTO)
    assert oferta.foto is None


@pytest.mark.parametrize(
    "foto",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "http://recambiosnorte.example/a.jpg",
        "https://recambiosnorte.example/a b.jpg",
        'https://recambiosnorte.example/a".jpg',
        "https://recambiosnorte.example/a'.jpg",
        "https://recambiosnorte.example/<a>.jpg",
        "https://",
        "https://recambiosnorte.example/" + "a" * 2000,
        "",
    ],
)
def test_foto_no_valida(almacen: Almacen, foto: str) -> None:
    with pytest.raises(DatoNoValido):
        _guardar(almacen, datetime(2026, 10, 1, 9), foto)


def test_la_misma_oferta_ya_vista_actualiza_la_foto(almacen: Almacen) -> None:
    instante = datetime(2026, 10, 1, 9)
    _guardar(almacen, instante, None)
    _guardar(almacen, instante, FOTO, visita=datetime(2026, 10, 2, 9))
    (oferta,) = almacen.ultimas_ofertas(PRODUCTO)
    assert oferta.foto == FOTO
    assert len(almacen.historico(PRODUCTO)[0].puntos) == 1


def test_migracion_11_conserva_las_filas_antiguas(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    with Almacen(ruta, migraciones=MIGRACIONES[: MIGRACION_FOTO - 1]) as a:
        assert a.pragma("user_version") == MIGRACION_FOTO - 1
    # Una fila escrita por la versión anterior del código, sin la columna `foto`.
    con = sqlite3.connect(ruta)
    try:
        con.execute("INSERT INTO productos (id, nombre) VALUES (?, ?)", (PRODUCTO, NOMBRE))
        con.execute(
            "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia, titulo, visita)"
            " VALUES (?, 'recambiosnorte.example', ?, '38.90', '2026-10-01T09:00:00',"
            " '2026-10-01', 'Pastillas TRW', '2026-10-01T09:00:00')",
            (PRODUCTO, URL),
        )
        con.commit()
    finally:
        con.close()
    with Almacen(ruta) as a:
        assert a.pragma("user_version") == len(MIGRACIONES)
        (oferta,) = a.ultimas_ofertas(PRODUCTO)
        assert (oferta.titulo, oferta.precio, oferta.foto) == (
            "Pastillas TRW",
            Decimal("38.90"),
            None,
        )


def test_una_foto_envenenada_en_la_base_no_se_lee(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    with Almacen(ruta) as a:
        _guardar(a, datetime(2026, 10, 1, 9), FOTO)
    con = sqlite3.connect(ruta)
    try:
        con.execute("UPDATE precios SET foto = 'javascript:alert(1)'")
        con.commit()
    finally:
        con.close()
    with Almacen(ruta) as a:
        (oferta,) = a.ultimas_ofertas(PRODUCTO)
        assert oferta.foto is None
