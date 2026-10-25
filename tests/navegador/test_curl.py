"""`TransporteCurl` sin proxy, contra servidores en 127.0.0.1 (sin Internet ni DNS real).

El nombre `tienda.example` lo «resuelve» un resolver de mentira y curl lo fija con `RESOLVE`, así
que la petición lleva el nombre (Host, SNI, certificado) y va a la IP validada.
"""

import asyncio
import gzip
import socket
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from precia.navegador.con_proxy import conexion_cortada
from precia.navegador.curl import ACCEPT_LANGUAGE_NAVEGADOR, TransporteCurl
from precia.navegador.http import MAXIMO_BYTES, DescargadorHTTP, TransporteNavegador
from precia.navegador.tipos import DemasiadoGrande, ErrorDescarga, URLNoPermitida
from precia.navegador.url import IP, ip_publica
from tests.navegador.servidores_curl import (
    respuesta_http,
    servidor_alpn,
    servidor_clienthello,
    servidor_crudo,
    servidor_goteo,
    servidor_reset,
    valores_grease,
)

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
CABECERAS = {"User-Agent": UA, "Accept-Encoding": "identity", "X-Otra": "no debe salir"}


def es_loopback(ip: IP) -> bool:
    return ip.is_loopback


class ResolverFalso:
    """`getaddrinfo` de mentira: cada nombre a la IP que diga la tabla (cambiable)."""

    def __init__(self, ip: str = "127.0.0.1", *, espera_s: float = 0.0) -> None:
        self.ip = ip
        self.espera_s = espera_s
        self.consultas: list[str] = []

    def __call__(self, host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        self.consultas.append(host)
        if self.espera_s:
            time.sleep(self.espera_s)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.ip, puerto))]


def _resolver_prohibido(*_: object, **__: object) -> list[tuple[Any, ...]]:
    raise AssertionError("Con una IP literal no se pregunta al DNS.")


@pytest.fixture
def curl() -> Iterator[TransporteCurl]:
    transporte = TransporteCurl(ip_permitida=es_loopback, resolver=ResolverFalso())
    try:
        yield transporte
    finally:
        transporte.cerrar()


def pedir(
    transporte: TransporteCurl,
    url: str,
    *,
    timeout: float = 5,
    max_bytes: int = MAXIMO_BYTES,
) -> tuple[int, dict[str, str], bytes]:
    crudo = transporte.pedir(url, CABECERAS, timeout, max_bytes)
    try:
        assert crudo.descomprimida
        return crudo.estado, dict(crudo.cabeceras), crudo.cuerpo.read(1 << 30)
    finally:
        crudo.cuerpo.close()


def cabeceras_recibidas(peticion: bytes) -> list[tuple[str, str]]:
    lineas = peticion.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")[1:]
    return [
        (n.strip().lower(), v.strip()) for n, _, v in (linea.partition(":") for linea in lineas)
    ]


# --- Contrato y anti-SSRF -----------------------------------------------------------------------


def test_cumple_el_contrato_y_fija_la_ip_validada(curl: TransporteCurl) -> None:
    assert isinstance(curl, TransporteNavegador)
    with servidor_crudo(respuesta_http(b"<p>Hola</p>")) as servidor:
        estado, cabeceras, cuerpo = pedir(curl, f"http://tienda.example:{servidor.puerto}/f?x=1")
    assert estado == 200 and cuerpo == b"<p>Hola</p>"
    assert cabeceras["content-type"] == "text/html; charset=utf-8"
    assert all(nombre == nombre.lower() for nombre in cabeceras)
    linea, *_ = servidor.peticiones[0].split(b"\r\n")
    assert linea == b"GET /f?x=1 HTTP/1.1"
    assert ("host", f"tienda.example:{servidor.puerto}") in cabeceras_recibidas(
        servidor.peticiones[0]
    )


