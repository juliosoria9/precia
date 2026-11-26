"""Estado del producto con un vocabulario común: `Nuevo`, `Usado` o `Reacondicionado`.

Lo encontró el banco de evaluación (S06): la IA devolvía el estado con las palabras de la
página («Material de segunda mano») y cada tienda lo escribía distinto, así que no se podía
comparar ni filtrar por estado.
"""

import pytest

from precia.extraccion.estados import estado_canonico


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Usado", "Usado"),
        ("Material de segunda mano", "Usado"),
        ("Pieza usada en buen estado", "Usado"),
        ("De ocasión", "Usado"),
        ("Como nuevo", "Usado"),
        ("NUEVO", "Nuevo"),
        ("Producto nuevo con garantía", "Nuevo"),
        ("Reacondicionado", "Reacondicionado"),
        ("Motor remanufacturado", "Reacondicionado"),
        ("Refurbished", "Reacondicionado"),
    ],
)
def test_reconoce_el_estado_escrito_de_otra_forma(texto: str, esperado: str) -> None:
    assert estado_canonico(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "Disponible",
        "En stock",
        # Contradictorio: no se decide.
        "Usado (precio nuevo 300 €)",
        # Demasiado largo para ser el valor de un estado: es una descripción.
        "Pieza usada " + "x" * 200,
    ],
)
def test_sin_estado_claro_devuelve_none(texto: str) -> None:
    assert estado_canonico(texto) is None
