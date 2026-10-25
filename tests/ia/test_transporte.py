"""Transporte HTTP sobre `urllib`, probado sustituyendo la apertura de la conexión."""

import http.client
import io
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from precia.ia.transporte import ErrorTransporte, _SinRedirecciones, crear_transporte_urllib


def cabeceras(**valores: str) -> Message:
    mensaje = Message()
    for clave, valor in valores.items():
        mensaje[clave.replace("_", "-")] = valor
    return mensaje


class RespuestaFalsa(io.BytesIO):
    def __init__(self, cuerpo: bytes, estado: int = 200) -> None:
        super().__init__(cuerpo)
        self.status = estado
        self.headers = cabeceras(Content_Type="application/json", Retry_After="3")


class AperturaFalsa:
    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.peticiones: list[tuple[urllib.request.Request, float]] = []

    def __call__(self, peticion: urllib.request.Request, espera: float) -> Any:
        self.peticiones.append((peticion, espera))
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def test_respuesta_correcta_y_peticion_post() -> None:
    abrir = AperturaFalsa(RespuestaFalsa(b'{"ok": true}'))
    transporte = crear_transporte_urllib(timeout=12, max_bytes=1000, abrir=abrir)
    estado, cab, cuerpo = transporte("https://api.ejemplo.es/v1", {"X-Uno": "1"}, b"{}")
    assert (estado, cuerpo) == (200, b'{"ok": true}')
    assert cab["retry-after"] == "3"
    peticion, espera = abrir.peticiones[0]
    assert peticion.get_method() == "POST"
    assert peticion.data == b"{}"
    assert peticion.get_header("X-uno") == "1"
    assert espera == 12


def test_los_errores_http_se_devuelven() -> None:
    error = urllib.error.HTTPError(
        "https://api.ejemplo.es", 529, "Overloaded", cabeceras(Retry_After="9"), io.BytesIO(b"{}")
    )
    transporte = crear_transporte_urllib(timeout=1, max_bytes=1000, abrir=AperturaFalsa(error))
    estado, cab, cuerpo = transporte("https://api.ejemplo.es", {}, b"")
    assert (estado, cab["retry-after"], cuerpo) == (529, "9", b"{}")


def test_respuesta_demasiado_grande() -> None:
    abrir = AperturaFalsa(RespuestaFalsa(b"x" * 101))
    transporte = crear_transporte_urllib(timeout=1, max_bytes=100, abrir=abrir)
    with pytest.raises(ErrorTransporte, match="tamaño"):
        transporte("https://api.ejemplo.es", {}, b"")


def test_error_de_red() -> None:
    abrir = AperturaFalsa(urllib.error.URLError("detalle interno con datos"))
    transporte = crear_transporte_urllib(timeout=1, max_bytes=100, abrir=abrir)
    with pytest.raises(ErrorTransporte) as error:
        transporte("https://api.ejemplo.es", {}, b"")
    assert "detalle interno" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://servidor/x", "api.ejemplo.es"])
def test_solo_http_y_https(url: str) -> None:
    abrir = AperturaFalsa(RespuestaFalsa(b""))
    transporte = crear_transporte_urllib(timeout=1, max_bytes=100, abrir=abrir)
    with pytest.raises(ErrorTransporte):
        transporte(url, {}, b"")
    assert abrir.peticiones == []


def test_no_sigue_redirecciones() -> None:
    peticion = urllib.request.Request("https://api.ejemplo.es", data=b"{}", method="POST")
    manejador = _SinRedirecciones()
    nueva = manejador.redirect_request(
        peticion, io.BytesIO(), 302, "Found", cabeceras(), "https://otro.ejemplo"
    )
    assert nueva is None


@pytest.mark.parametrize(
    "fallo",
    [
        http.client.IncompleteRead(b"{"),
        http.client.BadStatusLine("basura"),
        http.client.InvalidURL("nonnumeric port"),
        # `http.client` copia el valor de la cabecera en el mensaje: aquí iría la clave.
        ValueError("Invalid header value b'sk-ant-secreta\nX: 1'"),
    ],
)
def test_otros_fallos_de_http_client_son_errores_de_transporte(fallo: Exception) -> None:
    transporte = crear_transporte_urllib(timeout=1, max_bytes=100, abrir=AperturaFalsa(fallo))
    with pytest.raises(ErrorTransporte) as error:
        transporte("https://api.ejemplo.es", {}, b"")
    assert "secreta" not in str(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__


class CuerpoQueFalla(io.BytesIO):
    def read(self, *args: Any) -> bytes:
        raise TimeoutError("timed out")


def test_fallo_al_leer_el_cuerpo_de_un_error_http() -> None:
    error = urllib.error.HTTPError(
        "https://api.ejemplo.es", 529, "Overloaded", cabeceras(), CuerpoQueFalla()
    )
    transporte = crear_transporte_urllib(timeout=1, max_bytes=100, abrir=AperturaFalsa(error))
    with pytest.raises(ErrorTransporte, match="TimeoutError"):
        transporte("https://api.ejemplo.es", {}, b"")
