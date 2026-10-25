"""Orquestador: el flujo de una búsqueda (nota 14) con los modos de ADR-002, sin red.

La tienda es la de `tests.perfiles.sitio` (portada, buscador, listado y fichas falsas) y la IA,
el `ProveedorSimulado` detrás de un `ServicioIA` de verdad con el almacén como registro.
"""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen, ErrorAlmacen
from precia.fuentes.sitemap import MAX_ENTRADAS_SITEMAP, MAX_FICHEROS_SITEMAP, palabras_url
from precia.ia.servicio import ServicioIA
from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import ErrorIA, IANoDisponible, PeticionIA, PresupuestoAgotado
from precia.navegador.tipos import Bloqueado, ErrorDescarga, Pagina, URLNoPermitida
from precia.nucleo.modelos import (
    Coincidencia,
    ConsultaBusqueda,
    EstadoPaso,
    EstadoPerfil,
    EstadoTienda,
    FalloConfiguracion,
    Modo,
    OrigenDato,
    Paso,
    ProgresoTienda,
    ResultadoBusqueda,
)
from precia.nucleo.orquestador import (
    BLOQUEO,
    CONSULTAS_CONFIGURACION,
    EN_ESPERA,
    ESTRUCTURADOS,
    MAX_PAGINAS_LISTADO,
    MAX_RESULTADOS_PAGINA,
    REFRESCO_CATALOGO,
    RESPALDO_OEM,
    RESPALDO_TEXTO,
    Orquestador,
    aviso_en_espera,
    consulta_de_configuracion,
    espera_configuracion,
)
from precia.perfiles import catalogo
from precia.perfiles.tipos import (
    TIPO_API_SHOPIFY,
    TIPO_API_WOOCOMMERCE,
    ConfiguracionFallida,
    Perfil,
)
from tests.cronometro import segundos_de_cpu
from tests.navegador.falsos import validar_con_dns_publico
from tests.nucleo.falsos import SeguimientoFalso
from tests.perfiles.plataformas import (
    SHOPIFY,
    SHOPIFY_API,
    SHOPIFY_PORTADA,
    WOO,
    WOO_API,
    WOO_PLANTILLA,
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
    PLANTILLA,
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
    # Los tests de configuración usan la consulta de siempre como consulta de prueba (A1).
    opciones.setdefault("consultas_configuracion", (CONSULTA,))
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
    assert nuevo.autor == "configurador IA"
    pasos = progreso(r).pasos
    assert pasos[0] == Paso("robots.txt", EstadoPaso.OK, "Permite la búsqueda")
    assert paso(r, "perfil").estado is EstadoPaso.IA
    assert "pendiente de revisión" in paso(r, "perfil").detalle
    assert len(ofertas(r)) == len(PRODUCTOS)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.PENDIENTE_REVISION


def test_auto_con_perfil_sin_revisar_lo_usa_y_avisa() -> None:
    sin_revisar = perfil(autor="configurador IA", revisado=False)
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
    assert (reparado.version, reparado.revisado, reparado.autor) == (2, False, "reparador IA")
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
    # A3: un valor no anclado no cuenta como aporte, así que tras la 1.ª ficha el extractor ya
    # no se llama en esta tienda (antes, 4 llamadas y «4 valores»).
    assert "1 valores" in verificacion.detalle
    assert m.peticiones_ia == ["extractor"]


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
    assert all(g.criterio != "Emparejado por IA" for g in r.grupos)


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
    assert all(d.motivo.startswith("IA · ") for d in r.descartados)
    assert {g.criterio for g in r.grupos} == {"Emparejado por IA"}


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


def test_robots_ignorado_se_avisa_en_el_progreso() -> None:
    # ADR-017 (y su enmienda del 2026-10-25): se visita siempre aunque el robots.txt lo prohíba,
    # pero el usuario lo ve en el progreso.
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
    # Sin `sel_precio_listado`: el listado no trae precios que aprovechar (R3-H).
    bloqueo = Bloqueado(PRODUCTOS[1].url, "hCaptcha")
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[1].url: bloqueo})
    m = montar(nuevo_almacen(perfil(sel_precio_listado=None)), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]
    assert navegador.urls == [BUSQUEDA, PRODUCTOS[0].url, PRODUCTOS[1].url]
    assert paso(r, "extracción").estado is EstadoPaso.ERROR
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.bloqueos == 1


