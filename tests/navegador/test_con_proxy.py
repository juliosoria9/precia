"""Envoltorio con proxy: directo, por el proxy o «directo y, si bloquea, una vez por el proxy»."""

import asyncio
import contextlib
import errno
import logging
import socketserver
import threading
import time
import urllib.error
from collections.abc import Iterator

import pytest

from precia.navegador.con_proxy import DescargadorConProxy, UsoProxy
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.http import MAXIMO_BYTES, DescargadorHTTP, TransporteURLLib
from precia.navegador.proxy import ModoProxy
from precia.navegador.tipos import ConexionCortada, DescargadorGrande, ErrorDescarga, Pagina
from tests.navegador.falsos import DescargadorFalso, DescargadorGrandeFalso, Reloj, pagina

URL = "https://www.b-parts.example/es/buscar?q=1K0698151"
OTRA = "https://b-parts.example/es/ficha/1"
DE_OTRA_TIENDA = "https://www.ecooparts.example/buscar"
BLOQUEO = pagina(
    URL, "<html><head><title>Just a moment...</title></head>cloudflare</html>", estado=403
)
NORMAL = pagina(URL, "<html><body>Pastillas 38,90 €</body></html>")


class Dormir:
    def __init__(self) -> None:
        self.esperas: list[float] = []

    async def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)


def montar(
    directas: dict[str, Pagina | Exception],
    por_proxy: dict[str, Pagina | Exception],
    modo: ModoProxy = ModoProxy.BLOQUEO,
    **opciones: object,
) -> tuple[DescargadorConProxy, DescargadorFalso, DescargadorFalso]:
    directo, proxy = DescargadorFalso(directas), DescargadorFalso(por_proxy)
    return DescargadorConProxy(directo, proxy, modo=modo, **opciones), directo, proxy  # type: ignore[arg-type]


def test_modo_bloqueo_sin_bloqueo_va_directo() -> None:
    envoltorio, directo, proxy = montar({URL: NORMAL}, {})
    obtenida = asyncio.run(envoltorio.descargar(URL))
    assert obtenida == NORMAL and not obtenida.via_proxy
    assert directo.pedidas == [URL] and proxy.pedidas == []
    assert envoltorio.peticiones_proxy == 0 and envoltorio.dominios_por_proxy() == ()


def test_modo_bloqueo_repite_una_vez_por_el_proxy_y_marca_la_tienda(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dormir = Dormir()
    envoltorio, directo, proxy = montar(
        {URL: BLOQUEO}, {URL: NORMAL, OTRA: NORMAL}, espera_reintento_s=2.0, dormir=dormir
    )
    with caplog.at_level(logging.INFO, logger="precia.navegador.con_proxy"):
        obtenida = asyncio.run(envoltorio.descargar(URL))
    assert obtenida.via_proxy and obtenida.html == NORMAL.html
    assert directo.pedidas == [URL] and proxy.pedidas == [URL]
    # La cortesía también vale para el reintento: no se repite pegado a la primera petición.
    assert dormir.esperas == [2.0]
    assert envoltorio.dominios_por_proxy() == ("b-parts.example",)
    assert "b-parts.example bloqueó la conexión directa" in caplog.text
    assert "se reintenta por el proxy" in caplog.text

    # La tienda queda marcada (con o sin «www.»): lo siguiente va por el proxy sin probar directo.
    asyncio.run(envoltorio.descargar(OTRA))
    assert directo.pedidas == [URL] and proxy.pedidas == [URL, OTRA]
    assert envoltorio.peticiones_proxy == 2
    assert envoltorio.bytes_proxy == 2 * len(NORMAL.html.encode())


def test_el_proxy_tambien_bloqueado_se_devuelve_tal_cual() -> None:
    # Una sola repetición: el bloqueo del proxy lo detecta el Navegador y lanza `Bloqueado`.
    envoltorio, directo, proxy = montar({URL: BLOQUEO}, {URL: BLOQUEO})
    obtenida = asyncio.run(envoltorio.descargar(URL))
    assert obtenida.estado == 403 and obtenida.via_proxy
    assert directo.pedidas == [URL] and proxy.pedidas == [URL]


def test_error_del_proxy_se_propaga() -> None:
    envoltorio, _, _ = montar({URL: BLOQUEO}, {URL: ErrorDescarga("El proxy rechazó (407).")})
    with pytest.raises(ErrorDescarga, match="407"):
        asyncio.run(envoltorio.descargar(URL))


def _cortada(error: OSError) -> ErrorDescarga:
    """Como la lanza `TransporteURLLib`: `ErrorDescarga` desde un `URLError` con el `OSError`."""
    try:
        try:
            raise urllib.error.URLError(error)
        except urllib.error.URLError as envuelto:
            raise ErrorDescarga("No se pudo conectar con la tienda.") from envuelto
    except ErrorDescarga as final:
        return final


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError(errno.ECONNRESET, "reset"),
        ConnectionAbortedError(errno.ECONNABORTED, "abortada"),
    ],
)
def test_conexion_cortada_por_la_tienda_se_repite_por_el_proxy(error: OSError) -> None:
    envoltorio, _, proxy = montar({URL: _cortada(error)}, {URL: NORMAL})
    assert asyncio.run(envoltorio.descargar(URL)).via_proxy
    assert proxy.pedidas == [URL]


