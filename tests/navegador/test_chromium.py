"""Descargador con Chromium: ninguna petición de la página sale sin pasar `validar_url`.

Los primeros tests prueban el filtro de peticiones con rutas falsas. Los marcados `chromium` abren
un Chromium real contra un servidor HTTP en 127.0.0.1 levantado por el propio test; en producción
127.0.0.1 está prohibido, así que aquí se inyecta un validador que solo deja pasar ese servidor y
trata `/secreto` como si fuera un servicio interno.
"""

import asyncio
import contextlib
import http.server
import socket
import struct
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import pytest
from playwright.async_api import Error as ErrorPlaywright

from precia.navegador.chromium import DescargadorChromium, FiltroPeticiones
from precia.navegador.proxy import Proxy
from precia.navegador.rele import ReleProxy
from precia.navegador.tipos import ConexionCortada, ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import Validador
from tests.navegador.servidores_proxy import proxy_falso, tienda

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"


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
class RespuestaFalsa:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


def salto(destino: str, estado: int = 302) -> RespuestaFalsa:
    return RespuestaFalsa(estado, {"location": destino})


@dataclass
class RutaFalsa:
    """`respuestas[None]` es la de la URL original; el resto, por URL pedida con `fetch(url=)`."""

    request: PeticionFalsa
    respuestas: dict[str | None, RespuestaFalsa] = field(default_factory=dict)
    acciones: list[str] = field(default_factory=list)
    fallo_de_red: bool = False

    async def abort(self, error_code: str | None = None) -> None:
        self.acciones.append(f"abort:{error_code}")

    async def fetch(
        self, *, url: str | None = None, max_redirects: int | None = None
    ) -> RespuestaFalsa:
        assert max_redirects == 0, "el filtro nunca deja que Playwright siga redirecciones"
        self.acciones.append(f"fetch:{url or self.request.url}")
        if self.fallo_de_red:
            raise ErrorPlaywright("net::ERR_CONNECTION_RESET")
        return self.respuestas.get(url, RespuestaFalsa())

    async def fulfill(self, *, response: RespuestaFalsa) -> None:
        self.acciones.append(f"fulfill:{response.status}")


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


def test_deja_pasar_lo_valido() -> None:
    ruta = RutaFalsa(PeticionFalsa("https://tienda.example/app.js", resource_type="script"))
    filtrar(ruta)
    assert ruta.acciones == ["fetch:https://tienda.example/app.js", "fulfill:200"]


def test_subrecurso_redirigido_a_destino_prohibido() -> None:
    ruta = RutaFalsa(
        PeticionFalsa("https://tienda.example/api", resource_type="fetch"),
        {None: salto("http://169.254.169.254/")},
    )
    filtrar(ruta)
    assert ruta.acciones == ["fetch:https://tienda.example/api", "abort:blockedbyclient"]


def test_subrecurso_sigue_la_cadena_validando_cada_salto() -> None:
    # Chromium sigue por su cuenta (sin pasar por `route`) un 302 entregado con `fulfill`: por eso
    # el filtro sigue la cadena él mismo y solo entrega la respuesta final.
    ruta = RutaFalsa(
        PeticionFalsa("https://tienda.example/a.js", resource_type="script"),
        {
            None: salto("/b.js", 301),
            "https://tienda.example/b.js": salto("http://127.0.0.1/c.js"),
        },
    )
    filtrar(ruta)
    assert ruta.acciones == [
        "fetch:https://tienda.example/a.js",
        "fetch:https://tienda.example/b.js",
        "abort:blockedbyclient",
    ]


def test_subrecurso_redirigido_correctamente() -> None:
    ruta = RutaFalsa(
        PeticionFalsa("https://tienda.example/a.js", resource_type="script"),
        {None: salto("/b.js")},
    )
    filtrar(ruta)
    assert ruta.acciones[-1] == "fulfill:200"


