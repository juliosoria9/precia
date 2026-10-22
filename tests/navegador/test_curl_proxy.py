"""`TransporteCurl` por un proxy `CONNECT`, con el proxy y la tienda falsos de `servidores_proxy`.

Con un proxy remoto (Evomi) va curl con el nombre de la tienda en el `CONNECT`; el nombre del
proxy se resuelve una vez y se fija con `RESOLVE`. Con un proxy local va `TransporteURLLib`, que
pide el túnel a la IP ya validada (decisión de Julio, S03). Los proxies falsos están en 127.0.0.1,
así que el modo remoto se simula con `proxy_remoto`. Ningún error puede citar las credenciales.
"""

import contextlib
import socket
import ssl
import threading
import time
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from precia.navegador.curl import TransporteCurl
from precia.navegador.http import MAXIMO_BYTES, RespuestaCruda
from precia.navegador.proxy import Proxy
from precia.navegador.tipos import ErrorDescarga, URLNoPermitida
from tests.navegador.servidores_proxy import CUERPO_TIENDA, ServidorTienda, proxy_falso, tienda

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
USUARIO = "usuario-proxy-prueba"
SECRETO = "contrasena-proxy-prueba_country-ES"
AUTORIZACION = Proxy("127.0.0.1", 1, USUARIO, SECRETO).cabecera_autorizacion
IP_TIENDA = "93.184.215.14"


def _resolver_prohibido(*_: object, **__: object) -> list[tuple[Any, ...]]:
    raise AssertionError("Con un proxy remoto, el nombre de la tienda lo resuelve el proxy.")


def _siempre_remoto(_: object) -> bool:
    return True


def remoto(
    puerto: int, *, ca: Path | None = None, contrasena: str = SECRETO, host: str = "127.0.0.1"
) -> TransporteCurl:
    """Como con Evomi: curl por el túnel, con el nombre de la tienda en el `CONNECT`."""
    return TransporteCurl(
        proxy=Proxy(host, puerto, USUARIO, contrasena, nombre="Proxy de prueba"),
        resolver=_resolver_prohibido,
        proxy_remoto=_siempre_remoto,
        certificados_ca=None if ca is None else str(ca),
    )


def resolver_a(ip: str) -> Any:
    def resolver(host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        assert host == "tienda.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, puerto))]

    return resolver


def local(puerto: int, ip: str = IP_TIENDA, *, ca: Path | None = None) -> TransporteCurl:
    """Un proxy en este equipo (como un Squid propio): va urllib con el túnel a la IP validada."""
    return TransporteCurl(
        proxy=Proxy("127.0.0.1", puerto, USUARIO, SECRETO, nombre="Proxy de prueba"),
        resolver=resolver_a(ip),
        certificados_ca=None if ca is None else str(ca),
    )


def pedir(transporte: TransporteCurl, url: str, timeout: float = 5) -> tuple[RespuestaCruda, bytes]:
    try:
        crudo = transporte.pedir(url, {"User-Agent": UA}, timeout, MAXIMO_BYTES)
        try:
            return crudo, crudo.cuerpo.read(1 << 20)
        finally:
            crudo.cuerpo.close()
    finally:
        transporte.cerrar()


def _sin_secretos(error: BaseException) -> None:
    textos = ["".join(traceback.format_exception(error))]
    actual: BaseException | None = error
    for _ in range(10):
        if actual is None:
            break
        textos += [str(actual), repr(actual)]
        for vecino in (actual.__cause__, actual.__context__):
            if vecino is not None:
                textos += [str(vecino), repr(vecino)]
        actual = actual.__cause__ or actual.__context__
    for texto in textos:
        assert SECRETO not in texto and USUARIO not in texto


@contextlib.contextmanager
def tienda_tls(certificado: tuple[Path, Path]) -> Iterator[ServidorTienda]:
    clave, cert = certificado
    contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    contexto.load_cert_chain(cert, clave)
    with tienda(contexto) as servidor:
        yield servidor


# --- Proxy remoto: curl por el túnel ------------------------------------------------------------


def test_http_va_por_un_tunel_connect_con_autorizacion() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        crudo, cuerpo = pedir(remoto(puerto), "http://tienda.example/ficha?x=1")
    assert crudo.estado == 200 and cuerpo == CUERPO_TIENDA.encode()
    assert estado.connects == ["CONNECT tienda.example:80 HTTP/1.1"]
    assert estado.autorizaciones == [AUTORIZACION]
    ruta, cabeceras = servidor.peticiones[0]
    assert ruta == "/ficha?x=1" and cabeceras["host"] == "tienda.example"
    assert "Chrome/" in cabeceras["user-agent"]
    assert "proxy-authorization" not in cabeceras


def test_https_por_el_tunel_verifica_el_certificado_del_destino(
    certificado_tienda: tuple[Path, Path],
) -> None:
    cert = certificado_tienda[1]
    with (
        tienda_tls(certificado_tienda) as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        crudo, cuerpo = pedir(remoto(puerto, ca=cert), "https://tienda.example/ficha")
        assert crudo.estado == 200 and "túnel" in cuerpo.decode()
        assert estado.connects == ["CONNECT tienda.example:443 HTTP/1.1"]
        with pytest.raises(ErrorDescarga) as error:
            pedir(remoto(puerto, ca=cert), "https://otra.example/")
    _sin_secretos(error.value)


def test_no_proxy_del_entorno_no_salta_el_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        pedir(remoto(puerto), "http://tienda.example/")
    assert estado.connects == ["CONNECT tienda.example:80 HTTP/1.1"]


def test_proxy_que_rechaza_las_credenciales() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada="Basic otra") as (puerto, _),
        pytest.raises(ErrorDescarga) as error,
    ):
        pedir(remoto(puerto), "http://tienda.example/")
    assert "407" in str(error.value) and "proxy" in str(error.value).lower()
    assert servidor.peticiones == []
    _sin_secretos(error.value)


