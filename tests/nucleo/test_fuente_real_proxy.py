"""FuenteReal con proxy: credenciales de Evomi una sola vez, envoltorio y estado para la web.

Sin red: la API de Evomi se sustituye por una función falsa y no se da de alta ninguna tienda, así
que la búsqueda crea el navegador pero no descarga nada (Chromium no llega a arrancar).
"""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from precia.navegador.chromium import DescargadorChromium
from precia.navegador.con_proxy import DescargadorConProxy
from precia.navegador.http import DescargadorHTTP, TransporteURLLib
from precia.navegador.proxy import ErrorProxy, ModoProxy, Proxy
from precia.nucleo.config import Config
from precia.nucleo.fuente_datos import FuenteDatos
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import Ajustes, ConsultaBusqueda, EstadoProxy, Modo
from tests.navegador.falsos import DescargadorFalso, validar_con_dns_publico

AHORA = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
CLAVE_EVOMI = "clave-evomi-de-prueba-que-nunca-debe-aparecer"
SECRETO = "contrasena-proxy-secreta"
PROXY = Proxy("core-residential.evomi.com", 1000, "usu", f"{SECRETO}_country-ES", nombre="Evomi")


def configuracion(raiz: Path, **proxy: Any) -> Config:
    return Config(
        raiz=raiz,
        ruta_bd=raiz / "datos" / "precia.sqlite3",
        ruta_cache=raiz / "cache",
        demo=False,
        **proxy,
    )


class EvomiFalso:
    def __init__(self, resultado: Proxy | BaseException = PROXY) -> None:
        self.resultado = resultado
        self.llamadas: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, clave: str, **opciones: Any) -> Proxy:
        self.llamadas.append((clave, opciones))
        if isinstance(self.resultado, BaseException):
            raise self.resultado
        return self.resultado


def fuente(raiz: Path, evomi: EvomiFalso, **proxy: Any) -> FuenteReal:
    return FuenteReal(
        configuracion(raiz, **proxy),
        credenciales_evomi=evomi,
        crear_proveedor=lambda _: (None, "sin IA en el test"),
        validador=validar_con_dns_publico,
        reloj=lambda: AHORA,
    )


def buscar(real: FuenteReal) -> None:
    asyncio.run(real.buscar(ConsultaBusqueda("faro", modo=Modo.CLASICO)))


def _http_del_navegador(real: FuenteReal) -> object:
    navegador = real._navegador
    assert navegador is not None
    return navegador._http


def test_sin_proxy_configurado(tmp_path: Path) -> None:
    evomi = EvomiFalso()
    real = fuente(tmp_path, evomi)
    try:
        buscar(real)
        estado = real.estado_proxy()
        assert estado == EstadoProxy(modo="bloqueo")
        assert not estado.configurado and not estado.activo
        assert evomi.llamadas == []
        assert isinstance(_http_del_navegador(real), DescargadorHTTP)
    finally:
        asyncio.run(real.cerrar())


def test_evomi_se_pide_una_vez_al_crear_el_navegador(tmp_path: Path) -> None:
    evomi = EvomiFalso()
    real = fuente(tmp_path, evomi, clave_evomi=CLAVE_EVOMI, evomi_producto="rpc", proxy_pais="PT")
    try:
        assert real.estado_proxy().pendiente
        assert evomi.llamadas == []  # perezoso: nada hasta la primera búsqueda
        buscar(real)
        buscar(real)
        # Unos ajustes nuevos crean otro navegador, pero no vuelven a pedir las credenciales.
        real.guardar_ajustes(replace(real.ajustes(), user_agent="PrecIA/0.2 (pruebas)"))
        buscar(real)
        assert len(evomi.llamadas) == 1
        clave, opciones = evomi.llamadas[0]
        assert clave == CLAVE_EVOMI
        assert opciones == {"user_agent": Ajustes().user_agent, "producto": "rpc", "pais": "PT"}
        envoltorio = _http_del_navegador(real)
        assert isinstance(envoltorio, DescargadorConProxy)
        navegador = real._navegador
        assert navegador is not None
        # El robots.txt también va por el envoltorio, y Chromium tiene el suyo.
        assert navegador._robots._descargador is envoltorio
        assert isinstance(navegador._chromium, DescargadorConProxy)
        estado = real.estado_proxy()
        assert estado.configurado and estado.activo and not estado.pendiente
        assert (estado.nombre, estado.pais, estado.modo, estado.error) == (
            "Evomi",
            "PT",
            "bloqueo",
            "",
        )
        assert (estado.peticiones, estado.bytes, estado.dominios) == (0, 0, ())
        assert SECRETO not in repr(estado) and CLAVE_EVOMI not in repr(estado)
    finally:
        asyncio.run(real.cerrar())