def test_ip_literal_no_pregunta_al_dns() -> None:
    transporte = TransporteCurl(ip_permitida=es_loopback, resolver=_resolver_prohibido)
    try:
        with servidor_crudo(respuesta_http()) as servidor:
            assert pedir(transporte, f"http://127.0.0.1:{servidor.puerto}/")[0] == 200
    finally:
        transporte.cerrar()


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1"])
def test_dns_a_una_ip_no_permitida_no_conecta(ip: str) -> None:
    transporte = TransporteCurl(ip_permitida=ip_publica, resolver=ResolverFalso(ip))
    try:
        with servidor_crudo(respuesta_http()) as servidor, pytest.raises(URLNoPermitida):
            pedir(transporte, f"http://tienda.example:{servidor.puerto}/")
        assert servidor.conexiones == 0
    finally:
        transporte.cerrar()


def test_ip_literal_no_permitida_no_conecta() -> None:
    transporte = TransporteCurl(resolver=_resolver_prohibido)  # ip_publica por defecto
    try:
        with servidor_crudo(respuesta_http()) as servidor, pytest.raises(URLNoPermitida):
            pedir(transporte, f"http://127.0.0.1:{servidor.puerto}/")
        assert servidor.conexiones == 0
    finally:
        transporte.cerrar()


def test_ignora_los_proxies_del_entorno(
    curl: TransporteCurl, monkeypatch: pytest.MonkeyPatch
) -> None:
    with servidor_crudo(respuesta_http(b"proxy")) as proxy:
        for variable in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "ALL_PROXY"):
            monkeypatch.setenv(variable, f"http://127.0.0.1:{proxy.puerto}")
        with servidor_crudo(respuesta_http(b"tienda")) as servidor:
            cuerpo = pedir(curl, f"http://tienda.example:{servidor.puerto}/")[2]
    assert cuerpo == b"tienda"
    assert proxy.conexiones == 0


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://127.0.0.1/", "gopher://127.0.0.1:70/", "dict://x.example/"]
)
def test_solo_http_y_https(curl: TransporteCurl, url: str) -> None:
    with pytest.raises(ErrorDescarga, match="http"):
        curl.pedir(url, {}, 5, MAXIMO_BYTES)


def test_no_sigue_redirecciones(curl: TransporteCurl) -> None:
    redireccion = respuesta_http(b"", estado="302 Found", Location="/destino")
    with servidor_crudo(redireccion) as servidor:
        estado, cabeceras, _ = pedir(curl, f"http://tienda.example:{servidor.puerto}/origen")
    assert estado == 302 and cabeceras["location"] == "/destino"
    assert len(servidor.peticiones) == 1


def test_conexion_reutilizada_tras_cambiar_el_dns_no_es_ssrf() -> None:
    # Con keep-alive, curl reutiliza la conexión a una IP que ya pasó la validación aunque el DNS
    # diga ahora otra: no es un rebinding y la petición no se rechaza. La segunda respuesta es del
    # primer servidor y al segundo no llega nada.
    resolver = ResolverFalso("127.0.0.1")
    transporte = TransporteCurl(ip_permitida=es_loopback, resolver=resolver)
    seguir = respuesta_http(b"A", cerrar=False)
    try:
        with servidor_crudo(seguir, keep_alive=True) as primero:
            try:
                otro = servidor_crudo(respuesta_http(b"B"), host="127.0.0.2", puerto=primero.puerto)
                segundo = otro.__enter__()
            except OSError:
                pytest.skip("No se puede escuchar en 127.0.0.2")
            try:
                url = f"http://tienda.example:{primero.puerto}/"
                assert pedir(transporte, url)[2] == b"A"
                resolver.ip = "127.0.0.2"
                assert pedir(transporte, url)[2] == b"A"
            finally:
                # Antes de salir del servidor keep-alive: si no, su hilo espera 5 s al `join`.
                transporte.cerrar()
                otro.__exit__(None, None, None)
        assert primero.conexiones == 1 and len(primero.peticiones) == 2
        assert segundo.conexiones == 0
    finally:
        transporte.cerrar()