def test_proxy_que_no_puede_llegar_a_la_tienda() -> None:
    fallo = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
    with (
        proxy_falso(1, esperada=None, respuesta_fallo=fallo) as (puerto, _),
        pytest.raises(ErrorDescarga, match="502") as error,
    ):
        pedir(remoto(puerto), "http://tienda.example/")
    _sin_secretos(error.value)


@pytest.mark.parametrize(
    "respuesta",
    [b"SSH-2.0-OpenSSH_9.6\r\n", b"\x05\xff"],  # un SSH; el puerto SOCKS5 de Evomi (1002)
    ids=["ssh", "socks5"],
)
def test_proxy_que_no_habla_http_se_dice(respuesta: bytes) -> None:
    with (
        proxy_falso(1, esperada=None, respuesta_fallo=respuesta) as (puerto, _),
        pytest.raises(ErrorDescarga, match="proxy HTTP") as error,
    ):
        pedir(remoto(puerto), "http://tienda.example/")
    _sin_secretos(error.value)


def test_proxy_inalcanzable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as libre:
        puerto = libre.getsockname()[1]
    with pytest.raises(ErrorDescarga, match="con el proxy") as error:
        pedir(remoto(puerto), "http://tienda.example/")
    _sin_secretos(error.value)


def test_proxy_que_gotea_el_connect_da_tiempo_agotado() -> None:
    with (
        proxy_falso(1, esperada=AUTORIZACION, goteo_s=0.2) as (puerto, _),
        pytest.raises(ErrorDescarga, match="tiempo") as error,
    ):
        inicio = time.monotonic()
        pedir(remoto(puerto), "http://tienda.example/", timeout=0.6)
    assert time.monotonic() - inicio < 3
    _sin_secretos(error.value)


def test_fallo_de_setopt_no_cita_la_contrasena() -> None:
    # Un `setopt` que falla mete su valor en el mensaje del error de curl (código 43).
    enorme = SECRETO + "x" * 8_000_001
    with pytest.raises(ErrorDescarga) as error:
        pedir(remoto(1, contrasena=enorme), "http://tienda.example/")
    assert error.value.__cause__ is None and error.value.__context__ is None
    _sin_secretos(error.value)


def test_propiedad_proxy() -> None:
    proxy = Proxy("127.0.0.1", 8080, USUARIO, SECRETO)
    assert TransporteCurl(proxy=proxy).proxy is proxy
    assert TransporteCurl().proxy is None


