"""Validación de URL contra SSRF: cada URL que viene de fuera pasa por aquí antes de pedirse."""

import asyncio
import ipaddress

import pytest

from precia.navegador.tipos import URLNoPermitida
from precia.navegador.url import ip_publica, validar_url
from tests.navegador.falsos import IP_PUBLICA, ResolverFalso, validar_con_dns_publico

RESOLVER = ResolverFalso(
    {
        "tienda.example": [IP_PUBLICA],
        "www.tienda.example": [IP_PUBLICA, "2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
        "xn--mnchen-3ya.example": [IP_PUBLICA],
        "interno.example": ["10.0.0.7"],
        "rebote.example": [IP_PUBLICA, "127.0.0.1"],
        "metadatos.example": ["169.254.169.254"],
        "mapeada.example": ["::ffff:192.168.1.1"],
        "vacia.example": [],
        "basura.example": ["no-es-una-ip"],
    }
)


def validar(url: str) -> str:
    return asyncio.run(validar_url(url, resolver=RESOLVER))


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        ("https://tienda.example/buscar?q=pastillas", "https://tienda.example/buscar?q=pastillas"),
        ("HTTPS://Tienda.EXAMPLE", "https://tienda.example/"),
        ("http://tienda.example:80/a", "http://tienda.example/a"),
        ("https://tienda.example:443/a", "https://tienda.example/a"),
        ("https://tienda.example./a", "https://tienda.example/a"),
        ("https://www.tienda.example/a#ficha", "https://www.tienda.example/a"),
        ("  https://tienda.example/a  ", "https://tienda.example/a"),
        ("https://tienda.example/pastillas freno", "https://tienda.example/pastillas%20freno"),
        ("https://tienda.example/pièce?q=niño", "https://tienda.example/pi%C3%A8ce?q=ni%C3%B1o"),
        ("https://münchen.example/", "https://xn--mnchen-3ya.example/"),
        ("https://93.184.215.14/", "https://93.184.215.14/"),
        ("https://[2606:2800:21f:cb07:6820:80da:af6b:8b2c]/", None),
    ],
)
def test_urls_publicas_se_normalizan(url: str, esperada: str | None) -> None:
    resultado = validar(url)
    assert resultado == (esperada or url)


@pytest.mark.parametrize(
    "url",
    [
        # Esquemas que no son web.
        "file:///etc/passwd",
        "ftp://tienda.example/",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "gopher://tienda.example:70/_SET",
        "//tienda.example/sin-esquema",
        "tienda.example/sin-esquema",
        "",
        # Credenciales en la URL (y el truco de esconder el host real detrás de la @).
        "http://usuario@tienda.example/",
        "http://usuario:clave@tienda.example/",
        "http://tienda.example@127.0.0.1/",
        "http://tienda.example:80@169.254.169.254/",
        # Puertos raros.
        "http://tienda.example:8080/",
        "https://tienda.example:22/",
        "http://tienda.example:0/",
        "http://tienda.example:99999/",
        "http://tienda.example:80a/",
        # Loopback y redes privadas escritas como IP.
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0.0.0.0/",
        "http://0/",
        "http://10.1.2.3/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://255.255.255.255/",
        # La misma IP en decimal, octal, hexadecimal o abreviada.
        "http://2130706433/",
        "http://017700000001/",
        "http://0177.0.0.1/",
        "http://0x7f.0.0.1/",
        "http://0x7f.1/",
        "http://0x7f000001/",
        "http://127.000.000.001/",
        "http://8.8.8.010/",
        "http://tienda.0x7f/",
        "http://tienda.123/",
        # IPv6.
        "http://[::1]/",
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:7f00:1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://[::127.0.0.1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[2002:7f00:1::]/",
        "http://[fe80::1]/",
        "http://[fe80::1%25eth0]/",
        "http://[fc00::1]/",
        "http://[ff02::1]/",
        "http://[::1/",
        # Nombres internos o que resuelven a direcciones no públicas.
        "http://localhost/",
        "http://LOCALHOST./",
        "http://api.localhost/",
        "http://metadata/",
        "http://servidor.internal/",
        "http://impresora.local/",
        "http://interno.example/",
        "http://rebote.example/",
        "http://metadatos.example/",
        "http://mapeada.example/",
        "http://vacia.example/",
        "http://basura.example/",
        "http://no-existe.example/",
        # IDN que se normaliza a una IP privada o a caracteres no válidos.
        "http://\uff11\uff12\uff17\uff0e\uff10\uff0e\uff10\uff0e\uff11/",  # ancho completo
        "http://\u2460\u2461\u2466.0.0.1/",  # dígitos en círculo
        "http://tienda\u2100.example/",  # U+2100 se descompone en «a/c»
        "http://tien_da.example/",
        "http://-tienda.example/",
        "http://" + "a" * 64 + ".example/",
        # Caracteres que los navegadores y urllib interpretan distinto.
        "http://tienda.example\\@127.0.0.1/",
        "http://tienda.example/\r\nHost: interno",
        "http://tienda.example/\x00",
        "http://tienda.example/" + "a" * 3000,
    ],
)
def test_urls_peligrosas_se_rechazan(url: str) -> None:
    with pytest.raises(URLNoPermitida):
        validar(url)


def test_no_resuelve_las_ip_literales() -> None:
    resolver = ResolverFalso({})
    assert asyncio.run(validar_url("https://93.184.215.14/", resolver=resolver))
    assert resolver.consultas == []


def test_resuelve_el_nombre_normalizado() -> None:
    resolver = ResolverFalso({"xn--mnchen-3ya.example": [IP_PUBLICA]})
    asyncio.run(validar_url("https://MÜNCHEN.example/", resolver=resolver))
    assert resolver.consultas == ["xn--mnchen-3ya.example"]


def test_el_mensaje_no_repite_la_url_entera() -> None:
    # El mensaje se enseña al usuario: debe decir el motivo sin volcar datos arbitrarios.
    with pytest.raises(URLNoPermitida) as error:
        validar("http://127.0.0.1/" + "<script>" * 10)
    assert "<script>" not in str(error.value)


@pytest.mark.parametrize(
    ("ip", "publica"),
    [
        ("8.8.8.8", True),
        ("2001:4860:4860::8888", True),
        ("::ffff:8.8.8.8", True),
        ("127.0.0.1", False),
        ("::ffff:127.0.0.1", False),
        ("64:ff9b::a00:1", False),
        ("224.0.0.251", False),
        ("ff0e::1", False),
        ("198.18.0.1", False),
        ("192.0.2.1", False),
    ],
)
def test_ip_publica(ip: str, publica: bool) -> None:
    assert ip_publica(ipaddress.ip_address(ip)) is publica


@pytest.mark.parametrize("url", ["http://[v1.fe]/", "http://[v1.tienda.example]/"])
def test_corchetes_sin_ipv6_se_rechazan(url: str) -> None:
    # «IPvFuture»: urlsplit lo acepta y devolvía el nombre sin corchetes («v1.fe»), otro host
    # distinto del escrito. Aunque ese nombre resuelva a una IP pública, no es una URL web.
    with pytest.raises(URLNoPermitida):
        asyncio.run(validar_con_dns_publico(url))
