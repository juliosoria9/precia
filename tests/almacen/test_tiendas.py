"""Tiendas: alta, consulta y actualización de su estado."""

from datetime import datetime
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen, DatoNoValido, TiendaDuplicada, TiendaNoEncontrada
from precia.nucleo.modelos import EstadoPerfil

MALICIOSO = "Tienda'); DROP TABLE tiendas; --"


def test_alta_de_una_tienda_nueva(almacen: Almacen) -> None:
    tienda = almacen.alta_tienda("recambiosnorte.example", "Recambios Norte")
    assert tienda.dominio == "recambiosnorte.example"
    assert tienda.nombre == "Recambios Norte"
    assert tienda.estado is EstadoPerfil.SIN_PERFIL
    assert tienda.ultima_visita is None
    assert tienda.bloqueos == 0
    assert tienda.versiones == ()
    assert almacen.tienda("recambiosnorte.example") == tienda


def test_tienda_inexistente_es_none(almacen: Almacen) -> None:
    assert almacen.tienda("nada.example") is None


def test_alta_duplicada_falla(almacen: Almacen) -> None:
    almacen.alta_tienda("a.example", "A")
    with pytest.raises(TiendaDuplicada):
        almacen.alta_tienda("a.example", "Otra A")


def test_tiendas_ordenadas_por_dominio(almacen: Almacen) -> None:
    almacen.alta_tienda("b.example", "B")
    almacen.alta_tienda("a.example", "A")
    assert [t.dominio for t in almacen.tiendas()] == ["a.example", "b.example"]


def test_inyeccion_sql_en_el_nombre_se_guarda_como_texto(almacen: Almacen) -> None:
    almacen.alta_tienda("mala.example", MALICIOSO)
    almacen.alta_tienda("buena.example", "Buena")
    mala = almacen.tienda("mala.example")
    assert mala is not None
    assert mala.nombre == MALICIOSO
    assert len(almacen.tiendas()) == 2


def test_inyeccion_sql_en_el_dominio_no_encuentra_nada(almacen: Almacen) -> None:
    almacen.alta_tienda("a.example", "A")
    assert almacen.tienda("x' OR '1'='1") is None
    with pytest.raises(TiendaNoEncontrada):
        almacen.actualizar_tienda("x' OR '1'='1", bloqueos=99)
    tienda = almacen.tienda("a.example")
    assert tienda is not None
    assert tienda.bloqueos == 0


def test_actualizar_tienda_solo_cambia_lo_indicado(almacen: Almacen) -> None:
    almacen.alta_tienda("a.example", "A")
    visita = datetime(2026, 10, 19, 10, 30)
    tienda = almacen.actualizar_tienda(
        "a.example",
        estado=EstadoPerfil.ACTIVO,
        robots="Permite /buscar",
        robots_permite=False,
        ultima_visita=visita,
    )
    assert tienda.estado is EstadoPerfil.ACTIVO
    assert tienda.robots == "Permite /buscar"
    assert tienda.robots_permite is False
    assert tienda.ultima_visita == visita
    assert tienda.bloqueos == 0

    tienda = almacen.actualizar_tienda("a.example", bloqueos=3)
    assert tienda.bloqueos == 3
    assert tienda.estado is EstadoPerfil.ACTIVO
    assert almacen.tienda("a.example") == tienda


def test_actualizar_tienda_inexistente(almacen: Almacen) -> None:
    with pytest.raises(TiendaNoEncontrada):
        almacen.actualizar_tienda("nada.example", estado=EstadoPerfil.ROTO)


@pytest.mark.parametrize(
    ("dominio", "nombre"),
    [
        ("", "Vacía"),
        ("a.example", ""),
        ("a" * 254, "Larga"),
        ("a.example", "n" * 201),
        # Un sustituto UTF-16 suelto no se puede guardar en SQLite (llega con `json.loads`).
        ("a.example", "Tienda \ud800"),
    ],
)
def test_alta_valida_tamanos(almacen: Almacen, dominio: str, nombre: str) -> None:
    with pytest.raises(DatoNoValido):
        almacen.alta_tienda(dominio, nombre)


def test_actualizar_valida_bloqueos_y_robots(almacen: Almacen) -> None:
    almacen.alta_tienda("a.example", "A")
    with pytest.raises(DatoNoValido):
        almacen.actualizar_tienda("a.example", bloqueos=-1)
    with pytest.raises(DatoNoValido):
        # SQLite solo guarda enteros de 64 bits.
        almacen.actualizar_tienda("a.example", bloqueos=2**63)
    with pytest.raises(DatoNoValido):
        almacen.actualizar_tienda("a.example", robots="x" * 2001)


# --- URL de entrada (nota 16 §A) ----------------------------------------------------------------

ENTRADA = "https://a.example/buscar?q={consulta}"


def test_una_tienda_nueva_no_tiene_entrada(almacen: Almacen) -> None:
    assert almacen.alta_tienda("a.example", "A").entrada is None


def test_alta_con_entrada(almacen: Almacen) -> None:
    tienda = almacen.alta_tienda("a.example", "A", entrada=ENTRADA)
    assert tienda.entrada == ENTRADA
    assert almacen.tienda("a.example") == tienda
    assert [t.entrada for t in almacen.tiendas()] == [ENTRADA]


def test_cambiar_y_quitar_la_entrada(almacen: Almacen) -> None:
    almacen.alta_tienda("a.example", "A")
    listado = "https://www.a.example/catalogo/frenos?page={pagina}"
    assert almacen.cambiar_entrada("a.example", listado).entrada == listado
    tienda = almacen.tienda("a.example")
    assert tienda is not None and tienda.entrada == listado
    assert almacen.cambiar_entrada("a.example", None).entrada is None


def test_cambiar_entrada_de_tienda_inexistente(almacen: Almacen) -> None:
    with pytest.raises(TiendaNoEncontrada):
        almacen.cambiar_entrada("nada.example", "https://nada.example/")


@pytest.mark.parametrize(
    "entrada",
    [
        "",
        "https://a.example/" + "x" * 500,
        "https://otra.example/buscar?q={consulta}",
        "https://a.example.otra.example/",
        "https://otra.example/?q=a.example",
        "ftp://a.example/catalogo",
        "javascript:alert(1)",
        "https://usuario@a.example/",
        "https://a.example:8080/",
        "/catalogo",
        "https://a.example/\ud800",
        3,
    ],
)
def test_la_entrada_tiene_que_ser_una_url_de_la_tienda(almacen: Almacen, entrada: Any) -> None:
    # La normaliza `perfiles.entrada`; el almacén no guarda nada que apunte a otro servidor.
    almacen.alta_tienda("a.example", "A")
    with pytest.raises(DatoNoValido):
        almacen.cambiar_entrada("a.example", entrada)
    with pytest.raises(DatoNoValido):
        almacen.alta_tienda("b.example", "B", entrada=entrada)
    tienda = almacen.tienda("a.example")
    assert tienda is not None and tienda.entrada is None
    assert almacen.tienda("b.example") is None


def test_inyeccion_sql_en_la_entrada_se_guarda_como_texto(almacen: Almacen) -> None:
    almacen.alta_tienda("a.example", "A")
    mala = "https://a.example/?q=');DROP%20TABLE%20tiendas;--"
    assert almacen.cambiar_entrada("a.example", mala).entrada == mala
    assert len(almacen.tiendas()) == 1
