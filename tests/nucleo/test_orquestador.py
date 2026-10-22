"""Orquestador: el flujo de una búsqueda (nota 14) con los modos de ADR-002, sin red.

La tienda es la de `tests.perfiles.sitio` (portada, buscador, listado y fichas falsas) y la IA,
el `ProveedorSimulado` detrás de un `ServicioIA` de verdad con el almacén como registro.
"""

import asyncio
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen
from precia.fuentes.sitemap import MAX_ENTRADAS_SITEMAP, MAX_FICHEROS_SITEMAP, palabras_url
from precia.ia.servicio import ServicioIA
from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import ErrorIA, IANoDisponible, PeticionIA, PresupuestoAgotado
from precia.navegador.tipos import Bloqueado, ErrorDescarga, Pagina, ProhibidoPorRobots
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    EstadoPaso,
    EstadoPerfil,
    Modo,
    OrigenDato,
    Paso,
    ProgresoTienda,
    ResultadoBusqueda,
)
from precia.nucleo.orquestador import (
    BLOQUEO,
    ESTRUCTURADOS,
    MAX_PAGINAS_LISTADO,
    MAX_RESULTADOS_PAGINA,
    REFRESCO_CATALOGO,
    Orquestador,
)
from precia.perfiles import catalogo
from precia.perfiles.tipos import (
    TIPO_API_SHOPIFY,
    TIPO_API_WOOCOMMERCE,
    Perfil,
)
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.plataformas import (
    SHOPIFY,
    SHOPIFY_API,
    SHOPIFY_PORTADA,
    WOO,
    WOO_API,
    WOO_TITULOS,
    paginas_shopify,
    paginas_woo,
    perfil_api,
)
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    LISTADO,
    LISTADO_OK,
    PORTADA,
    PRODUCTOS,
    SITEMAP,
    TIENDA,
    NavegadorFalso,
    Producto,
    html_ficha,
    html_listado,
    html_next,
    next_data,
    paginas_sitio,
    perfil,
    perfil_listado,
    perfil_sitemap,
    xml_indice,
    xml_urlset,
)

AHORA = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
OTRA = "desguace-prueba.es"
ROTO = perfil(
    sel_resultado="div.resultado",
    sel_titulo="span.titulo",
    sel_enlace="a.ver @href",
    sel_precio_listado="b.pvp",
)
PROPUESTA: dict[str, Any] = {**LISTADO_OK, "sel_campos": CAMPOS_OK}
SIN_RESULTADOS = (
    "<html><body><h1>Buscar</h1><p>No hay productos que coincidan con tu búsqueda.</p>"
    "</body></html>"
)


def nuevo_almacen(*perfiles: Perfil, tiendas: Iterable[str] = (DOMINIO,)) -> Almacen:
    almacen = Almacen(":memory:", reloj=lambda: AHORA)
    for dominio in tiendas:
        almacen.alta_tienda(dominio, dominio.split(".")[0].title())
    for p in perfiles:
        almacen.guardar_perfil(p)
    return almacen


@dataclass
class Montaje:
    orquestador: Orquestador
    almacen: Almacen
    navegador: Any
    proveedor: ProveedorSimulado | None

    def buscar(self, **opciones: Any) -> ResultadoBusqueda:
        opciones.setdefault("texto", CONSULTA)
        return asyncio.run(self.orquestador.buscar(ConsultaBusqueda(**opciones)))

    @property
    def peticiones_ia(self) -> list[str]:
        return [p.componente for p in self.proveedor.peticiones] if self.proveedor else []


def montar(
    almacen: Almacen,
    navegador: Any,
    guion: Mapping[str, Iterable[Guion]] | None = None,
    *,
    tope: int = 20,
    **opciones: Any,
) -> Montaje:
    proveedor = None
    servicio = None
    if guion is not None:
        proveedor = ProveedorSimulado(guion, coste_usd=Decimal("0.001"))
        servicio = ServicioIA(
            proveedor,
            almacen,
            presupuesto_mes_usd=Decimal(10),
            tope_llamadas=tope,
            reloj=lambda: AHORA,
        )
    orquestador = Orquestador(
        almacen,
        navegador,
        servicio,
        validador=validar_con_dns_publico,
        reloj=lambda: AHORA,
        **opciones,
    )
    return Montaje(orquestador, almacen, navegador, proveedor)


def progreso(resultado: ResultadoBusqueda, tienda: str = DOMINIO) -> ProgresoTienda:
    return next(p for p in resultado.progreso if p.tienda == tienda)


def paso(resultado: ResultadoBusqueda, nombre: str, tienda: str = DOMINIO) -> Paso:
    return next(p for p in progreso(resultado, tienda).pasos if p.nombre == nombre)


def ofertas(resultado: ResultadoBusqueda) -> list[Any]:
    return [o for g in resultado.grupos for o in g.ofertas]


def redirigida(**opciones: Any) -> NavegadorFalso:
    return NavegadorFalso(paginas_sitio(**opciones), redirecciones={f"https://{DOMINIO}/": PORTADA})


# --- Modo clásico ------------------------------------------------------------------------------


def test_clasico_con_perfil_manual_da_resultados_sin_ia() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.CLASICO)

    assert not r.es_demo
    assert sorted(o.url for o in ofertas(r)) == sorted(p.url for p in PRODUCTOS)
    assert m.peticiones_ia == []
    assert (r.llamadas_ia, r.coste_ia_usd) == (0, Decimal(0))
    assert [p.nombre for p in progreso(r).pasos] == [
        "robots.txt",
        "perfil",
        "búsqueda",
        "extracción",
        "verificación",
    ]
    assert all(p.estado is EstadoPaso.OK for p in progreso(r).pasos)
    # El anuncio patrocinado de otra web no se visita.
    assert m.navegador.urls == [BUSQUEDA, *(p.url for p in PRODUCTOS)]


def test_clasico_guarda_el_historico_y_actualiza_la_tienda() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO)

    for grupo in r.grupos:
        guardado = m.almacen.producto(grupo.id)
        assert guardado is not None
        assert {o.url for o in guardado.ofertas} == {o.url for o in grupo.ofertas}
        assert m.almacen.historico(grupo.id)
    izquierdo = next(o for o in ofertas(r) if o.url == PRODUCTOS[0].url)
    assert izquierdo.precio == Decimal("89.90")
    assert izquierdo.campos["marca"].valor == "Valeo"
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None
    assert (tienda.estado, tienda.ultima_visita, tienda.robots_permite) == (
        EstadoPerfil.ACTIVO,
        AHORA,
        True,
    )
    (busqueda,) = m.almacen.busquedas()
    assert (busqueda.texto, busqueda.modo) == (CONSULTA, Modo.CLASICO)


def test_clasico_sin_perfil_no_configura() -> None:
    m = montar(nuevo_almacen(), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert paso(r, "perfil") == Paso(
        "perfil", EstadoPaso.OMITIDO, "Sin perfil (en modo clásico no se configura)"
    )
    assert m.navegador.pedidas == []
    assert m.peticiones_ia == []
    assert r.grupos == ()


@pytest.mark.parametrize(
    "roto",
    [ROTO, perfil(sel_resultado="li.producto:has(a)")],
    ids=["listado-vacio", "selector-no-valido"],
)
def test_clasico_perfil_roto_no_se_repara(roto: Perfil) -> None:
    m = montar(nuevo_almacen(roto), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.CLASICO)
    error = [p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR]
    assert len(error) == 1 and "perfil roto" in error[0].detalle.lower()
    assert m.peticiones_ia == []
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO


def test_listado_sin_resultados_no_es_un_perfil_roto() -> None:
    # Sin productos con precio en la página no se gasta una llamada al reparador: la tienda
    # simplemente no tiene lo buscado (o lo esconde a propósito para provocar llamadas).
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = SIN_RESULTADOS
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, "extracción").estado is EstadoPaso.AVISO
    assert "Sin resultados" in paso(r, "extracción").detalle
    assert m.peticiones_ia == []
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


