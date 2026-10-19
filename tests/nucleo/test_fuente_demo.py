import asyncio
from decimal import Decimal

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    EstadoPaso,
    Modo,
    OrigenDato,
    ResultadoBusqueda,
)


def _buscar(**kwargs: object) -> ResultadoBusqueda:
    return asyncio.run(FuenteDemo().buscar(ConsultaBusqueda(**kwargs)))  # type: ignore[arg-type]


def test_busqueda_por_referencia_oem_empareja_tiendas() -> None:
    resultado = _buscar(texto="1K0698151")
    assert resultado.es_demo
    assert len(resultado.grupos) == 1
    grupo = resultado.grupos[0]
    assert grupo.referencia_oem == "1K0698151"
    assert "OEM" in grupo.criterio
    assert len({o.tienda for o in grupo.ofertas}) >= 3


def test_referencia_con_espacios_y_guiones_tambien_coincide() -> None:
    assert _buscar(texto="1k0-698 151").grupos


def test_busqueda_por_texto() -> None:
    nombres = [g.nombre.lower() for g in _buscar(texto="pastillas freno").grupos]
    assert nombres and all("pastillas" in n for n in nombres)


def test_sin_coincidencias() -> None:
    resultado = _buscar(texto="zzzz-inexistente")
    assert resultado.grupos == ()


def test_modo_clasico_no_usa_ia() -> None:
    resultado = _buscar(texto="pastillas", modo=Modo.CLASICO)
    assert resultado.llamadas_ia == 0
    assert resultado.coste_ia_usd == Decimal("0")
    for grupo in resultado.grupos:
        for oferta in grupo.ofertas:
            assert all(v.origen is not OrigenDato.IA for v in oferta.campos.values())
    assert all(p.estado is not EstadoPaso.IA for t in resultado.progreso for p in t.pasos)


def test_modo_ia_recupera_mas_ofertas_que_el_clasico() -> None:
    clasico = _buscar(texto="pastillas", modo=Modo.CLASICO)
    ia = _buscar(texto="pastillas", modo=Modo.IA)
    assert ia.llamadas_ia > 0 and ia.coste_ia_usd > 0
    assert sum(len(g.ofertas) for g in ia.grupos) > sum(len(g.ofertas) for g in clasico.grupos)


def test_solo_devuelve_los_campos_pedidos() -> None:
    resultado = _buscar(texto="pastillas", campos=("precio", "marca"))
    for grupo in resultado.grupos:
        for oferta in grupo.ofertas:
            assert set(oferta.campos) <= {"precio", "marca"}


def test_filtra_por_tiendas_elegidas() -> None:
    resultado = _buscar(texto="pastillas", tiendas=("recambiosnorte.example",))
    tiendas = {o.tienda for g in resultado.grupos for o in g.ofertas}
    assert tiendas == {"recambiosnorte.example"}
    assert [p.tienda for p in resultado.progreso] == ["recambiosnorte.example"]


def test_tienda_que_prohibe_en_robots_se_omite() -> None:
    resultado = _buscar(texto="pastillas")
    omitida = next(p for p in resultado.progreso if p.tienda == "autodescuento.example")
    assert omitida.pasos[0].estado is EstadoPaso.OMITIDO
    assert all(o.tienda != "autodescuento.example" for g in resultado.grupos for o in g.ofertas)


def test_descarta_accesorios_y_variantes_con_motivo() -> None:
    resultado = _buscar(texto="pastillas", modo=Modo.IA)
    assert resultado.descartados
    assert all(d.motivo for d in resultado.descartados)


def test_crear_alerta() -> None:
    fuente = FuenteDemo()
    antes = len(fuente.alertas())
    alerta = fuente.crear_alerta("pastillas-1k0698151", Decimal("30"), "telegram")
    assert len(fuente.alertas()) == antes + 1
    assert alerta.producto_nombre


def test_historico_y_producto() -> None:
    fuente = FuenteDemo()
    assert fuente.producto("pastillas-1k0698151") is not None
    assert fuente.producto("no-existe") is None
    series = fuente.historico("pastillas-1k0698151")
    assert series and all(len(s.puntos) > 1 for s in series)


# --- Hallazgos de la revisión adversarial de H1W ---------------------------------------------


def _estados(resultado: ResultadoBusqueda) -> set[str]:
    return {o.campos["estado"].valor.split(" ")[0] for g in resultado.grupos for o in g.ofertas}


def test_filtro_solo_usadas_deja_las_usadas() -> None:
    assert _estados(_buscar(texto="pastillas", modo=Modo.IA, filtro="solo usadas")) == {"Usado"}


def test_filtro_solo_nuevas_deja_las_nuevas() -> None:
    resultado = _buscar(texto="pastillas", modo=Modo.IA, filtro="solo piezas nuevas")
    assert _estados(resultado) == {"Nuevo"}


def test_filtro_que_no_entiende_no_descarta_nada() -> None:
    con = _buscar(texto="pastillas", modo=Modo.IA, filtro="sin kits")
    sin = _buscar(texto="pastillas", modo=Modo.IA)
    assert len(con.grupos[0].ofertas) == len(sin.grupos[0].ofertas)


def test_modo_auto_no_usa_ia_si_no_falla_nada() -> None:
    resultado = _buscar(texto="pastillas", modo=Modo.AUTO, tiendas=("recambiosnorte.example",))
    assert resultado.llamadas_ia == 0 and resultado.coste_ia_usd == 0


def test_modo_auto_usa_ia_cuando_una_tienda_falla() -> None:
    assert _buscar(texto="pastillas", modo=Modo.AUTO).llamadas_ia > 0


def test_en_modo_clasico_no_hay_descartes_semanticos() -> None:
    motivos = [d.motivo for d in _buscar(texto="pastillas", modo=Modo.CLASICO).descartados]
    assert motivos and not any(m.startswith("🤖") for m in motivos)
    motivos_ia = [d.motivo for d in _buscar(texto="pastillas", modo=Modo.IA).descartados]
    assert any(m.startswith("🤖") for m in motivos_ia)


def test_ningun_perfil_hecho_por_la_ia_se_fija_sin_revision_humana() -> None:
    # Memoria §4: revisión humana del perfil antes de fijarlo.
    for tienda in FuenteDemo().tiendas():
        if tienda.estado.value in ("activo", "reparado"):
            for version in tienda.versiones:
                assert "revisado por" in version.autor, (tienda.dominio, version.numero)


def test_hay_ofertas_con_origenes_mezclados() -> None:
    # Cascada de la memoria §5: una misma ficha puede mezclar JSON-LD, perfil e IA.
    resultado = _buscar(texto="pastillas", modo=Modo.IA)
    assert any(
        len({v.origen for v in o.campos.values()}) > 1 for g in resultado.grupos for o in g.ofertas
    )