@pytest.mark.parametrize(
    "error",
    [
        ErrorDescarga("Se agotó el tiempo de espera de la tienda."),
        _cortada(ConnectionRefusedError(errno.ECONNREFUSED, "rechazada")),
        _cortada(TimeoutError("lento")),
        ErrorDescarga("La página es demasiado grande."),
    ],
)
def test_otros_errores_no_se_repiten_por_el_proxy(error: ErrorDescarga) -> None:
    envoltorio, _, proxy = montar({URL: error}, {URL: NORMAL})
    with pytest.raises(ErrorDescarga):
        asyncio.run(envoltorio.descargar(URL))
    assert proxy.pedidas == [] and envoltorio.dominios_por_proxy() == ()


def test_modo_siempre_todo_por_el_proxy() -> None:
    envoltorio, directo, proxy = montar({}, {URL: NORMAL}, ModoProxy.SIEMPRE)
    assert asyncio.run(envoltorio.descargar(URL)).via_proxy
    assert directo.pedidas == [] and proxy.pedidas == [URL]
    assert envoltorio.peticiones_proxy == 1


def test_modo_nunca_todo_directo_aunque_bloquee() -> None:
    envoltorio, directo, proxy = montar({URL: BLOQUEO}, {URL: NORMAL}, ModoProxy.NUNCA)
    assert asyncio.run(envoltorio.descargar(URL)) == BLOQUEO
    assert directo.pedidas == [URL] and proxy.pedidas == []


def test_otra_tienda_no_se_ve_afectada() -> None:
    otra = pagina(DE_OTRA_TIENDA)
    envoltorio, directo, _ = montar({URL: BLOQUEO, DE_OTRA_TIENDA: otra}, {URL: NORMAL})
    asyncio.run(envoltorio.descargar(URL))
    assert asyncio.run(envoltorio.descargar(DE_OTRA_TIENDA)) == otra
    assert directo.pedidas == [URL, DE_OTRA_TIENDA]


def test_uso_compartido_entre_envoltorios() -> None:
    # HTTP y Chromium comparten el uso: la tienda marcada por uno va por el proxy en el otro.
    uso = UsoProxy()
    http, _, _ = montar({URL: BLOQUEO}, {URL: NORMAL}, uso=uso)
    chromium, directo_js, proxy_js = montar({}, {OTRA: NORMAL}, uso=uso)
    asyncio.run(http.descargar(URL))
    asyncio.run(chromium.descargar(OTRA))
    assert directo_js.pedidas == [] and proxy_js.pedidas == [OTRA]
    assert uso.peticiones == 2 and chromium.dominios_por_proxy() == ("b-parts.example",)


class DescargadorLento(DescargadorFalso):
    """Cede el bucle antes de responder: así las peticiones concurrentes están en vuelo a la vez."""

    async def descargar(self, url: str) -> Pagina:
        for _ in range(3):
            await asyncio.sleep(0)
        return await super().descargar(url)