def test_subrecurso_con_demasiadas_redirecciones() -> None:
    respuestas: dict[str | None, RespuestaFalsa] = {None: salto("/0")}
    for i in range(10):
        respuestas[f"https://tienda.example/{i}"] = salto(f"/{i + 1}")
    ruta = RutaFalsa(PeticionFalsa("https://tienda.example/x", resource_type="fetch"), respuestas)
    filtrar(ruta)
    assert ruta.acciones[-1] == "abort:failed"
    assert len(ruta.acciones) == 1 + 6


def test_fallo_de_red_aborta() -> None:
    ruta = RutaFalsa(PeticionFalsa("https://tienda.example/api", resource_type="xhr"))
    ruta.fallo_de_red = True
    filtrar(ruta)
    assert ruta.acciones == ["fetch:https://tienda.example/api", "abort:failed"]


def test_navegacion_principal_redirigida_se_delega_validada() -> None:
    ruta = RutaFalsa(
        PeticionFalsa("https://tienda.example/vieja", navegacion=True),
        {None: salto("/nueva", 301)},
    )
    filtro = filtrar(ruta)
    assert ruta.acciones == ["fetch:https://tienda.example/vieja", "abort:aborted"]
    assert filtro.redireccion == "https://tienda.example/nueva"


def test_navegacion_principal_redirigida_a_destino_prohibido() -> None:
    ruta = RutaFalsa(
        PeticionFalsa("https://tienda.example/vieja", navegacion=True),
        {None: salto("http://169.254.169.254/")},
    )
    filtro = filtrar(ruta)
    assert ruta.acciones[-1] == "abort:blockedbyclient"
    assert isinstance(filtro.bloqueo_principal, URLNoPermitida)
    assert filtro.redireccion is None


def test_recuerda_el_bloqueo_de_la_navegacion_principal() -> None:
    ruta = RutaFalsa(PeticionFalsa("http://127.0.0.1/", navegacion=True))
    filtro = filtrar(ruta)
    assert isinstance(filtro.bloqueo_principal, URLNoPermitida)


def test_navegacion_de_un_iframe_no_es_la_principal() -> None:
    ruta = RutaFalsa(PeticionFalsa("http://127.0.0.1/", navegacion=True, frame=IFRAME))
    filtro = filtrar(ruta)
    assert ruta.acciones == ["abort:blockedbyclient"]
    assert filtro.bloqueo_principal is None


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
  fetch("/secreto").catch(() => {});
  fetch("/redirige").catch(() => {});
  fetch("/cadena").catch(() => {});
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


