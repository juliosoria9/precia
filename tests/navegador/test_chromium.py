"""Descargador con Chromium: red propia de Chrome detrás del relé SOCKS5, que valida cada conexión.

Los primeros tests prueban el filtro de `route` con rutas falsas, las funciones de la huella y el
arranque con un Playwright falso. Los marcados `chromium` abren el headless shell de Playwright
contra un servidor HTTP en 127.0.0.1 levantado por el propio test. Chromium nunca resuelve nombres:
todo sale por el relé, que en los tests «resuelve» cualquier nombre a 127.0.0.1 y valida con el
mismo validador que el descargador (solo deja pasar `tienda.example`, `localhost` y 127.0.0.1 en el
puerto del servidor). `interno.example` hace de servicio interno. Ningún test sale a Internet: lo
comprueba `test_sin_trafico_fuera_del_rele` con el netlog de Chromium.
"""

import asyncio
import contextlib
import http.server
import json
import re
import socket
import struct
import threading
import time
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Error as ErrorPlaywright

from precia.navegador import chromium as modulo_chromium
from precia.navegador.chromium import (
    ARGUMENTOS,
    DescargadorChromium,
    FiltroPeticiones,
    metadatos_de_chrome,
    ua_de_chrome,
)
from precia.navegador.proxy import Proxy
from precia.navegador.rele import ReleProxy
from precia.navegador.tipos import ConexionCortada, ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import Validador
from tests.navegador.servidores_proxy import proxy_falso, tienda

UA_HEADLESS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/156.0.8078.4 Safari/537.36"
)


# --- Filtro de peticiones con dobles ------------------------------------------------------------


@dataclass(frozen=True)
class MarcoFalso:
    parent_frame: "MarcoFalso | None" = None


PRINCIPAL = MarcoFalso()
IFRAME = MarcoFalso(parent_frame=PRINCIPAL)


@dataclass
class PeticionFalsa:
    url: str
    resource_type: str = "document"
    navegacion: bool = False
    frame: MarcoFalso = PRINCIPAL

    def is_navigation_request(self) -> bool:
        return self.navegacion


@dataclass
class RutaFalsa:
    """Solo `abort` y `continue_`: si el filtro llamara a `fetch` o `fulfill`, AttributeError."""

    request: PeticionFalsa
    acciones: list[str] = field(default_factory=list)

    async def abort(self, error_code: str | None = None) -> None:
        self.acciones.append(f"abort:{error_code}")

    async def continue_(self) -> None:
        self.acciones.append("continue")


async def solo_tienda(url: str) -> str:
    partes = urlsplit(url)
    if partes.scheme != "https" or partes.hostname != "tienda.example":
        raise URLNoPermitida("No permitida en el test.")
    return url


def filtrar(ruta: RutaFalsa) -> FiltroPeticiones:
    filtro = FiltroPeticiones(solo_tienda)
    asyncio.run(filtro(ruta))
    return filtro


@pytest.mark.parametrize("tipo", ["image", "font", "media"])
def test_bloquea_imagenes_fuentes_y_medios(tipo: str) -> None:
    ruta = RutaFalsa(PeticionFalsa("https://tienda.example/foto.jpg", resource_type=tipo))
    filtrar(ruta)
    assert ruta.acciones == ["abort:blockedbyclient"]


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:6379/", "file:///etc/passwd"],
)
def test_aborta_urls_no_validas(url: str) -> None:
    ruta = RutaFalsa(PeticionFalsa(url, resource_type="fetch"))
    filtro = filtrar(ruta)
    assert ruta.acciones == ["abort:blockedbyclient"]
    assert filtro.bloqueo_principal is None


@pytest.mark.parametrize("tipo", ["document", "script", "xhr", "fetch", "stylesheet"])
def test_deja_pasar_lo_valido(tipo: str) -> None:
    ruta = RutaFalsa(PeticionFalsa("https://tienda.example/app", resource_type=tipo))
    filtrar(ruta)
    assert ruta.acciones == ["continue"]


def test_recuerda_el_bloqueo_de_la_navegacion_principal() -> None:
    ruta = RutaFalsa(PeticionFalsa("http://127.0.0.1/", navegacion=True))
    filtro = filtrar(ruta)
    assert ruta.acciones == ["abort:blockedbyclient"]
    assert isinstance(filtro.bloqueo_principal, URLNoPermitida)


def test_navegacion_de_un_iframe_no_es_la_principal() -> None:
    ruta = RutaFalsa(PeticionFalsa("http://127.0.0.1/", navegacion=True, frame=IFRAME))
    filtro = filtrar(ruta)
    assert ruta.acciones == ["abort:blockedbyclient"]
    assert filtro.bloqueo_principal is None