# --- Modo auto ---------------------------------------------------------------------------------


def test_auto_sin_perfil_lo_configura_y_lo_usa_pendiente_de_revision() -> None:
    m = montar(nuevo_almacen(), redirigida(), {"configurador": [LISTADO_OK, CAMPOS_OK]})
    r = m.buscar(modo=Modo.AUTO)

    assert m.peticiones_ia == ["configurador", "configurador"]
    assert r.llamadas_ia == 2 and r.coste_ia_usd == Decimal("0.002")
    nuevo = m.almacen.perfil_vigente(DOMINIO)
    assert nuevo is not None and (nuevo.version, nuevo.revisado) == (1, False)
    assert nuevo.autor == "🤖 configurador"
    pasos = progreso(r).pasos
    assert pasos[0] == Paso("robots.txt", EstadoPaso.OK, "Permite la búsqueda")
    assert paso(r, "perfil").estado is EstadoPaso.IA
    assert "pendiente de revisión" in paso(r, "perfil").detalle
    assert len(ofertas(r)) == len(PRODUCTOS)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.PENDIENTE_REVISION


def test_auto_con_perfil_sin_revisar_lo_usa_y_avisa() -> None:
    sin_revisar = perfil(autor="🤖 configurador", revisado=False)
    m = montar(nuevo_almacen(sin_revisar), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, "perfil").estado is EstadoPaso.AVISO
    assert "pendiente de revisión" in paso(r, "perfil").detalle
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert m.peticiones_ia == []


def test_auto_perfil_roto_lo_repara_con_una_version_nueva() -> None:
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(paginas_sitio()), {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)

    assert m.peticiones_ia == ["reparador"]
    reparado = m.almacen.perfil_vigente(DOMINIO)
    assert reparado is not None
    assert (reparado.version, reparado.revisado, reparado.autor) == (2, False, "🤖 reparador")
    reparacion = [p for p in progreso(r).pasos if p.estado is EstadoPaso.IA]
    assert any("Reparador" in p.detalle and "v2" in p.detalle for p in reparacion)
    assert len(ofertas(r)) == len(PRODUCTOS)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.PENDIENTE_REVISION


def test_auto_reparacion_que_no_valida_deja_el_perfil_roto() -> None:
    anuncio = {**PROPUESTA, "sel_resultado": "div.patrocinado"}
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(paginas_sitio()), {"reparador": [anuncio]})
    r = m.buscar(modo=Modo.AUTO)
    assert any(p.estado is EstadoPaso.ERROR and "Reparador" in p.detalle for p in progreso(r).pasos)
    vigente = m.almacen.perfil_vigente(DOMINIO)
    assert vigente is not None and vigente.version == 1
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO
    assert r.grupos == ()


def test_auto_error_de_ia_en_el_reparador_degrada_a_clasico() -> None:
    agotado = PresupuestoAgotado("Se ha agotado el presupuesto de IA del mes (10 $)")
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(paginas_sitio()), {"reparador": [agotado]})
    r = m.buscar(modo=Modo.AUTO)
    avisos = [p for p in progreso(r).pasos if p.estado is EstadoPaso.AVISO]
    assert any("presupuesto" in p.detalle for p in avisos)
    assert any(p.estado is EstadoPaso.ERROR for p in progreso(r).pasos)


def test_auto_error_de_ia_en_el_configurador_no_rompe_la_busqueda() -> None:
    agotado = PresupuestoAgotado("Se ha alcanzado el tope de 20 llamadas de IA por búsqueda")
    m = montar(nuevo_almacen(), redirigida(), {"configurador": [agotado]})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, "perfil").estado is EstadoPaso.AVISO
    assert "tope" in paso(r, "perfil").detalle
    assert m.almacen.perfil_vigente(DOMINIO) is None


def test_auto_configuracion_fallida_es_un_error_de_la_tienda() -> None:
    sitio = paginas_sitio(busqueda=None, formulario=False)
    m = montar(nuevo_almacen(), NavegadorFalso(sitio), {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, "perfil").estado is EstadoPaso.ERROR
    assert "Configurador" in paso(r, "perfil").detalle


def test_auto_usa_el_extractor_de_respaldo_si_faltan_campos() -> None:
    solo_precio = perfil(sel_campos={"precio": CAMPOS_OK["precio"]})
    guion = {"extractor": [{"referencia_oem": p.referencia} for p in PRODUCTOS]}
    m = montar(nuevo_almacen(solo_precio), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "referencia_oem"))
    assert m.peticiones_ia == ["extractor"] * len(PRODUCTOS)
    assert paso(r, "extracción").estado is EstadoPaso.IA
    assert all(o.campos["referencia_oem"].origen.value == "ia" for o in ofertas(r))


def test_clasico_no_usa_el_extractor_aunque_falten_campos() -> None:
    solo_precio = perfil(sel_campos={"precio": CAMPOS_OK["precio"]})
    m = montar(nuevo_almacen(solo_precio), NavegadorFalso(paginas_sitio(jsonld=())), {})
    r = m.buscar(modo=Modo.CLASICO, campos=("precio", "referencia_oem"))
    assert m.peticiones_ia == []
    assert all("referencia_oem" not in o.campos for o in ofertas(r))


def test_auto_con_valor_no_anclado_avisa_en_la_verificacion() -> None:
    solo_precio = perfil(sel_campos={"precio": CAMPOS_OK["precio"]})
    guion = {"extractor": [{"marca": "Marca inventada"} for _ in PRODUCTOS]}
    m = montar(nuevo_almacen(solo_precio), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "marca"))
    verificacion = paso(r, "verificación")
    assert verificacion.estado is EstadoPaso.AVISO
    assert "4 valores" in verificacion.detalle


# --- Sin IA ------------------------------------------------------------------------------------


def test_sin_servicio_de_ia_auto_se_comporta_como_clasico_y_lo_dice() -> None:
    m = montar(nuevo_almacen(), redirigida())
    r = m.buscar(modo=Modo.AUTO, filtro="solo nuevas")
    general = progreso(r, "PrecIA")
    assert any(
        p.estado is EstadoPaso.AVISO and p.detalle.startswith("IA no disponible")
        for p in general.pasos
    )
    assert any(p.nombre == "filtro" and p.estado is EstadoPaso.AVISO for p in general.pasos)
    assert paso(r, "perfil").estado is EstadoPaso.OMITIDO
    assert m.navegador.pedidas == []


def test_sin_servicio_de_ia_modo_ia_usa_el_perfil_sin_ia() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.IA)
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert r.llamadas_ia == 0
    assert all(g.criterio != "🤖 Emparejado por IA" for g in r.grupos)


def test_clasico_no_avisa_de_que_no_hay_ia() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO)
    assert all(p.tienda != "PrecIA" for p in r.progreso)


# --- Modo IA -----------------------------------------------------------------------------------


def _clasificaciones(*clases: str) -> dict[str, Any]:
    return {
        "clasificaciones": [
            {"indice": i, "clase": c, "motivo": "Motivo de prueba"} for i, c in enumerate(clases)
        ]
    }


def test_ia_emparejado_semantico() -> None:
    guion = {"emparejado": [_clasificaciones("misma_pieza", "misma_pieza", "variante", "otra")]}
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), guion)
    r = m.buscar(modo=Modo.IA)
    assert m.peticiones_ia == ["emparejado"]
    assert len(ofertas(r)) == 2
    assert all(d.motivo.startswith("🤖") for d in r.descartados)
    assert {g.criterio for g in r.grupos} == {"🤖 Emparejado por IA"}


