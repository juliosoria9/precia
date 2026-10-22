"""Transporte HTTP por un proxy con `CONNECT`, contra un proxy y una tienda falsos en 127.0.0.1.

Con un proxy remoto (Evomi), el nombre de la tienda lo resuelve el proxy: el transporte no
pregunta al DNS local. Con un proxy de la red local (o del propio equipo), el nombre se resuelve
aquí, la IP se valida (`ip_publica`) y el `CONNECT` va a esa IP: si no, una tienda con *DNS
rebinding* podría usar ese proxy para llegar a la red interna. Los proxies falsos de estos tests
están en 127.0.0.1, así que el modo remoto se simula con `proxy_remoto`.
"""

import asyncio
import contextlib
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from precia.navegador.http import DescargadorHTTP, TransporteURLLib
from precia.navegador.proxy import Proxy
from precia.navegador.tipos import ErrorDescarga, URLNoPermitida
from tests.navegador.servidores_proxy import CUERPO_TIENDA, ServidorTienda, proxy_falso, tienda

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
USUARIO = "usuario-proxy-prueba"
SECRETO = "contrasena-proxy-prueba_country-ES"


PROXY_SIN_PUERTO = Proxy("127.0.0.1", 1, USUARIO, SECRETO, nombre="Proxy de prueba")


def _resolver_prohibido(*_: object, **__: object) -> list[tuple[object, ...]]:
    raise AssertionError("Con proxy, el nombre de la tienda lo resuelve el proxy, no este equipo.")


def _siempre_remoto(_: object) -> bool:
    return True


def transporte(puerto: int, *, contexto_tls: ssl.SSLContext | None = None) -> TransporteURLLib:
    """Como con Evomi: el proxy es remoto y resuelve él el nombre de la tienda."""
    proxy = Proxy("127.0.0.1", puerto, USUARIO, SECRETO, nombre="Proxy de prueba")
    return TransporteURLLib(
        proxy=proxy,
        resolver=_resolver_prohibido,
        contexto_tls=contexto_tls,
        proxy_remoto=_siempre_remoto,
    )


IP_TIENDA = "93.184.215.14"


def resolver_a(ip: str) -> Any:
    def resolver(host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        assert host == "tienda.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, puerto))]

    return resolver


def transporte_local(
    puerto: int, ip: str = IP_TIENDA, *, contexto_tls: ssl.SSLContext | None = None
) -> TransporteURLLib:
    """Un proxy en este equipo (127.0.0.1, como un Squid propio): el destino se resuelve aquí."""
    proxy = Proxy("127.0.0.1", puerto, USUARIO, SECRETO, nombre="Proxy de prueba")
    return TransporteURLLib(proxy=proxy, resolver=resolver_a(ip), contexto_tls=contexto_tls)


def _sin_secretos(error: BaseException) -> None:
    actual: BaseException | None = error
    while actual is not None:
        for texto in (str(actual), repr(actual)):
            assert SECRETO not in texto and USUARIO not in texto
        actual = actual.__cause__ or actual.__context__


# --- HTTP ---------------------------------------------------------------------------------------


def test_http_va_por_un_tunel_connect_con_autorizacion() -> None:
    with (
        tienda() as servidor,
        proxy_falso(
            servidor.server_address[1], esperada=PROXY_SIN_PUERTO.cabecera_autorizacion
        ) as (puerto, estado),
    ):
        crudo = transporte(puerto)("http://tienda.example/ficha?x=1", {"User-Agent": UA}, 5)
        try:
            cuerpo = crudo.cuerpo.read(65536)
        finally:
            crudo.cuerpo.close()
    assert crudo.estado == 200
    assert cuerpo == CUERPO_TIENDA.encode()
    assert estado.connects == ["CONNECT tienda.example:80 HTTP/1.1"]
    assert estado.autorizaciones == [PROXY_SIN_PUERTO.cabecera_autorizacion]
    ruta, cabeceras = servidor.peticiones[0]
    assert ruta == "/ficha?x=1"
    assert cabeceras["host"] == "tienda.example"
    assert cabeceras["user-agent"] == UA
    # La autorización del proxy se queda en el CONNECT: la tienda nunca la ve.
    assert "proxy-authorization" not in cabeceras


def test_descargador_completo_por_el_proxy() -> None:
    async def permisivo(url: str) -> str:
        return url

    with (
        tienda() as servidor,
        proxy_falso(
            servidor.server_address[1], esperada=PROXY_SIN_PUERTO.cabecera_autorizacion
        ) as (puerto, _),
    ):
        d = DescargadorHTTP(UA, validador=permisivo, transporte=transporte(puerto))
        pagina = asyncio.run(d.descargar("http://tienda.example:8080/"))
    assert pagina.html == CUERPO_TIENDA
    assert servidor.peticiones[0][1]["host"] == "tienda.example:8080"


def test_proxy_que_rechaza_las_credenciales() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada="Basic otra") as (puerto, _),
        pytest.raises(ErrorDescarga) as error,
    ):
        transporte(puerto)("http://tienda.example/", {"User-Agent": UA}, 5)
    assert "407" in str(error.value) and "proxy" in str(error.value).lower()
    assert servidor.peticiones == []
    _sin_secretos(error.value)


