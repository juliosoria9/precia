from collections.abc import Iterator
from datetime import datetime

import pytest

from precia.almacen.sqlite import Almacen


class RelojFalso:
    """Reloj inyectable: devuelve siempre `ahora`, que el test puede mover."""

    def __init__(self, ahora: datetime) -> None:
        self.ahora = ahora

    def __call__(self) -> datetime:
        return self.ahora


@pytest.fixture
def reloj() -> RelojFalso:
    return RelojFalso(datetime(2026, 10, 19, 12, 0))


@pytest.fixture
def almacen(reloj: RelojFalso) -> Iterator[Almacen]:
    a = Almacen(":memory:", reloj=reloj)
    yield a
    a.cerrar()