class PermitidaSoloAlValidar:
    """`ip_permitida` que acepta las primeras `veces` consultas (las del DNS) y luego rechaza."""

    def __init__(self, veces: int = 1) -> None:
        self.veces = veces
        self.consultas: list[IP] = []

    def __call__(self, ip: IP) -> bool:
        self.consultas.append(ip)
        return len(self.consultas) <= self.veces and ip.is_loopback


def test_comprueba_la_ip_a_la_que_conecto_curl() -> None:
    # Defensa extra tras la respuesta: si la IP real de la conexión no está permitida (una
    # conexión reutilizada o un RESOLVE que no se aplicó), `URLNoPermitida` aunque el DNS
    # validado fuera bueno.
    permitida = PermitidaSoloAlValidar()
    transporte = TransporteCurl(ip_permitida=permitida, resolver=ResolverFalso())
    try:
        with (
            servidor_crudo(respuesta_http()) as servidor,
            pytest.raises(URLNoPermitida, match="no pública"),
        ):
            pedir(transporte, f"http://tienda.example:{servidor.puerto}/")
        assert servidor.conexiones == 1  # sí llegó a conectar: lo para la comprobación posterior
        assert [str(ip) for ip in permitida.consultas] == ["127.0.0.1", "127.0.0.1"]
    finally:
        transporte.cerrar()


def test_sin_ip_de_conexion_conocida_no_se_da_por_buena(
    curl: TransporteCurl, monkeypatch: pytest.MonkeyPatch
) -> None:
    hacer = TransporteCurl._hacer

    def sin_primary_ip(self: TransporteCurl, *args: Any, **kwargs: Any) -> Any:
        respuesta, cuerpo = hacer(self, *args, **kwargs)
        respuesta.primary_ip = ""
        return respuesta, cuerpo

    monkeypatch.setattr(TransporteCurl, "_hacer", sin_primary_ip)
    with (
        servidor_crudo(respuesta_http()) as servidor,
        pytest.raises(URLNoPermitida, match="No se sabe"),
    ):
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    assert servidor.conexiones == 1


# --- Huella de Chrome ---------------------------------------------------------------------------


def test_manda_las_cabeceras_de_chrome_en_su_orden(curl: TransporteCurl) -> None:
    with servidor_crudo(respuesta_http()) as servidor:
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    recibidas = cabeceras_recibidas(servidor.peticiones[0])
    nombres = [nombre for nombre, _ in recibidas]
    valores = dict(recibidas)
    assert nombres.index("sec-ch-ua") < nombres.index("user-agent")
    assert "Chrome/" in valores["user-agent"] and "HeadlessChrome" not in valores["user-agent"]
    assert valores["accept-encoding"] == "gzip, deflate, br, zstd"
    assert valores["accept-language"] == ACCEPT_LANGUAGE_NAVEGADOR
    # Sin `Upgrade: h2c`: HTTP/2 solo por ALPN en TLS, como Chrome.
    assert "upgrade" not in valores and "http2-settings" not in valores


def test_no_manda_las_cabeceras_del_llamador(curl: TransporteCurl) -> None:
    with servidor_crudo(respuesta_http()) as servidor:
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    valores = dict(cabeceras_recibidas(servidor.peticiones[0]))
    assert valores["user-agent"] != UA
    assert valores["accept-encoding"] != "identity"
    assert "x-otra" not in valores


