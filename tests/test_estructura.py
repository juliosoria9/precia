"""Prueba de humo: el paquete y los subpaquetes de la memoria (§6) existen y se importan."""

import importlib
from importlib.metadata import version

import pytest

import precia

SUBPAQUETES = (
    "web",
    "nucleo",
    "fuentes",
    "navegador",
    "extraccion",
    "perfiles",
    "emparejado",
    "ia",
    "almacen",
    "seguimiento",
    "evaluacion",
)


@pytest.mark.parametrize("nombre", SUBPAQUETES)
def test_subpaquete_importa_y_documenta_su_responsabilidad(nombre: str) -> None:
    modulo = importlib.import_module(f"precia.{nombre}")
    assert modulo.__doc__, f"precia.{nombre} debe explicar su responsabilidad en el docstring"


def test_version_coincide_con_pyproject() -> None:
    assert precia.__version__ == version("precia")