def test_error_de_ia_en_el_emparejado_repite_sin_sesion_y_avisa() -> None:
    caida = ErrorIA("El servidor de IA no responde")
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), {"emparejado": [caida]})
    r = m.buscar(modo=Modo.IA, filtro="solo usadas")
    assert len(ofertas(r)) == len(PRODUCTOS)
    general = progreso(r, "PrecIA")
    assert any(p.nombre == "emparejado" and p.estado is EstadoPaso.AVISO for p in general.pasos)
    assert any(p.nombre == "filtro" and p.estado is EstadoPaso.AVISO for p in general.pasos)


def test_auto_con_filtro_usa_la_ia_para_filtrar() -> None:
    filtro = {
        "clasificaciones": [
            {"indice": i, "cumple_filtro": p.estado == "Usado", "motivo": "x"}
            for i, p in enumerate(PRODUCTOS)
        ]
    }
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), {"emparejado": [filtro]})
    r = m.buscar(modo=Modo.AUTO, filtro="solo usadas")
    assert m.peticiones_ia == ["emparejado"]
    assert len(ofertas(r)) == 2


# --- Navegador: robots, bloqueos y errores ------------------------------------------------------


def test_robots_que_prohibe_la_busqueda_omite_la_tienda() -> None:
    prohibido = ProhibidoPorRobots(f"El robots.txt de www.{DOMINIO} no permite visitar esa página.")
    navegador = NavegadorFalso(paginas_sitio(), errores={BUSQUEDA: prohibido})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert progreso(r).pasos == (Paso("robots.txt", EstadoPaso.OMITIDO, str(prohibido)),)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and not tienda.robots_permite
    assert r.grupos == ()


def test_robots_ignorado_se_avisa_en_el_progreso() -> None:
    # ADR-017: se visita aunque el robots.txt lo prohíba, pero el usuario lo ve en el progreso.
    navegador = NavegadorFalso(paginas_sitio(), ignorados=frozenset({f"www.{DOMINIO}"}))
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    paso = progreso(r).pasos[0]
    assert paso.nombre == "robots.txt" and paso.estado is EstadoPaso.AVISO
    assert "ignora" in paso.detalle and "ADR-017" in paso.detalle
    assert ofertas(r)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and not tienda.robots_permite


