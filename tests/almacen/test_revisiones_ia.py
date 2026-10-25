"""Caché de la revisión con IA de los resultados: un veredicto por oferta, con TTL de 7 días."""

from datetime import timedelta

import pytest

from precia.almacen.sqlite import TTL_REVISIONES_IA_S, Almacen, DatoNoValido
from tests.almacen.conftest import RelojFalso

CLAVE = "ab" * 32


def test_guarda_y_lee_un_veredicto(almacen: Almacen) -> None:
    almacen.guardar_revision_ia(CLAVE, "accesorio", "Es la cerradura", True)
    assert almacen.leer_revision_ia(CLAVE, max_edad_s=60) == ("accesorio", "Es la cerradura", True)
    assert almacen.leer_revision_ia("cd" * 32, max_edad_s=60) is None


def test_guardar_otra_vez_sustituye(almacen: Almacen) -> None:
    almacen.guardar_revision_ia(CLAVE, "accesorio", "x", True)
    almacen.guardar_revision_ia(CLAVE, "misma_pieza", "", False)
    assert almacen.leer_revision_ia(CLAVE, max_edad_s=60) == ("misma_pieza", "", False)


def test_caduca_con_el_ttl_y_se_borra(almacen: Almacen, reloj: RelojFalso) -> None:
    assert timedelta(seconds=TTL_REVISIONES_IA_S) == timedelta(days=7)
    almacen.guardar_revision_ia(CLAVE, "kit", "", True)
    reloj.ahora += timedelta(seconds=60)
    assert almacen.leer_revision_ia(CLAVE, max_edad_s=60) is not None
    reloj.ahora += timedelta(seconds=1)
    assert almacen.leer_revision_ia(CLAVE, max_edad_s=60) is None
    assert almacen.borrar_revisiones_ia(max_edad_s=60) == 1
    assert almacen.borrar_revisiones_ia(max_edad_s=60) == 0


@pytest.mark.parametrize(
    ("clave", "clase", "motivo"),
    [
        ("corta", "kit", ""),
        (CLAVE, "borrar_todo", ""),
        (CLAVE, "kit", "x" * 10_000),
    ],
)
def test_rechaza_lo_que_no_es_un_veredicto(
    almacen: Almacen, clave: str, clase: str, motivo: str
) -> None:
    with pytest.raises(DatoNoValido):
        almacen.guardar_revision_ia(clave, clase, motivo, True)