def test_si_falla_evomi_se_sigue_sin_proxy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    error = ErrorProxy("La API de Evomi rechazó la clave (HTTP 401): revisa EVOMI_API_KEY.")
    evomi = EvomiFalso(error)
    real = fuente(tmp_path, evomi, clave_evomi=CLAVE_EVOMI)
    try:
        with caplog.at_level(logging.WARNING):
            buscar(real)
            buscar(real)
        assert len(evomi.llamadas) == 1
        assert isinstance(_http_del_navegador(real), DescargadorHTTP)
        estado = real.estado_proxy()
        assert estado.configurado and not estado.activo
        assert "HTTP 401" in estado.error
        assert "401" in caplog.text and CLAVE_EVOMI not in caplog.text
    finally:
        asyncio.run(real.cerrar())


def test_precia_proxy_manda_sobre_evomi(tmp_path: Path) -> None:
    evomi = EvomiFalso()
    url = f"http://usu:{SECRETO}@proxy.example:8080"
    real = fuente(tmp_path, evomi, proxy_url=url, clave_evomi=CLAVE_EVOMI)
    try:
        buscar(real)
        assert evomi.llamadas == []
        estado = real.estado_proxy()
        assert estado.activo and estado.nombre == "proxy propio" and estado.pais == ""
        assert SECRETO not in repr(estado)
    finally:
        asyncio.run(real.cerrar())


def test_precia_proxy_no_valida_se_sigue_sin_proxy(tmp_path: Path) -> None:
    real = fuente(tmp_path, EvomiFalso(), proxy_url=f"socks5://usu:{SECRETO}@proxy.example:1080")
    try:
        buscar(real)
        estado = real.estado_proxy()
        assert not estado.activo and "PRECIA_PROXY" in estado.error
        assert SECRETO not in estado.error and "proxy.example" not in estado.error
    finally:
        asyncio.run(real.cerrar())


def test_modo_nunca_no_pide_credenciales(tmp_path: Path) -> None:
    evomi = EvomiFalso()
    real = fuente(tmp_path, evomi, clave_evomi=CLAVE_EVOMI, proxy_modo=ModoProxy.NUNCA)
    try:
        buscar(real)
        assert evomi.llamadas == []
        estado = real.estado_proxy()
        assert estado.configurado and not estado.activo and estado.modo == "nunca"
        assert not estado.pendiente
    finally:
        asyncio.run(real.cerrar())


def test_con_descargadores_inyectados_no_hay_proxy(tmp_path: Path) -> None:
    evomi = EvomiFalso()
    real = FuenteReal(
        configuracion(tmp_path, clave_evomi=CLAVE_EVOMI),
        crear_descargadores=lambda _: (DescargadorFalso(), None),
        credenciales_evomi=evomi,
        crear_proveedor=lambda _: (None, "sin IA en el test"),
        validador=validar_con_dns_publico,
    )
    try:
        buscar(real)
        assert evomi.llamadas == []
    finally:
        asyncio.run(real.cerrar())


def test_el_uso_del_proxy_sale_en_el_estado(tmp_path: Path) -> None:
    real = fuente(tmp_path, EvomiFalso(), clave_evomi=CLAVE_EVOMI)
    try:
        buscar(real)
        envoltorio = _http_del_navegador(real)
        assert isinstance(envoltorio, DescargadorConProxy)
        real._uso_proxy.peticiones = 4
        real._uso_proxy.bytes = 2 * 1024 * 1024
        real._uso_proxy.dominios.update({"ecooparts.example", "b-parts.example"})
        estado = real.estado_proxy()
        assert estado.peticiones == 4 and estado.mb == pytest.approx(2.0)
        assert estado.dominios == ("b-parts.example", "ecooparts.example")
    finally:
        asyncio.run(real.cerrar())


