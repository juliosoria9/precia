"""Modelos de dominio: coincidencia con la referencia buscada, mejor oferta y alertas (B5), y
configuraciones fallidas en espera (A1)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from precia.nucleo.modelos import (
    SOLO_EXACTAS,
    Alerta,
    Coincidencia,
    Descartado,
    EstadoPerfil,
    FalloConfiguracion,
    GrupoProducto,
    Oferta,
    Tienda,
)

T = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def oferta(precio: str, coincidencia: Coincidencia = Coincidencia.EXACTA) -> Oferta:
    return Oferta(
        "tienda.example",
        f"https://tienda.example/p/{precio}",
        "Pastillas",
        Decimal(precio),
        {},
        coincidencia=coincidencia,
    )


def grupo(*ofertas: Oferta) -> GrupoProducto:
    return GrupoProducto("oem-1k0698151", "Pastillas", "1K0698151", "Referencia OEM", ofertas)


def test_oferta_es_exacta_por_defecto() -> None:
    sin_indicar = Oferta("t.example", "https://t.example/p", "Pastillas", Decimal("20"), {}, True)
    assert sin_indicar.coincidencia is Coincidencia.EXACTA


def test_mejor_oferta_de_solo_exactas_ignora_equivalentes() -> None:
    mixto = grupo(oferta("20", Coincidencia.EQUIVALENTE), oferta("30"))
    assert mixto.mejor_oferta is not None
    assert mixto.mejor_oferta.precio == Decimal("20")
    mejor_exacta = mixto.mejor_oferta_de(SOLO_EXACTAS)
    assert mejor_exacta is not None
    assert mejor_exacta.precio == Decimal("30")
    solo_equivalentes = grupo(oferta("20", Coincidencia.EQUIVALENTE))
    assert solo_equivalentes.mejor_oferta_de(SOLO_EXACTAS) is None


def test_descartado_admite_url_y_precio_opcionales() -> None:
    descartado = Descartado("t.example", "Pastillas traseras", "Referencia OEM distinta")
    assert descartado.url == ""
    assert descartado.precio is None


def test_alerta_solo_exactas_por_defecto() -> None:
    alerta = Alerta(1, "oem-1k0698151", "Pastillas", Decimal("25"), "correo")
    assert alerta.solo_exactas is True
    assert alerta.coincidencias == SOLO_EXACTAS
    amplia = Alerta(2, "oem-1k0698151", "Pastillas", Decimal("25"), "correo", solo_exactas=False)
    assert amplia.coincidencias == frozenset(Coincidencia)


def test_precio_para_alerta() -> None:
    # Lo que usará el H7 al evaluar una alerta: por defecto, solo las ofertas exactas.
    mixto = grupo(oferta("20", Coincidencia.EQUIVALENTE), oferta("30"))
    por_defecto = Alerta(1, mixto.id, mixto.nombre, Decimal("25"), "correo")
    amplia = Alerta(2, mixto.id, mixto.nombre, Decimal("25"), "correo", solo_exactas=False)
    exacta = mixto.mejor_oferta_de(por_defecto.coincidencias)
    cualquiera = mixto.mejor_oferta_de(amplia.coincidencias)
    assert exacta is not None
    assert cualquiera is not None
    assert exacta.precio == Decimal("30")
    assert cualquiera.precio == Decimal("20")


def test_fallo_en_espera_hasta_reintentar_desde() -> None:
    fallo = FalloConfiguracion(1, T, "x", T + timedelta(hours=1))
    assert fallo.en_espera(T + timedelta(minutes=59))
    assert not fallo.en_espera(T + timedelta(hours=1))
    assert not fallo.en_espera(T + timedelta(hours=2))
    # Sin fecha de reintento (el usuario pulsó «Reintentar»): nunca está en espera.
    assert not FalloConfiguracion(3, T, "x", None).en_espera(T)


def test_tienda_sin_fallo_por_defecto() -> None:
    tienda = Tienda("t.example", "T", EstadoPerfil.SIN_PERFIL, "", True, None, 0)
    assert tienda.fallo is None