@pytest.mark.parametrize(
    "error", [ErrorDescarga("No se pudo conectar con la tienda."), URLNoPermitida("No.")]
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


def _sitio_doble(**opciones: Any) -> dict[str, str]:
    """La misma tienda falsa, servida también en otro dominio."""
    sitio = paginas_sitio(**opciones)
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


# --- A3: el extractor de respaldo se apaga si no aporta en la 1.ª ficha -----------------------

SIN_ESTADO = perfil(sel_campos={k: v for k, v in CAMPOS_OK.items() if k != "estado"})
"""Un perfil que no lee «estado»: lo tiene que buscar el extractor de respaldo."""


def test_auto_deja_de_llamar_al_extractor_si_la_primera_ficha_no_aporta() -> None:
    # Lo que pasa en Oscaro: la IA solo busca «estado» y no lo encuentra en ninguna ficha.
    guion = {"extractor": [{"estado": None} for _ in PRODUCTOS]}
    m = montar(nuevo_almacen(SIN_ESTADO), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert m.peticiones_ia == ["extractor"]
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert "no aportó nada en la 1.ª ficha" in paso(r, "extracción").detalle
    assert r.llamadas_por_componente == {"extractor": 1}


def test_auto_sigue_con_el_extractor_si_la_primera_ficha_aporta() -> None:
    guion = {"extractor": [{"estado": p.estado} for p in PRODUCTOS]}
    m = montar(nuevo_almacen(SIN_ESTADO), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert m.peticiones_ia == ["extractor"] * len(PRODUCTOS)
    assert paso(r, "extracción").estado is EstadoPaso.IA
    assert "no aportó" not in paso(r, "extracción").detalle
    assert all(o.campos["estado"].origen is OrigenDato.IA for o in ofertas(r))


def test_un_valor_no_anclado_no_cuenta_como_aporte() -> None:
    # Inventado o una inyección: no está en la página, así que no demuestra que la IA sirva.
    guion = {"extractor": [{"estado": "Seminuevo de exposición"} for _ in PRODUCTOS]}
    m = montar(nuevo_almacen(SIN_ESTADO), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert m.peticiones_ia == ["extractor"]
    assert "no aportó nada en la 1.ª ficha" in paso(r, "extracción").detalle


def test_el_corte_dura_toda_la_busqueda_de_la_tienda() -> None:
    # Los datos estructurados enlazan fichas sin precio visible: no dan ofertas válidas, el
    # perfil se repara y las fichas se vuelven a leer (segunda pasada por las fichas). El
    # extractor, apagado en la primera, no se vuelve a llamar en la segunda.
    retirados = [replace(p, ruta=p.ruta + "-retirado") for p in PRODUCTOS]
    sitio = paginas_sitio(jsonld=())
    sitio[BUSQUEDA] = html_listado().replace("</body>", next_data(retirados) + "</body>")
    for producto in retirados:
        sitio[producto.url] = html_ficha(producto, precio_visible=False)
    roto = replace(ROTO, sel_campos=SIN_ESTADO.sel_campos)
    propuesta = {**LISTADO_OK, "sel_campos": {**SIN_ESTADO.sel_campos, "estado": None}}
    guion: dict[str, list[Guion]] = {
        "extractor": [{"precio": None, "estado": None} for _ in PRODUCTOS],
        "reparador": [propuesta],
    }
    m = montar(nuevo_almacen(roto), NavegadorFalso(sitio), guion)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert m.peticiones_ia == ["extractor", "reparador"]
    assert len([o for o in ofertas(r) if o.precio_anclado]) == len(PRODUCTOS)


class ProveedorPorFicha(ProveedorSimulado):
    """Contesta al extractor con el estado de la ficha si la página lo dice, y si no, `None`."""

    async def completar(self, peticion: PeticionIA) -> Any:
        self.peticiones.append(peticion)
        estado = next(
            (
                p.estado
                for p in PRODUCTOS
                if p.titulo in peticion.datos and "Estado" in peticion.datos
            ),
            None,
        )
        return await ProveedorSimulado({"extractor": [{"estado": estado}]}).completar(peticion)


def test_el_corte_es_por_tienda() -> None:
    # Las dos tiendas van en paralelo con la misma sesión de IA: que el extractor no sirva en
    # una no lo apaga en la otra.
    sitio = _sitio_doble(jsonld=())
    for p in PRODUCTOS:
        otra = p.url.replace(DOMINIO, OTRA)
        sitio[otra] = sitio[otra].replace(
            f'<tr><th>Estado</th><td class="estado">{p.estado}</td></tr>', ""
        )
    otro_perfil = replace(
        SIN_ESTADO,
        dominio=OTRA,
        url_busqueda=f"https://www.{OTRA}/buscar?orden=relevancia&q={{consulta}}",
    )
    almacen = nuevo_almacen(SIN_ESTADO, otro_perfil, tiendas=(DOMINIO, OTRA))
    m = montar(almacen, NavegadorFalso(sitio), {})
    proveedor = ProveedorPorFicha({})
    m.orquestador._servicio.proveedor = proveedor  # type: ignore[union-attr]
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert len(proveedor.peticiones) == len(PRODUCTOS) + 1
    assert "no aportó" not in paso(r, "extracción").detalle
    assert "no aportó nada en la 1.ª ficha" in paso(r, "extracción", OTRA).detalle
    de_la_tienda = [o for o in ofertas(r) if o.tienda == DOMINIO]
    assert all(o.campos["estado"].origen is OrigenDato.IA for o in de_la_tienda)


# --- A3: caché del extractor de respaldo -------------------------------------------------------


def test_la_segunda_busqueda_saca_el_extractor_de_la_cache() -> None:
    guion = {"extractor": [{"estado": p.estado} for p in PRODUCTOS]}
    m = montar(
        nuevo_almacen(SIN_ESTADO),
        NavegadorFalso(paginas_sitio(jsonld=())),
        guion,
        ttl_cache_ia=3600.0,
    )
    m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert m.peticiones_ia == ["extractor"] * len(PRODUCTOS)
    assert r.llamadas_ia == 0 and r.llamadas_por_componente == {}
    assert r.ia_desde_cache == len(PRODUCTOS)
    assert "4 desde la caché" in paso(r, "extracción").detalle
    # Lo que viene de la caché se vuelve a anclar en la página.
    assert all(o.campos["estado"].anclado for o in ofertas(r))
    assert all(o.campos["estado"].origen is OrigenDato.IA for o in ofertas(r))


def test_sin_ttl_no_hay_cache_del_extractor() -> None:
    guion = {"extractor": [{"estado": p.estado} for p in PRODUCTOS] * 2}
    m = montar(nuevo_almacen(SIN_ESTADO), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert m.peticiones_ia == ["extractor"] * 2 * len(PRODUCTOS)
    assert r.ia_desde_cache == 0


def test_un_error_del_almacen_en_la_cache_no_para_la_busqueda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def falla(*_: Any, **__: Any) -> Any:
        raise ErrorAlmacen("disco lleno")

    almacen = nuevo_almacen(SIN_ESTADO)
    for metodo in ("leer_extraccion_ia", "guardar_extraccion_ia", "borrar_extracciones_ia"):
        monkeypatch.setattr(almacen, metodo, falla)
    guion = {"extractor": [{"estado": p.estado} for p in PRODUCTOS]}
    m = montar(almacen, NavegadorFalso(paginas_sitio(jsonld=())), guion, ttl_cache_ia=3600.0)
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert m.peticiones_ia == ["extractor"] * len(PRODUCTOS)


def test_clasico_no_usa_la_cache_del_extractor() -> None:
    guion = {"extractor": [{"estado": p.estado} for p in PRODUCTOS]}
    m = montar(
        nuevo_almacen(SIN_ESTADO),
        NavegadorFalso(paginas_sitio(jsonld=())),
        guion,
        ttl_cache_ia=3600.0,
    )
    m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    r = m.buscar(modo=Modo.CLASICO, campos=("precio", "estado"))
    assert r.ia_desde_cache == 0
    assert not any(v.origen is OrigenDato.IA for o in ofertas(r) for v in o.campos.values())


def test_los_campos_por_defecto_siguen_pidiendo_estado_y_marca() -> None:
    # Decisión de Julio en A3: «estado» y «marca» siguen en los campos por defecto aunque
    # muchas tiendas solo los den con la IA; el corte de A3 limita lo que cuestan.
    assert ConsultaBusqueda("x").campos == ("precio", "referencia_oem", "marca", "estado")


@pytest.mark.parametrize(
    ("caso", "esperados"),
    [
        ("aporta", ["extractor de respaldo con IA en 4"]),
        (
            "inutil",
            ["extractor de respaldo con IA en 1 (aportó en 0)", "no aportó nada en la 1.ª ficha"],
        ),
        ("cache", ["extractor de respaldo con IA en 4, 4 desde la caché"]),
    ],
)
def test_el_paso_de_extraccion_dice_para_que_se_uso_la_ia(caso: str, esperados: list[str]) -> None:
    respuestas = [{"estado": None if caso == "inutil" else p.estado} for p in PRODUCTOS]
    m = montar(
        nuevo_almacen(SIN_ESTADO),
        NavegadorFalso(paginas_sitio(jsonld=())),
        {"extractor": respuestas},
        ttl_cache_ia=3600.0 if caso == "cache" else 0.0,
    )
    r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    if caso == "cache":
        r = m.buscar(modo=Modo.AUTO, campos=("precio", "estado"))
    extraccion = paso(r, "extracción")
    assert extraccion.estado is EstadoPaso.IA
    for esperado in esperados:
        assert esperado in extraccion.detalle
    if caso != "inutil":
        assert "aportó" not in extraccion.detalle
    if caso == "aporta":
        assert "caché" not in extraccion.detalle


# --- A3: al histórico, con la fecha en que se descargó la ficha ---------------------------------


class NavegadorDeCache(NavegadorFalso):
    """Sirve las fichas como si salieran de la caché de páginas, descargadas en `obtenida`."""

    def __init__(self, paginas: dict[str, str], obtenida: datetime) -> None:
        super().__init__(paginas)
        self.obtenida = obtenida

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        pagina = await super().obtener(url, js=js, grande=grande)
        if "/p/" not in url:
            return pagina
        return replace(pagina, obtenida=self.obtenida, desde_cache=True)


def _dias_del_historico(m: Montaje, r: ResultadoBusqueda) -> set[Any]:
    return {p.fecha for g in r.grupos for s in m.almacen.historico(g.id) for p in s.puntos}


def test_una_oferta_servida_de_la_cache_va_al_historico_con_su_fecha_de_obtencion() -> None:
    # Descargada ayer a las 23:00 (UTC, como el reloj): el histórico la apunta ayer, no hoy.
    ayer = AHORA - timedelta(hours=13)
    m = montar(nuevo_almacen(perfil()), NavegadorDeCache(paginas_sitio(), ayer))
    r = m.buscar(modo=Modo.CLASICO)
    assert _dias_del_historico(m, r) == {ayer.date()}
    assert all(o.obtenida == ayer for o in ofertas(r))


def test_la_fecha_de_obtencion_se_pasa_a_la_zona_del_reloj() -> None:
    # 23:30 UTC del 18 son las 01:30 del 19 en Madrid (UTC+2): el día es el 19 en la hora local.
    madrid = timezone(timedelta(hours=2))
    obtenida = datetime(2026, 10, 18, 23, 30, tzinfo=UTC)
    m = montar(nuevo_almacen(perfil()), NavegadorDeCache(paginas_sitio(), obtenida))
    m.orquestador._reloj = lambda: datetime(2026, 10, 19, 3, 0, tzinfo=madrid)
    r = m.buscar(modo=Modo.CLASICO)
    assert _dias_del_historico(m, r) == {date(2026, 10, 19)}


def test_una_fecha_de_obtencion_futura_no_se_usa() -> None:
    # Un reloj del descargador adelantado no puede apuntar precios en el futuro.
    m = montar(
        nuevo_almacen(perfil()), NavegadorDeCache(paginas_sitio(), AHORA + timedelta(days=2))
    )
    r = m.buscar(modo=Modo.CLASICO)
    assert _dias_del_historico(m, r) == {AHORA.date()}


# --- Perfiles api_* y datos estructurados (nota 15 §D) --------------------------------------------


def test_perfil_api_busca_en_la_api_sin_abrir_fichas() -> None:
    # Una consulta que nombran los tres productos de la respuesta (A2 deja fuera los demás).
    texto = "faro pastillas tapacubos"
    api = WOO_PLANTILLA.replace("{consulta}", texto.replace(" ", "+"))
    paginas = paginas_woo()
    paginas[api] = paginas.pop(WOO_API)
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas), {})
    r = m.buscar(texto=texto, modo=Modo.AUTO, campos=("precio", "referencia_oem"))
    # Una sola petición, a la API, sin JavaScript ni fichas; y sin IA.
    assert m.navegador.pedidas == [(api, False)]
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
    # Solo el faro: las pastillas y el tapacubos no nombran «faro golf» (A2, bug 2).
    assert len(ofertas(r)) == 1
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
    *urls: str,
    reloj: Reloj | None = None,
    parcial: bool = False,
    guardado: bool = True,
    pendientes: Iterable[str] = (),
) -> Almacen:
    reloj = reloj or Reloj(AHORA)
    almacen = Almacen(":memory:", reloj=reloj)
    almacen.alta_tienda(DOMINIO, "Recambios")
    almacen.guardar_perfil(perfil_sitemap(sel_campos=dict(CAMPOS_OK)))
    if guardado:
        almacen.guardar_catalogo(
            DOMINIO,
            SITEMAP,
            [(u, palabras_url(u), None) for u in urls],
            parcial=parcial,
            pendientes=pendientes,
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


def test_perfil_sitemap_deja_las_paginas_de_marca_para_el_final() -> None:
    # R2-C14a: en el catálogo de MediaMarkt, las «/es/brand/…» iban antes que las fichas y se
    # abrían en su lugar. La de marca nombra igual de bien la consulta, pero no es un producto.
    marca = f"{TIENDA}/marca/valeo/faro-golf"
    almacen = almacen_con_catalogo(marca, *TODAS)
    m = montar(almacen, con_sitemaps(), {}, max_fichas=3)
    m.buscar(modo=Modo.CLASICO)
    assert marca not in m.navegador.urls
    assert sorted(m.navegador.urls) == sorted(p.url for p in PRODUCTOS[:3])


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


def test_perfil_sitemap_rehace_el_catalogo_con_su_prefijo_de_idioma() -> None:
    # R5-C: al rehacer, solo se conocía el sitemap y el prefijo de la portada (/es/es/ en
    # Decántalo) se perdía: entraban las URL de todos los países. Ahora va guardado con el
    # catálogo y se aplica igual que al configurar.
    reloj = Reloj(AHORA - timedelta(days=8))
    almacen = almacen_con_catalogo(guardado=False, reloj=reloj)
    espanolas = [f"{TIENDA}/es/es{p.ruta}" for p in PRODUCTOS]
    almacen.guardar_catalogo(
        DOMINIO,
        SITEMAP,
        [(espanolas[0], palabras_url(espanolas[0]), None)],
        parcial=False,
        idioma=("es", "es"),
    )
    reloj.ahora = AHORA
    otras = [f"{TIENDA}/au/en/p/front-headlamp-golf-vi", f"{TIENDA}/es/ca/p/far-golf-vi"]
    navegador = con_sitemaps({SITEMAP: xml_urlset([*otras, *espanolas])})
    m = montar(almacen, navegador, {})
    m.buscar(modo=Modo.CLASICO)
    assert m.navegador.grandes == [SITEMAP]
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None
    assert (info.actualizado, info.total, info.idioma) == (AHORA, len(espanolas), ("es", "es"))


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


HIJO_FAROS = f"{TIENDA}/sitemaps/sitemap-faros-es.xml"
HIJO_FRENOS = f"{TIENDA}/sitemaps/sitemap-frenos-es.xml"
CON_FARO = [p.url for p in PRODUCTOS]
SIN_FARO = [p.url for p in OTROS]


def test_perfil_sitemap_lee_el_hijo_pendiente_que_nombra_la_consulta() -> None:
    # R2-C14b: en PcComponentes, `sitemap-portatiles-es.xml` no cabía en el tope de ficheros y
    # «portátil 16gb» solo encontraba módulos de RAM. El hijo que nombra la consulta se descarga
    # en la búsqueda, sus URL se añaden al catálogo y no se vuelve a descargar.
    almacen = almacen_con_catalogo(*SIN_FARO, parcial=True, pendientes=[HIJO_FRENOS, HIJO_FAROS])
    navegador = con_sitemaps({HIJO_FAROS: xml_urlset(CON_FARO), HIJO_FRENOS: xml_urlset([])})
    m = montar(almacen, navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.grandes == [HIJO_FAROS]
    assert m.navegador.pedidas[0] == (HIJO_FAROS, False)
    assert sorted(o.url for o in ofertas(r)) == sorted(CON_FARO[:3])
    leido = paso(r, "catálogo")
    assert leido.estado is EstadoPaso.OK
    assert leido.detalle == "1 sitemap más del índice que nombra la consulta: 4 URL nuevas"
    assert "Catálogo por sitemap (6 URL, parcial): 3 coincidencias" in paso(r, "búsqueda").detalle
    assert m.almacen.hijos_pendientes(DOMINIO) == [HIJO_FRENOS]
    m.navegador.grandes.clear()
    m.buscar(modo=Modo.CLASICO)
    assert m.navegador.grandes == []


def test_perfil_sitemap_sin_hijos_que_nombren_la_consulta_no_descarga_ninguno() -> None:
    almacen = almacen_con_catalogo(*TODAS, parcial=True, pendientes=[HIJO_FRENOS])
    m = montar(almacen, con_sitemaps(), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.grandes == []
    assert [p.nombre for p in progreso(r).pasos].count("catálogo") == 0
    assert m.almacen.hijos_pendientes(DOMINIO) == [HIJO_FRENOS]


def test_perfil_sitemap_parcial_sin_coincidencias_avisa() -> None:
    # R2-C14b (3): en Casa del Libro el índice no publica los libros impresos. Si el catálogo es
    # parcial y nada casa, puede que la tienda sí lo tenga: se dice.
    almacen = almacen_con_catalogo(*TODAS, parcial=True, pendientes=[HIJO_FRENOS])
    m = montar(almacen, con_sitemaps(), {})
    r = m.buscar(texto="embrague", modo=Modo.CLASICO)
    aviso = paso(r, "catálogo")
    assert aviso.estado is EstadoPaso.AVISO
    assert "parcial" in aviso.detalle and "no se han leído" in aviso.detalle
    assert m.navegador.urls == []


def test_perfil_sitemap_completo_sin_coincidencias_no_avisa() -> None:
    m = montar(almacen_con_catalogo(*TODAS), con_sitemaps(), {})
    r = m.buscar(texto="embrague", modo=Modo.CLASICO)
    assert all(p.nombre != "catálogo" for p in progreso(r).pasos)


def test_perfil_sitemap_hijo_pendiente_bloqueado_se_para() -> None:
    almacen = almacen_con_catalogo(*SIN_FARO, parcial=True, pendientes=[HIJO_FAROS])
    navegador = con_sitemaps(errores={HIJO_FAROS: Bloqueado(HIJO_FAROS, "Desafío de Cloudflare")})
    m = montar(almacen, navegador, {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [HIJO_FAROS]
    errores = [p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR]
    assert len(errores) == 1 and errores[0].detalle.startswith(BLOQUEO)
    assert m.almacen.hijos_pendientes(DOMINIO) == [HIJO_FAROS]


def test_perfil_sitemap_hijo_pendiente_que_no_responde_no_se_repite() -> None:
    almacen = almacen_con_catalogo(*SIN_FARO, parcial=True, pendientes=[HIJO_FAROS])
    m = montar(almacen, con_sitemaps(), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.grandes == [HIJO_FAROS]
    assert paso(r, "catálogo").detalle.endswith(": 0 URL nuevas")
    assert m.almacen.hijos_pendientes(DOMINIO) == []


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
    hechas: list[tuple[Montaje, Any]] = []

    def buscar() -> None:
        # Un montaje nuevo en cada intento: repetir la búsqueda saldría de la caché.
        m = montar(nuevo_almacen(perfil_listado()), NavegadorFalso(sitio), {})
        hechas.append((m, m.buscar(texto=consulta, modo=Modo.CLASICO)))

    cpu = segundos_de_cpu(buscar, 10)
    m, r = hechas[-1]
    # La página 2 repite los mismos resultados: se para ahí.
    assert m.navegador.urls == [pagina_listado(1), pagina_listado(2)]
    assert f"coincidencias de {MAX_RESULTADOS_PAGINA}" in paso(r, "búsqueda").detalle
    assert cpu < 10, f"un listado hostil costó {cpu:.2f} s de CPU"


def test_sitemap_con_el_maximo_de_entradas_se_guarda_y_se_busca_rapido() -> None:
    urls = [f"{TIENDA}/p/pieza-recambio-{n}-modelo-{n % 97}" for n in range(MAX_ENTRADAS_SITEMAP)]
    urls[-1] = PRODUCTOS[0].url
    hechas: list[tuple[Montaje, Any]] = []

    def buscar() -> None:
        # Un montaje nuevo en cada intento: repetir la búsqueda saldría de la caché.
        m = montar(almacen_con_catalogo(guardado=False), con_sitemaps({SITEMAP: xml}), {})
        hechas.append((m, m.buscar(modo=Modo.CLASICO)))

    xml = xml_urlset(urls)
    cpu = segundos_de_cpu(buscar, 20)
    m, r = hechas[-1]
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.total == MAX_ENTRADAS_SITEMAP
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]
    assert cpu < 20, f"rehacer un catálogo de 50 000 URL costó {cpu:.2f} s de CPU"


def test_indice_hostil_con_miles_de_hijos_solo_descarga_el_tope() -> None:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(MAX_ENTRADAS_SITEMAP)]
    xml = {SITEMAP: xml_indice(hijos), hijos[0]: xml_urlset(TODAS)}
    montajes: list[Montaje] = []

    def buscar() -> None:
        # Un montaje nuevo en cada intento: repetir la búsqueda saldría de la caché.
        montajes.append(montar(almacen_con_catalogo(guardado=False), con_sitemaps(xml), {}))
        montajes[-1].buscar(modo=Modo.CLASICO)

    cpu = segundos_de_cpu(buscar, 10)
    m = montajes[-1]
    assert len(m.navegador.grandes) == MAX_FICHEROS_SITEMAP
    info = m.almacen.catalogo(DOMINIO)
    assert info is not None and info.parcial and info.total == len(TODAS)
    assert cpu < 10, f"un índice de 50 000 hijos costó {cpu:.2f} s de CPU"


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
    argumentos: list[tuple[Any, ...]] = field(default_factory=list)
    """Los posicionales de cada llamada: (dominio, navegador, sesión, consulta de prueba)."""
    error: Exception | None = None
    """Lo que lanza en vez de devolver el perfil."""

    async def __call__(self, dominio: str, *args: Any, **opciones: Any) -> Perfil:
        self.llamadas.append(opciones)
        self.argumentos.append((dominio, *args))
        if self.error is not None:
            raise self.error
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
    espia = ConfiguradorEspia(perfil_listado(revisado=False, autor="configurador IA"))
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
    espia = ConfiguradorEspia(perfil_listado(revisado=False, autor="configurador IA"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(nuevo_almacen(), NavegadorFalso(sitio_listado([PRODUCTOS[0]], [])), {})
    m.buscar(modo=Modo.AUTO)
    assert espia.llamadas[0]["entrada"] is None


def test_perfil_sitemap_nuevo_sin_ia_lo_dice_y_busca_en_su_catalogo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nuevo = perfil_sitemap(revisado=False, autor="configurador IA", sel_campos=dict(CAMPOS_OK))
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

    espia = ConIA(perfil_listado(revisado=False, autor="configurador IA"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(
        nuevo_almacen(), NavegadorFalso(sitio_listado([PRODUCTOS[0]], [])), {"configurador": [{}]}
    )
    r = m.buscar(modo=Modo.AUTO)
    configuracion = paso(r, "perfil")
    assert configuracion.estado is EstadoPaso.IA
    assert "listado" in configuracion.detalle and "pendiente de revisión" in configuracion.detalle


# --- Emparejado por OEM con equivalentes (B5) ----------------------------------------------------

OSCARO_VALEO = (
    Path(__file__).parents[1] / "fixtures" / "extraccion" / "oscaro_ficha_valeo_301635.html"
)


def test_ficha_que_cita_la_oem_buscada_entra_como_equivalente() -> None:
    # La ficha de Oscaro solo cita 1K0698151 en su pestaña oculta de referencias OEM.
    paginas = paginas_sitio(busqueda=PLANTILLA.replace("{consulta}", "1K0698151"))
    paginas[PRODUCTOS[0].url] = OSCARO_VALEO.read_text(encoding="utf-8")
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas), {})
    r = m.buscar(texto="1K0698151", modo=Modo.CLASICO)
    (grupo,) = r.grupos
    (oferta,) = grupo.ofertas
    assert grupo.id == "oem-1k0698151"
    assert (oferta.url, oferta.precio, oferta.coincidencia) == (
        PRODUCTOS[0].url,
        Decimal("31.52"),
        Coincidencia.EQUIVALENTE,
    )
    # Las demás fichas (5K1941006E…) se descartan, con su enlace y su precio.
    assert r.descartados
    assert all(d.url and d.precio is not None for d in r.descartados)
    assert m.peticiones_ia == []
    # Y al histórico va con su coincidencia.
    guardado = m.almacen.producto("oem-1k0698151")
    assert guardado is not None
    assert guardado.ofertas[0].coincidencia is Coincidencia.EQUIVALENTE


def test_oscaro_real_solo_pide_estado_y_corta_tras_la_primera_ficha() -> None:
    # A3 con las fichas reales de Oscaro que guardó B5: JSON-LD da precio, marca y referencia, y
    # a la IA solo le falta «estado», que Oscaro no publica. Antes, una llamada por ficha en cada
    # búsqueda; ahora una en la primera y ninguna en la repetida.
    fichas = sorted((Path(__file__).parents[1] / "fixtures" / "extraccion").glob("oscaro_*"))
    paginas = paginas_sitio(busqueda=busqueda_de("1K0698151"))
    for producto, ficha in zip(PRODUCTOS, fichas, strict=False):
        paginas[producto.url] = ficha.read_text(encoding="utf-8")
    # Respuestas de sobra: sin A3 se gastarían todas.
    guion = {"extractor": [{"estado": None}] * 2 * len(PRODUCTOS)}
    m = montar(
        nuevo_almacen(perfil(sel_campos={})), NavegadorFalso(paginas), guion, ttl_cache_ia=3600.0
    )
    r = m.buscar(texto="1K0698151", modo=Modo.AUTO)
    (peticion,) = m.proveedor.peticiones if m.proveedor else []
    assert peticion.esquema["required"] == ["estado"]
    assert len([o for o in ofertas(r) if o.precio_anclado]) == len(PRODUCTOS)
    assert "no aportó nada en la 1.ª ficha" in paso(r, "extracción").detalle
    repetida = m.buscar(texto="1K0698151", modo=Modo.AUTO)
    assert len(m.peticiones_ia) == 1
    assert (repetida.llamadas_ia, repetida.ia_desde_cache) == (0, 1)


# --- A2: filtrar y ordenar por la consulta antes de abrir fichas ---------------------------------

REALES = Path(__file__).parents[1] / "fixtures" / "reales"


def busqueda_de(texto: str) -> str:
    return PLANTILLA.replace("{consulta}", texto.replace(" ", "+"))


def sitio_con(productos: Iterable[Producto], texto: str = CONSULTA) -> dict[str, str]:
    """El listado de la búsqueda de `texto` con `productos` y la ficha de cada uno."""
    lista = tuple(productos)
    sitio = {busqueda_de(texto): html_listado(lista)}
    for producto in lista:
        sitio[producto.url] = html_ficha(producto)
    return sitio


def test_resultados_que_no_nombran_la_consulta_no_se_abren() -> None:
    sitio = sitio_con([OTROS[0], PRODUCTOS[0], OTROS[1]])
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio))
    r = m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [BUSQUEDA, PRODUCTOS[0].url]
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]


def test_resultados_se_abren_primero_los_que_nombran_toda_la_consulta() -> None:
    golf = replace(OTROS[0], ruta="/p/retrovisor-golf", titulo="Retrovisor Golf VI")
    sitio = sitio_con([golf, PRODUCTOS[0]])
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio), max_fichas=1)
    m.buscar(modo=Modo.CLASICO)
    assert m.navegador.urls == [BUSQUEDA, PRODUCTOS[0].url]


def test_ningun_resultado_nombra_la_consulta_es_sin_resultados() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio_con(OTROS)), {})
    r = m.buscar(modo=Modo.AUTO)
    assert m.navegador.urls == [BUSQUEDA]
    assert ofertas(r) == [] and m.peticiones_ia == []
    detalle = paso(r, "extracción").detalle
    assert "Sin resultados" in detalle and "ninguno de los 2 resultados" in detalle
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


@pytest.mark.parametrize(
    ("texto", "titulo"),
    [
        ("faros", "Faro delantero izquierdo Golf VI"),
        ("amortiguadores delanteros", "Amortiguador delantero Monroe G7345"),
        ("bujias", "Bujía NGK BKR6E"),
        ("luces", "Luz de matrícula Golf"),
        ("sensores", "Sensor ABS delantero"),
    ],
)
def test_consulta_en_plural_abre_los_resultados_en_singular(texto: str, titulo: str) -> None:
    producto = replace(PRODUCTOS[0], ruta="/p/123", titulo=titulo)
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio_con([producto, OTROS[0]], texto)))
    m.buscar(texto=texto, modo=Modo.CLASICO)
    assert m.navegador.urls == [busqueda_de(texto), producto.url]


