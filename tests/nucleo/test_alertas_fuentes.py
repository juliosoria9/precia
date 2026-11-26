"""Alertas en las fuentes de datos (H7): comprobar, pausar, reanudar y borrar. Sin red."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.nucleo.config import Config
from precia.nucleo.fuente_datos import AlertaNoExiste, FuenteDatos
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    GrupoProducto,
    Modo,
    Oferta,
    OrigenDato,
    ResultadoBusqueda,
    Seguimiento,
    ValorCampo,
)
from precia.seguimiento.avisos import Aviso

AHORA = datetime(2026, 11, 24, 9, 30, tzinfo=UTC)
ID = "oem-1k0698151"
CLAVE = "clave-smtp-QUE-NUNCA-debe-salir"


@dataclass
class CanalFalso:
    enviados: list[Aviso] = field(default_factory=list)

    async def enviar(self, aviso: Aviso) -> None:
        self.enviados.append(aviso)


def _oferta(precio: str) -> Oferta:
    return Oferta(
        tienda="a.example",
        url="https://a.example/p/1",
        titulo="Pastillas 1K0698151",
        precio=Decimal(precio),
        campos={"precio": ValorCampo(precio, OrigenDato.JSONLD)},
    )


class FuenteConBusquedaFija(FuenteReal):
    """`FuenteReal` cuya búsqueda devuelve el precio de hoy sin visitar nada."""

    precio_hoy = "25"
    consultas: list[ConsultaBusqueda]

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        self.consultas.append(consulta)
        grupo = GrupoProducto(ID, "Pastillas", "1K0698151", "OEM", (_oferta(self.precio_hoy),))
        return ResultadoBusqueda(consulta, (grupo,), (), (), 0, Decimal(0), 0.1)


def _config(raiz: Path, **extra: object) -> Config:
    return Config(
        raiz=raiz,
        ruta_bd=raiz / "datos" / "precia.sqlite3",
        ruta_cache=raiz / "cache",
        demo=False,
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
def canal() -> CanalFalso:
    return CanalFalso()


@pytest.fixture
def fuente(tmp_path: Path, canal: CanalFalso) -> Iterator[FuenteConBusquedaFija]:
    f = FuenteConBusquedaFija(_config(tmp_path), reloj=lambda: AHORA, canales={"email": canal})
    f.consultas = []
    f.almacen.guardar_precio(
        ID, "Pastillas", "1K0698151", "a.example", "https://a.example/p/1", Decimal(40), AHORA
    )
    yield f
    asyncio.run(f.cerrar())


def test_las_dos_fuentes_cumplen_el_protocolo(fuente: FuenteConBusquedaFija) -> None:
    real: FuenteDatos = fuente
    demo: FuenteDatos = FuenteDemo()
    assert real.es_demo is False and demo.es_demo is True


def test_comprobar_repite_la_busqueda_sin_ia_avisa_y_lo_guarda(
    fuente: FuenteConBusquedaFija, canal: CanalFalso
) -> None:
    alerta = fuente.crear_alerta(ID, Decimal(30), "email")
    informe = asyncio.run(fuente.comprobar_alertas())

    assert fuente.consultas == [
        ConsultaBusqueda(texto="1K0698151", tiendas=("a.example",), modo=Modo.CLASICO)
    ]
    assert [(a.precio, a.tienda) for a in canal.enviados] == [(Decimal(25), "a.example")]
    assert (informe.comprobadas, informe.avisos, informe.errores) == (1, 1, ())
    [guardada] = fuente.alertas()
    assert guardada.id == alerta.id
    assert guardada.avisada is True
    assert guardada.ultima_activacion == AHORA and guardada.ultima_comprobacion == AHORA


def test_la_segunda_comprobacion_no_repite_el_aviso(
    fuente: FuenteConBusquedaFija, canal: CanalFalso
) -> None:
    fuente.crear_alerta(ID, Decimal(30), "email")
    asyncio.run(fuente.comprobar_alertas())
    asyncio.run(fuente.comprobar_alertas())
    assert len(canal.enviados) == 1
    fuente.precio_hoy = "35"  # vuelve a subir: se rearma
    asyncio.run(fuente.comprobar_alertas())
    assert fuente.alertas()[0].avisada is False
    fuente.precio_hoy = "20"
    asyncio.run(fuente.comprobar_alertas())
    assert len(canal.enviados) == 2


def test_canal_sin_configurar_deja_el_motivo_en_la_alerta(
    fuente: FuenteConBusquedaFija, canal: CanalFalso
) -> None:
    fuente.crear_alerta(ID, Decimal(30), "telegram")
    informe = asyncio.run(fuente.comprobar_alertas())
    assert canal.enviados == [] and informe.avisos == 0
    [guardada] = fuente.alertas()
    assert guardada.avisada is False
    assert guardada.ultimo_error


def test_pausar_reanudar_y_borrar(fuente: FuenteConBusquedaFija) -> None:
    alerta = fuente.crear_alerta(ID, Decimal(30), "email")
    fuente.cambiar_alerta(alerta.id, activa=False)
    assert fuente.alertas()[0].activa is False
    assert asyncio.run(fuente.comprobar_alertas()).comprobadas == 0
    fuente.cambiar_alerta(alerta.id, activa=True)
    assert fuente.alertas()[0].activa is True
    fuente.borrar_alerta(alerta.id)
    assert fuente.alertas() == []


def test_una_alerta_que_no_existe(fuente: FuenteConBusquedaFija) -> None:
    with pytest.raises(AlertaNoExiste):
        fuente.cambiar_alerta(99, activa=False)
    with pytest.raises(AlertaNoExiste):
        fuente.borrar_alerta(99)


def test_los_canales_salen_de_la_configuracion_sin_ensenar_secretos(tmp_path: Path) -> None:
    avisos = {
        "PRECIA_SMTP_SERVIDOR": "smtp.example",
        "PRECIA_SMTP_USUARIO": "u@example.org",
        "PRECIA_SMTP_CLAVE": CLAVE,
        "PRECIA_ALERTAS_EMAIL": "j@example.org",
    }
    fuente = FuenteReal(_config(tmp_path, avisos=avisos))
    try:
        estado = fuente.canales_aviso()
    finally:
        asyncio.run(fuente.cerrar())
    assert estado["email"] == ""
    assert "PRECIA_TELEGRAM_TOKEN" in estado["telegram"]
    assert CLAVE not in repr(estado)


# --- Demo -------------------------------------------------------------------------------------


def test_la_demo_pausa_reanuda_y_borra() -> None:
    demo = FuenteDemo()
    primera = demo.alertas()[0]
    demo.cambiar_alerta(primera.id, activa=False)
    assert demo.alertas()[0].activa is False
    demo.cambiar_alerta(primera.id, activa=True)
    assert demo.alertas()[0].activa is True
    demo.borrar_alerta(primera.id)
    assert primera.id not in [a.id for a in demo.alertas()]
    with pytest.raises(AlertaNoExiste):
        demo.borrar_alerta(primera.id)


def test_la_demo_no_repite_identificadores_tras_borrar() -> None:
    demo = FuenteDemo()
    producto = demo.alertas()[0].producto_id
    demo.borrar_alerta(demo.alertas()[0].id)
    nueva = demo.crear_alerta(producto, Decimal(10), "email")
    assert [a.id for a in demo.alertas()].count(nueva.id) == 1


def test_la_demo_comprueba_sin_enviar_nada() -> None:
    demo = FuenteDemo()
    informe = asyncio.run(demo.comprobar_alertas())
    assert informe.avisos == 0
    assert informe.comprobadas == len([a for a in demo.alertas() if a.activa])
    assert set(demo.canales_aviso()) == {"email", "telegram"}