# --- Huella de Chrome sin «HeadlessChrome» ------------------------------------------------------


def test_ua_de_chrome() -> None:
    assert ua_de_chrome(UA_HEADLESS) == (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    )
    chrome = UA_HEADLESS.replace("HeadlessChrome/156.0.8078.4", "Chrome/156.0.0.0")
    assert ua_de_chrome(chrome) == chrome


VALORES_HEADLESS: dict[str, Any] = {
    "architecture": "x86",
    "bitness": "64",
    "brands": [
        {"brand": "Not:A-Brand", "version": "8"},
        {"brand": "Chromium", "version": "156"},
        {"brand": "HeadlessChrome", "version": "156"},
    ],
    "fullVersionList": [
        {"brand": "Not:A-Brand", "version": "8.0.0.0"},
        {"brand": "Chromium", "version": "156.0.8078.4"},
        {"brand": "HeadlessChrome", "version": "156.0.8078.4"},
    ],
    "mobile": False,
    "model": "",
    "platform": "Windows",
    "platformVersion": "19.0.0",
    "uaFullVersion": "156.0.8078.4",
    "wow64": False,
}


def test_metadatos_sin_headless() -> None:
    metadatos = metadatos_de_chrome(VALORES_HEADLESS)
    assert metadatos["brands"] == [
        {"brand": "Not:A-Brand", "version": "8"},
        {"brand": "Chromium", "version": "156"},
    ]
    assert metadatos["fullVersionList"] == [
        {"brand": "Not:A-Brand", "version": "8.0.0.0"},
        {"brand": "Chromium", "version": "156.0.8078.4"},
    ]
    assert "HeadlessChrome" not in json.dumps(metadatos)
    assert metadatos["platform"] == "Windows"
    assert metadatos["platformVersion"] == "19.0.0"
    assert metadatos["architecture"] == "x86"
    assert metadatos["bitness"] == "64"
    assert metadatos["model"] == ""
    assert metadatos["mobile"] is False
    assert metadatos["wow64"] is False
    assert metadatos["fullVersion"] == "156.0.8078.4"


# --- Arranque (Playwright falso) ----------------------------------------------------------------


class _SesionCDP:
    async def send(self, metodo: str, parametros: object = None) -> dict[str, object]:
        assert metodo == "Browser.getVersion"
        return {"userAgent": UA_HEADLESS}

    async def detach(self) -> None:
        pass


class _NavegadorArrancado:
    def __init__(self) -> None:
        self.cerrado = False
        self.contextos: list[dict[str, object]] = []

    async def new_browser_cdp_session(self) -> _SesionCDP:
        return _SesionCDP()

    async def new_context(self, **opciones: object) -> object:
        self.contextos.append(opciones)
        raise RuntimeError("el test para aquí")

    async def close(self) -> None:
        self.cerrado = True


class _PlaywrightFalso:
    def __init__(self, fallo: Exception | None = None) -> None:
        self.lanzamientos: list[dict[str, object]] = []
        self.navegadores: list[_NavegadorArrancado] = []
        self.chromium = self
        self.fallo = fallo
        self.parado = False

    async def start(self) -> "_PlaywrightFalso":
        return self

    async def launch(self, **opciones: object) -> _NavegadorArrancado:
        self.lanzamientos.append(opciones)
        if self.fallo is not None:
            raise self.fallo
        self.navegadores.append(_NavegadorArrancado())
        return self.navegadores[-1]

    async def stop(self) -> None:
        self.parado = True


async def permisivo(url: str) -> str:
    return url


def _lanzar(
    monkeypatch: pytest.MonkeyPatch,
    proxy: Proxy | None,
    *,
    falso: _PlaywrightFalso | None = None,
    crear_rele: Callable[[Proxy | None], ReleProxy] | None = None,
    error: type[Exception] = RuntimeError,
) -> _PlaywrightFalso:
    falso = falso or _PlaywrightFalso()
    monkeypatch.setattr("precia.navegador.chromium.async_playwright", lambda: falso)
    descargador = DescargadorChromium(validador=permisivo, proxy=proxy, crear_rele=crear_rele)

    async def probar() -> None:
        with pytest.raises(error):
            await descargador.descargar("https://tienda.example/")
        await descargador.cerrar()

    asyncio.run(probar())
    assert len(falso.lanzamientos) == 1
    return falso