def test_peticiones_concurrentes_como_mucho_un_reintento_cada_una(
    caplog: pytest.LogCaptureFixture,
) -> None:
    urls = [f"https://www.b-parts.example/p/{n}" for n in range(5)]
    directo = DescargadorLento({u: pagina(u, BLOQUEO.html, estado=403) for u in urls})
    proxy = DescargadorLento({u: pagina(u, NORMAL.html) for u in urls})
    envoltorio = DescargadorConProxy(directo, proxy)

    async def todas() -> list[Pagina]:
        return await asyncio.gather(*(envoltorio.descargar(u) for u in urls))

    with caplog.at_level(logging.WARNING, logger="precia.navegador.con_proxy"):
        resultado = asyncio.run(todas())
    assert all(p.via_proxy for p in resultado)
    # Las cinco salieron directas a la vez (ninguna vio la tienda marcada) y fallaron a la vez:
    # cada una se repite una sola vez por el proxy y el aviso sale una vez.
    assert sorted(directo.pedidas) == sorted(urls)
    assert sorted(proxy.pedidas) == sorted(urls)
    assert caplog.text.count("bloqueó la conexión directa") == 1
    assert envoltorio.dominios_por_proxy() == ("b-parts.example",)
    assert envoltorio.peticiones_proxy == 5


def test_un_429_no_se_repite_por_el_proxy() -> None:
    # La tienda pide bajar el ritmo: cambiar de IP para seguir pidiendo sería saltarse su límite.
    # La página se devuelve tal cual y `Navegador` lanza `Bloqueado`.
    limitada = pagina(
        URL,
        '<script type="application/ld+json">{"@type": "Product"}</script><p>38,90 €</p>',
        estado=429,
    )
    envoltorio, directo, proxy = montar({URL: limitada}, {URL: NORMAL})
    assert asyncio.run(envoltorio.descargar(URL)) == limitada
    assert directo.pedidas == [URL] and proxy.pedidas == []
    assert envoltorio.dominios_por_proxy() == () and envoltorio.peticiones_proxy == 0


def test_un_429_de_un_anti_bot_si_se_repite_por_el_proxy() -> None:
    # Kasada contesta su desafío con un 429: no es la tienda pidiendo calma, es la reputación de
    # la IP. Solo el límite de peticiones a secas se respeta sin cambiar de IP.
    kasada = pagina(
        URL, '<html><script src="/ips.js"></script><script>KPSDK.configure()</script>', estado=429
    )
    envoltorio, _, proxy = montar({URL: kasada}, {URL: NORMAL})
    assert asyncio.run(envoltorio.descargar(URL)).via_proxy
    assert proxy.pedidas == [URL]


def test_conexion_cortada_de_chromium_se_repite_por_el_proxy() -> None:
    # `DescargadorChromium` no tiene el `ConnectionResetError` en la cadena: da `ConexionCortada`.
    envoltorio, _, proxy = montar(
        {URL: ConexionCortada("La tienda cortó la conexión.")}, {URL: NORMAL}
    )
    assert asyncio.run(envoltorio.descargar(URL)).via_proxy
    assert proxy.pedidas == [URL]


def test_un_corte_con_el_contexto_suprimido_no_cuenta() -> None:
    # `raise ... from None` dentro de un `except ConnectionAbortedError` (nuestro propio corte por
    # tiempo): el contexto sigue guardado, pero está suprimido y no es la causa.
    def lanzar() -> ErrorDescarga:
        try:
            try:
                raise ConnectionAbortedError(errno.ECONNABORTED, "abortada")
            except ConnectionAbortedError:
                raise ErrorDescarga("Se agotó el tiempo de espera de la tienda.") from None
        except ErrorDescarga as error:
            return error

    envoltorio, _, proxy = montar({URL: lanzar()}, {URL: NORMAL})
    with pytest.raises(ErrorDescarga, match="tiempo"):
        asyncio.run(envoltorio.descargar(URL))
    assert proxy.pedidas == []


def test_reintento_dentro_del_turno_espera_el_crawl_delay_y_suelta_la_plaza() -> None:
    reloj = Reloj()
    otra_tienda: list[str] = []

    async def dormir(segundos: float) -> None:
        reloj.esperas.append(segundos)
        # Con una sola plaza global: si el reintento la tuviera cogida mientras espera, el turno
        # de otra tienda no podría entrar y esto se agotaría.
        await asyncio.wait_for(turno_de_otra_tienda(), 1)
        reloj.ahora += segundos

    async def turno_de_otra_tienda() -> None:
        async with limitador.turno("www.ecooparts.example"):
            otra_tienda.append("dentro")

    limitador = LimitadorDominio(2.0, concurrencia=1, reloj=reloj, dormir=dormir)
    sin_turno = Dormir()
    envoltorio, directo, proxy = montar(
        {URL: BLOQUEO}, {URL: NORMAL}, espera_reintento_s=2.0, dormir=sin_turno
    )

    async def pedir() -> Pagina:
        async with limitador.turno("www.b-parts.example", retardo_minimo=10.0):
            return await envoltorio.descargar(URL)

    assert asyncio.run(pedir()).via_proxy
    # El Crawl-delay (10 s) manda sobre el retardo (2 s), como entre dos peticiones normales.
    assert reloj.esperas == [10.0] and sin_turno.esperas == []
    assert otra_tienda == ["dentro"]
    assert directo.pedidas == [URL] and proxy.pedidas == [URL]