def test_proxy_que_no_puede_llegar_a_la_tienda() -> None:
    fallo = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
    with (
        proxy_falso(1, esperada=None, respuesta_fallo=fallo) as (puerto, _),
        pytest.raises(ErrorDescarga, match="502") as error,
    ):
        transporte(puerto)("http://tienda.example/", {"User-Agent": UA}, 5)
    _sin_secretos(error.value)


def test_proxy_inalcanzable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as libre:
        puerto = libre.getsockname()[1]
    with pytest.raises(ErrorDescarga, match="proxy") as error:
        transporte(puerto)("http://tienda.example/", {"User-Agent": UA}, 5)
    _sin_secretos(error.value)


def test_proxy_que_no_contesta_respeta_el_tiempo() -> None:
    escucha = socket.create_server(("127.0.0.1", 0))
    aceptadas: list[socket.socket] = []

    def aceptar() -> None:
        escucha.settimeout(5)
        with contextlib.suppress(OSError):
            aceptadas.append(escucha.accept()[0])

    hilo = threading.Thread(target=aceptar, daemon=True)
    hilo.start()
    inicio = time.monotonic()
    try:
        with pytest.raises(ErrorDescarga):
            transporte(escucha.getsockname()[1])("http://tienda.example/", {}, 1)
        assert time.monotonic() - inicio < 5
    finally:
        escucha.close()
        hilo.join(5)
        for conexion in aceptadas:
            conexion.close()


# --- HTTPS --------------------------------------------------------------------------------------


