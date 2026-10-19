"""FuenteReal: la `FuenteDatos` de verdad, sobre el almacén SQLite y el orquestador.

Sin red: los descargadores y el proveedor de IA se inyectan; el validador anti-SSRF usa un DNS
de mentira. La búsqueda completa contra una tienda falsa está en `tests/test_extremo_a_extremo.py`.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.ia.claude import ProveedorClaude
from precia.ia.ollama import ProveedorOllama
from precia.ia.simulado import ProveedorSimulado
from precia.ia.tipos import ProveedorIA
from precia.navegador.tipos import Descargador, Pagina, URLNoPermitida
from precia.nucleo.config import Config
from precia.nucleo.fuente_datos import (
    DominioNoValido,
    ErrorFuente,
    FuenteDatos,
    RevisionNoValida,
    TiendaNoExiste,
    TiendaYaExiste,
)
from precia.nucleo.fuente_real import FuenteReal, proveedor_desde_ajustes
from precia.nucleo.modelos import (
    Ajustes,
    ConsultaBusqueda,
    EstadoPerfil,
    LlamadaIA,
    Modo,
)
from tests.navegador.falsos import DescargadorFalso, validar_con_dns_publico
from tests.perfiles.sitio import DOMINIO, perfil

AHORA = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
CLAVE = "sk-ant-api03-CLAVE-DE-PRUEBA-que-nunca-debe-aparecer"


def configuracion(raiz: Path, clave: str | None = None) -> Config:
    return Config(
        raiz=raiz,
        ruta_bd=raiz / "datos" / "precia.sqlite3",
        ruta_cache=raiz / "cache",
        demo=False,
        clave_api=clave,
    )


class Fabrica:
    """Crea descargadores falsos y apunta cuántas veces se le pide un navegador nuevo."""

    def __init__(self) -> None:
        self.creados: list[tuple[DescargadorFalso, Ajustes]] = []

    def __call__(self, ajustes: Ajustes) -> tuple[Descargador, Descargador | None]:
        http = DescargadorFalso()
        self.creados.append((http, ajustes))
        return http, None


@pytest.fixture
def fabrica() -> Fabrica:
    return Fabrica()


@pytest.fixture
def fuente(tmp_path: Path, fabrica: Fabrica) -> Iterator[FuenteReal]:
    real = FuenteReal(
        configuracion(tmp_path),
        crear_descargadores=fabrica,
        crear_proveedor=lambda _: (None, "sin IA en el test"),
        validador=validar_con_dns_publico,
        reloj=lambda: AHORA,
    )
    yield real
    asyncio.run(real.cerrar())


def test_cumple_el_protocolo_y_no_es_demo(fuente: FuenteReal) -> None:
    datos: FuenteDatos = fuente
    assert datos.es_demo is False
    assert fuente.tiendas() == []
    assert fuente.evaluacion() == []


def test_crea_la_base_de_datos_en_la_ruta_de_la_configuracion(tmp_path: Path) -> None:
    real = FuenteReal(configuracion(tmp_path), crear_descargadores=Fabrica())
    asyncio.run(real.cerrar())
    assert (tmp_path / "datos" / "precia.sqlite3").is_file()


def test_el_navegador_se_crea_en_la_primera_busqueda_y_se_cierra(
    fuente: FuenteReal, fabrica: Fabrica
) -> None:
    assert fabrica.creados == []
    resultado = asyncio.run(fuente.buscar(ConsultaBusqueda("faro", modo=Modo.CLASICO)))
    assert not resultado.es_demo and resultado.grupos == ()
    asyncio.run(fuente.buscar(ConsultaBusqueda("faro", modo=Modo.CLASICO)))
    assert len(fabrica.creados) == 1
    http, ajustes = fabrica.creados[0]
    assert ajustes.user_agent == Ajustes().user_agent
    asyncio.run(fuente.cerrar())
    assert http.cerrado


def test_cambiar_el_user_agent_crea_un_navegador_nuevo(
    fuente: FuenteReal, fabrica: Fabrica
) -> None:
    asyncio.run(fuente.buscar(ConsultaBusqueda("faro", modo=Modo.CLASICO)))
    fuente.guardar_ajustes(replace(fuente.ajustes(), user_agent="PrecIA/0.2 (pruebas)"))
    asyncio.run(fuente.buscar(ConsultaBusqueda("faro", modo=Modo.CLASICO)))
    (viejo, _), (_, nuevos) = fabrica.creados
    assert viejo.cerrado
    assert nuevos.user_agent == "PrecIA/0.2 (pruebas)"


def test_sin_ia_lo_dice_en_la_busqueda(fuente: FuenteReal) -> None:
    resultado = asyncio.run(fuente.buscar(ConsultaBusqueda("faro", modo=Modo.AUTO)))
    (general,) = resultado.progreso
    assert general.pasos[0].detalle.startswith("IA no disponible (sin IA en el test)")


# --- Proveedor de IA según los ajustes ----------------------------------------------------------


def test_proveedor_api_con_clave(tmp_path: Path) -> None:
    proveedor, motivo = proveedor_desde_ajustes(
        configuracion(tmp_path, CLAVE), Ajustes(modelo_api="claude-haiku-5-5")
    )
    assert isinstance(proveedor, ProveedorClaude) and motivo == ""
    assert proveedor.modelo == "claude-haiku-5-5"
    assert CLAVE not in repr(proveedor)


def test_proveedor_api_sin_clave_no_hay_ia(tmp_path: Path) -> None:
    proveedor, motivo = proveedor_desde_ajustes(configuracion(tmp_path), Ajustes())
    assert proveedor is None
    assert "ANTHROPIC_API_KEY" in motivo


def test_proveedor_api_con_clave_no_valida_no_hay_ia(tmp_path: Path) -> None:
    proveedor, motivo = proveedor_desde_ajustes(
        configuracion(tmp_path, "clave con espacios"), Ajustes()
    )
    assert proveedor is None and motivo
    assert "clave con espacios" not in motivo


def test_modelo_sin_tarifa_no_hay_ia(tmp_path: Path) -> None:
    # Sin tarifa no se puede llevar la cuenta del presupuesto: mejor sin IA que sin límite.
    proveedor, motivo = proveedor_desde_ajustes(
        configuracion(tmp_path, CLAVE), Ajustes(modelo_api="claude-desconocido")
    )
    assert proveedor is None and "tarifa" in motivo
    assert CLAVE not in motivo


def test_proveedor_ollama_no_necesita_clave(tmp_path: Path) -> None:
    proveedor, _ = proveedor_desde_ajustes(
        configuracion(tmp_path), Ajustes(proveedor="ollama", modelo_local="qwen2.5:7b")
    )
    assert isinstance(proveedor, ProveedorOllama)
    assert proveedor.modelo == "qwen2.5:7b"


def test_presupuesto_y_tope_salen_de_los_ajustes(tmp_path: Path) -> None:
    proveedores: list[ProveedorIA] = []

    def crear(ajustes: Ajustes) -> tuple[ProveedorIA | None, str]:
        proveedor = ProveedorSimulado({})
        proveedores.append(proveedor)
        return proveedor, ""

    real = FuenteReal(
        configuracion(tmp_path),
        crear_descargadores=Fabrica(),
        crear_proveedor=crear,
        reloj=lambda: AHORA,
    )
    real.guardar_ajustes(replace(real.ajustes(), tope_llamadas_busqueda=0))
    try:
        resultado = asyncio.run(real.buscar(ConsultaBusqueda("faro", modo=Modo.IA)))
    finally:
        asyncio.run(real.cerrar())
    # Con IA no hay aviso de «IA no disponible»; el tope 0 no deja hacer ninguna llamada.
    assert len(proveedores) == 1
    assert resultado.progreso == () and resultado.llamadas_ia == 0


# --- Tiendas ------------------------------------------------------------------------------------


def test_alta_de_tienda_normaliza_y_valida(fuente: FuenteReal) -> None:
    tienda = asyncio.run(fuente.alta_tienda(" WWW.Recambios-Prueba.ES ", "Recambios Prueba"))
    assert (tienda.dominio, tienda.nombre, tienda.estado) == (
        DOMINIO,
        "Recambios Prueba",
        EstadoPerfil.SIN_PERFIL,
    )
    assert fuente.tiendas() == [tienda]
    assert fuente.tienda("www.recambios-prueba.es") == tienda
    assert fuente.tienda("otra.es#x") is None


def test_alta_sin_nombre_usa_el_dominio(fuente: FuenteReal) -> None:
    assert asyncio.run(fuente.alta_tienda(DOMINIO, "")).nombre == DOMINIO


def test_alta_duplicada(fuente: FuenteReal) -> None:
    asyncio.run(fuente.alta_tienda(DOMINIO, "Una"))
    with pytest.raises(TiendaYaExiste):
        asyncio.run(fuente.alta_tienda(f"www.{DOMINIO}", "Otra"))


@pytest.mark.parametrize("dominio", ["localhost", "10.0.0.1", "tienda.es/admin", "x.local"])
def test_alta_de_dominios_no_validos(fuente: FuenteReal, dominio: str) -> None:
    with pytest.raises(DominioNoValido):
        asyncio.run(fuente.alta_tienda(dominio, "x"))
    assert fuente.tiendas() == []


def test_alta_de_un_dominio_que_resuelve_a_una_ip_interna(tmp_path: Path) -> None:
    async def interno(url: str) -> str:
        raise URLNoPermitida("El servidor resuelve a una dirección IP no pública.")

    real = FuenteReal(configuracion(tmp_path), crear_descargadores=Fabrica(), validador=interno)
    try:
        with pytest.raises(DominioNoValido, match="no pública"):
            asyncio.run(real.alta_tienda("rebote.es", "Rebote"))
    finally:
        asyncio.run(real.cerrar())


def test_alta_con_nombre_demasiado_largo(fuente: FuenteReal) -> None:
    with pytest.raises(ErrorFuente):
        asyncio.run(fuente.alta_tienda(DOMINIO, "n" * 201))


def _con_perfil(fuente: FuenteReal, **cambios: object) -> None:
    asyncio.run(fuente.alta_tienda(DOMINIO, "Recambios Prueba"))
    almacen = fuente.almacen
    almacen.guardar_perfil(perfil(revisado=False, autor="🤖 configurador", **cambios))
    almacen.actualizar_tienda(DOMINIO, estado=EstadoPerfil.PENDIENTE_REVISION)


def test_revisar_el_perfil_pendiente_lo_fija(fuente: FuenteReal) -> None:
    _con_perfil(fuente)
    tienda = fuente.revisar_perfil(DOMINIO, 1)
    assert tienda.estado is EstadoPerfil.ACTIVO
    vigente = fuente.almacen.perfil_vigente(DOMINIO)
    assert vigente is not None and vigente.revisado


def test_revisar_un_perfil_del_reparador_lo_deja_reparado(fuente: FuenteReal) -> None:
    _con_perfil(fuente)
    fuente.almacen.guardar_perfil(perfil(revisado=False, autor="🤖 reparador"))
    assert fuente.revisar_perfil(DOMINIO, 2).estado is EstadoPerfil.REPARADO


def test_solo_se_revisa_la_version_actual(fuente: FuenteReal) -> None:
    _con_perfil(fuente)
    fuente.almacen.guardar_perfil(perfil(revisado=False))
    with pytest.raises(RevisionNoValida, match="v2"):
        fuente.revisar_perfil(DOMINIO, 1)


def test_revisar_dos_veces_o_sin_perfil(fuente: FuenteReal) -> None:
    asyncio.run(fuente.alta_tienda(DOMINIO, "Recambios Prueba"))
    with pytest.raises(RevisionNoValida):
        fuente.revisar_perfil(DOMINIO, 1)
    fuente.almacen.guardar_perfil(perfil(revisado=False))
    fuente.revisar_perfil(DOMINIO, 1)
    with pytest.raises(RevisionNoValida):
        fuente.revisar_perfil(DOMINIO, 1)
    with pytest.raises(TiendaNoExiste):
        fuente.revisar_perfil("no-existe.es", 1)


# --- Productos, alertas, IA y ajustes -----------------------------------------------------------


def test_producto_historico_y_alertas_desde_el_almacen(fuente: FuenteReal) -> None:
    assert fuente.producto("oem-x") is None and fuente.historico("oem-x") == []
    fuente.almacen.guardar_precio(
        "oem-5k1941005e",
        "Faro izquierdo",
        "5K1941005E",
        DOMINIO,
        f"https://www.{DOMINIO}/p/faro",
        Decimal("89.90"),
        AHORA,
        titulo="Faro delantero izquierdo Golf VI",
        criterio="Referencia OEM exacta",
    )
    grupo = fuente.producto("oem-5k1941005e")
    assert grupo is not None and grupo.ofertas[0].titulo == "Faro delantero izquierdo Golf VI"
    assert fuente.historico("oem-5k1941005e")[0].tienda == DOMINIO
    alerta = fuente.crear_alerta("oem-5k1941005e", Decimal("80"), "email")
    assert fuente.alertas() == [alerta]
    with pytest.raises(LookupError):
        fuente.crear_alerta("oem-x", Decimal("80"), "email")


def test_resumen_de_ia_del_mes_con_nombres_legibles(fuente: FuenteReal) -> None:
    def llamada(fecha: datetime, componente: str, resultado: str, coste: str) -> LlamadaIA:
        return LlamadaIA(
            fecha, componente, "api", "claude-haiku-5-5", 10, 5, Decimal(coste), resultado, 9
        )

    fuente.almacen.registrar_llamada(llamada(AHORA, "configurador", "ok", "0.01"))
    fuente.almacen.registrar_llamada(llamada(AHORA, "extractor", "salida_no_valida", "0.002"))
    fuente.almacen.registrar_llamada(
        llamada(datetime(2026, 9, 30, tzinfo=UTC), "reparador", "ok", "0.5")
    )
    resumen = fuente.resumen_ia()
    assert [c.componente for c in resumen.llamadas] == [
        "Extractor de respaldo",
        "Configurador de perfiles",
    ]
    assert resumen.llamadas[1].resultado.startswith("Válida")
    assert not resumen.llamadas[0].resultado.startswith("Válida")
    assert resumen.gasto_mes_usd == Decimal("0.012")
    assert resumen.presupuesto_mes_usd == Ajustes().presupuesto_mensual_usd
    assert resumen.por_componente == {"Extractor de respaldo": 1, "Configurador de perfiles": 1}


def test_los_ajustes_se_guardan_en_la_base_de_datos(tmp_path: Path) -> None:
    primera = FuenteReal(configuracion(tmp_path), crear_descargadores=Fabrica())
    primera.guardar_ajustes(replace(primera.ajustes(), tope_llamadas_busqueda=7))
    asyncio.run(primera.cerrar())
    segunda = FuenteReal(configuracion(tmp_path), crear_descargadores=Fabrica())
    try:
        assert segunda.ajustes().tope_llamadas_busqueda == 7
    finally:
        asyncio.run(segunda.cerrar())


class DescargadorEnEspera(DescargadorFalso):
    """Cada descarga espera a que el test la suelte: una búsqueda que sigue en vuelo."""

    def __init__(self) -> None:
        super().__init__()
        self.en_vuelo = asyncio.Event()
        self.soltar = asyncio.Event()
        self.usado_cerrado = False

    async def descargar(self, url: str) -> Pagina:
        self.en_vuelo.set()
        await self.soltar.wait()
        # Un descargador cerrado (Chromium parado) que se vuelve a usar arrancaría otro
        # Chromium que ya nadie cerraría.
        self.usado_cerrado |= self.cerrado
        return await super().descargar(url)


def test_cambiar_los_ajustes_no_cierra_el_navegador_de_una_busqueda_en_vuelo(
    tmp_path: Path,
) -> None:
    descargadores: list[DescargadorEnEspera] = []

    def crear(_: Ajustes) -> tuple[Descargador, Descargador | None]:
        descargadores.append(DescargadorEnEspera())
        return descargadores[-1], None

    real = FuenteReal(
        configuracion(tmp_path),
        crear_descargadores=crear,
        crear_proveedor=lambda _: (None, "sin IA en el test"),
        validador=validar_con_dns_publico,
        reloj=lambda: AHORA,
    )
    real.almacen.alta_tienda(DOMINIO, "Recambios")
    real.almacen.guardar_perfil(perfil())

    async def escenario() -> None:
        primera = asyncio.create_task(real.buscar(ConsultaBusqueda("faro", modo=Modo.CLASICO)))
        await asyncio.wait_for(_esperar(descargadores, 1), 2)
        viejo = descargadores[0]
        await asyncio.wait_for(viejo.en_vuelo.wait(), 2)
        real.guardar_ajustes(replace(real.ajustes(), user_agent="PrecIA/0.2 (pruebas)"))
        segunda = asyncio.create_task(real.buscar(ConsultaBusqueda("faro", modo=Modo.CLASICO)))
        await asyncio.wait_for(_esperar(descargadores, 2), 2)
        descargadores[1].soltar.set()
        await asyncio.wait_for(segunda, 2)
        assert not viejo.cerrado, "se cerró el navegador que usaba la primera búsqueda"
        viejo.soltar.set()
        await asyncio.wait_for(primera, 2)
        assert not viejo.usado_cerrado
        # Al terminar la búsqueda que lo usaba, el navegador viejo sí se cierra.
        assert viejo.cerrado
        await real.cerrar()
        assert descargadores[1].cerrado

    asyncio.run(escenario())


async def _esperar(descargadores: list[DescargadorEnEspera], cuantos: int) -> None:
    while len(descargadores) < cuantos:
        await asyncio.sleep(0)