DLI = "desguacesdli.com"
DLI_BUSQUEDA = f"https://{DLI}/recambios?s=faro"
DLI_CATALOGO = f"https://{DLI}/recambios/"


def test_dli_que_ignora_la_busqueda_no_abre_fichas() -> None:
    # Página real (S01): /recambios?s=faro redirige a /recambios/ y salen sus destacados, sin
    # ningún faro. Antes se abrían 5 de sus fichas y salían como ofertas (bug 10).
    html = (REALES / "dli_busqueda_faro_chromium.html").read_text(encoding="utf-8")
    dli = perfil(
        dominio=DLI,
        url_busqueda=f"https://{DLI}/recambios?s={{consulta}}",
        sel_resultado="ul.products > li.product",
        sel_titulo="a.product-title @title",
        sel_enlace="a.product-title @href",
        sel_precio_listado=None,
        necesita_js=False,
    )
    navegador = NavegadorFalso({DLI_CATALOGO: html}, redirecciones={DLI_BUSQUEDA: DLI_CATALOGO})
    m = montar(nuevo_almacen(dli, tiendas=(DLI,)), navegador, {})
    r = m.buscar(texto="faro", modo=Modo.AUTO, tiendas=(DLI,))
    assert navegador.urls == [DLI_BUSQUEDA]
    assert ofertas(r) == [] and m.peticiones_ia == []
    assert "ninguno de los 10 resultados" in paso(r, "extracción", DLI).detalle