def test_arranca_siempre_contra_el_rele_socks(monkeypatch: pytest.MonkeyPatch) -> None:
    opciones = _lanzar(monkeypatch, None).lanzamientos[0]
    assert opciones.keys() == {"headless", "proxy", "args"}  # sin `channel`: headless shell
    assert opciones["headless"] is True
    ajustes = opciones["proxy"]
    assert isinstance(ajustes, dict) and ajustes.keys() == {"server", "bypass"}
    assert re.fullmatch(r"socks5://127\.0\.0\.1:\d+", ajustes["server"])
    # Sin esto, PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK sacaría el loopback directo.
    assert ajustes["bypass"] == "<-loopback>"
    assert opciones["args"] == [
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-quic",
    ]
    assert list(ARGUMENTOS) == opciones["args"]


def test_con_proxy_arranca_contra_el_rele_sin_credenciales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Playwright usaría el usuario y la contraseña del proxy para contestar a cualquier 401 Basic
    # de un destino: Chromium va al relé local, que es quien las añade en el CONNECT.
    proxy = Proxy("core-residential.evomi.com", 1000, "usu", "secreta_country-ES")
    opciones = _lanzar(monkeypatch, proxy).lanzamientos[0]
    assert opciones.keys() == {"headless", "proxy", "args"}
    ajustes = opciones["proxy"]
    assert isinstance(ajustes, dict) and ajustes.keys() == {"server", "bypass"}
    assert ajustes["server"].startswith("socks5://127.0.0.1:")
    assert "secreta" not in repr(opciones) and "usu" not in repr(opciones)


@pytest.mark.parametrize("con_proxy", [False, True])
def test_el_rele_recibe_el_proxy_y_el_mismo_validador(
    monkeypatch: pytest.MonkeyPatch, con_proxy: bool
) -> None:
    proxy = Proxy("core-residential.evomi.com", 1000, "usu", "secreta") if con_proxy else None
    descargador = DescargadorChromium(validador=permisivo, proxy=proxy)
    rele = descargador._crear_rele(proxy)
    assert rele._validador is permisivo
    assert rele._proxy is proxy
    pedidos: list[Proxy | None] = []

    def crear(p: Proxy | None) -> ReleProxy:
        pedidos.append(p)
        return ReleProxy(p, validador=permisivo)

    _lanzar(monkeypatch, proxy, crear_rele=crear)
    assert pedidos == [proxy]


def test_new_context_sin_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    # `new_context(user_agent=...)` deja «HeadlessChrome» en las marcas y cambia platformVersion:
    # la huella se pone por CDP en cada página.
    falso = _lanzar(monkeypatch, None)
    contextos = falso.navegadores[0].contextos
    assert contextos and all("user_agent" not in opciones for opciones in contextos)


def test_si_chromium_no_arranca_se_cierra_el_rele(monkeypatch: pytest.MonkeyPatch) -> None:
    reles: list[ReleProxy] = []

    def crear(p: Proxy | None) -> ReleProxy:
        reles.append(ReleProxy(p, validador=permisivo))
        return reles[-1]

    falso = _PlaywrightFalso(fallo=ErrorPlaywright("Executable doesn't exist at C:\\x\nmás"))
    _lanzar(monkeypatch, None, falso=falso, crear_rele=crear, error=ErrorDescarga)
    assert falso.parado
    assert reles and reles[0]._servidor is None


def test_si_falla_la_lectura_de_la_huella_se_cierra_todo(monkeypatch: pytest.MonkeyPatch) -> None:
    reles: list[ReleProxy] = []

    def crear(p: Proxy | None) -> ReleProxy:
        reles.append(ReleProxy(p, validador=permisivo))
        return reles[-1]

    falso = _lanzar(monkeypatch, None, crear_rele=crear)
    assert falso.navegadores[0].cerrado and falso.parado
    assert reles[0]._servidor is None


class _ContextoQueFalla:
    """Contexto de Playwright cuya sesión CDP falla (Chromium murió a mitad)."""

    def __init__(self) -> None:
        self.cerrado = False

    def set_default_timeout(self, _: float) -> None:
        pass

    async def route(self, *_: object) -> None:
        pass

    async def route_web_socket(self, *_: object) -> None:
        pass

    async def new_page(self) -> object:
        return object()

    async def new_cdp_session(self, _: object) -> "_ContextoQueFalla":
        return self

    async def send(self, *_: object) -> None:
        raise ErrorPlaywright("Target page, context or browser has been closed")

    async def close(self) -> None:
        self.cerrado = True
        raise ErrorPlaywright("Target page, context or browser has been closed")


