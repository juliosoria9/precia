"""Perfiles versionados de las tiendas."""

from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from precia.almacen.sqlite import (
    Almacen,
    DatoNoValido,
    PerfilNoEncontrado,
    TiendaNoEncontrada,
)
from precia.perfiles.tipos import Perfil

DOMINIO = "piezasya.example"


def _perfil(version: int = 1, **cambios: Any) -> Perfil:
    base = Perfil(
        dominio=DOMINIO,
        version=version,
        url_busqueda="https://piezasya.example/search?text={consulta}",
        sel_resultado="ul.listado > li.producto",
        sel_titulo="h2.nombre a",
        sel_enlace="h2.nombre a @href",
        sel_precio_listado=".precio .importe",
        sel_campos={"precio": "[itemprop=price] @content", "marca": ".ficha .marca"},
        necesita_js=False,
        autor="🤖 configurador",
        revisado=False,
        creado=datetime(2026, 10, 6, 9, 5),
        nota="Validado en 3 fichas.",
    )
    return replace(base, **cambios)


@pytest.fixture
def con_tienda(almacen: Almacen) -> Almacen:
    almacen.alta_tienda(DOMINIO, "PiezasYa")
    return almacen


def test_sin_perfil(con_tienda: Almacen) -> None:
    assert con_tienda.perfil_vigente(DOMINIO) is None
    assert con_tienda.versiones(DOMINIO) == []


def test_guardar_y_leer_un_perfil(con_tienda: Almacen) -> None:
    perfil = _perfil()
    assert con_tienda.guardar_perfil(perfil) == perfil
    vigente = con_tienda.perfil_vigente(DOMINIO)
    assert vigente == perfil
    assert vigente is not None
    assert vigente.revisado is False


def test_cada_guardado_es_una_version_nueva(con_tienda: Almacen) -> None:
    con_tienda.guardar_perfil(_perfil())
    # Aunque el llamante repita el número, nunca se sobrescribe una versión anterior.
    segunda = con_tienda.guardar_perfil(
        _perfil(1, sel_precio_listado="[data-testid=precio-final]", autor="🤖 reparador")
    )
    assert segunda.version == 2
    assert [p.version for p in con_tienda.versiones(DOMINIO)] == [1, 2]
    vigente = con_tienda.perfil_vigente(DOMINIO)
    assert vigente is not None
    assert vigente.version == 2
    assert vigente.sel_precio_listado == "[data-testid=precio-final]"
    assert con_tienda.versiones(DOMINIO)[0].sel_precio_listado == ".precio .importe"


def test_perfiles_de_tiendas_distintas_no_se_mezclan(con_tienda: Almacen) -> None:
    con_tienda.alta_tienda("otra.example", "Otra")
    con_tienda.guardar_perfil(_perfil())
    con_tienda.guardar_perfil(_perfil(dominio="otra.example"))
    assert [p.version for p in con_tienda.versiones("otra.example")] == [1]
    assert [p.version for p in con_tienda.versiones(DOMINIO)] == [1]


def test_marcar_revisado(con_tienda: Almacen) -> None:
    con_tienda.guardar_perfil(_perfil())
    con_tienda.guardar_perfil(_perfil(2))
    con_tienda.marcar_revisado(DOMINIO, 1)
    assert [p.revisado for p in con_tienda.versiones(DOMINIO)] == [True, False]
    vigente = con_tienda.perfil_vigente(DOMINIO)
    assert vigente is not None
    assert vigente.revisado is False


@pytest.mark.parametrize("version", [7, 0, -1, 2**63, 10**30])
def test_marcar_revisado_inexistente(con_tienda: Almacen, version: int) -> None:
    # La versión puede venir de la URL de la web: un número enorme no debe dar OverflowError.
    con_tienda.guardar_perfil(_perfil())
    with pytest.raises(PerfilNoEncontrado):
        con_tienda.marcar_revisado(DOMINIO, version)
    assert [p.revisado for p in con_tienda.versiones(DOMINIO)] == [False]


def test_perfil_de_tienda_no_dada_de_alta(almacen: Almacen) -> None:
    with pytest.raises(TiendaNoEncontrada):
        almacen.guardar_perfil(_perfil())


def test_selector_precio_listado_opcional(con_tienda: Almacen) -> None:
    con_tienda.guardar_perfil(_perfil(sel_precio_listado=None, necesita_js=True))
    vigente = con_tienda.perfil_vigente(DOMINIO)
    assert vigente is not None
    assert vigente.sel_precio_listado is None
    assert vigente.necesita_js is True


def test_la_tienda_lleva_sus_versiones(con_tienda: Almacen) -> None:
    con_tienda.guardar_perfil(_perfil())
    con_tienda.marcar_revisado(DOMINIO, 1)
    con_tienda.guardar_perfil(_perfil(2, autor="🤖 reparador", nota="Cambió el HTML."))
    tienda = con_tienda.tienda(DOMINIO)
    assert tienda is not None
    assert [v.numero for v in tienda.versiones] == [1, 2]
    v1, v2 = tienda.versiones
    assert v1.autor == "🤖 configurador · revisado"
    assert v2.autor == "🤖 reparador"
    assert v2.nota == "Cambió el HTML."
    assert v1.validado and v2.validado
    assert v1.fecha == datetime(2026, 10, 6, 9, 5)
    assert v1.selectores == {
        "resultado": "ul.listado > li.producto",
        "titulo": "h2.nombre a",
        "enlace": "h2.nombre a @href",
        "precio_listado": ".precio .importe",
        "precio": "[itemprop=price] @content",
        "marca": ".ficha .marca",
    }
    assert tienda.version_actual == v2


def test_inyeccion_sql_en_selectores_y_nota(con_tienda: Almacen) -> None:
    malo = "a'); DELETE FROM perfiles; --"
    con_tienda.guardar_perfil(_perfil(sel_titulo=malo, nota=malo, sel_campos={"marca": malo}))
    con_tienda.guardar_perfil(_perfil(2))
    versiones = con_tienda.versiones(DOMINIO)
    assert len(versiones) == 2
    assert versiones[0].sel_campos == {"marca": malo}
    assert versiones[0].sel_titulo == malo


@pytest.mark.parametrize(
    "cambios",
    [
        {"sel_titulo": "x" * 1001},
        {"url_busqueda": "https://a.example/" + "x" * 2048},
        {"nota": "x" * 2001},
        {"sel_campos": {f"c{i}": "x" for i in range(51)}},
        {"sel_campos": {"precio": "x" * 1001}},
        {"sel_campos": {"": "x"}},
        {"sel_campos": {"precio": ".precio \ud800"}},
    ],
)
def test_valida_tamanos_del_perfil(con_tienda: Almacen, cambios: dict[str, Any]) -> None:
    with pytest.raises(DatoNoValido):
        con_tienda.guardar_perfil(_perfil(**cambios))
    assert con_tienda.versiones(DOMINIO) == []


@pytest.mark.parametrize("clave", ["titulo", "resultado", "enlace", "precio_listado", "otra"])
def test_sel_campos_solo_admite_campos_conocidos(con_tienda: Almacen, clave: str) -> None:
    # Una clave como «titulo» taparía en `Tienda.versiones` el selector real del listado, que es
    # lo que revisa una persona antes de fijar un perfil generado por la IA.
    with pytest.raises(DatoNoValido):
        con_tienda.guardar_perfil(_perfil(sel_campos={clave: "h1"}))
    assert con_tienda.versiones(DOMINIO) == []