def test_dli_que_ignora_la_busqueda_no_abre_fichas_tampoco_en_modo_ia() -> None:
    # R3-D: el respaldo por texto no se activa: la página no repite la consulta (ni en el campo
    # de búsqueda, vacío, ni en el <title> o el <h1>).
    html = (REALES / "dli_busqueda_faro_chromium.html").read_text(encoding="utf-8")
    dli = perfil(
        dominio=DLI,
        url_busqueda=f"https://{DLI}/recambios?s={{consulta}}",
        sel_resultado="ul.products > li.product",
        sel_titulo="a.product-title @title",
        sel_enlace="a.product-title @href",
        sel_precio_listado=None,
        necesita_js=False,
    )
    navegador = NavegadorFalso({DLI_CATALOGO: html}, redirecciones={DLI_BUSQUEDA: DLI_CATALOGO})
    m = montar(nuevo_almacen(dli, tiendas=(DLI,)), navegador, {})
    r = m.buscar(texto="faro", modo=Modo.IA, tiendas=(DLI,), campos=("precio",))
    assert navegador.urls == [DLI_BUSQUEDA]
    assert ofertas(r) == [] and m.peticiones_ia == []


# --- R3-D: la tienda titula con marca y modelo, sin la categoría (respaldo por texto) ----------

THO = "thomann.es"
THO_CONSULTA = "guitarra acústica"
THO_BUSQUEDA = "https://www.thomann.es/search.html?sw=guitarra+ac%C3%BAstica"
THO_HTML = (REALES / "thomann_busqueda_guitarra_acustica_titulos_sin_categoria.html").read_text(
    encoding="utf-8"
)
THO_FICHAS = (
    ("thomann_gigbag1_western.htm", "Thomann Acoustic-Steel Gigbag BK", "24,90 €"),
    ("sheeran_loopers_looper.htm", "Sheeran Loopers Looper +", "249 €"),
    ("audio_sprockets_tonedexter_ii_acoustic_preamp.htm", "ToneDexter II Acoustic Preamp", "729 €"),
    ("thomann_western_gitarre_case.htm", "Thomann Western Guitar Case", "99 €"),
    ("taylor_214ce_483303.htm", "Taylor 214ce", "999 €"),
    ("harley_benton_clg_70asr_ce_nat_allsolid.htm", "Harley Benton CLG-70ASR CE NAT", "349 €"),
)
"""Las 6 tarjetas del recorte real, en el orden de la tienda: ruta, título de la ficha, precio."""
THO_URLS = [f"https://www.{THO}/{ruta}?type=quickSearch" for ruta, _, _ in THO_FICHAS]
TITULO_THO = 'Resultados de búsqueda - página 1 - "guitarra acústica"'
H1_THO = ">guitarra acústica</h1>"
INPUT_THO = 'type="search" value="guitarra acústica"'


def perfil_thomann() -> Perfil:
    """El perfil que hizo el configurador en la campaña real (ronda 3, consulta «filtro»)."""
    return perfil(
        dominio=THO,
        url_busqueda="https://www.thomann.es/search.html?sw={consulta}",
        sel_resultado="div.fx-product-list-entry",
        sel_titulo=".title__name",
        sel_enlace="a.product__content @href",
        sel_precio_listado=".product__price-primary",
        sel_campos={"precio": "div.price"},
        necesita_js=False,
    )


def sitio_thomann(busqueda: str = THO_HTML) -> dict[str, str]:
    sitio = {THO_BUSQUEDA: busqueda}
    for url, (_, titulo, precio) in zip(THO_URLS, THO_FICHAS, strict=True):
        sitio[url] = (
            f"<html><head><title>{titulo} | Thomann España</title></head><body>"
            f'<h1>{titulo}</h1><div class="price">{precio}</div></body></html>'
        )
    return sitio


def sin_senales(html: str, *quitar: str) -> str:
    """La búsqueda de Thomann sin la consulta en las señales de `quitar` (title, h1, input)."""
    cambios = {
        "title": (TITULO_THO, "Resultados de búsqueda - página 1"),
        "h1": (H1_THO, ">Lo más vendido</h1>"),
        "input": (INPUT_THO, 'type="search" value=""'),
    }
    for senal in quitar:
        antes, despues = cambios[senal]
        assert antes in html
        html = html.replace(antes, despues)
    return html


def buscar_en_thomann(
    sitio: dict[str, str], modo: Modo = Modo.IA, guion: Mapping[str, Iterable[Any]] | None = None
) -> tuple[Montaje, ResultadoBusqueda]:
    m = montar(nuevo_almacen(perfil_thomann(), tiendas=(THO,)), NavegadorFalso(sitio), guion)
    r = m.buscar(texto=THO_CONSULTA, modo=modo, tiendas=(THO,), campos=("precio",))
    return m, r


def test_thomann_titulos_sin_la_consulta_abre_los_primeros_si_la_pagina_es_su_busqueda() -> None:
    # Página real (campaña, ronda 3): ningún título nombra «guitarra acústica» (214ce,
    # CLG-70ASR...), pero la página es su búsqueda: la repiten el <title>, el <h1> y el campo
    # de búsqueda. Antes se descartaban todos los resultados y salía «sin resultados».
    clases = ("accesorio", "otra", "accesorio", "accesorio", "misma_pieza")
    m, r = buscar_en_thomann(sitio_thomann(), guion={"emparejado": [_clasificaciones(*clases)]})
    assert m.navegador.urls == [THO_BUSQUEDA, *THO_URLS[:5]]
    respaldo = [p for p in progreso(r, THO).pasos if p.detalle == RESPALDO_TEXTO.format(n=5)]
    assert [(p.nombre, p.estado) for p in respaldo] == [("búsqueda", EstadoPaso.AVISO)]
    assert "Sin resultados" not in paso(r, "extracción", THO).detalle
    # Los accesorios y lo que no tiene relación los descarta el emparejado con IA.
    assert m.peticiones_ia == ["emparejado"]
    assert [o.url for o in ofertas(r)] == [THO_URLS[4]]
    assert len(r.descartados) == 4 and all(d.motivo.startswith("IA · ") for d in r.descartados)


def test_thomann_respaldo_por_texto_respeta_max_fichas() -> None:
    m = montar(
        nuevo_almacen(perfil_thomann(), tiendas=(THO,)),
        NavegadorFalso(sitio_thomann()),
        {"emparejado": [_clasificaciones("misma_pieza", "misma_pieza")]},
        max_fichas=2,
    )
    r = m.buscar(texto=THO_CONSULTA, modo=Modo.IA, tiendas=(THO,), campos=("precio",))
    assert m.navegador.urls == [THO_BUSQUEDA, *THO_URLS[:2]]
    assert any(p.detalle == RESPALDO_TEXTO.format(n=2) for p in progreso(r, THO).pasos)


@pytest.mark.parametrize(
    "quitar",
    [
        ("title", "h1", "input"),  # la página no dice de qué es la búsqueda
        ("title", "h1"),  # solo el campo de búsqueda (lo puede rellenar el script de la tienda)
        ("input",),  # solo el <title> y el <h1>: «0 resultados para X» con destacados (bug 10)
        ("input", "h1"),  # solo el <title>
        ("input", "title"),  # solo el <h1>
    ],
    ids=["ninguna", "solo-input", "title-y-h1", "solo-title", "solo-h1"],
)
def test_thomann_sin_el_campo_de_busqueda_y_el_titulo_no_abre_ninguna(
    quitar: tuple[str, ...],
) -> None:
    m, r = buscar_en_thomann(sitio_thomann(sin_senales(THO_HTML, *quitar)), guion={})
    assert m.navegador.urls == [THO_BUSQUEDA]
    assert ofertas(r) == [] and m.peticiones_ia == []
    assert "ninguno de los 6 resultados" in paso(r, "extracción", THO).detalle
    assert not any(p.detalle == RESPALDO_TEXTO.format(n=5) for p in progreso(r, THO).pasos)


@pytest.mark.parametrize("quitar", [("title",), ("h1",)], ids=["con-h1", "con-title"])
def test_thomann_basta_el_campo_de_busqueda_y_el_title_o_el_h1(quitar: tuple[str, ...]) -> None:
    clases = ("misma_pieza",) * 5
    m, _ = buscar_en_thomann(
        sitio_thomann(sin_senales(THO_HTML, *quitar)),
        guion={"emparejado": [_clasificaciones(*clases)]},
    )
    assert m.navegador.urls == [THO_BUSQUEDA, *THO_URLS[:5]]


def test_thomann_respaldo_por_texto_exige_todas_las_palabras() -> None:
    # «guitarra eléctrica»: la página dice «guitarra acústica», no es su búsqueda.
    sitio = sitio_thomann()
    sitio["https://www.thomann.es/search.html?sw=guitarra+el%C3%A9ctrica"] = THO_HTML
    m = montar(nuevo_almacen(perfil_thomann(), tiendas=(THO,)), NavegadorFalso(sitio), {})
    m.buscar(texto="guitarra eléctrica", modo=Modo.IA, tiendas=(THO,), campos=("precio",))
    assert len(m.navegador.urls) == 1


@pytest.mark.parametrize("modo", [Modo.AUTO, Modo.CLASICO], ids=["auto", "clasico"])
def test_thomann_sin_emparejado_con_ia_no_abre_ninguna(modo: Modo) -> None:
    # Sin el emparejado con IA (solo en modo IA), nadie descartaría el Looper o el ToneDexter.
    m, r = buscar_en_thomann(sitio_thomann(), modo=modo, guion={})
    assert m.navegador.urls == [THO_BUSQUEDA]
    assert ofertas(r) == [] and m.peticiones_ia == []
    detalle = paso(r, "extracción", THO).detalle
    assert "ninguno de los 6 resultados" in detalle
    assert "en modo IA" in detalle


def test_thomann_en_modo_ia_sin_servicio_de_ia_no_abre_ninguna() -> None:
    m, r = buscar_en_thomann(sitio_thomann(), modo=Modo.IA, guion=None)
    assert m.navegador.urls == [THO_BUSQUEDA]
    assert ofertas(r) == []


def test_resultados_que_nombran_la_consulta_no_usan_el_respaldo_por_texto() -> None:
    # Recambios (caso de referencia): la búsqueda repite la consulta en el campo y en el
    # <title>, y los títulos la nombran: se abre solo el que la nombra, como siempre.
    sitio = sitio_con([OTROS[0], PRODUCTOS[0], OTROS[1]])
    sitio[BUSQUEDA] = sitio[BUSQUEDA].replace('name="q"', 'name="q" value="faro golf"', 1)
    guion = {"emparejado": [_clasificaciones("misma_pieza")]}
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio), guion)
    r = m.buscar(modo=Modo.IA, campos=("precio",))
    assert m.navegador.urls == [BUSQUEDA, PRODUCTOS[0].url]
    assert not any(p.detalle.startswith(RESPALDO_TEXTO[:30]) for p in progreso(r).pasos)


def test_recambios_que_repiten_la_consulta_sin_resultados_que_la_nombren_en_modo_ia() -> None:
    # La búsqueda de recambios repite «faro golf» en el campo y en el <title>, pero solo trae
    # otras piezas: en modo IA se abren y el emparejado las descarta.
    sitio = sitio_con(OTROS)
    sitio[BUSQUEDA] = sitio[BUSQUEDA].replace('name="q"', 'name="q" value="faro golf"', 1)
    guion = {"emparejado": [_clasificaciones("otra", "otra")]}
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio), guion)
    r = m.buscar(modo=Modo.IA, campos=("precio",))
    assert m.navegador.urls == [BUSQUEDA, OTROS[0].url, OTROS[1].url]
    assert ofertas(r) == [] and len(r.descartados) == 2


