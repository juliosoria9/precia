"""Proxy de salida: datos, URL de la configuración (`PRECIA_PROXY`) y credenciales a salvo."""

import base64

import pytest

from precia.navegador.proxy import (
    ModoProxy,
    Proxy,
    URLProxyNoValida,
    modo_proxy,
    proxy_desde_url,
)

USUARIO = "usuario-de-prueba"
SECRETO = "contrasena-secreta-de-prueba"


def test_repr_y_str_no_llevan_credenciales() -> None:
    proxy = Proxy("proxy.example", 1000, USUARIO, SECRETO, nombre="Proxy de prueba")
    for texto in (repr(proxy), str(proxy), f"{proxy}", f"{proxy!r}"):
        assert SECRETO not in texto
        assert USUARIO not in texto
    assert "Proxy de prueba" in str(proxy)


def test_cabecera_proxy_authorization_basic() -> None:
    proxy = Proxy("proxy.example", 1000, USUARIO, SECRETO)
    esperado = base64.b64encode(f"{USUARIO}:{SECRETO}".encode()).decode("ascii")
    assert proxy.cabecera_autorizacion == f"Basic {esperado}"


def test_sin_usuario_no_hay_cabecera() -> None:
    proxy = Proxy("proxy.example", 3128)
    assert proxy.cabecera_autorizacion is None
    assert proxy.servidor == "http://proxy.example:3128"


def test_no_hay_opciones_con_credenciales_para_playwright() -> None:
    # Playwright repite con `Authorization: Basic usuario:contraseña` del proxy cualquier
    # respuesta 401 de un destino: las credenciales nunca se le dan (va por `navegador.rele`).
    assert not hasattr(Proxy("proxy.example", 1000, USUARIO, SECRETO), "para_playwright")


def test_servidor_con_ipv6_lleva_corchetes() -> None:
    assert Proxy("::1", 8080).servidor == "http://[::1]:8080"


@pytest.mark.parametrize(
    ("host", "puerto", "usuario", "contrasena"),
    [
        ("", 1000, "", ""),
        ("proxy example", 1000, "", ""),
        ("proxy.example/ruta", 1000, "", ""),
        ("usu@proxy.example", 1000, "", ""),
        ("proxy.example", 0, "", ""),
        ("proxy.example", 70000, "", ""),
        ("proxy.example", True, "", ""),
        ("proxy.example", 1000, "con:dos-puntos", "x"),
        ("proxy.example", 1000, "usu", "salto\r\nX-Inyectada: 1"),
        ("proxy.example", 1000, "", "solo-contrasena"),
    ],
)
def test_datos_no_validos(host: str, puerto: int, usuario: str, contrasena: str) -> None:
    with pytest.raises(ValueError) as error:
        Proxy(host, puerto, usuario, contrasena)
    assert "Inyectada" not in str(error.value) and "solo-contrasena" not in str(error.value)


# --- PRECIA_PROXY -------------------------------------------------------------------------------


def test_url_completa_con_credenciales_codificadas() -> None:
    proxy = proxy_desde_url("http://usu%40casa:cla%3Ave%2F1@proxy.example:8080")
    assert (proxy.host, proxy.puerto) == ("proxy.example", 8080)
    assert proxy.usuario == "usu@casa"
    assert proxy.contrasena == "cla:ve/1"
    assert proxy.nombre == "proxy propio"


def test_url_sin_credenciales_y_con_barra_final() -> None:
    proxy = proxy_desde_url("  http://127.0.0.1:3128/  ")
    assert (proxy.host, proxy.puerto, proxy.usuario) == ("127.0.0.1", 3128, "")


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "proxy.example:8080",
        "https://usu:" + SECRETO + "@proxy.example:443",
        "socks5://usu:" + SECRETO + "@proxy.example:1080",
        "http://usu:" + SECRETO + "@proxy.example",
        "http://usu:" + SECRETO + "@proxy.example:puerto",
        "http://usu:" + SECRETO + "@proxy.example:99999",
        "http://usu:" + SECRETO + "@:8080",
        "http://usu:" + SECRETO + "@proxy.example:8080/ruta",
        "http://usu:" + SECRETO + "@proxy.example:8080?x=1",
        "http://usu:" + SECRETO + "@proxy.example:8080\nX: 1",
    ],
)
def test_url_no_valida_sin_filtrar_la_url(texto: str) -> None:
    with pytest.raises(ValueError) as error:
        proxy_desde_url(texto)
    mensaje = str(error.value)
    assert SECRETO not in mensaje
    assert "proxy.example" not in mensaje
    # Ni en la cadena de excepciones (un traceback la imprimiría).
    assert error.value.__cause__ is None
    assert error.value.__context__ is None or error.value.__suppress_context__


@pytest.mark.parametrize(
    "texto",
    [
        # Caracteres que NFKC convierte en separadores: `urlsplit` lanza un `ValueError` cuyo
        # mensaje cita el `netloc` entero, con el usuario y la contraseña.
        "http://" + USUARIO + ":" + SECRETO + "\uff03x@proxy.example:8080",
        "http://" + USUARIO + ":" + SECRETO + "\uff20x@proxy.example:8080",
        "http://" + USUARIO + ":" + SECRETO + "\uff1ax@proxy.example:8080",
        "http://" + USUARIO + ":" + SECRETO + "\uff0fx@proxy.example:8080",
        "http://" + USUARIO + ":" + SECRETO + "@[proxy.example]:8080",
    ],
)
def test_url_con_caracteres_que_rompen_urlsplit_no_filtra_la_contrasena(texto: str) -> None:
    with pytest.raises(URLProxyNoValida) as error:
        proxy_desde_url(texto)
    for mensaje in (str(error.value), repr(error.value)):
        assert SECRETO not in mensaje and USUARIO not in mensaje
        assert "proxy.example" not in mensaje
    assert error.value.__cause__ is None
    assert error.value.__context__ is None or error.value.__suppress_context__


# --- Modo -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "modo"),
    [
        (None, ModoProxy.BLOQUEO),
        ("", ModoProxy.BLOQUEO),
        ("bloqueo", ModoProxy.BLOQUEO),
        (" SIEMPRE ", ModoProxy.SIEMPRE),
        ("nunca", ModoProxy.NUNCA),
        ("a veces", ModoProxy.BLOQUEO),
    ],
)
def test_modo_desde_texto(texto: str | None, modo: ModoProxy) -> None:
    assert modo_proxy(texto) is modo
