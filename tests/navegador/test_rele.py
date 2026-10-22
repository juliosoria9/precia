"""Relé local del proxy para Chromium, contra un proxy y una tienda falsos en 127.0.0.1.

El relé no pide credenciales al cliente (Chromium) y las añade él en el `CONNECT` al proxy.
"""

import asyncio
import socket
from typing import Any

import pytest

from precia.navegador.proxy import Proxy
from precia.navegador.rele import ReleProxy
from tests.navegador.servidores_proxy import CUERPO_TIENDA, proxy_falso, tienda

USUARIO = "usuario-rele-prueba"
SECRETO = "contrasena-rele-prueba_country-ES"
IP_TIENDA = "93.184.215.14"


def _proxy(puerto: int) -> Proxy:
    return Proxy("127.0.0.1", puerto, USUARIO, SECRETO, nombre="Proxy de prueba")


def _siempre_remoto(_: object) -> bool:
    return True


def resolver_a(ip: str) -> Any:
    def resolver(host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, puerto))]

    return resolver


async def _pedir(rele: ReleProxy, peticion: bytes, despues: bytes = b"") -> bytes:
    servidor = await rele.abrir()
    puerto = int(servidor.rsplit(":", 1)[1])
    assert servidor.startswith("http://127.0.0.1:")
    lector, escritor = await asyncio.open_connection("127.0.0.1", puerto)
    try:
        escritor.write(peticion)
        await escritor.drain()
        respuesta = await asyncio.wait_for(lector.readuntil(b"\r\n\r\n"), 5)
        if despues and respuesta.startswith(b"HTTP/1.1 200"):
            escritor.write(despues)
            await escritor.drain()
            respuesta += await asyncio.wait_for(lector.read(65536), 5)
        return respuesta
    finally:
        escritor.close()
        await rele.cerrar()


GET = b"GET /ficha HTTP/1.1\r\nHost: tienda.example\r\nConnection: close\r\n\r\n"


def test_connect_se_reenvia_al_proxy_con_las_credenciales() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=_proxy(1).cabecera_autorizacion) as (
            puerto,
            estado,
        ),
    ):
        rele = ReleProxy(_proxy(puerto), proxy_remoto=_siempre_remoto)
        respuesta = asyncio.run(
            _pedir(
                rele, b"CONNECT tienda.example:80 HTTP/1.1\r\nHost: tienda.example:80\r\n\r\n", GET
            )
        )
    assert respuesta.startswith(b"HTTP/1.1 200 Connection established")
    assert CUERPO_TIENDA.encode() in respuesta
    assert estado.connects == ["CONNECT tienda.example:80 HTTP/1.1"]
    assert estado.autorizaciones == [_proxy(1).cabecera_autorizacion]
    # La tienda no ve nada del proxy.
    assert not any(k.startswith("proxy-") for k in servidor.peticiones[0][1])


def test_la_respuesta_al_cliente_no_lleva_credenciales_aunque_el_proxy_rechace() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada="Basic otra") as (puerto, _),
    ):
        rele = ReleProxy(_proxy(puerto), proxy_remoto=_siempre_remoto)
        respuesta = asyncio.run(_pedir(rele, b"CONNECT tienda.example:443 HTTP/1.1\r\n\r\n"))
    assert respuesta.startswith(b"HTTP/1.1 502")
    assert SECRETO.encode() not in respuesta and USUARIO.encode() not in respuesta
    assert servidor.peticiones == []


def test_proxy_local_conecta_a_la_ip_validada() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=_proxy(1).cabecera_autorizacion) as (
            puerto,
            estado,
        ),
    ):
        rele = ReleProxy(_proxy(puerto), resolver=resolver_a(IP_TIENDA))
        respuesta = asyncio.run(_pedir(rele, b"CONNECT tienda.example:80 HTTP/1.1\r\n\r\n", GET))
    assert respuesta.startswith(b"HTTP/1.1 200")
    assert estado.connects == [f"CONNECT {IP_TIENDA}:80 HTTP/1.1"]


@pytest.mark.parametrize("ip", ["192.168.1.1", "127.0.0.1", "169.254.169.254"])
def test_proxy_local_no_lleva_a_la_red_interna(ip: str) -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=_proxy(1).cabecera_autorizacion) as (
            puerto,
            estado,
        ),
    ):
        rele = ReleProxy(_proxy(puerto), resolver=resolver_a(ip))
        respuesta = asyncio.run(_pedir(rele, b"CONNECT rebind.example:80 HTTP/1.1\r\n\r\n"))
    assert respuesta.startswith(b"HTTP/1.1 403")
    assert estado.connects == []


@pytest.mark.parametrize(
    "peticion",
    [
        b"GET http://tienda.example/ HTTP/1.1\r\nHost: tienda.example\r\n\r\n",
        b"CONNECT tienda.example HTTP/1.1\r\n\r\n",
        b"CONNECT usu:clave@tienda.example:80 HTTP/1.1\r\n\r\n",
        b"basura\r\n\r\n",
    ],
    ids=["get-absoluto", "sin-puerto", "con-credenciales", "basura"],
)
def test_solo_acepta_connect_bien_formado(peticion: bytes) -> None:
    with proxy_falso(1, esperada=None) as (puerto, estado):
        rele = ReleProxy(_proxy(puerto), proxy_remoto=_siempre_remoto)
        respuesta = asyncio.run(_pedir(rele, peticion))
    assert respuesta.split(b" ")[1] in (b"400", b"405")
    assert estado.connects == []


def test_proxy_inalcanzable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as libre:
        puerto = libre.getsockname()[1]
    rele = ReleProxy(_proxy(puerto), proxy_remoto=_siempre_remoto)
    respuesta = asyncio.run(_pedir(rele, b"CONNECT tienda.example:80 HTTP/1.1\r\n\r\n"))
    assert respuesta.startswith(b"HTTP/1.1 502")


def test_repr_sin_credenciales() -> None:
    texto = repr(ReleProxy(_proxy(1000)))
    assert SECRETO not in texto and USUARIO not in texto