def test_por_referencia_sigue_con_el_respaldo_oem_y_no_el_de_texto() -> None:
    sitio = sitio_con(PRODUCTOS, OEM)
    busqueda = busqueda_de(OEM)
    sitio[busqueda] = sitio[busqueda].replace('name="q"', f'name="q" value="{OEM}"', 1)
    sitio[busqueda] = sitio[busqueda].replace("faro golf", OEM)
    guion = {"emparejado": [_clasificaciones("misma_pieza", "misma_pieza")]}
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio), guion, max_fichas=2)
    r = m.buscar(texto=OEM, modo=Modo.IA, campos=("precio",))
    detalles = [p.detalle for p in progreso(r).pasos]
    assert RESPALDO_OEM.format(n=2) in detalles
    assert not any(d.startswith(RESPALDO_TEXTO[:30]) for d in detalles)


# --- La búsqueda redirige a otra página de la tienda (R2-C12) ---------------------------------

JUG = "juguetilandia.com"
JUG_BUSQUEDA = f"https://www.{JUG}/buscador.htm?search=lego"
JUG_MARCA = f"https://www.{JUG}/lego__s3.htm"


def perfil_juguetilandia() -> Perfil:
    """El perfil que hizo el configurador en la campaña real (ronda 2, consulta «filtro»)."""
    return perfil(
        dominio=JUG,
        url_busqueda=f"https://www.{JUG}/buscador.htm?search={{consulta}}",
        sel_resultado="div.articulos",
        sel_titulo="h2.articuloNombre",
        sel_enlace="div.cajaPrecioFichaArticulo a @href",
        sel_precio_listado="div.cajaPrecioFichaArticulo p span",
        necesita_js=False,
    )


def test_busqueda_que_redirige_a_una_pagina_de_marca_no_es_un_perfil_roto() -> None:
    # Página real (campaña, ronda 2): «lego» redirige a la página de marca, sin ningún
    # `div.articulos`. Sus «productos con precio» son enlaces de la cabecera (envío gratis,
    # carrito, outlet). Antes se llamaba al reparador y la tienda quedaba en ROTO, aunque el
    # perfil sigue valiendo para otras consultas.
    html = (REALES / "juguetilandia_busqueda_redirige_marca.html").read_text(encoding="utf-8")
    navegador = NavegadorFalso({JUG_MARCA: html}, redirecciones={JUG_BUSQUEDA: JUG_MARCA})
    almacen = nuevo_almacen(perfil_juguetilandia(), tiendas=(JUG,))
    m = montar(almacen, navegador, {"reparador": [PROPUESTA]})
    r = m.buscar(texto="lego", modo=Modo.AUTO, tiendas=(JUG,))
    assert m.peticiones_ia == []
    assert navegador.urls == [JUG_BUSQUEDA]
    avisos = [p for p in progreso(r, JUG).pasos if "redirige la búsqueda" in p.detalle]
    assert [(p.nombre, p.estado) for p in avisos] == [("perfil", EstadoPaso.AVISO)]
    assert JUG_MARCA in avisos[0].detalle
    assert not any(p.estado is EstadoPaso.ERROR for p in progreso(r, JUG).pasos)
    assert ofertas(r) == []
    tienda = m.almacen.tienda(JUG)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO
    vigente = m.almacen.perfil_vigente(JUG)
    assert vigente is not None and vigente.version == 1


def test_redireccion_a_una_pagina_de_marca_en_modo_clasico_no_deja_la_tienda_rota() -> None:
    html = (REALES / "juguetilandia_busqueda_redirige_marca.html").read_text(encoding="utf-8")
    navegador = NavegadorFalso({JUG_MARCA: html}, redirecciones={JUG_BUSQUEDA: JUG_MARCA})
    m = montar(nuevo_almacen(perfil_juguetilandia(), tiendas=(JUG,)), navegador, {})
    r = m.buscar(texto="lego", modo=Modo.CLASICO, tiendas=(JUG,))
    assert not any(p.estado is EstadoPaso.ERROR for p in progreso(r, JUG).pasos)
    assert "Sin resultados" in paso(r, "extracción", JUG).detalle
    tienda = m.almacen.tienda(JUG)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


@pytest.mark.parametrize(
    "destino",
    [
        f"{TIENDA}/search?q=faro+golf",  # el buscador se ha movido y conserva la consulta
        f"{TIENDA}/es/buscar/faro%20golf",  # la consulta va ahora en la ruta
        PORTADA,  # una plantilla obsoleta que manda a la portada
    ],
    ids=["consulta-en-la-query", "consulta-en-la-ruta", "portada"],
)
def test_busqueda_que_redirige_sin_dejar_de_ser_la_busqueda_sigue_siendo_un_perfil_roto(
    destino: str,
) -> None:
    sitio = paginas_sitio()
    sitio[destino] = sitio[BUSQUEDA]
    navegador = NavegadorFalso(sitio, redirecciones={BUSQUEDA: destino})
    m = montar(nuevo_almacen(ROTO), navegador, {"reparador": [PROPUESTA]})
    r = m.buscar(modo=Modo.AUTO)
    assert m.peticiones_ia == ["reparador"]
    assert not any("redirige la búsqueda" in p.detalle for p in progreso(r).pasos)


def test_busqueda_sin_redireccion_con_el_listado_vacio_sigue_siendo_un_perfil_roto() -> None:
    m = montar(nuevo_almacen(ROTO), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert not any("redirige la búsqueda" in p.detalle for p in progreso(r).pasos)
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO


OEM = "5K1941005E"


def test_por_referencia_se_abren_solo_los_que_la_nombran() -> None:
    con_ref = replace(PRODUCTOS[0], ruta="/p/pieza-5k1-941-005-e", titulo="Faro 5K1 941 005 E")
    sitio = sitio_con([PRODUCTOS[1], con_ref, OTROS[0]], OEM)
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio))
    r = m.buscar(texto=OEM, modo=Modo.CLASICO)
    assert m.navegador.urls == [busqueda_de(OEM), con_ref.url]
    assert not any(p.detalle.startswith(RESPALDO_OEM[:20]) for p in progreso(r).pasos)


def test_por_referencia_la_exacta_se_abre_antes_que_las_revisiones() -> None:
    # Revisión: exacta y revisión empataban y decidía el orden de la tienda; con cinco
    # revisiones delante, la exacta se quedaba sin abrir.
    revisiones = [
        replace(PRODUCTOS[0], ruta=f"/p/{n}", titulo=f"Faro 1K0941005{letra}")
        for n, letra in enumerate("ABCDE")
    ]
    exacta = replace(PRODUCTOS[0], ruta="/p/exacta", titulo="Faro 1K0941005")
    sitio = sitio_con([*revisiones, exacta], "1K0941005")
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio), max_fichas=2)
    m.buscar(texto="1K0941005", modo=Modo.CLASICO)
    assert m.navegador.urls[:2] == [busqueda_de("1K0941005"), exacta.url]


def test_por_referencia_sin_ninguno_que_la_nombre_abre_los_primeros() -> None:
    # Como en Oscaro: la referencia solo sale en la ficha (sus equivalencias), no en el listado.
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio_con(PRODUCTOS, OEM)), max_fichas=2)
    r = m.buscar(texto=OEM, modo=Modo.CLASICO)
    assert m.navegador.urls == [busqueda_de(OEM), PRODUCTOS[0].url, PRODUCTOS[1].url]
    respaldo = [p for p in progreso(r).pasos if p.detalle == RESPALDO_OEM.format(n=2)]
    assert [(p.nombre, p.estado) for p in respaldo] == [("búsqueda", EstadoPaso.AVISO)]
    # La ficha lleva 5K1941005E en su referencia OEM: el agrupador la acepta.
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]


def test_por_referencia_y_palabras_sin_la_referencia_abre_antes_los_que_nombran_palabras() -> None:
    # Respaldo con una consulta mixta: no se pierde el orden por las palabras.
    texto = f"faro {OEM}"
    sitio = sitio_con([OTROS[0], OTROS[1], PRODUCTOS[1]], texto)
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio), max_fichas=2)
    m.buscar(texto=texto, modo=Modo.CLASICO)
    assert m.navegador.urls == [busqueda_de(texto), PRODUCTOS[1].url, OTROS[0].url]


def test_perfil_api_solo_da_los_productos_que_nombran_la_consulta() -> None:
    # Bug 2: la API de WooCommerce busca también en la descripción y devolvía las pastillas y
    # el tapacubos al buscar «faro golf».
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas_woo()), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert [o.titulo for o in ofertas(r)] == [WOO_TITULOS[0]]
    detalle = paso(r, "extracción", WOO).detalle
    assert "1 productos" in detalle and "2 no nombran la búsqueda" in detalle


def test_perfil_api_sin_ninguno_que_nombre_la_consulta_es_sin_resultados() -> None:
    paginas = paginas_woo()
    api = WOO_PLANTILLA.replace("{consulta}", "alternador")
    paginas[api] = paginas.pop(WOO_API)
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas), {})
    r = m.buscar(texto="alternador", modo=Modo.AUTO)
    assert m.navegador.urls == [api] and m.peticiones_ia == []
    assert ofertas(r) == [] and r.descartados == ()
    detalle = paso(r, "extracción", WOO).detalle
    assert "Sin resultados" in detalle and "ninguno de sus 3 productos" in detalle
    tienda = m.almacen.tienda(WOO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def test_perfil_api_por_referencia_sin_nombrarla_conserva_el_orden() -> None:
    paginas = paginas_woo()
    api = WOO_PLANTILLA.replace("{consulta}", OEM)
    paginas[api] = paginas.pop(WOO_API)
    almacen = nuevo_almacen(perfil_api(revisado=True), tiendas=(WOO,))
    m = montar(almacen, NavegadorFalso(paginas), {}, max_fichas=2)
    r = m.buscar(texto=OEM, modo=Modo.CLASICO)
    assert m.navegador.urls == [api]
    assert "2 productos" in paso(r, "extracción", WOO).detalle


VELAZQUEZ = "desguacesvelazquez.com"
VELAZQUEZ_PLANTILLA = (
    "https://www.desguacesvelazquez.com/wp-json/wc/store/v1/products?search={consulta}&per_page=10"
)


def test_perfil_api_real_ordena_por_la_consulta() -> None:
    # Respuesta real de la API de Velázquez a «faro» (S01): los dos faros del Captur van en las
    # posiciones 1 y 3 de 10; con dos fichas, antes salían el Captur y el antiniebla del Master.
    texto = (REALES / "velazquez_api_woocommerce_faro.json").read_text(encoding="utf-8")
    api = VELAZQUEZ_PLANTILLA.replace("{consulta}", "faro+captur")
    velazquez = perfil_api(dominio=VELAZQUEZ, url_busqueda=VELAZQUEZ_PLANTILLA, revisado=True)
    almacen = nuevo_almacen(velazquez, tiendas=(VELAZQUEZ,))
    m = montar(almacen, NavegadorFalso({api: texto}), {}, max_fichas=2)
    r = m.buscar(texto="faro captur", modo=Modo.CLASICO, tiendas=(VELAZQUEZ,))
    assert m.navegador.urls == [api]
    titulos = [o.titulo for o in ofertas(r)] + [d.titulo for d in r.descartados]
    assert len(titulos) == 2 and all("CAPTUR" in t for t in titulos)


@pytest.mark.parametrize(
    ("titulo", "ruta"),
    [
        ("Faro 1K0941005E", "/p/faro-1k0941005e"),
        ("Faro 1K0 941 005 E", "/p/faro-izquierdo"),  # Escrita por grupos (bug 7).
        ("Faro izquierdo", "/p/faro-1k0-941-005-e"),  # Solo en la ruta, con guiones.
    ],
)
def test_por_referencia_cuenta_las_revisiones_y_no_otras_referencias(
    titulo: str, ruta: str
) -> None:
    # El criterio de B5: la buscada entera o con un sufijo de revisión de 1-2 letras; una
    # referencia más larga con dígitos (o con otro dígito delante) es otra pieza.
    buscada = "1K0941005"
    otras = (
        replace(OTROS[0], ruta="/p/pieza-1k09410051", titulo="Pieza 1K09410051"),
        replace(OTROS[1], ruta="/p/pieza-11k0941005", titulo="Pieza 11K0941005"),
    )
    revision = replace(PRODUCTOS[0], ruta=ruta, titulo=titulo)
    sitio = sitio_con([*otras, revision], buscada)
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio))
    m.buscar(texto=buscada, modo=Modo.CLASICO)
    assert m.navegador.urls == [busqueda_de(buscada), revision.url]