class _NavegadorMuerto(_NavegadorArrancado):
    """Navegador cuyo proceso murió: `new_context` falla con el error en bruto de Playwright."""

    def __init__(self, *, conectado: bool = False, contexto: object | None = None) -> None:
        super().__init__()
        self.conectado = conectado
        self.contexto = contexto

    def is_connected(self) -> bool:
        return self.conectado

    async def new_context(self, **opciones: object) -> object:
        self.contextos.append(opciones)
        if self.contexto is not None:
            return self.contexto
        raise ErrorPlaywright(
            "Browser.new_context: Target page, context or browser has been closed."
        )


class _PlaywrightConMuertos(_PlaywrightFalso):
    def __init__(self, crear: Callable[[], _NavegadorArrancado]) -> None:
        super().__init__()
        self.crear = crear
        self.paradas = 0

    async def launch(self, **opciones: object) -> _NavegadorArrancado:
        self.lanzamientos.append(opciones)
        self.navegadores.append(self.crear())
        return self.navegadores[-1]

    async def stop(self) -> None:
        self.paradas += 1


async def _sin_huella(_: object, __: object) -> dict[str, Any]:
    return {}


def _descargar_dos_veces(
    monkeypatch: pytest.MonkeyPatch, falso: _PlaywrightConMuertos
) -> tuple[list[BaseException], list[ReleProxy]]:
    monkeypatch.setattr("precia.navegador.chromium.async_playwright", lambda: falso)
    monkeypatch.setattr(DescargadorChromium, "_leer_huella", _sin_huella)
    reles: list[ReleProxy] = []

    def crear(p: Proxy | None) -> ReleProxy:
        reles.append(ReleProxy(p, validador=permisivo))
        return reles[-1]

    descargador = DescargadorChromium(validador=permisivo, crear_rele=crear)
    errores: list[BaseException] = []

    async def probar() -> None:
        for _ in range(2):
            try:
                await descargador.descargar("https://tienda.example/")
            except Exception as error:
                errores.append(error)
        await descargador.cerrar()

    asyncio.run(probar())
    return errores, reles


def test_errores_de_playwright_al_preparar_la_pagina_son_error_descarga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Antes, `new_context` (fuera de todo try) y la sesión CDP lanzaban `playwright.Error` en
    # bruto: el rescate no lo capturaba y la tienda acababa como error INESPERADO.
    contexto = _ContextoQueFalla()
    falso = _PlaywrightConMuertos(lambda: _NavegadorMuerto(conectado=True, contexto=contexto))
    errores, _ = _descargar_dos_veces(monkeypatch, falso)
    assert [type(error) for error in errores] == [ErrorDescarga, ErrorDescarga]
    assert contexto.cerrado
    assert len(falso.lanzamientos) == 1  # sigue conectado: no se relanza


def test_navegador_muerto_se_relanza(monkeypatch: pytest.MonkeyPatch) -> None:
    falso = _PlaywrightConMuertos(lambda: _NavegadorMuerto(conectado=False))
    errores, reles = _descargar_dos_veces(monkeypatch, falso)
    assert [type(error) for error in errores] == [ErrorDescarga, ErrorDescarga]
    # El segundo intento no reutiliza el navegador muerto: arranca otro (y suelta el anterior).
    assert len(falso.lanzamientos) == 2
    assert len(reles) == 2 and reles[0]._servidor is None
    assert falso.paradas >= 1


# --- Chromium real contra un servidor local ------------------------------------------------------

PORTADA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Pastillas de freno | Tienda local</title>
<link rel="stylesheet" href="/estilo.css">
<style>@font-face { font-family: Marca; src: url(/fuente.woff2) format("woff2"); }</style>
</head><body>
<h1>Pastillas de freno Brembo P85075</h1>
<img src="/foto.jpg" alt="Pastillas">
<video src="/video.mp4"></video>
<div id="precio"></div>
<script>
  fetch("http://interno.example:PUERTO/secreto").catch(() => {});
  fetch("/redirige-fuera").catch(() => {});
  new Image().src = "/pixel.gif";
  document.getElementById("precio").textContent = "Precio renderizado: 38,90 €";
</script>
</body></html>"""

# Una tienda hostil (o rota): tras `domcontentloaded` el hilo principal de la página se queda
# en un bucle infinito y Chromium ya no contesta a `page.content()`.
BUCLE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Oferta</title></head><body>
<h1>Kit de embrague</h1>
<script>
  addEventListener("DOMContentLoaded", () => setTimeout(() => { for (;;) {} }, 10));
</script>
</body></html>"""