@pytest.fixture
def certificado(tmp_path: Path) -> tuple[Path, Path]:
    """Certificado autofirmado para `tienda.example` (el nombre del destino, no del proxy)."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl no está instalado")
    clave, cert = tmp_path / "clave.pem", tmp_path / "cert.pem"
    orden = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1"]
    orden += ["-subj", "/CN=tienda.example", "-addext", "subjectAltName=DNS:tienda.example"]
    orden += ["-keyout", str(clave), "-out", str(cert)]
    subprocess.run(orden, check=True, capture_output=True, timeout=60)
    return clave, cert


@contextlib.contextmanager
def tienda_tls(certificado: tuple[Path, Path]) -> Iterator[ServidorTienda]:
    clave, cert = certificado
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(cert, clave)
    with tienda(contexto) as servidor:
        yield servidor


def test_https_por_el_tunel_verifica_el_certificado_del_destino(
    certificado: tuple[Path, Path],
) -> None:
    cliente_tls = ssl.create_default_context(cafile=str(certificado[1]))
    with (
        tienda_tls(certificado) as servidor,
        proxy_falso(
            servidor.server_address[1], esperada=PROXY_SIN_PUERTO.cabecera_autorizacion
        ) as (puerto, estado),
    ):
        crudo = transporte(puerto, contexto_tls=cliente_tls)(
            "https://tienda.example/ficha", {"User-Agent": UA}, 5
        )
        try:
            cuerpo = crudo.cuerpo.read(65536)
        finally:
            crudo.cuerpo.close()
    assert crudo.estado == 200 and "túnel" in cuerpo.decode()
    assert estado.connects == ["CONNECT tienda.example:443 HTTP/1.1"]
    assert "proxy-authorization" not in servidor.peticiones[0][1]


def test_https_con_el_certificado_de_otro_nombre_falla(certificado: tuple[Path, Path]) -> None:
    # El túnel llega a una tienda cuyo certificado es de `tienda.example`, pero se pidió
    # `otra.example`: TLS se comprueba contra el destino y la conexión se rechaza.
    cliente_tls = ssl.create_default_context(cafile=str(certificado[1]))
    with (
        tienda_tls(certificado) as servidor,
        proxy_falso(
            servidor.server_address[1], esperada=PROXY_SIN_PUERTO.cabecera_autorizacion
        ) as (puerto, _),
        pytest.raises(ErrorDescarga) as error,
    ):
        transporte(puerto, contexto_tls=cliente_tls)("https://otra.example/", {}, 5)
    assert servidor.peticiones == []
    _sin_secretos(error.value)


def test_https_por_un_proxy_local_va_a_la_ip_validada_con_el_nombre_en_tls(
    certificado: tuple[Path, Path],
) -> None:
    # El `CONNECT` va a la IP ya validada, pero TLS (SNI y certificado) sigue siendo el del nombre.
    cliente_tls = ssl.create_default_context(cafile=str(certificado[1]))
    with (
        tienda_tls(certificado) as servidor,
        proxy_falso(
            servidor.server_address[1], esperada=PROXY_SIN_PUERTO.cabecera_autorizacion
        ) as (puerto, estado),
    ):
        crudo = transporte_local(puerto, contexto_tls=cliente_tls)(
            "https://tienda.example/ficha", {"User-Agent": UA}, 5
        )
        try:
            cuerpo = crudo.cuerpo.read(65536)
        finally:
            crudo.cuerpo.close()
    assert crudo.estado == 200 and "túnel" in cuerpo.decode()
    assert estado.connects == [f"CONNECT {IP_TIENDA}:443 HTTP/1.1"]
    assert servidor.peticiones[0][1]["host"] == "tienda.example"


# --- Proxy local: contra el DNS rebinding -------------------------------------------------------


def test_proxy_local_conecta_a_la_ip_validada() -> None:
    with (
        tienda() as servidor,
        proxy_falso(
            servidor.server_address[1], esperada=PROXY_SIN_PUERTO.cabecera_autorizacion
        ) as (puerto, estado),
    ):
        crudo = transporte_local(puerto)("http://tienda.example/ficha", {"User-Agent": UA}, 5)
        crudo.cuerpo.close()
    assert crudo.estado == 200
    assert estado.connects == [f"CONNECT {IP_TIENDA}:80 HTTP/1.1"]
    # La tienda sigue recibiendo su nombre en `Host`.
    assert servidor.peticiones[0][1]["host"] == "tienda.example"


@pytest.mark.parametrize("ip", ["192.168.1.1", "127.0.0.1", "169.254.169.254", "10.0.0.7"])
def test_proxy_local_no_lleva_a_la_red_interna(ip: str) -> None:
    # `validar_url` vio una IP pública, pero ahora el nombre resuelve a una interna (rebinding):
    # ni siquiera se pide el túnel.
    with (
        tienda() as servidor,
        proxy_falso(
            servidor.server_address[1], esperada=PROXY_SIN_PUERTO.cabecera_autorizacion
        ) as (puerto, estado),
        pytest.raises(URLNoPermitida),
    ):
        transporte_local(puerto, ip)("http://tienda.example/", {"User-Agent": UA}, 5)
    assert estado.connects == [] and servidor.peticiones == []


# --- Proxy que no habla HTTP o que no contesta a tiempo -----------------------------------------


@pytest.mark.parametrize(
    "respuesta",
    [
        b"SSH-2.0-OpenSSH_9.6\r\n",
        b"\x05\xff",  # el puerto SOCKS5 de Evomi (1002) en lugar del HTTP (1000)
        b"HTTP/1.1 200 OK\r\n" + b"".join(b"X-%d: 1\r\n" % n for n in range(150)) + b"\r\n",
    ],
    ids=["ssh", "socks5", "demasiadas-cabeceras"],
)
def test_proxy_que_no_habla_http_se_dice(respuesta: bytes) -> None:
    with (
        proxy_falso(1, esperada=None, respuesta_fallo=respuesta) as (puerto, _),
        pytest.raises(ErrorDescarga, match="proxy HTTP") as error,
    ):
        transporte(puerto)("http://tienda.example/", {"User-Agent": UA}, 5)
    _sin_secretos(error.value)


def test_proxy_que_no_habla_http_con_el_descargador() -> None:
    with (
        proxy_falso(1, esperada=None, respuesta_fallo=b"SSH-2.0-x\r\n") as (puerto, _),
        pytest.raises(ErrorDescarga, match="proxy HTTP"),
    ):
        asyncio.run(
            DescargadorHTTP(UA, validador=_permisivo, transporte=transporte(puerto)).descargar(
                "http://tienda.example/"
            )
        )


def test_proxy_que_gotea_el_connect_da_tiempo_agotado() -> None:
    with (
        proxy_falso(1, esperada=PROXY_SIN_PUERTO.cabecera_autorizacion, goteo_s=0.2) as (
            puerto,
            _,
        ),
        pytest.raises(ErrorDescarga, match="tiempo") as error,
    ):
        transporte(puerto)("http://tienda.example/", {"User-Agent": UA}, 0.6)
    _sin_secretos(error.value)


async def _permisivo(url: str) -> str:
    return url