def test_la_demo_no_tiene_proxy() -> None:
    datos: FuenteDatos = FuenteDemo()
    estado = datos.estado_proxy()
    assert not estado.configurado and not estado.activo


@pytest.mark.parametrize("modo", [ModoProxy.BLOQUEO, ModoProxy.SIEMPRE])
def test_cableado_de_los_descargadores_con_proxy(tmp_path: Path, modo: ModoProxy) -> None:
    # Lo que sale por el «proxy» tiene que salir de verdad por él, y lo directo, sin él.
    real = fuente(tmp_path, EvomiFalso(), clave_evomi=CLAVE_EVOMI, proxy_modo=modo)
    try:
        buscar(real)
        navegador = real._navegador
        assert navegador is not None
        http, chromium = navegador._http, navegador._chromium
        assert isinstance(http, DescargadorConProxy) and isinstance(chromium, DescargadorConProxy)
        for envoltorio in (http, chromium):
            assert envoltorio.modo is modo
            assert envoltorio.espera_reintento_s == Ajustes().retardo_dominio_s
        assert isinstance(http.directo, DescargadorHTTP)
        assert isinstance(http.por_proxy, DescargadorHTTP)
        directo_http, proxy_http = http.directo.transporte, http.por_proxy.transporte
        assert isinstance(directo_http, TransporteURLLib) and directo_http.proxy is None
        assert isinstance(proxy_http, TransporteURLLib) and proxy_http.proxy is PROXY
        assert isinstance(chromium.directo, DescargadorChromium)
        assert isinstance(chromium.por_proxy, DescargadorChromium)
        assert chromium.directo.proxy is None and chromium.por_proxy.proxy is PROXY
    finally:
        asyncio.run(real.cerrar())


def test_precia_proxy_con_caracteres_raros_no_filtra_la_contrasena(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url = f"http://usu:{SECRETO}\uff03x@proxy.example:8080"
    real = fuente(tmp_path, EvomiFalso(), proxy_url=url)
    try:
        with caplog.at_level(logging.WARNING):
            buscar(real)
        estado = real.estado_proxy()
        assert not estado.activo and "PRECIA_PROXY" in estado.error
        assert SECRETO not in estado.error and SECRETO not in caplog.text
    finally:
        asyncio.run(real.cerrar())


def test_error_inesperado_al_pedir_evomi_no_rompe_la_busqueda(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Un `UnicodeEncodeError` (User-Agent con «€») o cualquier otro fallo que no sea `ErrorProxy`:
    # se sigue sin proxy y «Ajustes» dice por qué, sin citar el error (podría llevar la clave).
    fallo = UnicodeEncodeError("latin-1", CLAVE_EVOMI, 0, 1, "ordinal not in range(256)")
    evomi = EvomiFalso(fallo)
    real = fuente(tmp_path, evomi, clave_evomi=CLAVE_EVOMI)
    try:
        with caplog.at_level(logging.WARNING):
            buscar(real)
        assert isinstance(_http_del_navegador(real), DescargadorHTTP)
        estado = real.estado_proxy()
        assert estado.configurado and not estado.activo and not estado.pendiente
        assert estado.error and CLAVE_EVOMI not in estado.error
        assert CLAVE_EVOMI not in caplog.text
    finally:
        asyncio.run(real.cerrar())


def test_si_se_cancela_la_primera_busqueda_se_vuelve_a_intentar(tmp_path: Path) -> None:
    evomi = EvomiFalso(asyncio.CancelledError())
    real = fuente(tmp_path, evomi, clave_evomi=CLAVE_EVOMI)
    try:
        with pytest.raises(asyncio.CancelledError):
            buscar(real)
        assert real.estado_proxy().pendiente
        evomi.resultado = PROXY
        buscar(real)
        assert len(evomi.llamadas) == 2
        assert real.estado_proxy().activo
    finally:
        asyncio.run(real.cerrar())