def test_ofrece_h2_por_alpn(certificado_tienda: tuple[Path, Path]) -> None:
    clave, cert = certificado_tienda
    transporte = TransporteCurl(
        ip_permitida=es_loopback, resolver=ResolverFalso(), certificados_ca=str(cert)
    )
    try:
        with servidor_alpn(str(clave), str(cert)) as servidor:
            with pytest.raises(ErrorDescarga):
                transporte.pedir(f"https://tienda.example:{servidor.puerto}/", {}, 5, 1000)
            _esperar(lambda: bool(servidor.peticiones))
    finally:
        transporte.cerrar()
    assert servidor.peticiones[0].startswith(b"h2|PRI * HTTP/2.0")


def test_clienthello_con_grease(curl: TransporteCurl) -> None:
    with servidor_clienthello() as servidor:
        with pytest.raises(ErrorDescarga):
            curl.pedir(f"https://tienda.example:{servidor.puerto}/", {}, 5, 1000)
        _esperar(lambda: bool(servidor.peticiones))
    assert valores_grease(servidor.peticiones[0])


def test_no_aplica_hsts(certificado_tienda: tuple[Path, Path]) -> None:
    # Si curl hiciera caso del Strict-Transport-Security, pasaría http:// a https:// por su
    # cuenta (y a otro puerto, fuera de RESOLVE). La segunda petición va en claro y falla.
    import ssl

    clave, cert = certificado_tienda
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(cert, clave)
    transporte = TransporteCurl(
        ip_permitida=es_loopback, resolver=ResolverFalso(), certificados_ca=str(cert)
    )
    sts = respuesta_http(Strict_Transport_Security="max-age=31536000")
    try:
        with servidor_crudo(sts, tls=contexto) as servidor:
            assert pedir(transporte, f"https://tienda.example:{servidor.puerto}/")[0] == 200
            with pytest.raises(ErrorDescarga):
                pedir(transporte, f"http://tienda.example:{servidor.puerto}/")
    finally:
        transporte.cerrar()


def test_no_sigue_alt_svc(certificado_tienda: tuple[Path, Path]) -> None:
    # Si curl siguiera el Alt-Svc, iría a otro puerto, fuera de RESOLVE, resolviendo el nombre con
    # su propio DNS (rebinding). libcurl solo lo acepta por HTTPS: por http:// el test no probaría
    # nada (con Alt-Svc activado, la segunda petición falla al resolver `tienda.example`).
    import ssl

    clave, cert = certificado_tienda
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(cert, clave)
    transporte = TransporteCurl(
        ip_permitida=es_loopback, resolver=ResolverFalso(), certificados_ca=str(cert)
    )
    try:
        with servidor_crudo(respuesta_http(b"otro"), tls=contexto) as otro:
            alt = respuesta_http(b"origen", Alt_Svc=f'http/1.1=":{otro.puerto}"; ma=86400')
            with servidor_crudo(alt, tls=contexto) as servidor:
                url = f"https://tienda.example:{servidor.puerto}/"
                assert pedir(transporte, url)[2] == b"origen"
                assert pedir(transporte, url)[2] == b"origen"
        assert otro.conexiones == 0
        assert len(servidor.peticiones) == 2
    finally:
        transporte.cerrar()


# --- Compresión y topes -------------------------------------------------------------------------

HTML = "<p>Pastillas de freno — 25,90 €</p>".encode()


def _zstd_crudo(datos: bytes) -> bytes:
    """Una trama zstd con un solo bloque sin comprimir (RFC 8878), hecha a mano."""
    assert len(datos) < 256
    cabecera_bloque = (len(datos) << 3 | 1).to_bytes(3, "little")  # último bloque, tipo «raw»
    return b"\x28\xb5\x2f\xfd" + bytes([0x20, len(datos)]) + cabecera_bloque + datos


def _deflate(datos: bytes) -> bytes:
    return zlib.compress(datos)