class NavegadorPorProxy(NavegadorFalso):
    """Como el sitio falso, pero todo llega por el proxy (`Pagina.via_proxy`)."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        return replace(await super().obtener(url, js=js, grande=grande), via_proxy=True)


def test_busqueda_por_el_proxy_se_dice_en_el_progreso() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorPorProxy(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO)
    busqueda = paso(r, "búsqueda")
    assert busqueda.estado is EstadoPaso.OK
    assert busqueda.detalle == "Resultados descargados por el proxy"
    assert ofertas(r)


def test_busqueda_directa_no_menciona_el_proxy() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    assert "proxy" not in paso(m.buscar(modo=Modo.CLASICO), "búsqueda").detalle


class NavegadorProhibido:
    """Todo lo prohíbe el robots.txt: el configurador no llega a ninguna página."""

    def __init__(self) -> None:
        self.pedidas: list[str] = []

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        self.pedidas.append(url)
        raise ProhibidoPorRobots("El robots.txt de la tienda no permite visitar esa página.")

    async def sitemaps(self, url: str) -> tuple[str, ...]:
        return ()


def test_robots_que_lo_prohibe_todo_al_configurar_omite_la_tienda() -> None:
    m = montar(nuevo_almacen(), NavegadorProhibido(), {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    assert [(p.nombre, p.estado) for p in progreso(r).pasos] == [("robots.txt", EstadoPaso.OMITIDO)]
    assert m.peticiones_ia == []


def test_bloqueo_en_la_busqueda_es_un_error_y_se_cuenta() -> None:
    bloqueo = Bloqueado(BUSQUEDA, "reCAPTCHA")
    m = montar(
        nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio(), errores={BUSQUEDA: bloqueo})
    )
    r = m.buscar(modo=Modo.CLASICO)
    errores = [p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR]
    assert len(errores) == 1
    assert errores[0].detalle.startswith("Bloqueo (CAPTCHA o anti-bot): no se evade")
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.bloqueos == 1


def test_bloqueo_al_configurar_es_un_error() -> None:
    portada = f"https://{DOMINIO}/"
    bloqueo = Bloqueado(portada, "Cloudflare Turnstile")
    navegador = NavegadorFalso(paginas_sitio(), errores={portada: bloqueo})
    m = montar(nuevo_almacen(), navegador, {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, "perfil").estado is EstadoPaso.ERROR
    assert paso(r, "perfil").detalle.startswith("Bloqueo (CAPTCHA o anti-bot): no se evade")


def test_bloqueo_en_una_ficha_para_y_conserva_las_anteriores() -> None:
    bloqueo = Bloqueado(PRODUCTOS[1].url, "hCaptcha")
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[1].url: bloqueo})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]
    assert navegador.urls == [BUSQUEDA, PRODUCTOS[0].url, PRODUCTOS[1].url]
    assert paso(r, "extracción").estado is EstadoPaso.ERROR
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.bloqueos == 1


@pytest.mark.parametrize(
    "error", [ErrorDescarga("No se pudo conectar con la tienda."), ProhibidoPorRobots("No.")]
)
def test_ficha_que_no_se_puede_abrir_se_salta(error: Exception) -> None:
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[1].url: error})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert len(ofertas(r)) == len(PRODUCTOS) - 1
    assert paso(r, "extracción").estado is EstadoPaso.AVISO


def test_error_de_descarga_en_la_busqueda() -> None:
    error = ErrorDescarga("No se pudo conectar con la tienda.")
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio(), errores={BUSQUEDA: error}))
    r = m.buscar(modo=Modo.CLASICO)
    assert paso(r, "búsqueda") == Paso("búsqueda", EstadoPaso.ERROR, str(error))


def test_busqueda_que_redirige_a_otra_web_no_se_usa() -> None:
    sitio = paginas_sitio()
    sitio["https://otra-tienda.es/"] = html_listado()
    navegador = NavegadorFalso(sitio, redirecciones={BUSQUEDA: "https://otra-tienda.es/"})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert paso(r, "búsqueda").estado is EstadoPaso.ERROR
    assert r.grupos == ()


# --- Límites, tiendas y dominios ---------------------------------------------------------------


def test_solo_abre_las_primeras_fichas() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), max_fichas=2)
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [BUSQUEDA, PRODUCTOS[0].url, PRODUCTOS[1].url]
    assert len(ofertas(r)) == 2


def test_limita_las_ofertas_que_llegan_al_agrupador() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), max_ofertas=3)
    r = m.buscar(modo=Modo.CLASICO)
    assert len(ofertas(r)) + len(r.descartados) == 3
    general = progreso(r, "PrecIA")
    assert any(p.nombre == "emparejado" and "3" in p.detalle for p in general.pasos)


def test_tiendas_desconocidas_o_no_validas_no_se_visitan() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO, tiendas=("169.254.169.254", "otra.es", "localhost:22"))
    assert m.navegador.pedidas == []
    assert all(p.tienda == "PrecIA" for p in r.progreso)


def test_el_dominio_se_normaliza_antes_de_usar_el_almacen() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO, tiendas=(" WWW.Recambios-Prueba.ES. ",))
    assert len(ofertas(r)) == len(PRODUCTOS)


def test_google_pedido_avisa_de_que_no_se_consulta() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO, descubrir=True)
    assert progreso(r, "Google").pasos[0].nombre == "descubrir"


def test_duracion_medida_con_el_cronometro() -> None:
    tiempos = iter([100.0, 103.5])
    m = montar(
        nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), cronometro=lambda: next(tiempos)
    )
    assert m.buscar(modo=Modo.CLASICO).duracion_s == pytest.approx(3.5)


# --- Tiendas en paralelo -----------------------------------------------------------------------


def _sitio_doble() -> dict[str, str]:
    """La misma tienda falsa, servida también en otro dominio."""
    sitio = paginas_sitio()
    otra = {url.replace(DOMINIO, OTRA): html for url, html in sitio.items()}
    return {**sitio, **otra}


class NavegadorCita(NavegadorFalso):
    """Las búsquedas de las dos tiendas solo terminan si están en vuelo a la vez."""

    def __init__(self) -> None:
        super().__init__(_sitio_doble())
        self.en_vuelo = 0
        self.ambas = asyncio.Event()

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        if "/buscar" in url:
            self.en_vuelo += 1
            if self.en_vuelo == 2:
                self.ambas.set()
            await asyncio.wait_for(self.ambas.wait(), 2)
        return await super().obtener(url, js=js, grande=grande)


def test_las_tiendas_se_buscan_en_paralelo() -> None:
    otro_perfil = perfil(
        dominio=OTRA, url_busqueda=f"https://www.{OTRA}/buscar?orden=relevancia&q={{consulta}}"
    )
    almacen = nuevo_almacen(perfil(), otro_perfil, tiendas=(DOMINIO, OTRA))
    m = montar(almacen, NavegadorCita())
    r = m.buscar(modo=Modo.CLASICO)
    assert {o.tienda for o in ofertas(r)} == {DOMINIO, OTRA}
    assert [p.tienda for p in r.progreso] == sorted([DOMINIO, OTRA])


def test_una_tienda_con_error_no_impide_las_demas() -> None:
    otro_perfil = perfil(
        dominio=OTRA, url_busqueda=f"https://www.{OTRA}/buscar?orden=relevancia&q={{consulta}}"
    )
    almacen = nuevo_almacen(perfil(), otro_perfil, tiendas=(DOMINIO, OTRA))
    bloqueo = Bloqueado(BUSQUEDA, "reCAPTCHA")
    m = montar(almacen, NavegadorFalso(_sitio_doble(), errores={BUSQUEDA: bloqueo}))
    r = m.buscar(modo=Modo.CLASICO)
    assert {o.tienda for o in ofertas(r)} == {OTRA}
    assert any(p.estado is EstadoPaso.ERROR for p in progreso(r, DOMINIO).pasos)


def test_la_url_de_la_oferta_es_de_la_tienda() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO)
    assert all(o.url.startswith(TIENDA + "/p/") for o in ofertas(r))


def test_robots_que_prohibe_una_ficha_al_reparar_no_omite_la_tienda() -> None:
    # La búsqueda sí estaba permitida: no es una tienda omitida, es una reparación que no se
    # ha podido validar.
    prohibido = ProhibidoPorRobots("El robots.txt de la tienda no permite visitar esa página.")
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[0].url: prohibido})
    m = montar(nuevo_almacen(ROTO), navegador, {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)
    pasos = progreso(r).pasos
    assert [p.nombre for p in pasos].count("robots.txt") == 1
    assert paso(r, "robots.txt").estado is EstadoPaso.OK
    assert any(p.estado is EstadoPaso.ERROR and "robots.txt" in p.detalle for p in pasos)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.robots_permite


class FalloRaro(Exception):
    """Un error que nadie espera (un fallo de Playwright, de la librería HTTP...)."""


def test_un_error_inesperado_en_una_tienda_no_tumba_la_busqueda() -> None:
    # Sin esto, `asyncio.gather` propagaba la excepción: la búsqueda entera daba un 500 y las
    # demás tiendas se quedaban corriendo sin que nadie recogiera su resultado.
    otro_perfil = perfil(
        dominio=OTRA, url_busqueda=f"https://www.{OTRA}/buscar?orden=relevancia&q={{consulta}}"
    )
    almacen = nuevo_almacen(perfil(), otro_perfil, tiendas=(DOMINIO, OTRA))
    fallo = FalloRaro("detalle interno en /ruta/secreta")
    m = montar(almacen, NavegadorFalso(_sitio_doble(), errores={BUSQUEDA: fallo}))
    r = m.buscar(modo=Modo.CLASICO)
    assert {o.tienda for o in ofertas(r)} == {OTRA}
    errores = [p for p in progreso(r, DOMINIO).pasos if p.estado is EstadoPaso.ERROR]
    assert len(errores) == 1 and errores[0].nombre == "búsqueda"
    assert "inesperado" in errores[0].detalle
    # El detalle interno va al registro, no a la página.
    assert "secreta" not in errores[0].detalle
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.ultima_visita == AHORA


def test_un_error_inesperado_en_una_ficha_conserva_las_demas_tiendas() -> None:
    otro_perfil = perfil(
        dominio=OTRA, url_busqueda=f"https://www.{OTRA}/buscar?orden=relevancia&q={{consulta}}"
    )
    almacen = nuevo_almacen(perfil(), otro_perfil, tiendas=(DOMINIO, OTRA))
    m = montar(almacen, NavegadorFalso(_sitio_doble(), errores={PRODUCTOS[0].url: FalloRaro()}))
    r = m.buscar(modo=Modo.CLASICO)
    assert OTRA in {o.tienda for o in ofertas(r)}
    assert any(p.estado is EstadoPaso.ERROR for p in progreso(r, DOMINIO).pasos)


def test_un_error_de_ia_en_el_extractor_deja_la_tienda_sin_ia() -> None:
    # Con la API caída, cada ficha esperaría su propio timeout (y cada salida no válida son
    # dos llamadas de pago): tras el primer error de la IA, la tienda sigue sin IA y lo dice.
    solo_precio = perfil(sel_campos={"precio": CAMPOS_OK["precio"]})
    caida = IANoDisponible("La API de Anthropic no responde")
    guion: dict[str, list[Guion]] = {
        "extractor": [caida, *({"referencia_oem": p.referencia} for p in PRODUCTOS)]
    }
    m = montar(nuevo_almacen(solo_precio), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "referencia_oem"))
    assert m.peticiones_ia == ["extractor"]
    aviso = paso(r, "IA")
    assert aviso.estado is EstadoPaso.AVISO and "no responde" in aviso.detalle
    assert len(ofertas(r)) == len(PRODUCTOS)


# --- Perfiles api_* y datos estructurados (nota 15 §D) --------------------------------------------


def test_perfil_api_busca_en_la_api_sin_abrir_fichas() -> None:
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas_woo()), {})
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "referencia_oem"))
    # Una sola petición, a la API, sin JavaScript ni fichas; y sin IA.
    assert m.navegador.pedidas == [(WOO_API, False)]
    assert m.peticiones_ia == []
    leidas = ofertas(r)
    assert {o.titulo for o in leidas} == {WOO_TITULOS[0], WOO_TITULOS[1]}
    assert all(o.campos["precio"].origen is OrigenDato.API for o in leidas)
    assert all(o.url.startswith(f"https://{WOO}/producto/") for o in leidas)
    # La tienda marca las pastillas como agotadas.
    assert {o.titulo: o.disponible for o in leidas}[WOO_TITULOS[1]] is False
    extraccion = paso(r, "extracción", WOO)
    assert "API pública de WooCommerce" in extraccion.detalle
    assert "3 productos" in extraccion.detalle and "1 sin precio" in extraccion.detalle
    assert paso(r, "verificación", WOO).estado is EstadoPaso.OK
    tienda = m.almacen.tienda(WOO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def test_perfil_api_respeta_max_fichas() -> None:
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas_woo()), {}, max_fichas=1)
    r = m.buscar(modo=Modo.CLASICO)
    assert [o.titulo for o in ofertas(r)] == [WOO_TITULOS[0]]


def test_perfil_api_shopify_usa_la_moneda_de_la_portada() -> None:
    almacen = nuevo_almacen(perfil_api(TIPO_API_SHOPIFY, revisado=True), tiendas=(SHOPIFY,))
    m = montar(almacen, NavegadorFalso(paginas_shopify()), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [SHOPIFY_API, SHOPIFY_PORTADA]
    assert len(ofertas(r)) == 2


def test_perfil_api_sin_resultados_no_es_un_perfil_roto() -> None:
    paginas = paginas_woo()
    paginas[WOO_API] = "[]"
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas), {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, "extracción", WOO).estado is EstadoPaso.AVISO
    assert "Sin resultados" in paso(r, "extracción", WOO).detalle
    assert m.peticiones_ia == []
    tienda = m.almacen.tienda(WOO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def test_perfil_api_con_error_http_es_un_error_de_la_busqueda() -> None:
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso({}), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert paso(r, "búsqueda", WOO).estado is EstadoPaso.ERROR
    assert "404" in paso(r, "búsqueda", WOO).detalle


def test_bloqueo_en_la_api_se_para() -> None:
    bloqueo = Bloqueado(WOO_API, "La tienda rechaza el acceso automático (HTTP 403)")
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas_woo(), errores={WOO_API: bloqueo}), {})
    r = m.buscar(modo=Modo.CLASICO)
    errores = [p for p in progreso(r, WOO).pasos if p.estado is EstadoPaso.ERROR]
    assert len(errores) == 1
    assert errores[0].detalle.startswith("Bloqueo (CAPTCHA o anti-bot): no se evade")
    tienda = m.almacen.tienda(WOO)
    assert tienda is not None and tienda.bloqueos == 1


def test_auto_configura_un_perfil_api_sin_ia() -> None:
    m = montar(nuevo_almacen(tiendas=(WOO,)), NavegadorFalso(paginas_woo()), {})
    r = m.buscar(modo=Modo.AUTO)
    assert m.peticiones_ia == [] and r.llamadas_ia == 0
    nuevo = m.almacen.perfil_vigente(WOO)
    assert nuevo is not None and nuevo.tipo == TIPO_API_WOOCOMMERCE and not nuevo.revisado
    configuracion = paso(r, "perfil", WOO)
    assert configuracion.estado is EstadoPaso.AVISO
    assert "API pública de WooCommerce" in configuracion.detalle
    assert "sin IA" in configuracion.detalle and "pendiente de revisión" in configuracion.detalle
    assert len(ofertas(r)) == 2
    tienda = m.almacen.tienda(WOO)
    assert tienda is not None and tienda.estado is EstadoPerfil.PENDIENTE_REVISION


def test_selectores_sin_resultados_usan_los_datos_estructurados() -> None:
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = html_next()
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(sitio), {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)
    aviso = Paso(
        "perfil",
        EstadoPaso.AVISO,
        "Selectores sin resultados: se usan los datos estructurados de la página",
    )
    assert aviso in progreso(r).pasos
    # Ni reparador ni perfil roto: las ofertas salen y son válidas.
    assert m.peticiones_ia == []
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert m.almacen.perfil_vigente(DOMINIO) == ROTO
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def test_datos_estructurados_tambien_en_clasico() -> None:
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = html_next()
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert not any(p.estado is EstadoPaso.ERROR for p in progreso(r).pasos)


def test_datos_estructurados_sin_ofertas_validas_si_es_un_perfil_roto() -> None:
    # El JSON embebido enlaza a fichas que ya no existen: no salen ofertas válidas y el listado
    # visible sí tiene productos con precio, así que el perfil está roto y se repara.
    viejos = [replace(p, ruta=p.ruta + "-retirado") for p in PRODUCTOS]
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = html_listado().replace("</body>", next_data(viejos) + "</body>")
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(sitio), {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)
    assert ESTRUCTURADOS in [p.detalle for p in progreso(r).pasos]
    assert m.peticiones_ia == ["reparador"]
    reparado = m.almacen.perfil_vigente(DOMINIO)
    assert reparado is not None and reparado.version == 2
    assert len(ofertas(r)) == len(PRODUCTOS)


def test_selector_no_valido_no_se_tapa_con_los_datos_estructurados() -> None:
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = html_next()
    roto = perfil(sel_resultado="li.producto:has(a)")
    m = montar(nuevo_almacen(roto), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO
    assert r.grupos == ()


DESTACADOS = (
    replace(PRODUCTOS[0], ruta="/p/bateria-varta-70ah", titulo="Batería Varta Blue 70Ah"),
    replace(PRODUCTOS[1], ruta="/p/aceite-castrol-5w30", titulo="Aceite Castrol Edge 5W30 5L"),
)


def test_destacados_del_json_embebido_no_son_resultados_de_la_busqueda() -> None:
    # Revisión adversarial: la página dice que no hay resultados, pero su `__NEXT_DATA__` trae
    # los «destacados» de la tienda (con precio), como en casi todas las tiendas Next.js. No
    # son la pieza buscada: ni se abren sus fichas ni salen como ofertas.
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = SIN_RESULTADOS.replace("</body>", next_data(DESTACADOS) + "</body>")
    for producto in DESTACADOS:
        sitio[f"{TIENDA}{producto.ruta}"] = html_ficha(producto)
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(sitio), {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)
    assert ESTRUCTURADOS not in [p.detalle for p in progreso(r).pasos]
    assert ofertas(r) == []
    assert m.navegador.urls == [BUSQUEDA]
    assert m.peticiones_ia == []
    assert "Sin resultados" in paso(r, "extracción").detalle


# --- Perfiles sin buscador: listado y sitemap (nota 16) -------------------------------------------

OTROS = (
    replace(PRODUCTOS[0], ruta="/p/retrovisor-seat-ibiza", titulo="Retrovisor derecho Seat Ibiza"),
    replace(PRODUCTOS[1], ruta="/p/bomba-agua-opel-astra", titulo="Bomba de agua Opel Astra"),
)


def pagina_listado(numero: int) -> str:
    return LISTADO.replace("{pagina}", str(numero))


def otro(numero: int) -> Producto:
    """Un producto que no tiene nada que ver con «faro golf»."""
    return replace(
        PRODUCTOS[2], ruta=f"/p/alternador-seat-{numero}", titulo=f"Alternador Seat modelo {numero}"
    )


def sitio_listado(*paginas: Iterable[Producto]) -> dict[str, str]:
    """Las páginas del listado (1, 2...) y las fichas de todos sus productos."""
    sitio: dict[str, str] = {}
    for numero, productos in enumerate(paginas, start=1):
        lista = tuple(productos)
        sitio[pagina_listado(numero)] = html_listado(lista)
        for producto in lista:
            sitio[producto.url] = html_ficha(producto)
    return sitio


def test_perfil_listado_recorre_las_paginas_y_filtra_por_la_consulta() -> None:
    sitio = sitio_listado([OTROS[0], PRODUCTOS[0]], [PRODUCTOS[1], OTROS[1]], [])
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.AUTO)

    # Se para en la primera página sin resultados y solo abre las fichas que nombran la consulta.
    assert m.navegador.urls == [
        pagina_listado(1),
        pagina_listado(2),
        pagina_listado(3),
        PRODUCTOS[0].url,
        PRODUCTOS[1].url,
    ]
    assert sorted(o.url for o in ofertas(r)) == sorted([PRODUCTOS[0].url, PRODUCTOS[1].url])
    assert paso(r, "búsqueda") == Paso(
        "búsqueda",
        EstadoPaso.OK,
        "Listado de la tienda (sin buscador): 3 páginas, 2 coincidencias de 4",
    )
    assert [p.nombre for p in progreso(r).pasos] == [
        "robots.txt",
        "perfil",
        "búsqueda",
        "extracción",
        "verificación",
    ]
    assert m.peticiones_ia == []
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def test_perfil_listado_recorre_como_mucho_cinco_paginas() -> None:
    sitio = sitio_listado(*([otro(n)] for n in range(1, 9)))
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert MAX_PAGINAS_LISTADO == 5
    assert m.navegador.urls == [pagina_listado(n) for n in range(1, 6)]
    assert ofertas(r) == []
    assert "5 páginas, 0 coincidencias de 5" in paso(r, "búsqueda").detalle
    assert "Sin resultados" in paso(r, "extracción").detalle


def test_perfil_listado_se_para_en_una_pagina_repetida() -> None:
    # La tienda no entiende el número de página y devuelve siempre la primera.
    sitio = sitio_listado([PRODUCTOS[0], OTROS[0]], [PRODUCTOS[0], OTROS[0]], [PRODUCTOS[1]])
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [pagina_listado(1), pagina_listado(2), PRODUCTOS[0].url]
    assert "2 páginas, 1 coincidencias de 2" in paso(r, "búsqueda").detalle


def test_perfil_listado_sin_pagina_solo_lee_la_url_del_listado() -> None:
    url = f"{TIENDA}/categoria/faros"
    sitio = sitio_listado([PRODUCTOS[0], OTROS[0]])
    sitio[url] = sitio.pop(pagina_listado(1))
    m = montar(nuevo_almacen(perfil_listado(url_busqueda=url)), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [url, PRODUCTOS[0].url]
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]
    assert "1 páginas, 1 coincidencias de 2" in paso(r, "búsqueda").detalle


def test_perfil_listado_abre_como_mucho_max_fichas_y_deja_de_paginar() -> None:
    # Con `max_fichas` coincidencias que nombran toda la consulta ya no se piden más páginas.
    sitio = sitio_listado(PRODUCTOS[:2], PRODUCTOS[2:], [OTROS[0]])
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {}, max_fichas=2)
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [pagina_listado(1), PRODUCTOS[0].url, PRODUCTOS[1].url]
    assert len(ofertas(r)) == 2


def test_perfil_listado_prefiere_los_que_nombran_toda_la_consulta() -> None:
    golf = replace(OTROS[0], ruta="/p/retrovisor-golf", titulo="Retrovisor Golf VI")
    sitio = sitio_listado([golf, PRODUCTOS[0]], [])
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {}, max_fichas=1)
    r = m.buscar(modo=Modo.CLASICO)
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]


def test_perfil_listado_encuentra_la_referencia_oem_en_la_ruta() -> None:
    con_ref = replace(PRODUCTOS[0], ruta="/p/pieza-5k1-941-005-e", titulo="Pieza de recambio")
    sitio = sitio_listado([OTROS[0], con_ref], [])
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
    r = m.buscar(texto="5K1941005E", modo=Modo.CLASICO)
    assert m.navegador.urls[-1] == con_ref.url and OTROS[0].url not in m.navegador.urls
    assert [o.url for o in ofertas(r)] == [con_ref.url]


def test_perfil_listado_bloqueado_en_una_pagina_se_para() -> None:
    sitio = sitio_listado([PRODUCTOS[0]], [PRODUCTOS[1]], [PRODUCTOS[2]])
    bloqueo = Bloqueado(pagina_listado(2), "La tienda rechaza el acceso automático (HTTP 403)")
    navegador = NavegadorFalso(sitio, errores={pagina_listado(2): bloqueo})
    m = montar(nuevo_almacen(perfil_listado()), navegador, {})
    r = m.buscar(modo=Modo.AUTO)
    # Ni una petición más: ni la página 3 ni las fichas.
    assert m.navegador.urls == [pagina_listado(1), pagina_listado(2)]
    assert ofertas(r) == []
    errores = [p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR]
    assert len(errores) == 1 and errores[0].detalle.startswith(BLOQUEO)
    assert m.peticiones_ia == []
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.bloqueos == 1


def test_perfil_listado_con_la_primera_pagina_que_no_responde_es_un_error() -> None:
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso({}), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [pagina_listado(1)]
    assert paso(r, "búsqueda").estado is EstadoPaso.ERROR
    assert "404" in paso(r, "búsqueda").detalle


def test_perfil_listado_una_pagina_que_no_existe_termina_el_recorrido() -> None:
    sitio = sitio_listado([PRODUCTOS[0]], [PRODUCTOS[1]])
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls[:3] == [pagina_listado(1), pagina_listado(2), pagina_listado(3)]
    assert len(ofertas(r)) == 2
    assert paso(r, "búsqueda").estado is EstadoPaso.OK
    assert "3 páginas, 2 coincidencias de 2" in paso(r, "búsqueda").detalle


def test_perfil_listado_que_redirige_fuera_de_la_tienda_no_se_usa() -> None:
    sitio = sitio_listado([PRODUCTOS[0]])
    sitio["https://otra-tienda.es/listado"] = sitio[pagina_listado(1)]
    navegador = NavegadorFalso(
        sitio, redirecciones={pagina_listado(1): "https://otra-tienda.es/listado"}
    )
    m = montar(nuevo_almacen(perfil_listado()), navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    assert paso(r, "búsqueda").estado is EstadoPaso.ERROR
    assert m.navegador.urls == [pagina_listado(1)]
    assert ofertas(r) == []


def test_perfil_listado_con_selectores_que_ya_no_encuentran_nada_esta_roto() -> None:
    sitio = sitio_listado(PRODUCTOS)
    m = montar(nuevo_almacen(perfil_listado(sel_resultado="div.resultado")), NavegadorFalso(sitio))
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [pagina_listado(1)]
    error = [p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR]
    assert len(error) == 1 and "perfil roto" in error[0].detalle.lower()
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO


def test_perfil_listado_roto_no_llama_al_reparador() -> None:
    # El reparador solo sabe validar una plantilla de búsqueda con `{consulta}`.
    sitio = sitio_listado(PRODUCTOS)
    almacen = nuevo_almacen(perfil_listado(sel_resultado="div.resultado"))
    m = montar(almacen, NavegadorFalso(sitio), {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)
    assert m.peticiones_ia == []
    assert "perfil roto" in " ".join(p.detalle.lower() for p in progreso(r).pasos)


def test_perfil_listado_con_selector_no_valido_esta_roto() -> None:
    sitio = sitio_listado(PRODUCTOS)
    almacen = nuevo_almacen(perfil_listado(sel_resultado="li.producto:has(a)"))
    m = montar(almacen, NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO
    assert r.grupos == ()


def test_perfil_listado_vacio_sin_productos_no_esta_roto() -> None:
    sitio = {pagina_listado(1): SIN_RESULTADOS}
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert "Sin resultados" in paso(r, "extracción").detalle
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


SITEMAP_PRODUCTOS = f"{TIENDA}/sitemap-productos.xml"


class Reloj:
    """Un reloj que se puede adelantar (el del almacén fecha el catálogo)."""

    def __init__(self, ahora: datetime) -> None:
        self.ahora = ahora

    def __call__(self) -> datetime:
        return self.ahora


def con_sitemaps(xml: Mapping[str, str] | None = None, **opciones: Any) -> NavegadorFalso:
    """El sitio falso, que además sirve los sitemaps de `xml` (URL → XML)."""
    return NavegadorFalso({**paginas_sitio(), **(xml or {})}, **opciones)


def almacen_con_catalogo(
    *urls: str, reloj: Reloj | None = None, parcial: bool = False, guardado: bool = True
) -> Almacen:
    reloj = reloj or Reloj(AHORA)
    almacen = Almacen(":memory:", reloj=reloj)
    almacen.alta_tienda(DOMINIO, "Recambios")
    almacen.guardar_perfil(perfil_sitemap(sel_campos=dict(CAMPOS_OK)))
    if guardado:
        almacen.guardar_catalogo(
            DOMINIO, SITEMAP, [(u, palabras_url(u), None) for u in urls], parcial=parcial
        )
    return almacen


TODAS = [*(p.url for p in PRODUCTOS), *(p.url for p in OTROS)]


def test_perfil_sitemap_busca_en_el_catalogo_y_abre_las_fichas() -> None:
    almacen = almacen_con_catalogo(*TODAS)
    m = montar(almacen, con_sitemaps(), {})
    r = m.buscar(modo=Modo.AUTO)
    # Catálogo al día: no se descarga el sitemap; solo las fichas que nombran «faro» y «golf».
    con_faro_y_golf = [p.url for p in PRODUCTOS[:3]]
    assert sorted(m.navegador.urls) == sorted(con_faro_y_golf)
    assert sorted(o.url for o in ofertas(r)) == sorted(con_faro_y_golf)
    assert paso(r, "búsqueda") == Paso(
        "búsqueda", EstadoPaso.OK, "Catálogo por sitemap (6 URL): 3 coincidencias"
    )
    assert [p.nombre for p in progreso(r).pasos] == [
        "robots.txt",
        "perfil",
        "búsqueda",
        "extracción",
        "verificación",
    ]
    assert m.peticiones_ia == []
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def test_perfil_sitemap_avisa_si_el_catalogo_es_parcial() -> None:
    almacen = almacen_con_catalogo(*TODAS, parcial=True)
    m = montar(almacen, con_sitemaps(), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert paso(r, "búsqueda").detalle == "Catálogo por sitemap (6 URL, parcial): 3 coincidencias"


def test_perfil_sitemap_abre_como_mucho_max_fichas() -> None:
    almacen = almacen_con_catalogo(*TODAS)
    m = montar(almacen, con_sitemaps(), {}, max_fichas=2)
    r = m.buscar(modo=Modo.CLASICO)
    assert len(m.navegador.urls) == 2 and len(ofertas(r)) == 2


def test_perfil_sitemap_sin_coincidencias_no_abre_fichas() -> None:
    almacen = almacen_con_catalogo(*TODAS)
    m = montar(almacen, con_sitemaps(), {})
    r = m.buscar(texto="embrague", modo=Modo.CLASICO)
    assert m.navegador.urls == []
    assert "0 coincidencias" in paso(r, "búsqueda").detalle
    assert "Sin resultados" in paso(r, "extracción").detalle


def test_perfil_sitemap_con_catalogo_viejo_lo_rehace() -> None:
    reloj = Reloj(AHORA - timedelta(days=8))
    almacen = almacen_con_catalogo(PRODUCTOS[3].url, reloj=reloj)
    reloj.ahora = AHORA
    navegador = con_sitemaps({SITEMAP: xml_urlset(TODAS)})
    m = montar(almacen, navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.pedidas[0] == (SITEMAP, False)
    assert m.navegador.grandes == [SITEMAP]
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and (info.total, info.actualizado, info.parcial) == (6, AHORA, False)
    assert len(ofertas(r)) == 3
    assert "Catálogo por sitemap (6 URL): 3 coincidencias" in paso(r, "búsqueda").detalle


def test_perfil_sitemap_con_catalogo_de_siete_dias_no_lo_rehace() -> None:
    reloj = Reloj(AHORA - REFRESCO_CATALOGO)
    almacen = almacen_con_catalogo(*TODAS, reloj=reloj)
    navegador = con_sitemaps({SITEMAP: xml_urlset([])})
    m = montar(almacen, navegador, {})
    m.buscar(modo=Modo.CLASICO)
    assert SITEMAP not in m.navegador.urls


def test_perfil_sitemap_sin_catalogo_lo_hace_desde_el_indice() -> None:
    ajeno = "https://otra-tienda.es/sitemap-productos.xml"
    navegador = con_sitemaps(
        {
            SITEMAP: xml_indice([f"{TIENDA}/sitemap-paginas.xml", SITEMAP_PRODUCTOS, ajeno]),
            SITEMAP_PRODUCTOS: xml_urlset([*TODAS, "https://otra-tienda.es/p/faro-golf"]),
            f"{TIENDA}/sitemap-paginas.xml": xml_urlset([f"{TIENDA}/p/faro-golf-aviso-legal"]),
        },
    )
    m = montar(almacen_con_catalogo(guardado=False), navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    # Solo el sitemap de productos (por el nombre) y nunca uno de otro host.
    assert m.navegador.urls[:2] == [SITEMAP, SITEMAP_PRODUCTOS]
    assert all("otra-tienda" not in u for u in m.navegador.urls)
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and (info.sitemap, info.total) == (SITEMAP, 6)
    assert len(ofertas(r)) == 3


def test_perfil_sitemap_con_url_malas_en_el_sitemap_las_descarta() -> None:
    malas = [
        "https://otra-tienda.es/p/faro-golf",
        f"https://usuario@www.{DOMINIO}/p/faro-golf-x",
        f"{TIENDA}/p/{'faro-golf-' * 300}",
    ]
    navegador = con_sitemaps({SITEMAP: xml_urlset([*malas, *TODAS])})
    m = montar(almacen_con_catalogo(guardado=False), navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.total == 6
    assert len(ofertas(r)) == 3


def test_perfil_sitemap_con_tope_de_ficheros_queda_parcial() -> None:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(1, 15)]
    xml = {SITEMAP: xml_indice(hijos), **{h: xml_urlset([PRODUCTOS[0].url]) for h in hijos}}
    xml[hijos[0]] = xml_urlset(TODAS)
    navegador = con_sitemaps(xml)
    m = montar(almacen_con_catalogo(guardado=False), navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    assert len(m.navegador.grandes) == MAX_FICHEROS_SITEMAP
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.parcial
    assert ", parcial)" in paso(r, "búsqueda").detalle


def test_perfil_sitemap_bloqueado_al_rehacer_el_catalogo_se_para() -> None:
    bloqueo = Bloqueado(SITEMAP, "Desafío de Cloudflare")
    navegador = con_sitemaps(errores={SITEMAP: bloqueo})
    m = montar(almacen_con_catalogo(guardado=False), navegador, {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    assert m.navegador.urls == m.navegador.grandes == [SITEMAP]
    assert m.peticiones_ia == []
    errores = [p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR]
    assert len(errores) == 1 and errores[0].detalle.startswith(BLOQUEO)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.bloqueos == 1


def test_perfil_sitemap_que_no_responde_usa_el_catalogo_viejo_y_avisa() -> None:
    reloj = Reloj(AHORA - timedelta(days=30))
    almacen = almacen_con_catalogo(*TODAS, reloj=reloj)
    reloj.ahora = AHORA
    m = montar(almacen, con_sitemaps(), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls[0] == SITEMAP
    aviso = paso(r, "catálogo")
    assert aviso.estado is EstadoPaso.AVISO and "no responde" in aviso.detalle
    assert len(ofertas(r)) == 3
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.total == 6


def test_perfil_sitemap_sin_catalogo_y_sin_sitemap_es_un_error() -> None:
    m = montar(almacen_con_catalogo(guardado=False), con_sitemaps(), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == m.navegador.grandes == [SITEMAP]
    assert paso(r, "búsqueda").estado is EstadoPaso.ERROR
    assert ofertas(r) == []


def test_perfil_sitemap_que_no_es_un_sitemap_no_borra_el_catalogo() -> None:
    reloj = Reloj(AHORA - timedelta(days=30))
    almacen = almacen_con_catalogo(*TODAS, reloj=reloj)
    reloj.ahora = AHORA
    navegador = con_sitemaps({SITEMAP: "<html><body>Hola</body></html>"})
    m = montar(almacen, navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.total == 6
    assert paso(r, "catálogo").estado is EstadoPaso.AVISO
    assert len(ofertas(r)) == 3


# --- Casos hostiles (cronometrados) ------------------------------------------------------------


def test_listado_hostil_con_miles_de_resultados_y_titulos_enormes_es_lineal() -> None:
    # Cada página (2 MB) trae 1000 tarjetas con títulos de 2000 caracteres y una consulta enorme:
    # se leen como mucho `MAX_RESULTADOS_PAGINA` por página y el filtro es de subcadenas.
    largo = "golf " * 400
    piezas = [
        replace(OTROS[0], ruta=f"/p/pieza-{n}", titulo=f"Retrovisor {n} {largo}")
        for n in range(1000)
    ]
    sitio = {pagina_listado(n): html_listado(piezas) for n in range(1, 6)}
    consulta = " ".join(f"palabra{n}" for n in range(400)) + " faro"
    m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
    inicio = time.perf_counter()
    r = m.buscar(texto=consulta, modo=Modo.CLASICO)
    duracion = time.perf_counter() - inicio
    # La página 2 repite los mismos resultados: se para ahí.
    assert m.navegador.urls == [pagina_listado(1), pagina_listado(2)]
    assert f"coincidencias de {MAX_RESULTADOS_PAGINA}" in paso(r, "búsqueda").detalle
    assert duracion < 10, f"un listado hostil tardó {duracion:.2f} s"


def test_sitemap_con_el_maximo_de_entradas_se_guarda_y_se_busca_rapido() -> None:
    urls = [f"{TIENDA}/p/pieza-recambio-{n}-modelo-{n % 97}" for n in range(MAX_ENTRADAS_SITEMAP)]
    urls[-1] = PRODUCTOS[0].url
    navegador = con_sitemaps({SITEMAP: xml_urlset(urls)})
    m = montar(almacen_con_catalogo(guardado=False), navegador, {})
    inicio = time.perf_counter()
    r = m.buscar(modo=Modo.CLASICO)
    duracion = time.perf_counter() - inicio
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.total == MAX_ENTRADAS_SITEMAP
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]
    assert duracion < 20, f"rehacer un catálogo de 50 000 URL tardó {duracion:.2f} s"


def test_indice_hostil_con_miles_de_hijos_solo_descarga_el_tope() -> None:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(MAX_ENTRADAS_SITEMAP)]
    xml = {SITEMAP: xml_indice(hijos), hijos[0]: xml_urlset(TODAS)}
    navegador = con_sitemaps(xml)
    m = montar(almacen_con_catalogo(guardado=False), navegador, {})
    inicio = time.perf_counter()
    m.buscar(modo=Modo.CLASICO)
    duracion = time.perf_counter() - inicio
    assert len(m.navegador.grandes) == MAX_FICHEROS_SITEMAP
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.parcial and info.total == len(TODAS)
    assert duracion < 10, f"un índice de 50 000 hijos tardó {duracion:.2f} s"


def test_sitemap_con_entidades_o_dtd_no_borra_el_catalogo() -> None:
    bomba = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{TIENDA}/p/&lol2;'
        "</loc></url></urlset>"
    )
    reloj = Reloj(AHORA - timedelta(days=30))
    almacen = almacen_con_catalogo(*TODAS, reloj=reloj)
    reloj.ahora = AHORA
    m = montar(almacen, con_sitemaps({SITEMAP: bomba}), {})
    r = m.buscar(modo=Modo.CLASICO)
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.total == len(TODAS)
    assert paso(r, "catálogo").estado is EstadoPaso.AVISO


def test_al_llenar_el_catalogo_no_se_descargan_mas_sitemaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Con el tope del almacén ya alcanzado, otro sitemap solo serían peticiones de más.
    monkeypatch.setattr(catalogo, "MAX_URLS_CATALOGO", 4)
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(1, 4)]
    xml = {SITEMAP: xml_indice(hijos), **{h: xml_urlset(TODAS) for h in hijos}}
    m = montar(almacen_con_catalogo(guardado=False), con_sitemaps(xml), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.grandes == [SITEMAP, hijos[0]]
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and (info.total, info.parcial) == (4, True)
    assert ", parcial)" in paso(r, "búsqueda").detalle


# --- Configurar una tienda sin buscador (nota 16) ----------------------------------------------


@dataclass
class ConfiguradorEspia:
    """Sustituye a `perfiles.configurador.configurar`: apunta con qué se le llama y devuelve
    `perfil`, guardando antes el catálogo si se le da (como hace el de verdad con `sitemap`)."""

    perfil: Perfil
    entradas: tuple[str, ...] = ()
    llamadas: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, dominio: str, *args: Any, **opciones: Any) -> Perfil:
        self.llamadas.append(opciones)
        catalogo = opciones.get("catalogo")
        if catalogo is not None and self.entradas:
            catalogo.guardar_catalogo(
                dominio,
                self.perfil.url_busqueda,
                [(u, palabras_url(u), None) for u in self.entradas],
                parcial=False,
            )
        return self.perfil


def test_configurar_recibe_la_entrada_de_la_tienda_y_el_almacen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    espia = ConfiguradorEspia(perfil_listado(revisado=False, autor="🤖 configurador"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    almacen = nuevo_almacen()
    almacen.cambiar_entrada(DOMINIO, LISTADO)
    sitio = sitio_listado([PRODUCTOS[0]], [])
    m = montar(almacen, NavegadorFalso(sitio), {})
    m.buscar(modo=Modo.AUTO)
    (opciones,) = espia.llamadas
    assert opciones["entrada"] == LISTADO
    assert opciones["catalogo"] is almacen


def test_configurar_sin_entrada_pasa_none(monkeypatch: pytest.MonkeyPatch) -> None:
    espia = ConfiguradorEspia(perfil_listado(revisado=False, autor="🤖 configurador"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(nuevo_almacen(), NavegadorFalso(sitio_listado([PRODUCTOS[0]], [])), {})
    m.buscar(modo=Modo.AUTO)
    assert espia.llamadas[0]["entrada"] is None


def test_perfil_sitemap_nuevo_sin_ia_lo_dice_y_busca_en_su_catalogo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nuevo = perfil_sitemap(revisado=False, autor="🤖 configurador", sel_campos=dict(CAMPOS_OK))
    espia = ConfiguradorEspia(nuevo, entradas=tuple(TODAS))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(nuevo_almacen(), con_sitemaps(), {})
    r = m.buscar(modo=Modo.AUTO)
    configuracion = paso(r, "perfil")
    assert configuracion.estado is EstadoPaso.AVISO
    assert "sitemap" in configuracion.detalle and "sin IA" in configuracion.detalle
    assert "pendiente de revisión" in configuracion.detalle
    # El catálogo recién guardado vale: no se vuelve a descargar el sitemap.
    assert m.navegador.grandes == []
    assert len(ofertas(r)) == 3
    assert "Catálogo por sitemap (6 URL): 3 coincidencias" in paso(r, "búsqueda").detalle
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.PENDIENTE_REVISION


def test_perfil_listado_nuevo_con_ia_lo_dice(monkeypatch: pytest.MonkeyPatch) -> None:
    class ConIA(ConfiguradorEspia):
        async def __call__(self, dominio: str, *args: Any, **opciones: Any) -> Perfil:
            sesion = args[1]
            await sesion.pedir(
                PeticionIA(
                    componente="configurador",
                    instrucciones="x",
                    datos="y",
                    esquema={"type": "object"},
                    max_tokens=16,
                )
            )
            return await super().__call__(dominio, *args, **opciones)

    espia = ConIA(perfil_listado(revisado=False, autor="🤖 configurador"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(
        nuevo_almacen(), NavegadorFalso(sitio_listado([PRODUCTOS[0]], [])), {"configurador": [{}]}
    )
    r = m.buscar(modo=Modo.AUTO)
    configuracion = paso(r, "perfil")
    assert configuracion.estado is EstadoPaso.IA
    assert "listado" in configuracion.detalle and "pendiente de revisión" in configuracion.detalle
