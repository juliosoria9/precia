"""Caché del extractor de respaldo con IA (A3): por huella del texto, campos y modelo, con TTL."""

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from precia.almacen.sqlite import MAX_VALOR_CAMPO, Almacen, DatoNoValido
from tests.almacen.conftest import RelojFalso

SHA = "ab" * 32
MODELO = "api/claude-haiku-5-5#v1"


def test_guarda_y_lee_por_texto_campos_y_modelo(almacen: Almacen) -> None:
    almacen.guardar_extraccion_ia(SHA, ("marca", "estado"), MODELO, {"marca": "Valeo"})
    # El orden de los campos da igual; el conjunto y el modelo, no.
    assert almacen.leer_extraccion_ia(SHA, ("estado", "marca"), MODELO, max_edad_s=60) == {
        "marca": "Valeo"
    }
    assert almacen.leer_extraccion_ia(SHA, ("marca",), MODELO, max_edad_s=60) is None
    assert almacen.leer_extraccion_ia(SHA, ("marca", "estado"), "otro", max_edad_s=60) is None
    otro_texto = "cd" * 32
    assert almacen.leer_extraccion_ia(otro_texto, ("marca",), MODELO, max_edad_s=60) is None


def test_una_respuesta_vacia_tambien_se_lee(almacen: Almacen) -> None:
    almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {})
    assert almacen.leer_extraccion_ia(SHA, ("estado",), MODELO, max_edad_s=60) == {}


def test_caduca_con_el_ttl(almacen: Almacen, reloj: RelojFalso) -> None:
    almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {"estado": "Nuevo"})
    reloj.ahora += timedelta(seconds=60)
    assert almacen.leer_extraccion_ia(SHA, ("estado",), MODELO, max_edad_s=60) is not None
    reloj.ahora += timedelta(seconds=1)
    assert almacen.leer_extraccion_ia(SHA, ("estado",), MODELO, max_edad_s=60) is None


def test_una_fecha_futura_no_vale(almacen: Almacen, reloj: RelojFalso) -> None:
    almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {"estado": "Nuevo"})
    reloj.ahora -= timedelta(seconds=1)
    assert almacen.leer_extraccion_ia(SHA, ("estado",), MODELO, max_edad_s=60) is None


def test_borra_las_caducadas(almacen: Almacen, reloj: RelojFalso) -> None:
    almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {})
    reloj.ahora += timedelta(seconds=30)
    almacen.guardar_extraccion_ia(SHA, ("marca",), MODELO, {})
    reloj.ahora += timedelta(seconds=31)
    assert almacen.borrar_extracciones_ia(max_edad_s=60) == 1
    assert almacen.leer_extraccion_ia(SHA, ("marca",), MODELO, max_edad_s=60) == {}


def test_guardar_dos_veces_sustituye(almacen: Almacen, reloj: RelojFalso) -> None:
    almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {})
    reloj.ahora += timedelta(seconds=50)
    almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {"estado": "Usado"})
    reloj.ahora += timedelta(seconds=50)
    # Valores y fecha nuevos: a los 100 s de la primera sigue valiendo con un TTL de 60.
    assert almacen.leer_extraccion_ia(SHA, ("estado",), MODELO, max_edad_s=60) == {
        "estado": "Usado"
    }


@pytest.mark.parametrize(
    ("sha", "campos", "modelo", "valores"),
    [
        ("no-hex", ("estado",), MODELO, {}),
        ("AB" * 32, ("estado",), MODELO, {}),
        (SHA, (), MODELO, {}),
        (SHA, ("nombre_vendedor",), MODELO, {}),
        (SHA, ("estado",), "", {}),
        (SHA, ("estado",), "m" * 101, {}),
        (SHA, ("estado",), MODELO, {"marca": "Valeo"}),
        (SHA, ("estado",), MODELO, {"estado": "x" * (MAX_VALOR_CAMPO + 1)}),
        (SHA, ("estado",), MODELO, {"estado": "\ud800"}),
    ],
)
def test_rechaza_claves_y_valores_no_validos(
    almacen: Almacen, sha: str, campos: tuple[str, ...], modelo: str, valores: dict[str, str]
) -> None:
    with pytest.raises(DatoNoValido):
        almacen.guardar_extraccion_ia(sha, campos, modelo, valores)


@pytest.mark.parametrize("valores", ["{roto", '["Usado"]', '{"estado": 3}', '{"marca": "x"}'])
def test_una_fila_danada_cuenta_como_fallo(tmp_path: Path, valores: str) -> None:
    ruta = tmp_path / "precia.db"
    with Almacen(ruta) as almacen:
        almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {})
    con = sqlite3.connect(ruta)
    try:
        con.execute("UPDATE extracciones_ia SET valores = ?", (valores,))
        con.commit()
    finally:
        con.close()
    with Almacen(ruta) as almacen:
        assert almacen.leer_extraccion_ia(SHA, ("estado",), MODELO, max_edad_s=1e9) is None


def test_una_fecha_danada_cuenta_como_fallo(tmp_path: Path) -> None:
    ruta = tmp_path / "precia.db"
    with Almacen(ruta) as almacen:
        almacen.guardar_extraccion_ia(SHA, ("estado",), MODELO, {})
    con = sqlite3.connect(ruta)
    try:
        con.execute("UPDATE extracciones_ia SET creado = 'ayer'")
        con.commit()
    finally:
        con.close()
    with Almacen(ruta) as almacen:
        assert almacen.leer_extraccion_ia(SHA, ("estado",), MODELO, max_edad_s=1e9) is None