@pytest.mark.parametrize(
    ("codificacion", "cuerpo"),
    [
        ("gzip", gzip.compress(HTML)),
        ("deflate", _deflate(HTML)),
        ("zstd", _zstd_crudo(HTML)),
        ("gzip, gzip", gzip.compress(gzip.compress(HTML))),
        ("GZIP", gzip.compress(HTML)),
        ("identity, gzip", gzip.compress(HTML)),
    ],
)
def test_descomprime(curl: TransporteCurl, codificacion: str, cuerpo: bytes) -> None:
    with servidor_crudo(respuesta_http(cuerpo, Content_Encoding=codificacion)) as servidor:
        _, cabeceras, recibido = pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    assert recibido == HTML
    assert "content-encoding" not in cabeceras and "content-length" not in cabeceras


@cache
def _bomba_gzip(descomprimidos: int) -> bytes:
    compresor = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    bloque = b"\x00" * (1 << 20)
    partes = [compresor.compress(bloque) for _ in range(descomprimidos >> 20)]
    return b"".join([*partes, compresor.flush()])


def test_bomba_gzip_se_para_en_el_tope(curl: TransporteCurl) -> None:
    bomba = respuesta_http(_bomba_gzip(200 << 20), Content_Encoding="gzip")
    with servidor_crudo(bomba) as servidor:
        inicio = time.monotonic()
        with pytest.raises(ErrorDescarga, match="grande") as error:
            pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    assert time.monotonic() - inicio < 2
    assert error.value.__cause__ is None


def test_content_length_mayor_que_el_tope(curl: TransporteCurl) -> None:
    grande = respuesta_http(b"x" * 1000, Content_Length=str(10 << 20))
    with servidor_crudo(grande) as servidor, pytest.raises(ErrorDescarga, match="grande"):
        pedir(curl, f"http://tienda.example:{servidor.puerto}/", max_bytes=1 << 20)


def test_compresion_desconocida(curl: TransporteCurl) -> None:
    raro = respuesta_http(b"xx", Content_Encoding="compress")
    with servidor_crudo(raro) as servidor, pytest.raises(ErrorDescarga, match="compresión"):
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")


def test_la_sesion_sigue_valida_tras_el_tope(curl: TransporteCurl) -> None:
    with servidor_crudo(respuesta_http(b"y" * 5000)) as servidor:
        url = f"http://tienda.example:{servidor.puerto}/"
        with pytest.raises(ErrorDescarga, match="grande"):
            pedir(curl, url, max_bytes=1000)
        assert pedir(curl, url)[2] == b"y" * 5000


def test_con_el_descargador_http_completo(curl: TransporteCurl) -> None:
    async def permisivo(url: str) -> str:
        return url

    d = DescargadorHTTP(UA, validador=permisivo, transporte=curl)
    comprimida = respuesta_http(gzip.compress(HTML), Content_Encoding="gzip")
    with servidor_crudo(comprimida) as servidor:
        pagina = asyncio.run(d.descargar(f"http://tienda.example:{servidor.puerto}/"))
    assert pagina.html == HTML.decode() and pagina.tipo_contenido == "text/html"
    enorme = respuesta_http(b"z" * (6 << 20))
    with servidor_crudo(enorme) as servidor:
        url = f"http://tienda.example:{servidor.puerto}/"
        with pytest.raises(ErrorDescarga, match="grande"):
            asyncio.run(d.descargar(url))
        # El mismo fichero cabe en el tope de los sitemaps (20 MiB).
        assert len(asyncio.run(d.descargar_grande(url)).html) == 6 << 20


# --- Tiempos y cortes ---------------------------------------------------------------------------


def _esperar(condicion: Callable[[], bool], tope_s: float = 3) -> None:
    limite = time.monotonic() + tope_s
    while not condicion() and time.monotonic() < limite:
        time.sleep(0.01)


def _falla_a_tiempo(transporte: TransporteCurl, url: str, timeout: float = 1) -> ErrorDescarga:
    inicio = time.monotonic()
    with pytest.raises(ErrorDescarga, match="tiempo") as error:
        transporte.pedir(url, {}, timeout, MAXIMO_BYTES)
    assert time.monotonic() - inicio < timeout + 2
    assert error.value.__cause__ is None
    assert not conexion_cortada(error.value)
    return error.value