def test_el_nombre_del_proxy_se_resuelve_una_vez_y_se_fija() -> None:
    consultas: list[str] = []

    def resolver_proxy(host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        consultas.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", puerto))]

    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        transporte = TransporteCurl(
            proxy=Proxy("proxy.invalido.example", puerto, USUARIO, SECRETO),
            resolver=_resolver_prohibido,
            proxy_remoto=_siempre_remoto,
            resolver_proxy=resolver_proxy,
        )
        assert consultas == []  # nunca en `__init__`, que corre en el bucle de eventos
        try:
            for _ in range(3):
                crudo = transporte.pedir("http://tienda.example/", {}, 5, MAXIMO_BYTES)
                crudo.cuerpo.close()
        finally:
            transporte.cerrar()
    assert consultas == ["proxy.invalido.example"]
    assert len(estado.connects) == 3


def test_proxy_que_no_resuelve() -> None:
    def resolver_proxy(*_: object, **__: object) -> list[tuple[Any, ...]]:
        raise socket.gaierror("sin DNS")

    transporte = TransporteCurl(
        proxy=Proxy("proxy.invalido.example", 1000, USUARIO, SECRETO),
        resolver_proxy=resolver_proxy,
    )
    with pytest.raises(ErrorDescarga, match="con el proxy") as error:
        pedir(transporte, "http://tienda.example/")
    _sin_secretos(error.value)


# --- Proxy local: urllib con el túnel a la IP validada ------------------------------------------


def test_proxy_local_conecta_a_la_ip_validada_con_urllib() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        crudo, _ = pedir(local(puerto), "http://tienda.example/ficha")
    assert crudo.estado == 200 and not crudo.descomprimida
    assert estado.connects == [f"CONNECT {IP_TIENDA}:80 HTTP/1.1"]
    _, cabeceras = servidor.peticiones[0]
    # Es urllib: la tienda ve el Host del nombre y las cabeceras del descargador.
    assert cabeceras["host"] == "tienda.example" and cabeceras["user-agent"] == UA


def test_https_por_un_proxy_local_va_a_la_ip_con_el_nombre_en_tls(
    certificado_tienda: tuple[Path, Path],
) -> None:
    with (
        tienda_tls(certificado_tienda) as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        crudo, cuerpo = pedir(local(puerto, ca=certificado_tienda[1]), "https://tienda.example/")
    assert crudo.estado == 200 and "túnel" in cuerpo.decode()
    assert estado.connects == [f"CONNECT {IP_TIENDA}:443 HTTP/1.1"]


@pytest.mark.parametrize(
    "ip", ["192.168.1.1", "127.0.0.1", "169.254.169.254", "10.0.0.7", "::ffff:10.0.0.1"]
)
def test_proxy_local_no_lleva_a_la_red_interna(ip: str) -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
        pytest.raises(URLNoPermitida),
    ):
        pedir(local(puerto, ip), "http://tienda.example/")
    assert estado.connects == [] and servidor.peticiones == []


def test_proxy_local_y_remoto_en_paralelo_no_se_mezclan() -> None:
    # La decisión local o remoto se toma una vez, aunque lleguen dos peticiones a la vez.
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        transporte = local(puerto)
        errores: list[BaseException] = []

        def una() -> None:
            try:
                transporte.pedir("http://tienda.example/", {}, 5, MAXIMO_BYTES).cuerpo.close()
            except BaseException as error:
                errores.append(error)

        hilos = [threading.Thread(target=una) for _ in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)
        transporte.cerrar()
    assert errores == []
    assert estado.connects == [f"CONNECT {IP_TIENDA}:80 HTTP/1.1"] * 4


def test_si_no_se_puede_conectar_con_el_proxy_se_vuelve_a_resolver_su_nombre() -> None:
    # La IP fijada con RESOLVE no dura para siempre: si el proveedor cambia las IP de su nombre,
    # tras un fallo de conexión con el proxy se vuelve a preguntar al DNS.
    consultas: list[str] = []
    ips = ["127.0.0.2", "127.0.0.1"]  # la primera ya no es del proxy

    def resolver_proxy(host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        consultas.append(host)
        ip = ips[min(len(consultas), len(ips)) - 1]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, puerto))]

    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=AUTORIZACION) as (puerto, estado),
    ):
        transporte = TransporteCurl(
            proxy=Proxy("proxy.invalido.example", puerto, USUARIO, SECRETO),
            resolver=_resolver_prohibido,
            proxy_remoto=_siempre_remoto,
            resolver_proxy=resolver_proxy,
        )
        try:
            with pytest.raises(ErrorDescarga, match="con el proxy"):
                transporte.pedir("http://tienda.example/", {}, 5, MAXIMO_BYTES)
            crudo = transporte.pedir("http://tienda.example/", {}, 5, MAXIMO_BYTES)
            crudo.cuerpo.close()
        finally:
            transporte.cerrar()
    assert consultas == ["proxy.invalido.example"] * 2
    assert len(estado.connects) == 1


def test_esperar_a_que_otro_resuelva_el_proxy_respeta_el_plazo() -> None:
    # La primera petición resuelve el nombre del proxy con el candado tomado; las demás no esperan
    # más allá de su propio plazo total.
    def resolver_lento(host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        time.sleep(1.5)
        raise socket.gaierror("sin DNS")

    transporte = TransporteCurl(
        proxy=Proxy("proxy.invalido.example", 1000, USUARIO, SECRETO),
        resolver=_resolver_prohibido,
        proxy_remoto=_siempre_remoto,
        resolver_proxy=resolver_lento,
    )

    def resolver_primero() -> None:
        with contextlib.suppress(ErrorDescarga):
            transporte.pedir("http://tienda.example/", {}, 5, MAXIMO_BYTES)

    primera = threading.Thread(target=resolver_primero)
    try:
        primera.start()
        time.sleep(0.2)
        inicio = time.monotonic()
        with pytest.raises(ErrorDescarga, match="tiempo"):
            transporte.pedir("http://tienda.example/", {}, 0.3, MAXIMO_BYTES)
        assert time.monotonic() - inicio < 1.0
    finally:
        primera.join(5)
        transporte.cerrar()