class _Manejador(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        rutas: list[str] = self.server.rutas  # type: ignore[attr-defined]
        rutas.append(self.path)
        saltos = {
            "/redirige": "/secreto",
            "/cadena": "/cadena2",
            "/cadena2": "/secreto",
            "/vieja": "/",
        }
        if self.path in saltos:
            self.send_response(302)
            self.send_header("Location", saltos[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        tipos = {"/": "text/html; charset=utf-8", "/estilo.css": "text/css"}
        paginas = {"/": PORTADA, "/bucle": BUCLE}
        if self.path == "/bucle":
            tipos["/bucle"] = "text/html; charset=utf-8"
        cuerpo = paginas[self.path].encode() if self.path in paginas else b"/* nada */"
        self.send_response(200)
        self.send_header("Content-Type", tipos.get(self.path, "application/octet-stream"))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def servidor() -> Iterator[tuple[str, list[str]]]:
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Manejador)
    rutas: list[str] = []
    servidor.rutas = rutas  # type: ignore[attr-defined]
    hilo = threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    hilo.start()
    try:
        yield f"http://127.0.0.1:{servidor.server_port}", rutas
    finally:
        servidor.shutdown()
        servidor.server_close()


def validador_local(base: str) -> Validador:
    async def validar(url: str) -> str:
        if not url.startswith(base + "/") or urlsplit(url).path.startswith("/secreto"):
            raise URLNoPermitida("Fuera del servidor del test.")
        return url

    return validar


def con_chromium(base: str, url: str, *, timeout: float = 15) -> Pagina:
    async def ejecutar() -> Pagina:
        descargador = DescargadorChromium(
            UA, timeout=timeout, validador=validador_local(base), espera_red_s=0.5
        )
        try:
            # Margen holgado: si `descargar` no respeta su propio timeout, el test falla aquí.
            return await asyncio.wait_for(descargador.descargar(url), timeout * 4)
        finally:
            await descargador.cerrar()

    try:
        return asyncio.run(ejecutar())
    except URLNoPermitida:
        raise
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            pytest.skip("Chromium de Playwright no instalado")
        raise


@pytest.mark.chromium
def test_renderiza_y_bloquea_lo_no_permitido(servidor: tuple[str, list[str]]) -> None:
    base, rutas = servidor
    pagina = con_chromium(base, f"{base}/")
    assert "Precio renderizado: 38,90 €" in pagina.html
    assert pagina.estado == 200
    assert pagina.tipo_contenido == "text/html"
    assert pagina.url_final == f"{base}/"
    assert "/secreto" not in rutas
    assert not {"/foto.jpg", "/fuente.woff2", "/video.mp4", "/pixel.gif"} & set(rutas)
    assert rutas[0] == "/"


@pytest.mark.chromium
@pytest.mark.parametrize("inicio", ["/redirige", "/cadena"])
def test_redirecciones_de_la_navegacion_se_validan(
    servidor: tuple[str, list[str]], inicio: str
) -> None:
    base, rutas = servidor
    with pytest.raises(URLNoPermitida):
        con_chromium(base, f"{base}{inicio}")
    assert "/secreto" not in rutas


@pytest.mark.chromium
def test_url_inicial_no_valida(servidor: tuple[str, list[str]]) -> None:
    base, rutas = servidor
    with pytest.raises(URLNoPermitida):
        con_chromium(base, "http://169.254.169.254/")
    assert rutas == []


@pytest.mark.chromium
def test_redireccion_valida_de_la_navegacion(servidor: tuple[str, list[str]]) -> None:
    base, rutas = servidor
    pagina = con_chromium(base, f"{base}/vieja")
    assert pagina.url == f"{base}/vieja"
    assert pagina.url_final == f"{base}/"
    assert "Precio renderizado" in pagina.html
    assert rutas[:2] == ["/vieja", "/"]
    assert "/secreto" not in rutas


@pytest.mark.chromium
def test_pagina_que_cuelga_el_navegador_respeta_el_timeout(
    servidor: tuple[str, list[str]],
) -> None:
    base, _ = servidor
    inicio = time.monotonic()
    with pytest.raises(ErrorDescarga):
        con_chromium(base, f"{base}/bucle", timeout=3)
    assert time.monotonic() - inicio < 10


# --- Arranque con proxy (Playwright falso) ------------------------------------------------------


class _NavegadorArrancado:
    def __init__(self) -> None:
        self.cerrado = False

    async def new_context(self, **opciones: object) -> object:
        raise RuntimeError("el test para aquí")

    async def close(self) -> None:
        self.cerrado = True


class _PlaywrightFalso:
    def __init__(self) -> None:
        self.lanzamientos: list[dict[str, object]] = []
        self.chromium = self

    async def start(self) -> "_PlaywrightFalso":
        return self

    async def launch(self, **opciones: object) -> _NavegadorArrancado:
        self.lanzamientos.append(opciones)
        return _NavegadorArrancado()

    async def stop(self) -> None:
        pass


def _lanzar(monkeypatch: pytest.MonkeyPatch, proxy: Proxy | None) -> dict[str, object]:
    falso = _PlaywrightFalso()
    monkeypatch.setattr("precia.navegador.chromium.async_playwright", lambda: falso)

    async def permisivo(url: str) -> str:
        return url

    descargador = DescargadorChromium(UA, validador=permisivo, proxy=proxy)

    async def probar() -> None:
        with pytest.raises(RuntimeError, match="el test para aquí"):
            await descargador.descargar("https://tienda.example/")
        await descargador.cerrar()

    asyncio.run(probar())
    assert len(falso.lanzamientos) == 1
    return falso.lanzamientos[0]


def test_sin_proxy_arranca_sin_la_opcion(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _lanzar(monkeypatch, None) == {"headless": True}


def test_con_proxy_arranca_chromium_contra_el_rele_sin_credenciales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Playwright usaría el usuario y la contraseña del proxy para contestar a cualquier 401 Basic
    # de un destino: Chromium va al relé local, que es quien las añade en el CONNECT.
    proxy = Proxy("core-residential.evomi.com", 1000, "usu", "secreta_country-ES")
    opciones = _lanzar(monkeypatch, proxy)
    assert set(opciones) == {"headless", "proxy"} and opciones["headless"] is True
    ajustes = opciones["proxy"]
    assert isinstance(ajustes, dict) and set(ajustes) == {"server"}
    assert ajustes["server"].startswith("http://127.0.0.1:")
    assert "secreta" not in repr(opciones) and "usu" not in repr(opciones)


def test_fallo_de_red_de_la_navegacion_principal_se_recuerda() -> None:
    ruta = RutaFalsa(PeticionFalsa("https://tienda.example/", navegacion=True))
    ruta.fallo_de_red = True
    filtro = filtrar(ruta)
    assert ruta.acciones[-1] == "abort:failed"
    assert filtro.fallo_principal is not None and "ERR_CONNECTION_RESET" in filtro.fallo_principal


def test_fallo_de_red_de_un_subrecurso_no_es_el_de_la_pagina() -> None:
    ruta = RutaFalsa(PeticionFalsa("https://tienda.example/api", resource_type="xhr"))
    ruta.fallo_de_red = True
    assert filtrar(ruta).fallo_principal is None


# --- Chromium real por el proxy (relé local) ----------------------------------------------------

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


def _siempre_remoto(_: object) -> bool:
    return True


@pytest.mark.chromium
def test_por_el_proxy_un_401_basic_no_se_lleva_las_credenciales() -> None:
    with tienda(manejar=_pide_basic) as servidor:
        base = f"http://tienda.example:{servidor.server_address[1]}"
        proxy_esperado = Proxy("127.0.0.1", 1, USUARIO_PROXY, SECRETO_PROXY)
        with proxy_falso(
            servidor.server_address[1], esperada=proxy_esperado.cabecera_autorizacion
        ) as (puerto, estado):
            proxy = Proxy("127.0.0.1", puerto, USUARIO_PROXY, SECRETO_PROXY)

            async def validar(url: str) -> str:
                if not url.startswith(base + "/"):
                    raise URLNoPermitida("Fuera de la tienda del test.")
                return url

            async def ejecutar() -> Pagina:
                descargador = DescargadorChromium(
                    UA,
                    timeout=15,
                    validador=validar,
                    espera_red_s=0.5,
                    proxy=proxy,
                    crear_rele=lambda p: ReleProxy(p, proxy_remoto=_siempre_remoto),
                )
                try:
                    return await asyncio.wait_for(descargador.descargar(f"{base}/"), 60)
                finally:
                    await descargador.cerrar()

            try:
                pagina = asyncio.run(ejecutar())
            except Exception as error:
                if "Executable doesn't exist" in str(error):
                    pytest.skip("Chromium de Playwright no instalado")
                raise
    # Ha ido por el proxy (el nombre `tienda.example` solo lo «resuelve» el proxy falso)...
    assert estado.connects and all("tienda.example" in c for c in estado.connects)
    assert set(estado.autorizaciones) == {proxy_esperado.cabecera_autorizacion}
    # ...y el 401 se queda en 401: nadie repitió la petición con `Authorization`.
    assert pagina.estado == 401
    assert servidor.peticiones
    assert all("authorization" not in cabeceras for _, cabeceras in servidor.peticiones)
    assert "robadas" not in pagina.html


# --- La tienda corta la conexión -----------------------------------------------------------------


@pytest.fixture
def servidor_que_corta() -> Iterator[str]:
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
        yield f"http://127.0.0.1:{escucha.getsockname()[1]}"
    finally:
        parar.set()
        hilo.join(5)
        escucha.close()


@pytest.mark.chromium
def test_conexion_cortada_por_la_tienda(servidor_que_corta: str) -> None:
    base = servidor_que_corta
    with pytest.raises(ConexionCortada):
        con_chromium(base, f"{base}/")
