"""Orquestador: el flujo de una búsqueda (nota 14) con los modos de ADR-002, sin red.

La tienda es la de `tests.perfiles.sitio` (portada, buscador, listado y fichas falsas) y la IA,
el `ProveedorSimulado` detrás de un `ServicioIA` de verdad con el almacén como registro.
"""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen
from precia.ia.servicio import ServicioIA
from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import ErrorIA, IANoDisponible, PresupuestoAgotado
from precia.navegador.tipos import Bloqueado, ErrorDescarga, Pagina, ProhibidoPorRobots
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    EstadoPaso,
    EstadoPerfil,
    Modo,
    Paso,
    ProgresoTienda,
    ResultadoBusqueda,
)
from precia.nucleo.orquestador import Orquestador
from precia.perfiles.tipos import Perfil
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    LISTADO_OK,
    PORTADA,
    PRODUCTOS,
    TIENDA,
    NavegadorFalso,
    html_listado,
    paginas_sitio,
    perfil,
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


class NavegadorProhibido:
    """Todo lo prohíbe el robots.txt: el configurador no llega a ninguna página."""

    def __init__(self) -> None:
        self.pedidas: list[str] = []

    async def obtener(self, url: str, *, js: bool = False) -> Pagina:
        self.pedidas.append(url)
        raise ProhibidoPorRobots("El robots.txt de la tienda no permite visitar esa página.")


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

    async def obtener(self, url: str, *, js: bool = False) -> Pagina:
        if "/buscar" in url:
            self.en_vuelo += 1
            if self.en_vuelo == 2:
                self.ambas.set()
            await asyncio.wait_for(self.ambas.wait(), 2)
        return await super().obtener(url, js=js)


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