def test_cabeceras_a_goteo_respetan_el_tiempo_total(curl: TransporteCurl) -> None:
    with servidor_goteo(b"HTTP/1.1 200 OK\r\nX-Relleno: ") as servidor:
        _falla_a_tiempo(curl, f"http://tienda.example:{servidor.puerto}/")


def test_handshake_tls_a_goteo_respeta_el_tiempo_total(curl: TransporteCurl) -> None:
    with servidor_goteo(bytes([0x16, 0x03, 0x03, 0x3E, 0x80])) as servidor:
        _falla_a_tiempo(curl, f"https://tienda.example:{servidor.puerto}/")


def test_el_tiempo_incluye_el_dns() -> None:
    # Si el DNS no contara, tardaría la espera del DNS más el timeout entero. El tope queda a
    # mitad de camino entre lo bueno (el timeout) y lo malo (timeout + DNS): antes eran 1,5 s
    # con 1 + 0,6, y lo malo (1,6 s) apenas se distinguía de lo bueno con la máquina cargada.
    timeout, dns = 2.0, 1.2
    lento = TransporteCurl(ip_permitida=es_loopback, resolver=ResolverFalso(espera_s=dns))
    try:
        with servidor_goteo(b"HTTP/1.1 200 OK\r\nX-Relleno: ") as servidor:
            inicio = time.monotonic()
            _falla_a_tiempo(lento, f"http://tienda.example:{servidor.puerto}/", timeout)
            assert time.monotonic() - inicio < timeout + dns / 2
    finally:
        lento.cerrar()


def test_si_el_dns_agota_el_tiempo_no_se_conecta() -> None:
    lento = TransporteCurl(ip_permitida=es_loopback, resolver=ResolverFalso(espera_s=1.1))
    try:
        with servidor_crudo(respuesta_http()) as servidor:
            _falla_a_tiempo(lento, f"http://tienda.example:{servidor.puerto}/")
        assert servidor.conexiones == 0
    finally:
        lento.cerrar()


def test_reset_cuenta_como_corte_y_no_se_repite(curl: TransporteCurl) -> None:
    with servidor_reset() as servidor, pytest.raises(ErrorDescarga, match="cortó") as error:
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    assert conexion_cortada(error.value)
    assert servidor.conexiones == 1


def test_reset_tras_el_clienthello_cuenta_como_corte(curl: TransporteCurl) -> None:
    # Así corta un WAF que no reconoce la huella TLS (JA3/JA4): curl da el código 35.
    with servidor_reset(tras_clienthello=True) as servidor, pytest.raises(ErrorDescarga) as error:
        pedir(curl, f"https://tienda.example:{servidor.puerto}/")
    assert conexion_cortada(error.value)


def test_respuesta_vacia_cuenta_como_corte(curl: TransporteCurl) -> None:
    with servidor_crudo(b"") as servidor, pytest.raises(ErrorDescarga) as error:
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    assert conexion_cortada(error.value)


def test_conexion_rechazada_no_es_corte(curl: TransporteCurl) -> None:
    with socket.create_server(("127.0.0.1", 0)) as libre:
        puerto = libre.getsockname()[1]
    with pytest.raises(ErrorDescarga, match="No se pudo conectar") as error:
        pedir(curl, f"http://tienda.example:{puerto}/")
    assert not conexion_cortada(error.value)


def test_cabeceras_de_mas_de_300_kb_no_son_corte(curl: TransporteCurl) -> None:
    galletas = "".join(f"Set-Cookie: c{n}=" + "v" * 20 + "\r\n" for n in range(20_000))
    respuesta = f"HTTP/1.1 200 OK\r\n{galletas}Content-Length: 0\r\n\r\n".encode()
    with servidor_crudo(respuesta) as servidor, pytest.raises(ErrorDescarga) as error:
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    assert not conexion_cortada(error.value)


