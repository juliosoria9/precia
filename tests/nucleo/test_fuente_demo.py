import asyncio
from decimal import Decimal

import pytest

from precia.nucleo.fuente_datos import (
    DominioNoValido,
    RevisionNoValida,
    TiendaNoExiste,
    TiendaYaExiste,
    URLEntradaNoValida,
)
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    EstadoPaso,
    EstadoPerfil,
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


# --- Alta de tiendas y revisión del perfil (en memoria) -----------------------------------------


def test_alta_de_una_tienda_normaliza_el_dominio_y_nace_sin_perfil() -> None:
    fuente = FuenteDemo()
    tienda = asyncio.run(fuente.alta_tienda(" WWW.Nueva-Tienda.ES ", "Nueva Tienda"))
    assert (tienda.dominio, tienda.nombre) == ("nueva-tienda.es", "Nueva Tienda")
    assert tienda.estado is EstadoPerfil.SIN_PERFIL and tienda.versiones == ()
    assert fuente.tienda("nueva-tienda.es") == tienda
    assert tienda in fuente.tiendas()
    # Cada fuente tiene sus tiendas: el alta no cambia los datos de ejemplo del módulo.
    assert FuenteDemo().tienda("nueva-tienda.es") is None


def test_alta_sin_nombre_usa_el_dominio() -> None:
    tienda = asyncio.run(FuenteDemo().alta_tienda("nueva-tienda.es", "  "))
    assert tienda.nombre == "nueva-tienda.es"


@pytest.mark.parametrize("dominio", ["localhost", "169.254.169.254", "otra.es#x.es", "a b.es"])
def test_alta_rechaza_dominios_no_validos(dominio: str) -> None:
    with pytest.raises(DominioNoValido):
        asyncio.run(FuenteDemo().alta_tienda(dominio, "x"))


def test_alta_de_una_tienda_que_ya_existe() -> None:
    with pytest.raises(TiendaYaExiste):
        asyncio.run(FuenteDemo().alta_tienda("www.recambiosnorte.example", "Otra vez"))


def test_alta_con_url_de_entrada_la_guarda_normalizada() -> None:
    fuente = FuenteDemo()
    tienda = asyncio.run(
        fuente.alta_tienda(
            "nueva-tienda.es", "Nueva", entrada=" HTTPS://Nueva-Tienda.ES/catalogo?p={pagina} "
        )
    )
    assert tienda.entrada == "https://nueva-tienda.es/catalogo?p={pagina}"
    assert fuente.tienda("nueva-tienda.es") == tienda


@pytest.mark.parametrize(
    "entrada",
    [
        "https://otra.es/buscar?q={consulta}",
        "https://nueva-tienda.es@otra.es/",
        "data:text/html,hola",
        "https://nueva-tienda.es/buscar?q={consulta}&p={pagina}",
    ],
)
def test_alta_con_url_de_entrada_no_valida(entrada: str) -> None:
    fuente = FuenteDemo()
    with pytest.raises(URLEntradaNoValida):
        asyncio.run(fuente.alta_tienda("nueva-tienda.es", "Nueva", entrada=entrada))
    assert fuente.tienda("nueva-tienda.es") is None


def test_cambiar_y_quitar_la_url_de_entrada() -> None:
    fuente = FuenteDemo()
    busqueda = "https://www.recambiosnorte.example/buscar?q={consulta}"
    tienda = fuente.cambiar_entrada("WWW.recambiosnorte.example", busqueda)
    assert tienda.entrada == busqueda and tienda.dominio == "recambiosnorte.example"
    assert fuente.tienda("recambiosnorte.example") == tienda
    # El resto de la tienda (perfil, estado...) no cambia.
    original = next(t for t in FuenteDemo().tiendas() if t.dominio == tienda.dominio)
    assert tienda.versiones == original.versiones and tienda.estado is original.estado
    with pytest.raises(URLEntradaNoValida):
        fuente.cambiar_entrada("recambiosnorte.example", "https://piezasya.example/")
    assert fuente.tienda("recambiosnorte.example") == tienda
    assert fuente.cambiar_entrada("recambiosnorte.example", "  ").entrada is None
    assert FuenteDemo().tienda("recambiosnorte.example") == original


def test_cambiar_la_entrada_de_una_tienda_que_no_existe() -> None:
    with pytest.raises(TiendaNoExiste):
        FuenteDemo().cambiar_entrada("no-existe.example", "")


def test_tienda_nueva_sale_sin_perfil_en_la_busqueda() -> None:
    fuente = FuenteDemo()
    asyncio.run(fuente.alta_tienda("nueva-tienda.es", "Nueva"))
    resultado = asyncio.run(fuente.buscar(ConsultaBusqueda("pastillas", modo=Modo.CLASICO)))
    progreso = next(p for p in resultado.progreso if p.tienda == "nueva-tienda.es")
    assert [(p.nombre, p.estado) for p in progreso.pasos] == [("perfil", EstadoPaso.OMITIDO)]


def test_revisar_el_perfil_pendiente_lo_fija() -> None:
    fuente = FuenteDemo()
    tienda = fuente.revisar_perfil("desguacesur.example", 1)
    assert tienda.estado is EstadoPerfil.ACTIVO
    assert tienda.version_actual is not None
    assert "revisado por" in tienda.version_actual.autor
    assert fuente.tienda("desguacesur.example") == tienda


@pytest.mark.parametrize(
    ("dominio", "version", "error"),
    [
        ("desguacesur.example", 2, RevisionNoValida),
        ("recambiosnorte.example", 1, RevisionNoValida),
        ("autodescuento.example", 1, RevisionNoValida),
        ("no-existe.example", 1, TiendaNoExiste),
    ],
)
def test_revisar_un_perfil_que_no_se_puede_fijar(
    dominio: str, version: int, error: type[Exception]
) -> None:
    with pytest.raises(error):
        FuenteDemo().revisar_perfil(dominio, version)


def test_revisar_dos_veces_no_vale() -> None:
    fuente = FuenteDemo()
    fuente.revisar_perfil("desguacesur.example", 1)
    with pytest.raises(RevisionNoValida):
        fuente.revisar_perfil("desguacesur.example", 1)


def test_cerrar_no_hace_nada_en_la_demo() -> None:
    asyncio.run(FuenteDemo().cerrar())
