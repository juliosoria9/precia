"""La búsqueda real pasa el ajuste `revisar_resultados` y reserva una llamada a la revisión."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from precia.ia.simulado import ProveedorSimulado
from precia.ia.tipos import ProveedorIA
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import Ajustes, ConsultaBusqueda, Modo
from precia.nucleo.orquestador import Orquestador
from tests.nucleo.test_fuente_real import AHORA, Fabrica, configuracion


@pytest.mark.parametrize("revisar", [True, False])
def test_el_ajuste_llega_al_orquestador(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revisar: bool
) -> None:
    vistos: list[tuple[bool, frozenset[str]]] = []

    def capturar(*args: Any, **kwargs: Any) -> Any:
        vistos.append((kwargs["revisar_resultados"], args[2].reservadas))
        return Orquestador(*args, **kwargs)

    def crear(ajustes: Ajustes) -> tuple[ProveedorIA | None, str]:
        return ProveedorSimulado({}), ""

    monkeypatch.setattr("precia.nucleo.fuente_real.Orquestador", capturar)
    real = FuenteReal(
        configuracion(tmp_path),
        crear_descargadores=Fabrica(),
        crear_proveedor=crear,
        reloj=lambda: AHORA,
    )
    assert real.ajustes().revisar_resultados is True
    real.guardar_ajustes(replace(real.ajustes(), revisar_resultados=revisar))
    try:
        asyncio.run(real.buscar(ConsultaBusqueda("faro", modo=Modo.AUTO)))
    finally:
        asyncio.run(real.cerrar())
    # Solo se reserva la llamada si la búsqueda va a revisar (Auto sin revisión, no).
    assert vistos == [(revisar, frozenset({"revision"}) if revisar else frozenset())]


@pytest.mark.parametrize(
    ("modo", "texto", "reservadas"),
    [
        (Modo.CLASICO, "faro", frozenset()),
        (Modo.IA, "faro", frozenset({"emparejado"})),
        (Modo.AUTO, "faro 1K0941005", frozenset()),
    ],
)
def test_la_reserva_es_del_componente_que_va_a_revisar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modo: Modo, texto: str, reservadas: Any
) -> None:
    vistos: list[frozenset[str]] = []

    def capturar(*args: Any, **kwargs: Any) -> Any:
        vistos.append(args[2].reservadas)
        return Orquestador(*args, **kwargs)

    def crear(ajustes: Ajustes) -> tuple[ProveedorIA | None, str]:
        return ProveedorSimulado({}), ""

    monkeypatch.setattr("precia.nucleo.fuente_real.Orquestador", capturar)
    real = FuenteReal(
        configuracion(tmp_path),
        crear_descargadores=Fabrica(),
        crear_proveedor=crear,
        reloj=lambda: AHORA,
    )
    try:
        asyncio.run(real.buscar(ConsultaBusqueda(texto, modo=modo)))
    finally:
        asyncio.run(real.cerrar())
    assert vistos == [reservadas]