def test_descargar_grande_tambien_reintenta() -> None:
    grande = pagina(URL, "<urlset>" + "x" * (MAXIMO_BYTES + 10) + "</urlset>", tipo="text/xml")
    directo = DescargadorGrandeFalso({URL: BLOQUEO})
    proxy = DescargadorGrandeFalso({URL: grande})
    envoltorio = DescargadorConProxy(directo, proxy)
    assert isinstance(envoltorio, DescargadorGrande)
    obtenida = asyncio.run(envoltorio.descargar_grande(URL, max_bytes=123))
    assert obtenida.via_proxy and obtenida.html == grande.html
    assert directo.grandes == [(URL, 123)] and proxy.grandes == [(URL, 123)]


def test_descargar_grande_sin_soporte_en_los_internos() -> None:
    envoltorio, _, _ = montar({URL: NORMAL}, {URL: NORMAL})
    with pytest.raises(ErrorDescarga, match="grandes"):
        asyncio.run(envoltorio.descargar_grande(URL, max_bytes=10))


def test_cerrar_cierra_los_dos() -> None:
    envoltorio, directo, proxy = montar({}, {})
    asyncio.run(envoltorio.cerrar())
    assert directo.cerrado and proxy.cerrado


def test_mb_por_el_proxy() -> None:
    uso = UsoProxy(peticiones=3, bytes=3 * 1024 * 1024)
    assert uso.mb == pytest.approx(3.0)


# --- Una tienda lenta no es un bloqueo (transporte real contra 127.0.0.1) -----------------------


@contextlib.contextmanager
def tienda_que_gotea(respuesta: bytes, pausa_s: float = 0.2) -> Iterator[int]:
    """Acepta, lee la petición y suelta la respuesta byte a byte (más lenta que el timeout)."""

    class Goteo(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.recv(65536)
            with contextlib.suppress(OSError):
                for byte in respuesta:
                    self.request.sendall(bytes([byte]))
                    time.sleep(pausa_s)

    servidor = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Goteo)
    servidor.daemon_threads = True
    hilo = threading.Thread(target=servidor.serve_forever, kwargs={"poll_interval": 0.05})
    hilo.daemon = True
    hilo.start()
    try:
        yield servidor.server_address[1]
    finally:
        servidor.shutdown()
        servidor.server_close()


async def _permisivo(url: str) -> str:
    return url


@pytest.mark.parametrize(
    "respuesta",
    [
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<p>x</p>",  # gotea el estado
        b"HTTP/1.1 200 OK\r\n" + b"X-Relleno: 1\r\n" * 20 + b"\r\n",  # gotea las cabeceras
    ],
    ids=["estado", "cabeceras"],
)
def test_tienda_lenta_no_pasa_al_proxy(respuesta: bytes) -> None:
    # El corte por tiempo del transporte hace `shutdown` del socket: el `recv` siguiente da
    # `ConnectionAbortedError` (Windows) o `RemoteDisconnected` (Linux). Eso no es la tienda
    # cortando: no se marca ni se repite por el proxy.
    transporte = TransporteURLLib(ip_permitida=lambda _: True)
    directo = DescargadorHTTP(
        "PrecIA/0.1", timeout=0.6, validador=_permisivo, transporte=transporte
    )
    proxy = DescargadorFalso()
    envoltorio = DescargadorConProxy(directo, proxy)
    with tienda_que_gotea(respuesta) as puerto:
        url = f"http://127.0.0.1:{puerto}/"
        with pytest.raises(ErrorDescarga, match="tiempo") as error:
            asyncio.run(envoltorio.descargar(url))
    assert proxy.pedidas == [] and envoltorio.dominios_por_proxy() == ()
    assert error.value.__cause__ is None and error.value.__context__ is None