HINTS = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Huella</title></head><body>
<pre id="ua"></pre><pre id="marcas"></pre>
<iframe src="http://127.0.0.1:PUERTO/marco"></iframe>
<script>
  document.getElementById("ua").textContent = navigator.userAgent;
  document.getElementById("marcas").textContent = JSON.stringify(navigator.userAgentData.brands);
  fetch("/xhr").catch(() => {});
</script>
</body></html>"""

WEBRTC = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>WebRTC</title></head><body>
<p id="estado">empezando</p>
<script>
  const pc = new RTCPeerConnection({iceServers: [{urls: "stun:127.0.0.1:STUN"}]});
  pc.createDataChannel("x");
  pc.onicegatheringstatechange = () => {
    document.getElementById("estado").textContent = "ice:" + pc.iceGatheringState;
  };
  pc.createOffer().then((oferta) => pc.setLocalDescription(oferta))
    .then(() => { document.getElementById("estado").textContent = "oferta-lista"; });
</script>
</body></html>"""


@dataclass
class Visita:
    host: str
    ruta: str
    nombres: list[str]
    """Nombres de las cabeceras tal cual llegaron (orden y mayúsculas)."""
    cabeceras: dict[str, str]


class _Manejador(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        servidor: _Servidor = self.server  # type: ignore[assignment]
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        servidor.visitas.append(
            Visita(
                host,
                self.path,
                list(self.headers.keys()),
                {k.lower(): v for k, v in self.headers.items()},
            )
        )
        puerto = str(servidor.server_port)
        externo = f"http://interno.example:{puerto}/secreto"
        saltos = {
            "/redirige": externo,
            "/redirige-fuera": externo,
            "/vieja": "/",
            **{f"/r{i}": f"/r{i + 1}" for i in range(7)},
            "/r7": "/",
        }
        if self.path in saltos:
            self.send_response(302)
            self.send_header("Location", saltos[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        paginas = {
            "/": PORTADA.replace("PUERTO", puerto),
            "/bucle": BUCLE,
            "/hints": HINTS.replace("PUERTO", puerto),
            "/marco": "<p>marco</p>",
            "/webrtc": WEBRTC.replace("STUN", str(servidor.puerto_stun)),
        }
        if self.path in paginas:
            cuerpo, tipo = paginas[self.path].encode(), "text/html; charset=utf-8"
        else:
            cuerpo, tipo = b"/* nada */", "text/css"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_: object) -> None:
        pass


class _Servidor(http.server.ThreadingHTTPServer):
    daemon_threads = True
    visitas: list[Visita]
    puerto_stun: int


@pytest.fixture
def servidor() -> Iterator[_Servidor]:
    servidor = _Servidor(("127.0.0.1", 0), _Manejador)
    servidor.visitas = []
    servidor.puerto_stun = 9
    hilo = threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    hilo.start()
    try:
        yield servidor
    finally:
        servidor.shutdown()
        servidor.server_close()


HOSTS_DEL_TEST = ("tienda.example", "localhost", "127.0.0.1")


def validador_local(puerto: int) -> Validador:
    """Solo `tienda.example`, `localhost` y 127.0.0.1, por http y en el puerto del servidor."""

    async def validar(url: str) -> str:
        partes = urlsplit(url)
        if (
            partes.scheme != "http"
            or partes.hostname not in HOSTS_DEL_TEST
            or partes.port != puerto
        ):
            raise URLNoPermitida("Fuera del servidor del test.")
        return url

    return validar


def _resolver_local(host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", puerto))]


def _loopback(ip: Any) -> bool:
    return bool(ip.is_loopback)


def descargador_local(
    puerto: int, *, timeout: float = 15, espera_red_s: float = 0.5, proxy: Proxy | None = None
) -> DescargadorChromium:
    """El relé y el descargador comparten EL MISMO validador (como en producción)."""
    validador = validador_local(puerto)
    return DescargadorChromium(
        timeout=timeout,
        validador=validador,
        espera_red_s=espera_red_s,
        proxy=proxy,
        crear_rele=lambda p: ReleProxy(
            p,
            validador=validador,
            resolver=_resolver_local,
            ip_permitida=_loopback,
            proxy_remoto=lambda _: True,
            puertos=frozenset({puerto}),
        ),
    )


def con_chromium(
    puerto: int, url: str, *, timeout: float = 15, espera_red_s: float = 0.5
) -> Pagina:
    async def ejecutar() -> Pagina:
        descargador = descargador_local(puerto, timeout=timeout, espera_red_s=espera_red_s)
        try:
            # Margen holgado: si `descargar` no respeta su propio timeout, el test falla aquí.
            return await asyncio.wait_for(descargador.descargar(url), timeout * 4)
        finally:
            await descargador.cerrar()

    return _ejecutar(ejecutar)


def _ejecutar(funcion: Callable[[], Coroutine[Any, Any, Pagina]]) -> Pagina:
    try:
        return asyncio.run(funcion())
    except URLNoPermitida:
        raise
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Chromium de Playwright no instalado")
        raise


def _hosts(servidor: _Servidor) -> set[str]:
    return {visita.host for visita in servidor.visitas}


def _rutas(servidor: _Servidor) -> list[str]:
    return [visita.ruta for visita in servidor.visitas]


@pytest.mark.chromium
def test_renderiza_y_bloquea_lo_no_permitido(servidor: _Servidor) -> None:
    puerto = servidor.server_port
    base = f"http://tienda.example:{puerto}"
    pagina = con_chromium(puerto, f"{base}/")
    assert "Precio renderizado: 38,90 €" in pagina.html
    assert pagina.estado == 200
    assert pagina.tipo_contenido == "text/html"
    assert pagina.url_final == f"{base}/"
    assert _rutas(servidor)[0] == "/"
    assert not {"/foto.jpg", "/fuente.woff2", "/video.mp4", "/pixel.gif"} & set(_rutas(servidor))
    # Ni el fetch directo (lo corta `route`) ni el subrecurso redirigido (lo corta el relé).
    assert "interno.example" not in _hosts(servidor)
    assert "/redirige-fuera" in _rutas(servidor) and "/secreto" not in _rutas(servidor)


@pytest.mark.chromium
def test_redireccion_a_un_host_no_permitido_se_corta_en_el_rele(servidor: _Servidor) -> None:
    puerto = servidor.server_port
    with pytest.raises(URLNoPermitida):
        con_chromium(puerto, f"http://tienda.example:{puerto}/redirige")
    assert _rutas(servidor) == ["/redirige"]
    assert "interno.example" not in _hosts(servidor)


@pytest.mark.chromium
def test_url_inicial_no_valida(servidor: _Servidor) -> None:
    with pytest.raises(URLNoPermitida):
        con_chromium(servidor.server_port, "http://169.254.169.254/")
    assert servidor.visitas == []


@pytest.mark.chromium
def test_redireccion_valida_de_la_navegacion(servidor: _Servidor) -> None:
    puerto = servidor.server_port
    base = f"http://tienda.example:{puerto}"
    pagina = con_chromium(puerto, f"{base}/vieja")
    assert pagina.url == f"{base}/vieja"
    assert pagina.url_final == f"{base}/"
    assert "Precio renderizado" in pagina.html
    assert _rutas(servidor)[:2] == ["/vieja", "/"]


@pytest.mark.chromium
def test_demasiadas_redirecciones(servidor: _Servidor) -> None:
    puerto = servidor.server_port
    with pytest.raises(ErrorDescarga, match="redirecciones"):
        con_chromium(puerto, f"http://tienda.example:{puerto}/r0")


@pytest.mark.chromium
def test_navega_con_la_red_de_chromium(servidor: _Servidor) -> None:
    # Con `route.fetch` la petición la hacía Node: cabeceras en minúsculas y sin Sec-Fetch-*.
    puerto = servidor.server_port
    # En `localhost` (contexto seguro): Chromium solo manda Sec-Fetch-* a orígenes seguros.
    con_chromium(puerto, f"http://localhost:{puerto}/")
    portada = servidor.visitas[0]
    assert portada.ruta == "/"
    assert portada.nombres[0] == "Host"
    assert "User-Agent" in portada.nombres and "Upgrade-Insecure-Requests" in portada.nombres
    assert portada.cabeceras["sec-fetch-mode"] == "navigate"
    assert portada.cabeceras["accept-language"].startswith("es-ES")


@pytest.mark.chromium
def test_client_hints_de_chrome(servidor: _Servidor) -> None:
    # `localhost` es un contexto seguro: ahí existen `navigator.userAgentData` y `sec-ch-ua`.
    puerto = servidor.server_port
    pagina = con_chromium(puerto, f"http://localhost:{puerto}/hints", espera_red_s=1.5)
    visitas = {(v.host, v.ruta): v for v in servidor.visitas}
    for clave in (("localhost", "/hints"), ("localhost", "/xhr"), ("127.0.0.1", "/marco")):
        cabeceras = visitas[clave].cabeceras
        assert re.search(r"Chrome/\d+\.0\.0\.0 ", cabeceras["user-agent"]), clave
        assert "Headless" not in cabeceras["user-agent"], clave
        assert '"Chromium"' in cabeceras["sec-ch-ua"], clave
        assert "Headless" not in cabeceras["sec-ch-ua"], clave
    assert "Headless" not in pagina.html
    assert '"brand":"Chromium"' in pagina.html


@pytest.mark.chromium
def test_webrtc_no_manda_udp(servidor: _Servidor) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stun:
        stun.bind(("127.0.0.1", 0))
        stun.settimeout(0.5)
        servidor.puerto_stun = stun.getsockname()[1]
        puerto = servidor.server_port
        pagina = con_chromium(puerto, f"http://tienda.example:{puerto}/webrtc", espera_red_s=2)
        assert "oferta-lista" in pagina.html or "ice:" in pagina.html
        paquetes = 0
        with contextlib.suppress(TimeoutError):
            while stun.recv(2048):
                paquetes += 1
    assert paquetes == 0


SONDA_IPV6 = "[2001:4860:4860::8888]:443"
"""`connect()` UDP con el que Chromium mira si hay ruta IPv6
(`HOST_RESOLVER_MANAGER_IPV6_REACHABILITY_CHECK`). Un `connect()` UDP no manda ningún paquete."""


@dataclass
class Netlog:
    tcp: list[str] = field(default_factory=list)
    """Destinos de los `TCP_CONNECT*`."""
    udp_conectados: list[str] = field(default_factory=list)
    """Destinos de los `UDP_CONNECT` (sin tráfico por sí mismos)."""
    udp_enviados: list[str] = field(default_factory=list)
    """Destinos de los `UDP_BYTES_SENT` (por su parámetro o por el `connect` de su socket)."""


def _leer_netlog(ruta: Path) -> Netlog:
    texto = ruta.read_text(encoding="utf-8").rstrip().rstrip(",")
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        datos = json.loads(texto + "]}")  # Chromium cerrado sin terminar el fichero
    tipos = {valor: nombre for nombre, valor in datos["constants"]["logEventTypes"].items()}
    netlog = Netlog()
    destino_udp: dict[int, str] = {}
    for evento in datos["events"]:
        nombre = tipos.get(evento.get("type"), "")
        parametros = evento.get("params") or {}
        fuente = evento.get("source", {}).get("id", -1)
        direcciones = list(parametros.get("address_list", []))
        if "address" in parametros:
            direcciones.append(parametros["address"])
        if nombre.startswith("TCP_CONNECT"):
            netlog.tcp += direcciones
        elif nombre == "UDP_CONNECT" and direcciones:
            netlog.udp_conectados += direcciones
            destino_udp[fuente] = direcciones[0]
        elif nombre == "UDP_BYTES_SENT":
            netlog.udp_enviados += direcciones or [destino_udp.get(fuente, "desconocido")]
    return netlog


def _es_loopback(direccion: str) -> bool:
    host = direccion.rsplit(":", 1)[0].strip("[]")
    return host.startswith("127.") or host == "::1"


@pytest.mark.chromium
def test_sin_trafico_fuera_del_rele(
    servidor: _Servidor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Defensa contra una vuelta a Chrome for Testing (DoH directo a dns.google) o a cualquier
    # canal con tráfico de fondo: todo lo que abre Chromium tiene que ir al relé, en 127.0.0.1.
    netlog = tmp_path / "netlog.json"
    monkeypatch.setattr(
        modulo_chromium,
        "ARGUMENTOS",
        (*ARGUMENTOS, f"--log-net-log={netlog}", "--net-log-capture-mode=Everything"),
    )
    puerto = servidor.server_port
    con_chromium(puerto, f"http://tienda.example:{puerto}/", espera_red_s=1.5)
    registro = _leer_netlog(netlog)
    assert registro.tcp, "el netlog no apuntó ninguna conexión: la prueba no mediría nada"
    assert [d for d in registro.tcp if not _es_loopback(d)] == []
    assert [d for d in registro.udp_enviados if not _es_loopback(d)] == []
    assert {d for d in registro.udp_conectados if not _es_loopback(d)} <= {SONDA_IPV6}


@pytest.mark.chromium
def test_pagina_que_cuelga_el_navegador_respeta_el_timeout(servidor: _Servidor) -> None:
    puerto = servidor.server_port
    inicio = time.monotonic()
    with pytest.raises(ErrorDescarga):
        con_chromium(puerto, f"http://tienda.example:{puerto}/bucle", timeout=3)
    assert time.monotonic() - inicio < 10


# --- Chromium real por el proxy (relé encadenado) -----------------------------------------------

USUARIO_PROXY = "usuario-proxy-chromium"
SECRETO_PROXY = "contrasena-proxy-chromium_country-ES"


def _pide_basic(manejador: http.server.BaseHTTPRequestHandler) -> bool:
    """Como haría un script de terceros hostil: un 401 Basic para que le manden credenciales."""
    if manejador.headers.get("Authorization"):
        cuerpo = b"<html><body>Credenciales robadas</body></html>"
        estado = 200
    elif manejador.path in ("/", "/privado"):
        manejador.send_response(401)
        manejador.send_header("WWW-Authenticate", 'Basic realm="atacante"')
        manejador.send_header("Content-Type", "text/html")
        manejador.send_header("Content-Length", "0")
        manejador.end_headers()
        return True
    else:
        return False
    manejador.send_response(estado)
    manejador.send_header("Content-Type", "text/html")
    manejador.send_header("Content-Length", str(len(cuerpo)))
    manejador.end_headers()
    manejador.wfile.write(cuerpo)
    return True


@pytest.mark.chromium
def test_por_el_proxy_un_401_basic_no_se_lleva_las_credenciales() -> None:
    with tienda(manejar=_pide_basic) as servidor:
        puerto_tienda = servidor.server_address[1]
        base = f"http://tienda.example:{puerto_tienda}"
        proxy_esperado = Proxy("127.0.0.1", 1, USUARIO_PROXY, SECRETO_PROXY)
        with proxy_falso(puerto_tienda, esperada=proxy_esperado.cabecera_autorizacion) as (
            puerto,
            estado,
        ):
            proxy = Proxy("127.0.0.1", puerto, USUARIO_PROXY, SECRETO_PROXY)

            async def ejecutar() -> Pagina:
                descargador = descargador_local(puerto_tienda, proxy=proxy)
                try:
                    return await asyncio.wait_for(descargador.descargar(f"{base}/"), 60)
                finally:
                    await descargador.cerrar()

            pagina: Pagina = _ejecutar(ejecutar)
    # Ha ido por el proxy (el nombre `tienda.example` solo lo «resuelve» el proxy falso)...
    assert estado.connects and all("tienda.example" in c for c in estado.connects)
    assert set(estado.autorizaciones) == {proxy_esperado.cabecera_autorizacion}
    # ...y el 401 se queda en 401: nadie repitió la petición con `Authorization`.
    assert pagina.estado == 401
    assert servidor.peticiones
    assert all("authorization" not in cabeceras for _, cabeceras in servidor.peticiones)
    assert all("proxy-authorization" not in cabeceras for _, cabeceras in servidor.peticiones)
    assert "robadas" not in pagina.html


# --- La tienda corta la conexión -----------------------------------------------------------------


@pytest.fixture
def servidor_que_corta() -> Iterator[int]:
    """Acepta y cierra con RST (`SO_LINGER` 0), como un WAF que rechaza la IP."""
    escucha = socket.create_server(("127.0.0.1", 0))
    escucha.settimeout(0.2)
    parar = threading.Event()

    def aceptar() -> None:
        while not parar.is_set():
            try:
                conexion, _ = escucha.accept()
            except OSError:
                continue
            with contextlib.suppress(OSError):
                conexion.recv(65536)
                conexion.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            conexion.close()

    hilo = threading.Thread(target=aceptar, daemon=True)
    hilo.start()
    try:
        yield escucha.getsockname()[1]
    finally:
        parar.set()
        hilo.join(5)
        escucha.close()


@pytest.mark.chromium
def test_conexion_cortada_por_la_tienda(servidor_que_corta: int) -> None:
    puerto = servidor_que_corta
    with pytest.raises(ConexionCortada):
        con_chromium(puerto, f"http://tienda.example:{puerto}/")


@pytest.mark.chromium
def test_si_chromium_muere_se_vuelve_a_arrancar(servidor: _Servidor) -> None:
    puerto = servidor.server_address[1]
    url = f"http://tienda.example:{puerto}/"

    async def ejecutar() -> Pagina:
        descargador = descargador_local(puerto)
        try:
            await asyncio.wait_for(descargador.descargar(url), 60)
            navegador = descargador._navegador
            assert navegador is not None
            cdp = await navegador.new_browser_cdp_session()
            with contextlib.suppress(ErrorPlaywright, TimeoutError):
                await asyncio.wait_for(cdp.send("Browser.crash"), 5)
            for _ in range(50):
                if not navegador.is_connected():
                    break
                await asyncio.sleep(0.1)
            assert not navegador.is_connected()
            # Antes, el navegador muerto se reutilizaba para siempre (`TargetClosedError`).
            return await asyncio.wait_for(descargador.descargar(url), 60)
        finally:
            await descargador.cerrar()

    resultado = _ejecutar(ejecutar)
    assert "Brembo P85075" in resultado.html