@pytest.mark.parametrize(
    "respuesta",
    [
        b"HTTP/1.1 200 OK\r\n plegada\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nX-Nul: a\x00b\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nsin dos puntos\r\nContent-Length: 0\r\n\r\n",
        b"<html>HTTP/0.9</html>",
        b"HTTP/1.1 2OO OK\r\nContent-Length: 0\r\n\r\n",
    ],
    ids=["plegada-al-principio", "nul", "sin-dos-puntos", "http-0.9", "estado-raro"],
)
def test_respuestas_hostiles_dan_error_de_descarga(curl: TransporteCurl, respuesta: bytes) -> None:
    with servidor_crudo(respuesta) as servidor, pytest.raises(ErrorDescarga):
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")


def test_https_con_el_certificado_de_la_tienda(certificado_tienda: tuple[Path, Path]) -> None:
    import ssl

    clave, cert = certificado_tienda
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(cert, clave)
    confiado = TransporteCurl(
        ip_permitida=es_loopback, resolver=ResolverFalso(), certificados_ca=str(cert)
    )
    sin_ca = TransporteCurl(ip_permitida=es_loopback, resolver=ResolverFalso())
    try:
        with servidor_crudo(respuesta_http(b"segura"), tls=contexto) as servidor:
            url = f"https://tienda.example:{servidor.puerto}/"
            assert pedir(confiado, url)[2] == b"segura"
            with pytest.raises(ErrorDescarga):
                pedir(sin_ca, url)
    finally:
        confiado.cerrar()
        sin_ca.cerrar()


# --- Sesiones: cookies, concurrencia y cierre ---------------------------------------------------


def _galletas(peticion: bytes) -> str:
    return dict(cabeceras_recibidas(peticion)).get("cookie", "")


def test_cookies_aisladas_por_tienda(curl: TransporteCurl) -> None:
    def responder(peticion: bytes) -> bytes:
        if b"/poner" in peticion:
            return respuesta_http(Set_Cookie="sesion=1; Path=/")
        return respuesta_http()

    with servidor_crudo(responder) as servidor:
        p = servidor.puerto
        pedir(curl, f"http://tienda.example:{p}/poner")
        pedir(curl, f"http://tienda.example:{p}/ver")
        pedir(curl, f"http://otra.example:{p}/ver")
    assert "sesion=1" in _galletas(servidor.peticiones[1])
    assert _galletas(servidor.peticiones[2]) == ""


def test_tope_de_cookies() -> None:
    transporte = TransporteCurl(ip_permitida=es_loopback, resolver=ResolverFalso(), max_cookies=3)

    def responder(peticion: bytes) -> bytes:
        if b"/poner" not in peticion:
            return respuesta_http()
        galletas = "".join(f"Set-Cookie: c{n}=1; Path=/\r\n" for n in range(5))
        return f"HTTP/1.1 200 OK\r\n{galletas}Content-Length: 0\r\n\r\n".encode()

    try:
        with servidor_crudo(responder) as servidor:
            pedir(transporte, f"http://tienda.example:{servidor.puerto}/poner")
            pedir(transporte, f"http://tienda.example:{servidor.puerto}/ver")
        assert _galletas(servidor.peticiones[1]) == ""
    finally:
        transporte.cerrar()


def _en_otro_hilo(funcion: Callable[[], object]) -> tuple[threading.Thread, list[object]]:
    resultado: list[object] = []

    def correr() -> None:
        try:
            resultado.append(funcion())
        except Exception as error:
            resultado.append(error)

    hilo = threading.Thread(target=correr, daemon=True)
    hilo.start()
    return hilo, resultado