def test_por_referencia_escrita_por_grupos_sus_grupos_no_cuentan_como_palabras() -> None:
    # «1K0 941 005»: «941» y «005» son trozos de la referencia, no palabras de la consulta.
    texto = "1K0 941 005"
    otra = replace(OTROS[0], ruta="/p/pieza-1k09410051", titulo="Pieza 1K09410051")
    revision = replace(PRODUCTOS[0], ruta="/p/faro-1k0941005e", titulo="Faro 1K0941005E")
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio_con([otra, revision], texto)))
    m.buscar(texto=texto, modo=Modo.CLASICO)
    assert m.navegador.urls == [busqueda_de(texto), revision.url]


# --- Configuraciones fallidas (A1) ------------------------------------------------------------


def test_espera_crece_de_una_hora_a_un_dia() -> None:
    assert espera_configuracion(1) == timedelta(hours=1)
    assert espera_configuracion(2) == timedelta(days=1)
    assert espera_configuracion(7) == timedelta(days=1)


def test_consulta_de_configuracion_rota_por_la_lista() -> None:
    assert consulta_de_configuracion(("a", "b", "c"), 0) == "a"
    assert consulta_de_configuracion(("a", "b", "c"), 1) == "b"
    assert consulta_de_configuracion(("a", "b", "c"), 4) == "b"


def test_consultas_de_configuracion_por_defecto() -> None:
    # Decisión de Julio (S03): una lista fija de consultas genéricas de recambios.
    assert CONSULTAS_CONFIGURACION == ("filtro", "faro", "freno")


