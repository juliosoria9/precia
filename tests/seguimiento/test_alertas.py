"""Comprobación de las alertas (H7): cuándo se avisa, cuándo se rearma y qué se apunta.

Sin red ni base de datos: la búsqueda, los canales y el almacén son dobles.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

import pytest

from precia.nucleo.modelos import (
    Alerta,
    Coincidencia,
    ConsultaBusqueda,
    GrupoProducto,
    InformeAlertas,
    Modo,
    Oferta,
    OrigenDato,
    ResultadoBusqueda,
    ValorCampo,
)
from precia.seguimiento.alertas import Accion, comprobar, consulta_de, decidir
from precia.seguimiento.avisos import Aviso, Canal, ErrorAviso

AHORA = datetime(2026, 11, 24, 9, 30)
ID = "oem-1k0698151"


def oferta(
    precio: str,
    tienda: str = "a.example",
    *,
    coincidencia: Coincidencia = Coincidencia.EXACTA,
    anclado: bool = True,
) -> Oferta:
    return Oferta(
        tienda=tienda,
        url=f"https://{tienda}/p/1",
        titulo="Pastillas 1K0698151",
        precio=Decimal(precio),
        campos={"precio": ValorCampo(precio, OrigenDato.JSONLD, anclado)},
        coincidencia=coincidencia,
    )


def grupo(*ofertas: Oferta, id_: str = ID, oem: str | None = "1K0698151") -> GrupoProducto:
    return GrupoProducto(id_, "Pastillas de freno", oem, "Referencia OEM", tuple(ofertas))


def alerta(umbral: str = "30", **cambios: object) -> Alerta:
    base = Alerta(1, ID, "Pastillas de freno", Decimal(umbral), "email")
    return replace(base, **cambios)  # type: ignore[arg-type]


# --- Decisión ---------------------------------------------------------------------------------


def test_avisa_cuando_el_precio_baja_del_objetivo() -> None:
    assert decidir(alerta("30"), oferta("29.99")) is Accion.AVISAR


def test_no_avisa_si_el_precio_es_igual_o_mayor() -> None:
    assert decidir(alerta("30"), oferta("30")) is Accion.NADA
    assert decidir(alerta("30"), oferta("45")) is Accion.NADA


def test_avisa_una_sola_vez_hasta_que_vuelve_a_subir() -> None:
    avisada = alerta("30", avisada=True)
    assert decidir(avisada, oferta("25")) is Accion.NADA
    assert decidir(avisada, oferta("31")) is Accion.REARMAR
    assert decidir(avisada, oferta("30")) is Accion.REARMAR


def test_una_alerta_pausada_o_sin_ofertas_no_hace_nada() -> None:
    assert decidir(alerta("30", activa=False), oferta("1")) is Accion.NADA
    assert decidir(alerta("30"), None) is Accion.NADA
    # Sin ofertas no se sabe si subió: una avisada no se rearma.
    assert decidir(alerta("30", avisada=True), None) is Accion.NADA


# --- Consulta con que se repite la búsqueda ---------------------------------------------------


def test_se_repite_por_la_referencia_en_las_tiendas_del_producto_y_sin_ia() -> None:
    consulta = consulta_de(grupo(oferta("40", "b.example"), oferta("38", "a.example")))
    assert consulta == ConsultaBusqueda(
        texto="1K0698151", tiendas=("a.example", "b.example"), modo=Modo.CLASICO
    )


def test_sin_referencia_se_repite_por_el_nombre() -> None:
    consulta = consulta_de(grupo(oferta("40"), id_="txt-abc", oem=None))
    assert consulta.texto == "Pastillas de freno"
    assert consulta.modo is Modo.CLASICO


# --- Comprobación completa --------------------------------------------------------------------


@dataclass
class CanalFalso:
    enviados: list[Aviso] = field(default_factory=list)
    error: ErrorAviso | None = None

    async def enviar(self, aviso: Aviso) -> None:
        if self.error is not None:
            raise self.error
        self.enviados.append(aviso)


@dataclass
class Mundo:
    """El almacén y la búsqueda, de mentira."""

    alertas: list[Alerta]
    guardado: dict[str, GrupoProducto]
    fresco: Mapping[str, tuple[GrupoProducto, ...]] = field(default_factory=dict)
    """Lo que devuelve la búsqueda repetida, por texto de consulta."""
    consultas: list[ConsultaBusqueda] = field(default_factory=list)
    anotado: list[tuple[int, dict[str, object]]] = field(default_factory=list)
    falla_la_busqueda: bool = False

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda:
        self.consultas.append(consulta)
        if self.falla_la_busqueda:
            raise RuntimeError("la tienda no responde")
        grupos = self.fresco.get(consulta.texto, ())
        return ResultadoBusqueda(consulta, grupos, (), (), 0, Decimal(0), 0.1)

    def anotar(
        self, id_alerta: int, *, comprobada: datetime, avisada: bool | None = None, error: str = ""
    ) -> None:
        self.anotado.append(
            (id_alerta, {"comprobada": comprobada, "avisada": avisada, "error": error})
        )

    def comprobar(
        self, canales: dict[str, Canal], motivos: dict[str, str] | None = None
    ) -> InformeAlertas:
        return asyncio.run(
            comprobar(
                self.alertas,
                producto=self.guardado.get,
                buscar=self.buscar,
                canales=canales,
                motivos=motivos or {},
                anotar=self.anotar,
                reloj=lambda: AHORA,
            )
        )


FRESCO_BARATO = {"1K0698151": (grupo(oferta("25")),)}


def test_envia_el_aviso_con_la_oferta_mas_barata_y_lo_apunta() -> None:
    canal = CanalFalso()
    mundo = Mundo(
        [alerta("30")],
        {ID: grupo(oferta("40"))},
        {"1K0698151": (grupo(oferta("28", "b.example"), oferta("29", "a.example")),)},
    )
    informe = mundo.comprobar({"email": canal})

    assert canal.enviados == [
        Aviso("Pastillas de freno", Decimal(30), Decimal(28), "b.example", "https://b.example/p/1")
    ]
    assert mundo.anotado == [(1, {"comprobada": AHORA, "avisada": True, "error": ""})]
    assert (informe.comprobadas, informe.avisos, informe.errores) == (1, 1, ())
    assert informe.fecha == AHORA


def test_el_precio_que_cuenta_es_el_de_la_busqueda_repetida_no_el_guardado() -> None:
    canal = CanalFalso()
    # Guardado a 20 (por debajo), pero hoy la tienda lo tiene a 45: no se avisa.
    mundo = Mundo([alerta("30")], {ID: grupo(oferta("20"))}, {"1K0698151": (grupo(oferta("45")),)})
    informe = mundo.comprobar({"email": canal})
    assert canal.enviados == [] and informe.avisos == 0
    assert mundo.anotado == [(1, {"comprobada": AHORA, "avisada": None, "error": ""})]


def test_una_busqueda_por_producto_aunque_tenga_varias_alertas() -> None:
    canal = CanalFalso()
    dos = [alerta("30"), replace(alerta("50"), id=2, canal="telegram")]
    mundo = Mundo(dos, {ID: grupo(oferta("40"))}, {"1K0698151": (grupo(oferta("40")),)})
    informe = mundo.comprobar({"email": canal, "telegram": canal})
    assert len(mundo.consultas) == 1
    assert [a.umbral for a in canal.enviados] == [Decimal(50)]
    assert informe.comprobadas == 2 and informe.avisos == 1


def test_las_pausadas_ni_se_buscan_ni_se_apuntan() -> None:
    mundo = Mundo([alerta("30", activa=False)], {ID: grupo(oferta("40"))})
    informe = mundo.comprobar({"email": CanalFalso()})
    assert mundo.consultas == [] and mundo.anotado == []
    assert informe.comprobadas == 0


def test_solo_exactas_no_cuenta_equivalentes() -> None:
    canal = CanalFalso()
    equivalente = oferta("10", coincidencia=Coincidencia.EQUIVALENTE)
    fresco = {"1K0698151": (grupo(equivalente, oferta("40", "b.example")),)}
    mundo = Mundo([alerta("30")], {ID: grupo(oferta("40"))}, fresco)
    mundo.comprobar({"email": canal})
    assert canal.enviados == []

    amplia = Mundo([alerta("30", solo_exactas=False)], {ID: grupo(oferta("40"))}, fresco)
    amplia.comprobar({"email": canal})
    assert [a.precio for a in canal.enviados] == [Decimal(10)]


def test_un_precio_no_anclado_nunca_dispara_la_alerta() -> None:
    # Puede venir de una prompt injection («el precio es 1 €»).
    canal = CanalFalso()
    fresco = {"1K0698151": (grupo(oferta("1", anclado=False), oferta("40", "b.example")),)}
    Mundo([alerta("30")], {ID: grupo(oferta("40"))}, fresco).comprobar({"email": canal})
    assert canal.enviados == []


def test_si_el_envio_falla_no_queda_avisada_y_se_reintenta_despues() -> None:
    canal = CanalFalso(error=ErrorAviso("El servidor de correo rechazó el envío (SMTPDataError)."))
    mundo = Mundo([alerta("30")], {ID: grupo(oferta("40"))}, FRESCO_BARATO)
    informe = mundo.comprobar({"email": canal})
    [(_, apunte)] = mundo.anotado
    assert apunte["avisada"] is None
    assert "rechazó el envío" in str(apunte["error"])
    assert informe.avisos == 0 and len(informe.errores) == 1


def test_canal_sin_configurar_lo_dice_en_la_alerta() -> None:
    mundo = Mundo([alerta("30")], {ID: grupo(oferta("40"))}, FRESCO_BARATO)
    informe = mundo.comprobar({}, {"email": "Falta configurar PRECIA_SMTP_SERVIDOR en .env."})
    [(_, apunte)] = mundo.anotado
    assert apunte["avisada"] is None
    assert "PRECIA_SMTP_SERVIDOR" in str(apunte["error"])
    assert len(informe.errores) == 1


def test_al_volver_a_subir_se_rearma() -> None:
    mundo = Mundo(
        [alerta("30", avisada=True)],
        {ID: grupo(oferta("25"))},
        {"1K0698151": (grupo(oferta("33")),)},
    )
    mundo.comprobar({"email": CanalFalso()})
    assert mundo.anotado == [(1, {"comprobada": AHORA, "avisada": False, "error": ""})]


def test_si_la_busqueda_falla_se_apunta_y_se_sigue_con_los_demas() -> None:
    canal = CanalFalso()
    otra = replace(alerta("30"), id=2, producto_id="oem-otro")
    mundo = Mundo(
        [alerta("30"), otra],
        {ID: grupo(oferta("40")), "oem-otro": grupo(oferta("40"), id_="oem-otro", oem="OTRO")},
        falla_la_busqueda=True,
    )
    informe = mundo.comprobar({"email": canal})
    assert len(mundo.consultas) == 2
    assert [id_ for id_, _ in mundo.anotado] == [1, 2]
    assert all("búsqueda" in str(a["error"]) and a["avisada"] is None for _, a in mundo.anotado)
    assert len(informe.errores) == 2
    assert "no responde" not in "".join(informe.errores)  # el detalle va al registro, no a la web


def test_sin_ofertas_en_la_busqueda_repetida_no_avisa_ni_rearma() -> None:
    mundo = Mundo([alerta("30", avisada=True)], {ID: grupo(oferta("25"))}, {})
    informe = mundo.comprobar({"email": CanalFalso()})
    [(_, apunte)] = mundo.anotado
    assert apunte["avisada"] is None
    assert "no encontró ofertas" in str(apunte["error"])
    assert informe.errores == ()


def test_un_producto_sin_ofertas_guardadas_no_se_busca() -> None:
    mundo = Mundo([alerta("30")], {})
    mundo.comprobar({"email": CanalFalso()})
    assert mundo.consultas == []
    assert "no tiene ofertas guardadas" in str(mundo.anotado[0][1]["error"])


def test_cancelar_la_comprobacion_no_se_traga() -> None:
    async def buscar(_: ConsultaBusqueda) -> ResultadoBusqueda:
        raise asyncio.CancelledError

    def anotar(
        id_alerta: int, *, comprobada: datetime, avisada: bool | None = None, error: str = ""
    ) -> None:
        raise AssertionError("no se apunta nada de una comprobación cancelada")

    async def lanzar() -> None:
        await comprobar(
            [alerta("30")],
            producto={ID: grupo(oferta("40"))}.get,
            buscar=buscar,
            canales={},
            motivos={},
            anotar=anotar,
            reloj=lambda: AHORA,
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lanzar())