def test_peticiones_concurrentes_a_tiendas_distintas(curl: TransporteCurl) -> None:
    with (
        servidor_goteo(b"HTTP/1.1 200 OK\r\nX-Relleno: ") as lenta,
        servidor_crudo(respuesta_http(b"rapida")) as rapida,
    ):
        hilo, _ = _en_otro_hilo(lambda: pedir(curl, f"http://a.example:{lenta.puerto}/", timeout=2))
        _esperar(lambda: lenta.conexiones == 1)
        inicio = time.monotonic()
        assert pedir(curl, f"http://b.example:{rapida.puerto}/")[2] == b"rapida"
        assert time.monotonic() - inicio < 1
        hilo.join(5)


def test_cerrar_no_espera_a_la_peticion_en_vuelo(curl: TransporteCurl) -> None:
    with servidor_goteo(b"HTTP/1.1 200 OK\r\nX-Relleno: ") as lenta:
        hilo, resultado = _en_otro_hilo(
            lambda: pedir(curl, f"http://a.example:{lenta.puerto}/", timeout=2)
        )
        _esperar(lambda: lenta.conexiones == 1)
        inicio = time.monotonic()
        curl.cerrar()
        assert time.monotonic() - inicio < 0.5
        hilo.join(5)
    assert isinstance(resultado[0], ErrorDescarga)
    with pytest.raises(ErrorDescarga, match="cerrado"):
        curl.pedir("http://a.example/", {}, 1, MAXIMO_BYTES)


def test_el_desalojo_lru_no_espera_a_la_sesion_ocupada() -> None:
    transporte = TransporteCurl(ip_permitida=es_loopback, resolver=ResolverFalso(), max_sesiones=2)
    try:
        with (
            servidor_goteo(b"HTTP/1.1 200 OK\r\nX-Relleno: ") as lenta,
            servidor_crudo(respuesta_http()) as rapida,
        ):
            hilo, _ = _en_otro_hilo(
                lambda: pedir(transporte, f"http://a.example:{lenta.puerto}/", timeout=2)
            )
            _esperar(lambda: lenta.conexiones == 1)
            inicio = time.monotonic()
            pedir(transporte, f"http://b.example:{rapida.puerto}/")
            pedir(transporte, f"http://c.example:{rapida.puerto}/")  # desaloja a.example
            assert time.monotonic() - inicio < 1
            hilo.join(5)
        assert sorted(transporte._sesiones) == ["b.example", "c.example"]
    finally:
        transporte.cerrar()


def test_no_quedan_opciones_tras_la_peticion(curl: TransporteCurl) -> None:
    with servidor_crudo(respuesta_http()) as servidor:
        pedir(curl, f"http://tienda.example:{servidor.puerto}/")
    assert [e.sesion.curl_options for e in curl._sesiones.values()] == [{}]


def test_menos_de_un_milisegundo_de_plazo_cuenta_como_agotado() -> None:
    # curl_cffi pasa el plazo como `int(timeout * 1000)`: con 0,5 ms quedaría TIMEOUT_MS = 0, que
    # en curl es «sin límite», y la petición podría colgarse con el candado de la tienda tomado.
    from precia.navegador.curl import _restante

    with pytest.raises(ErrorDescarga, match="tiempo"):
        _restante(time.monotonic() + 0.0005)
    assert _restante(time.monotonic() + 1) > 0.5


def test_el_tope_es_demasiado_grande(curl: TransporteCurl) -> None:
    # A5 del plan de velocidad: el catálogo distingue el tope de tamaño de los demás fallos.
    with servidor_crudo(respuesta_http(b"y" * 5000)) as servidor, pytest.raises(DemasiadoGrande):
        pedir(curl, f"http://tienda.example:{servidor.puerto}/", max_bytes=1000)
    grande = respuesta_http(b"x" * 1000, Content_Length=str(10 << 20))
    with servidor_crudo(grande) as servidor, pytest.raises(DemasiadoGrande):
        pedir(curl, f"http://tienda.example:{servidor.puerto}/", max_bytes=1 << 20)