@pytest.mark.parametrize("consultas", [(), ("faro", "")], ids=["vacia", "texto-vacio"])
def test_orquestador_rechaza_consultas_de_configuracion_no_validas(
    consultas: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        Orquestador(nuevo_almacen(), NavegadorFalso({}), None, consultas_configuracion=consultas)


def test_configura_con_la_primera_consulta_generica_por_defecto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    espia = ConfiguradorEspia(perfil(revisado=False, autor="configurador IA"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(
        nuevo_almacen(),
        NavegadorFalso(paginas_sitio()),
        {},
        consultas_configuracion=CONSULTAS_CONFIGURACION,
    )
    m.buscar(modo=Modo.AUTO)
    (argumentos,) = espia.argumentos
    assert argumentos[3] == "filtro"


OEM_QUE_NO_VENDE = "1K0698151"


def sitio_generico() -> NavegadorFalso:
    """La tienda tiene faros (`q=faro`) pero no la OEM del usuario (`q=1K0698151`)."""
    sitio = {
        **paginas_sitio(busqueda=f"{TIENDA}/buscar?orden=relevancia&q=faro"),
        busqueda_de(OEM_QUE_NO_VENDE): SIN_RESULTADOS,
    }
    return NavegadorFalso(sitio, redirecciones={f"https://{DOMINIO}/": PORTADA})


def test_configura_con_una_consulta_generica_y_luego_busca_la_del_usuario() -> None:
    m = montar(
        nuevo_almacen(),
        sitio_generico(),
        {"configurador": [LISTADO_OK, CAMPOS_OK]},
        consultas_configuracion=("faro",),
    )
    r = m.buscar(texto=OEM_QUE_NO_VENDE, modo=Modo.AUTO)
    assert m.almacen.perfil_vigente(DOMINIO) is not None
    assert paso(r, "perfil").estado is EstadoPaso.IA
    assert "«faro»" in paso(r, "perfil").detalle
    assert paso(r, "extracción") == Paso(
        "extracción", EstadoPaso.AVISO, f"Sin resultados para «{OEM_QUE_NO_VENDE}» en esta tienda"
    )
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.fallo is None
    urls = m.navegador.urls
    generica = urls.index(f"{TIENDA}/buscar?orden=relevancia&q=faro")
    assert generica < urls.index(busqueda_de(OEM_QUE_NO_VENDE))


def test_configurar_con_la_oem_que_no_vende_falla() -> None:
    # Lo que pasaba antes de A1: la consulta del usuario como consulta de prueba no da los
    # productos que pide el descubrimiento (`MIN_PRODUCTOS`) y la configuración falla.
    m = montar(
        nuevo_almacen(),
        sitio_generico(),
        {"configurador": [LISTADO_OK, CAMPOS_OK]},
        consultas_configuracion=(OEM_QUE_NO_VENDE,),
    )
    r = m.buscar(texto=OEM_QUE_NO_VENDE, modo=Modo.AUTO)
    assert paso(r, "perfil").estado is EstadoPaso.ERROR
    assert m.almacen.perfil_vigente(DOMINIO) is None


HORA = timedelta(hours=1)


def fallo_de(m: Montaje) -> FalloConfiguracion | None:
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None
    return tienda.fallo


def en_espera(minutos_restantes: int = 50, intentos: int = 1) -> Almacen:
    almacen = nuevo_almacen()
    almacen.registrar_fallo_configuracion(
        DOMINIO,
        "No se ha podido configurar la tienda.",
        AHORA - timedelta(minutes=10),
        intentos=intentos,
        reintentar_desde=AHORA + timedelta(minutes=minutos_restantes),
    )
    return almacen


def test_configuracion_fallida_se_recuerda_una_hora() -> None:
    sitio = paginas_sitio(busqueda=None, formulario=False)
    m = montar(nuevo_almacen(), NavegadorFalso(sitio), {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    fallo = fallo_de(m)
    assert fallo is not None
    assert (fallo.intentos, fallo.fecha, fallo.reintentar_desde) == (1, AHORA, AHORA + HORA)
    assert "No se ha podido configurar" in fallo.motivo
    assert paso(r, "perfil").estado is EstadoPaso.ERROR
    # El detalle se recorta a 300 caracteres: el «hasta» va delante del mensaje largo.
    assert "no se reintenta hasta el 19 oct 13:00" in paso(r, "perfil").detalle
    assert "Reintentar" in paso(r, "perfil").detalle


def test_tienda_en_espera_no_se_configura_ni_se_visita() -> None:
    m = montar(en_espera(), NavegadorFalso(paginas_sitio()), {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    assert m.navegador.pedidas == []
    assert m.peticiones_ia == []
    perfil_paso = paso(r, "perfil")
    assert perfil_paso.estado is EstadoPaso.OMITIDO
    assert "19 oct 12:50" in perfil_paso.detalle and "Reintentar" in perfil_paso.detalle
    general = paso(r, EN_ESPERA, "PrecIA")
    assert general.estado is EstadoPaso.AVISO
    assert DOMINIO in general.detalle
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.ultima_visita is None
    # La espera no cambia: no ha habido otro intento.
    assert tienda.fallo is not None and tienda.fallo.intentos == 1


def test_aviso_de_tiendas_en_espera_concuerda_en_singular_y_plural() -> None:
    # Revisión: decía «Se han saltado 1 tiendas» y mandaba a Tiendas, donde no hay botón.
    assert aviso_en_espera(["caida.example"]) == (
        "Se ha saltado 1 tienda cuya configuración falló hace poco: caida.example. Puedes "
        "reintentarla desde su página"
    )
    assert aviso_en_espera(["a.example", "b.example"]) == (
        "Se han saltado 2 tiendas cuya configuración falló hace poco: a.example, b.example. "
        "Puedes reintentarlas desde la página de cada una"
    )
    m = montar(en_espera(), NavegadorFalso(paginas_sitio()), {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, EN_ESPERA, "PrecIA").detalle == aviso_en_espera([DOMINIO])


def test_tienda_elegida_a_mano_en_espera_tambien_se_salta() -> None:
    m = montar(en_espera(), NavegadorFalso(paginas_sitio()), {"configurador": []})
    r = m.buscar(modo=Modo.AUTO, tiendas=(DOMINIO,))
    assert m.navegador.pedidas == []
    assert paso(r, "perfil").estado is EstadoPaso.OMITIDO
    assert DOMINIO in paso(r, EN_ESPERA, "PrecIA").detalle


def test_sin_tiendas_en_espera_no_hay_aviso() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.AUTO)
    assert all(p.tienda != "PrecIA" for p in r.progreso)


def test_pasada_la_espera_se_reintenta_con_la_siguiente_consulta_y_espera_un_dia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    espia = ConfiguradorEspia(perfil(), error=ConfiguracionFallida("Sigue sin buscador."))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(
        en_espera(minutos_restantes=-1),
        NavegadorFalso(paginas_sitio()),
        {},
        consultas_configuracion=CONSULTAS_CONFIGURACION,
    )
    r = m.buscar(modo=Modo.AUTO)
    (argumentos,) = espia.argumentos
    assert argumentos[3] == CONSULTAS_CONFIGURACION[1]
    assert fallo_de(m) == FalloConfiguracion(
        2, AHORA, "Sigue sin buscador.", AHORA + timedelta(days=1)
    )
    assert "«faro»" in paso(r, "perfil").detalle
    assert all(p.tienda != "PrecIA" for p in r.progreso)


def test_reintentar_a_mano_configura_aunque_no_haya_pasado_la_hora(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    espia = ConfiguradorEspia(perfil(revisado=False, autor="configurador IA"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    almacen = en_espera()
    almacen.levantar_espera(DOMINIO)
    m = montar(almacen, NavegadorFalso(paginas_sitio()), {})
    m.buscar(modo=Modo.AUTO)
    assert len(espia.argumentos) == 1


def test_configuracion_correcta_borra_el_fallo() -> None:
    almacen = en_espera(minutos_restantes=-1)
    m = montar(almacen, redirigida(), {"configurador": [LISTADO_OK, CAMPOS_OK]})
    r = m.buscar(modo=Modo.AUTO)
    assert m.almacen.perfil_vigente(DOMINIO) is not None
    assert fallo_de(m) is None
    assert len(ofertas(r)) == len(PRODUCTOS)


def test_error_de_ia_al_configurar_no_se_recuerda() -> None:
    agotado = PresupuestoAgotado("Se ha alcanzado el tope de 20 llamadas de IA por búsqueda")
    m = montar(nuevo_almacen(), redirigida(), {"configurador": [agotado]})
    m.buscar(modo=Modo.AUTO)
    assert fallo_de(m) is None


def test_error_de_descarga_al_configurar_se_recuerda(monkeypatch: pytest.MonkeyPatch) -> None:
    espia = ConfiguradorEspia(perfil(), error=ErrorDescarga("La tienda no responde"))
    monkeypatch.setattr("precia.nucleo.orquestador.configurar", espia)
    m = montar(nuevo_almacen(), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.AUTO)
    fallo = fallo_de(m)
    assert fallo is not None and fallo.motivo == "La tienda no responde"
    assert "no se reintenta hasta" in paso(r, "perfil").detalle


def test_bloqueo_al_configurar_se_recuerda() -> None:
    portada = f"https://{DOMINIO}/"
    bloqueo = Bloqueado(portada, "Cloudflare Turnstile")
    navegador = NavegadorFalso(paginas_sitio(), errores={portada: bloqueo})
    m = montar(nuevo_almacen(), navegador, {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    fallo = fallo_de(m)
    assert fallo is not None
    assert BLOQUEO in fallo.motivo and "Cloudflare Turnstile" in fallo.motivo
    detalle = paso(r, "perfil").detalle
    assert detalle.startswith(BLOQUEO) and "no se reintenta hasta" in detalle
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.bloqueos == 1


def test_en_clasico_una_tienda_en_espera_sale_como_siempre() -> None:
    m = montar(en_espera(), NavegadorFalso(paginas_sitio()), {})
    r = m.buscar(modo=Modo.CLASICO)
    assert paso(r, "perfil") == Paso(
        "perfil", EstadoPaso.OMITIDO, "Sin perfil (en modo clásico no se configura)"
    )
    assert all(p.tienda != "PrecIA" for p in r.progreso)


class AlmacenSinFallos(Almacen):
    def registrar_fallo_configuracion(self, *args: Any, **opciones: Any) -> Any:
        raise ErrorAlmacen("disco lleno")


def test_si_no_se_puede_guardar_el_fallo_la_busqueda_sigue() -> None:
    almacen = AlmacenSinFallos(":memory:", reloj=lambda: AHORA)
    almacen.alta_tienda(DOMINIO, "Recambios")
    sitio = paginas_sitio(busqueda=None, formulario=False)
    m = montar(almacen, NavegadorFalso(sitio), {"configurador": []})
    r = m.buscar(modo=Modo.AUTO)
    assert paso(r, "perfil").estado is EstadoPaso.ERROR
    avisos = [p for p in progreso(r).pasos if p.estado is EstadoPaso.AVISO]
    assert any("disco lleno" in p.detalle for p in avisos)


def test_una_oferta_de_la_cache_se_apunta_en_la_visita_de_esta_busqueda() -> None:
    # A3 guarda su fecha de descarga; A1 apunta además la búsqueda que la ha visto, para que la
    # ficha del producto enseñe lo que vio la última visita a la tienda.
    ayer = AHORA - timedelta(hours=13)
    m = montar(nuevo_almacen(perfil()), NavegadorDeCache(paginas_sitio(), ayer))
    m.buscar(modo=Modo.CLASICO)
    filas = m.almacen._consultar("SELECT DISTINCT fecha, visita FROM precios")
    assert [(f["fecha"], f["visita"]) for f in filas] == [(ayer.isoformat(), AHORA.isoformat())]


# --- C1: seguimiento por tienda, parciales, plazo y cancelación --------------------------------


def _buscar_con(m: Montaje, seguimiento: SeguimientoFalso, **opciones: Any) -> ResultadoBusqueda:
    opciones.setdefault("texto", CONSULTA)
    consulta = ConsultaBusqueda(**opciones)
    return asyncio.run(m.orquestador.buscar(consulta, seguimiento=seguimiento))


def test_el_seguimiento_recibe_el_estado_de_cada_tienda() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    seg = SeguimientoFalso()
    r = _buscar_con(m, seg, modo=Modo.CLASICO)
    assert seg.estados == [(DOMINIO, EstadoTienda.DESCARGANDO), (DOMINIO, EstadoTienda.HECHA)]
    assert len(ofertas(r)) == len(PRODUCTOS)


def test_tienda_bloqueada_termina_como_bloqueada() -> None:
    bloqueo = Bloqueado(BUSQUEDA, "reCAPTCHA")
    navegador = NavegadorFalso(paginas_sitio(), errores={BUSQUEDA: bloqueo})
    m = montar(nuevo_almacen(perfil()), navegador)
    seg = SeguimientoFalso()
    _buscar_con(m, seg, modo=Modo.CLASICO)
    assert seg.de(DOMINIO)[-1] is EstadoTienda.BLOQUEADA


def test_sin_perfil_pasa_por_configurando() -> None:
    m = montar(nuevo_almacen(), redirigida(), {"configurador": [LISTADO_OK, CAMPOS_OK]})
    seg = SeguimientoFalso()
    _buscar_con(m, seg, modo=Modo.AUTO)
    assert seg.de(DOMINIO) == [
        EstadoTienda.CONFIGURANDO,
        EstadoTienda.DESCARGANDO,
        EstadoTienda.HECHA,
    ]


def test_tienda_en_espera_termina_en_espera() -> None:
    m = montar(en_espera(), NavegadorFalso(paginas_sitio()), {"configurador": []})
    seg = SeguimientoFalso()
    _buscar_con(m, seg, modo=Modo.AUTO)
    assert seg.de(DOMINIO) == [EstadoTienda.EN_ESPERA]


class NavegadorEnEspera(NavegadorFalso):
    """Las URL que cumplen `bloquear` esperan a que se ponga `soltar` (se crea en cada bucle)."""

    def __init__(self, paginas: dict[str, str], bloquear: Any) -> None:
        super().__init__(paginas)
        self.bloquear = bloquear
        self.soltar: asyncio.Event | None = None

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        if self.bloquear(url):
            if self.soltar is None:
                self.soltar = asyncio.Event()
            await self.soltar.wait()
        return await super().obtener(url, js=js, grande=grande)


def _dos_tiendas(navegador: Any, guion: Any = None, **opciones: Any) -> Montaje:
    otro_perfil = perfil(
        dominio=OTRA, url_busqueda=f"https://www.{OTRA}/buscar?orden=relevancia&q={{consulta}}"
    )
    almacen = nuevo_almacen(perfil(), otro_perfil, tiendas=(DOMINIO, OTRA))
    return montar(almacen, navegador, guion, **opciones)


async def _hasta(condicion: Any, vueltas: int = 10_000) -> None:
    for _ in range(vueltas):
        if condicion():
            return
        await asyncio.sleep(0)
    raise AssertionError("La condición no se ha cumplido a tiempo")


def test_los_resultados_parciales_llegan_por_tienda_segun_terminan() -> None:
    navegador = NavegadorEnEspera(_sitio_doble(), lambda url: OTRA in url)
    m = _dos_tiendas(navegador)
    seg = SeguimientoFalso()

    async def escenario() -> ResultadoBusqueda:
        navegador.soltar = asyncio.Event()
        consulta = ConsultaBusqueda(CONSULTA, modo=Modo.CLASICO)
        tarea = asyncio.create_task(m.orquestador.buscar(consulta, seguimiento=seg))
        # A1: antes llegan los parciales de cada ficha; este es el de la tienda terminada.
        await _hasta(lambda: any(p.progreso for p in seg.parciales))
        parcial = next(p for p in seg.parciales if p.progreso)
        assert parcial.provisional
        assert {o.tienda for o in ofertas(parcial)} == {DOMINIO}
        assert [p.tienda for p in parcial.progreso] == [DOMINIO]
        # Los parciales no escriben en el histórico.
        assert parcial.grupos and all(m.almacen.producto(g.id) is None for g in parcial.grupos)
        assert m.almacen.busquedas() == []
        navegador.soltar.set()
        return await tarea

    r = asyncio.run(escenario())
    assert not r.provisional
    assert {o.tienda for o in ofertas(r)} == {DOMINIO, OTRA}
    # Uno por ficha leída (A1) y uno al terminar cada tienda.
    assert len(seg.parciales) == 2 * len(PRODUCTOS) + 2
    n = len(PRODUCTOS)
    assert [len(p.progreso) for p in seg.parciales] == [0] * n + [1] * (n + 1) + [2]
    assert {o.tienda for o in ofertas(seg.parciales[-1])} == {DOMINIO, OTRA}
    assert all(m.almacen.producto(g.id) is not None for g in r.grupos)


def test_el_parcial_nunca_llama_a_la_ia() -> None:
    guion = {"emparejado": [_clasificaciones("misma_pieza", "misma_pieza", "variante", "otra")]}
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), guion)
    seg = SeguimientoFalso()
    r = _buscar_con(m, seg, modo=Modo.IA)
    assert m.peticiones_ia == ["emparejado"]
    assert {g.criterio for g in r.grupos} == {"Emparejado por IA"}
    assert all(p.provisional and p.llamadas_ia == 0 for p in seg.parciales)
    assert len(ofertas(seg.parciales[-1])) == len(PRODUCTOS)


def test_sin_seguimiento_no_hay_parciales_ni_cambia_el_resultado() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    r = m.buscar(modo=Modo.CLASICO)
    assert not r.provisional


class _TerceraFicha:
    """Bloquea la tercera ficha que se pide de `dominio` (las de una tienda van en serie)."""

    def __init__(self, dominio: str) -> None:
        self.dominio = dominio
        self.fichas = 0

    def __call__(self, url: str) -> bool:
        if self.dominio not in url or "/p/" not in url:
            return False
        self.fichas += 1
        return self.fichas == 3


def test_plazo_por_tienda_devuelve_lo_leido() -> None:
    navegador = NavegadorEnEspera(_sitio_doble(), _TerceraFicha(f"www.{DOMINIO}"))
    m = _dos_tiendas(navegador, plazo_tienda_s=0.05)
    seg = SeguimientoFalso()

    async def escenario() -> ResultadoBusqueda:
        consulta = ConsultaBusqueda(CONSULTA, modo=Modo.CLASICO)
        return await asyncio.wait_for(m.orquestador.buscar(consulta, seguimiento=seg), 2)

    r = asyncio.run(escenario())
    leidas = [u for u in navegador.urls if f"www.{DOMINIO}/p/" in u]
    assert len(leidas) == 2
    assert sorted(o.url for o in ofertas(r) if o.tienda == DOMINIO) == sorted(leidas)
    errores = [p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR]
    assert len(errores) == 1 and "Plazo" in errores[0].detalle and "2 fichas" in errores[0].detalle
    assert seg.de(DOMINIO)[-1] is EstadoTienda.PLAZO
    assert len([o for o in ofertas(r) if o.tienda == OTRA]) == len(PRODUCTOS)
    assert seg.de(OTRA)[-1] is EstadoTienda.HECHA
    tienda = m.almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.ultima_visita == AHORA


@pytest.mark.parametrize("plazo", [0, -1.0])
def test_plazo_por_tienda_tiene_que_ser_positivo(plazo: float) -> None:
    with pytest.raises(ValueError, match="plazo"):
        montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()), plazo_tienda_s=plazo)


def test_plazo_agotado_configurando_se_recuerda_como_fallo() -> None:
    # Si no, cada búsqueda volvería a configurar la tienda desde cero y a pagar la IA (A1).
    navegador = NavegadorEnEspera(paginas_sitio(), lambda url: True)
    m = montar(nuevo_almacen(), navegador, {"configurador": []}, plazo_tienda_s=0.05)
    r = m.buscar(modo=Modo.AUTO)
    fallo = fallo_de(m)
    assert fallo is not None
    assert (fallo.intentos, fallo.reintentar_desde) == (1, AHORA + HORA)
    assert "Plazo" in fallo.motivo and "configurando" in fallo.motivo
    detalle = paso(r, "perfil").detalle
    assert "configurando" in detalle and "no se reintenta hasta el 19 oct 13:00" in detalle
    assert "fichas leídas" not in detalle
    navegador.soltar = None
    r2 = m.buscar(modo=Modo.AUTO)
    assert paso(r2, "perfil").estado is EstadoPaso.OMITIDO
    assert m.peticiones_ia == []


class _NavegadorLento(NavegadorFalso):
    """Tarda `configurar_s` en la primera petición y `ficha_s` en cada ficha de la búsqueda."""

    def __init__(self, seguimiento: SeguimientoFalso, configurar_s: float, ficha_s: float) -> None:
        super().__init__(paginas_sitio(), redirecciones={f"https://{DOMINIO}/": PORTADA})
        self.seguimiento = seguimiento
        self.configurar_s = configurar_s
        self.ficha_s = ficha_s

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        estados = self.seguimiento.de(DOMINIO)
        if estados and estados[-1] is EstadoTienda.CONFIGURANDO:
            await asyncio.sleep(self.configurar_s)
            self.configurar_s = 0.0
        elif "/p/" in url:
            await asyncio.sleep(self.ficha_s)
        return await super().obtener(url, js=js, grande=grande)


def test_el_plazo_de_la_busqueda_empieza_al_acabar_de_configurar() -> None:
    # R5-D: configurar una tienda nueva (A1) se comía casi todo el plazo y la búsqueda del
    # usuario perdía fichas. Configurar y buscar suman más que el plazo, pero cada fase cabe.
    seg = SeguimientoFalso()
    navegador = _NavegadorLento(seg, configurar_s=0.35, ficha_s=0.3 / len(PRODUCTOS))
    m = montar(
        nuevo_almacen(), navegador, {"configurador": [LISTADO_OK, CAMPOS_OK]}, plazo_tienda_s=0.5
    )
    r = _buscar_con(m, seg, modo=Modo.AUTO)
    assert paso(r, "perfil").estado is EstadoPaso.IA
    assert not [p for p in progreso(r).pasos if "Plazo" in p.detalle]
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert seg.de(DOMINIO)[-1] is EstadoTienda.HECHA


def test_cancelar_la_busqueda_avisa_cancelada_y_no_guarda_historico() -> None:
    navegador = NavegadorEnEspera(_sitio_doble(), lambda url: f"www.{OTRA}/p/" in url)
    m = _dos_tiendas(navegador)
    seg = SeguimientoFalso()

    async def escenario() -> None:
        consulta = ConsultaBusqueda(CONSULTA, modo=Modo.CLASICO)
        tarea = asyncio.create_task(m.orquestador.buscar(consulta, seguimiento=seg))
        await _hasta(lambda: any(p.progreso for p in seg.parciales))
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea

    asyncio.run(escenario())
    assert seg.de(DOMINIO)[-1] is EstadoTienda.HECHA
    assert seg.de(OTRA)[-1] is EstadoTienda.CANCELADA
    # Tras cancelar no llega ningún parcial más: el último es el de la tienda terminada.
    parcial = seg.parciales[-1]
    assert [t.tienda for t in parcial.progreso] == [DOMINIO]
    assert all(m.almacen.producto(g.id) is None for p in seg.parciales for g in p.grupos)
    assert parcial.grupos and m.almacen.busquedas() == []
    tienda = m.almacen.tienda(OTRA)
    assert tienda is not None and tienda.ultima_visita == AHORA


# --- Fichas bloqueadas tras el listado: el precio que ya trae el listado (R3-H) ----------------

ALC = "compraonline.alcampo.es"
ALC_CONSULTA = "aceite de oliva"
ALC_BUSQUEDA = f"https://www.{ALC}/search?q=aceite+de+oliva"
ALC_HTML = (REALES / "alcampo_busqueda_aceite_precios_listado.html").read_text(encoding="utf-8")
ALC_FICHAS = (
    ("producto-alcampo-aceite-de-oliva-suave-botella-1-l/19824", Decimal("3.69")),
    ("producto-alcampo-aceite-de-oliva-virgen-extra-botella-1-l/29834", Decimal("4.75")),
    ("producto-alcampo-aceite-de-oliva-suave-garrafa-de-5-l/19827", Decimal("17.22")),
    ("la-mas%C3%ADa-aceite-de-oliva-intenso-botella-1-l/19599", Decimal("4.27")),
)
ALC_URLS = [f"https://www.{ALC}/products/{ruta}" for ruta, _ in ALC_FICHAS]
DEL_LISTADO = "con el precio del listado"


def perfil_alcampo() -> Perfil:
    """El perfil que hizo el configurador en la campaña real (ronda 3)."""
    return perfil(
        dominio=ALC,
        url_busqueda=f"https://www.{ALC}/search?q={{consulta}}",
        sel_resultado='div[data-test^="fop-wrapper"]',
        sel_titulo='[data-test="fop-title"]',
        sel_enlace='a[data-test="fop-product-link"] @href',
        sel_precio_listado='[data-test="fop-price"]',
        sel_campos={"precio": 'div[data-test="price-container"] span._display_xy0eg_1'},
        necesita_js=False,
    )


def sitio_alcampo() -> dict[str, str]:
    """La búsqueda real y la 1.ª ficha (la única que se pudo abrir en la campaña)."""
    return {
        ALC_BUSQUEDA: ALC_HTML,
        ALC_URLS[0]: (
            "<html><head><title>PRODUCTO ALCAMPO Aceite de oliva suave botella 1 l.</title>"
            "</head><body><h1>PRODUCTO ALCAMPO Aceite de oliva suave botella 1 l.</h1>"
            '<div data-test="price-container"><span class="_display_xy0eg_1">3,69 €</span>'
            "</div></body></html>"
        ),
    }


def buscar_en_alcampo(**opciones: Any) -> tuple[Montaje, ResultadoBusqueda]:
    waf = Bloqueado(ALC_URLS[1], "Desafío anti-bot de AWS WAF.")
    navegador = NavegadorFalso(sitio_alcampo(), errores={ALC_URLS[1]: waf})
    m = montar(nuevo_almacen(perfil_alcampo(), tiendas=(ALC,)), navegador, **opciones)
    r = m.buscar(texto=ALC_CONSULTA, modo=Modo.CLASICO, tiendas=(ALC,), campos=("precio",))
    return m, r


def test_alcampo_bloqueo_en_la_2a_ficha_usa_los_precios_del_listado() -> None:
    # Campaña real (ronda 3): la 2.ª ficha da el desafío de AWS WAF (también con Chromium) y la
    # tienda se quedaba con 1 oferta, aunque el listado ya traía título, enlace y precio de las
    # demás en la página de la propia tienda.
    m, r = buscar_en_alcampo()
    # Ni una petición más: solo se usa lo que ya está descargado.
    assert m.navegador.urls == [ALC_BUSQUEDA, ALC_URLS[0], ALC_URLS[1]]
    por_url = {o.url: o for o in ofertas(r)}
    assert {u: o.precio for u, o in por_url.items()} == {
        url: importe for url, (_, importe) in zip(ALC_URLS, ALC_FICHAS, strict=True)
    }
    # La de la ficha, como siempre; las del listado, con menos confianza y solo el precio.
    de_la_ficha = por_url[ALC_URLS[0]].campos["precio"]
    assert de_la_ficha.origen is OrigenDato.PERFIL
    for url in ALC_URLS[1:]:
        precio = por_url[url].campos["precio"]
        assert precio.origen is OrigenDato.PERFIL
        assert precio.anclado and precio.confianza < de_la_ficha.confianza
        assert set(por_url[url].campos) == {"precio"}
    # «Precio17,22 €»: la etiqueta oculta va pegada al importe y el precio sigue anclado.
    assert por_url[ALC_URLS[2]].precio_anclado
    # El bloqueo se sigue contando: la tienda queda bloqueada y el paso de error sigue.
    bloqueo = paso(r, "extracción", ALC)
    assert bloqueo.estado is EstadoPaso.ERROR and bloqueo.detalle.startswith(BLOQUEO)
    listado = paso(r, "listado", ALC)
    assert listado.estado is EstadoPaso.AVISO
    assert listado.detalle.startswith(f"3 ofertas {DEL_LISTADO}")
    tienda = m.almacen.tienda(ALC)
    assert tienda is not None and tienda.bloqueos == 1


def test_alcampo_precios_del_listado_respetan_max_fichas() -> None:
    _, r = buscar_en_alcampo(max_fichas=3)
    assert sorted(o.url for o in ofertas(r)) == sorted(ALC_URLS[:3])
    assert paso(r, "listado", ALC).detalle.startswith(f"2 ofertas {DEL_LISTADO}")


def test_bloqueo_con_precio_en_el_listado_da_las_que_quedaban_por_abrir() -> None:
    bloqueo = Bloqueado(PRODUCTOS[1].url, "hCaptcha")
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[1].url: bloqueo})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert navegador.urls == [BUSQUEDA, PRODUCTOS[0].url, PRODUCTOS[1].url]
    assert sorted(o.url for o in ofertas(r)) == sorted(p.url for p in PRODUCTOS)
    assert paso(r, "extracción").estado is EstadoPaso.ERROR
    assert paso(r, "listado").detalle.startswith(f"3 ofertas {DEL_LISTADO}")


def test_bloqueo_sin_precio_en_el_listado_no_da_ofertas_del_listado() -> None:
    bloqueo = Bloqueado(PRODUCTOS[1].url, "hCaptcha")
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[1].url: bloqueo})
    m = montar(nuevo_almacen(perfil(sel_precio_listado=None)), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]
    assert not any(p.nombre == "listado" for p in progreso(r).pasos)


def test_ficha_que_falla_antes_del_bloqueo_no_sale_del_listado() -> None:
    # Una ficha 404 puede ser un producto retirado: su precio del listado no sirve. Solo las
    # que no se han podido abrir por el bloqueo (la bloqueada y las siguientes).
    sitio = paginas_sitio()
    del sitio[PRODUCTOS[1].url]
    bloqueo = Bloqueado(PRODUCTOS[2].url, "hCaptcha")
    navegador = NavegadorFalso(sitio, errores={PRODUCTOS[2].url: bloqueo})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert {o.url for o in ofertas(r)} == {PRODUCTOS[0].url, PRODUCTOS[2].url, PRODUCTOS[3].url}
    assert paso(r, "listado").detalle.startswith(f"2 ofertas {DEL_LISTADO}")


@pytest.mark.parametrize(
    "error", [ErrorDescarga("No se pudo conectar con la tienda."), URLNoPermitida("No.")]
)
def test_ficha_que_no_se_puede_abrir_sin_bloqueo_no_usa_el_listado(error: Exception) -> None:
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[1].url: error})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    assert PRODUCTOS[1].url not in {o.url for o in ofertas(r)}
    assert not any(p.nombre == "listado" for p in progreso(r).pasos)


def test_respaldo_oem_con_bloqueo_no_da_ofertas_del_listado() -> None:
    # Ningún título nombra la referencia: se confiaba en que la ficha la citara. Sin ficha no se
    # puede comprobar, y es justo donde se colaría una pieza ajena.
    bloqueo = Bloqueado(PRODUCTOS[1].url, "hCaptcha")
    sitio = sitio_con(PRODUCTOS, OEM)
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio, errores={PRODUCTOS[1].url: bloqueo}))
    r = m.buscar(texto=OEM, modo=Modo.CLASICO)
    assert m.navegador.urls == [busqueda_de(OEM), PRODUCTOS[0].url, PRODUCTOS[1].url]
    assert [o.url for o in ofertas(r)] == [PRODUCTOS[0].url]
    assert not any(p.nombre == "listado" for p in progreso(r).pasos)


def test_referencia_en_el_titulo_del_listado_entra_como_exacta() -> None:
    primero = replace(PRODUCTOS[0], ruta="/p/faro-a", titulo="Faro 5K1941005E Valeo")
    segundo = replace(PRODUCTOS[1], ruta="/p/faro-b", titulo="Faro izquierdo 5K1 941 005 E")
    bloqueo = Bloqueado(segundo.url, "hCaptcha")
    sitio = sitio_con([primero, segundo], OEM)
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(sitio, errores={segundo.url: bloqueo}))
    r = m.buscar(texto=OEM, modo=Modo.CLASICO)
    por_url = {o.url: o for o in ofertas(r)}
    assert set(por_url) == {primero.url, segundo.url}
    assert por_url[segundo.url].coincidencia is Coincidencia.EXACTA


def test_respaldo_por_texto_con_bloqueo_no_da_ofertas_del_listado() -> None:
    # Thomann: ningún título nombra la consulta y se abren los primeros para que el emparejado
    # con IA decida. Sin ficha, el listado no basta para saber qué es lo buscado.
    bloqueo = Bloqueado(THO_URLS[1], "Desafío de Cloudflare")
    navegador = NavegadorFalso(sitio_thomann(), errores={THO_URLS[1]: bloqueo})
    guion = {"emparejado": [_clasificaciones("misma_pieza")]}
    m = montar(nuevo_almacen(perfil_thomann(), tiendas=(THO,)), navegador, guion)
    r = m.buscar(texto=THO_CONSULTA, modo=Modo.IA, tiendas=(THO,), campos=("precio",))
    assert navegador.urls == [THO_BUSQUEDA, THO_URLS[0], THO_URLS[1]]
    assert {o.url for o in ofertas(r)} <= {THO_URLS[0]}
    assert not any(p.nombre == "listado" for p in progreso(r, THO).pasos)
