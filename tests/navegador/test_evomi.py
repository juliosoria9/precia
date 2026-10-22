"""Credenciales del proxy residencial de Evomi: lectura de la API pública sin red.

La API (`GET https://api.evomi.com/public` con la cabecera `x-apikey`) se sustituye por un
transporte falso; la respuesta imita la estructura real con datos inventados.
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from precia.navegador.evomi import URL_API_EVOMI, credenciales_evomi, leer_productos_evomi
from precia.navegador.http import RespuestaCruda, TransporteURLLib
from precia.navegador.proxy import ErrorProxy
from precia.navegador.tipos import ErrorDescarga

CLAVE = "00000000-clave-evomi-de-prueba-0000"
USUARIO = "usuario-evomi-prueba"
SECRETO = "contrasena-evomi-prueba"
UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"


def producto(endpoint: str = "core-residential.evomi.com", saldo: float = 1024.0) -> dict[str, Any]:
    return {
        "username": USUARIO,
        "password": SECRETO,
        "balance_mb": saldo,
        "endpoint": endpoint,
        "ports": {"http": 1000, "socks5": 1002},
    }


def respuesta_api(**cambios: Any) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "success": True,
        "products": {
            "rp": producto("rp.evomi.com", saldo=0),
            "rpc": producto(),
            "mp": producto("mp.evomi.com", saldo=0),
        },
        "ownership_scope": "personal",
        "active_team_id": None,
    }
    datos.update(cambios)
    return datos


# --- Lectura de la respuesta (función pura) -----------------------------------------------------


def test_elige_rpc_por_defecto_y_pone_el_pais_en_la_contrasena() -> None:
    proxy = leer_productos_evomi(respuesta_api())
    assert (proxy.host, proxy.puerto) == ("core-residential.evomi.com", 1000)
    assert proxy.usuario == USUARIO
    assert proxy.contrasena == f"{SECRETO}_country-ES"
    assert "Evomi" in proxy.nombre and "(ES)" in proxy.nombre


def test_otro_pais_y_otro_producto() -> None:
    datos = respuesta_api()
    datos["products"]["mp"]["balance_mb"] = 10
    proxy = leer_productos_evomi(datos, producto="mp", pais="FR")
    assert proxy.host == "mp.evomi.com"
    assert proxy.contrasena.endswith("_country-FR")


@pytest.mark.parametrize("pais", ["es", "ESP", "E", "", "E1", "ES\n"])
def test_pais_no_valido(pais: str) -> None:
    with pytest.raises(ErrorProxy, match="país"):
        leer_productos_evomi(respuesta_api(), pais=pais)


def test_producto_que_no_existe() -> None:
    with pytest.raises(ErrorProxy, match="dcp"):
        leer_productos_evomi(respuesta_api(), producto="dcp")


def test_producto_sin_saldo() -> None:
    with pytest.raises(ErrorProxy, match="saldo"):
        leer_productos_evomi(respuesta_api(), producto="rp")


@pytest.mark.parametrize(
    "roto",
    [
        {"endpoint": None},
        {"endpoint": "host con espacios"},
        {"username": ""},
        {"password": 5},
        {"ports": {"socks5": 1002}},
        {"ports": {"http": "1000"}},
        {"ports": None},
    ],
)
def test_producto_sin_servidor_o_credenciales(roto: dict[str, Any]) -> None:
    datos = respuesta_api()
    datos["products"]["rpc"].update(roto)
    with pytest.raises(ErrorProxy) as error:
        leer_productos_evomi(datos)
    assert SECRETO not in str(error.value) and USUARIO not in str(error.value)


@pytest.mark.parametrize(
    "datos",
    [[], "texto", {"success": False}, {"success": True}, {"success": True, "products": []}],
)
def test_respuesta_no_valida(datos: Any) -> None:
    with pytest.raises(ErrorProxy, match="Evomi"):
        leer_productos_evomi(datos)


# --- Petición a la API (transporte falso) -------------------------------------------------------


class Cuerpo:
    def __init__(self, datos: bytes) -> None:
        self._datos = datos
        self.cerrado = False

    def read(self, n: int = -1) -> bytes:
        trozo, self._datos = self._datos[:n], self._datos[n:]
        return trozo

    def close(self) -> None:
        self.cerrado = True


class TransporteFalso:
    def __init__(
        self,
        cuerpo: bytes = b"",
        *,
        estado: int = 200,
        tipo: str = "application/json",
        error: Exception | None = None,
    ) -> None:
        self.respuesta = RespuestaCruda(estado, {"content-type": tipo}, Cuerpo(cuerpo))
        self.error = error
        self.peticiones: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        self.peticiones.append((url, dict(cabeceras), timeout))
        if self.error is not None:
            raise self.error
        return self.respuesta


def pedir(transporte: TransporteFalso, **opciones: Any) -> Any:
    return asyncio.run(credenciales_evomi(CLAVE, user_agent=UA, transporte=transporte, **opciones))


def test_pide_la_api_con_la_clave_el_user_agent_y_json() -> None:
    transporte = TransporteFalso(json.dumps(respuesta_api()).encode())
    proxy = pedir(transporte)
    assert proxy.contrasena == f"{SECRETO}_country-ES"
    assert transporte.respuesta.cuerpo.cerrado  # type: ignore[attr-defined]
    url, cabeceras, _ = transporte.peticiones[0]
    assert url == URL_API_EVOMI == "https://api.evomi.com/public"
    assert cabeceras["x-apikey"] == CLAVE
    # Cloudflare rechaza (error 1010) el User-Agent por defecto de urllib.
    assert cabeceras["User-Agent"] == UA
    assert cabeceras["Accept"] == "application/json"


def test_producto_y_pais_pedidos() -> None:
    datos = respuesta_api()
    datos["products"]["mp"]["balance_mb"] = 3
    transporte = TransporteFalso(json.dumps(datos).encode())
    proxy = pedir(transporte, producto="mp", pais="PT")
    assert proxy.host == "mp.evomi.com" and proxy.contrasena.endswith("_country-PT")


def _sin_secretos(error: BaseException) -> None:
    for texto in (str(error), repr(error)):
        assert CLAVE not in texto and SECRETO not in texto and USUARIO not in texto


@pytest.mark.parametrize(
    ("transporte", "texto"),
    [
        (TransporteFalso(b'{"success": false}', estado=401), "EVOMI_API_KEY"),
        (TransporteFalso(b"", estado=403, tipo="text/html"), "EVOMI_API_KEY"),
        (TransporteFalso(b"", estado=500), "500"),
        (TransporteFalso(b"<html></html>", tipo="text/html"), "JSON"),
        (TransporteFalso(b"{no es json " + SECRETO.encode()), "JSON"),
        (TransporteFalso(error=ErrorDescarga("No se pudo conectar.")), "conectar"),
        (TransporteFalso(error=OSError("red caída")), "conectar"),
    ],
)
def test_errores_de_la_api_sin_secretos(transporte: TransporteFalso, texto: str) -> None:
    with pytest.raises(ErrorProxy) as error:
        pedir(transporte)
    assert texto in str(error.value)
    _sin_secretos(error.value)


def test_respuesta_demasiado_grande() -> None:
    transporte = TransporteFalso(b" " * 2048 + json.dumps(respuesta_api()).encode())
    with pytest.raises(ErrorProxy, match="grande"):
        pedir(transporte, max_bytes=1024)


@pytest.mark.parametrize("clave", ["", "   ", "con\r\nsalto", "con espacio"])
def test_clave_no_valida_no_se_envia(clave: str) -> None:
    transporte = TransporteFalso(json.dumps(respuesta_api()).encode())
    with pytest.raises(ErrorProxy, match="EVOMI_API_KEY") as error:
        asyncio.run(credenciales_evomi(clave, user_agent=UA, transporte=transporte))
    assert transporte.peticiones == []
    assert "salto" not in str(error.value)


def test_pais_no_valido_no_llega_a_pedir() -> None:
    transporte = TransporteFalso(json.dumps(respuesta_api()).encode())
    with pytest.raises(ErrorProxy, match="país"):
        pedir(transporte, pais="espa\u00f1a")
    assert transporte.peticiones == []


def _resolver_prohibido(*_: object, **__: object) -> list[tuple[object, ...]]:
    raise AssertionError("La petición no debe llegar a salir.")


@pytest.mark.parametrize("clave", ["\u201c" + CLAVE + "\u201d", CLAVE + "\u20ac", "clave\u00f1"])
def test_clave_con_caracteres_que_no_van_en_una_cabecera(clave: str) -> None:
    # Una clave pegada con comillas tipográficas pasa `isprintable`, pero `http.client` la
    # codifica en latin-1: el `UnicodeEncodeError` guardaba la clave entera en `.object`.
    transporte = TransporteURLLib(resolver=_resolver_prohibido)
    with pytest.raises(ErrorProxy, match="EVOMI_API_KEY") as error:
        asyncio.run(credenciales_evomi(clave, user_agent=UA, transporte=transporte))
    _sin_secretos(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None or error.value.__suppress_context__


def test_user_agent_que_no_va_en_una_cabecera() -> None:
    transporte = TransporteURLLib(resolver=_resolver_prohibido)
    with pytest.raises(ErrorProxy, match="User-Agent") as error:
        asyncio.run(
            credenciales_evomi(CLAVE, user_agent="PrecIA/0.1 \u20ac", transporte=transporte)
        )
    _sin_secretos(error.value)


def test_error_inesperado_del_transporte_sin_la_cadena() -> None:
    fallo = UnicodeEncodeError("latin-1", CLAVE, 0, 1, "ordinal not in range(256)")
    with pytest.raises(ErrorProxy) as error:
        pedir(TransporteFalso(error=fallo))
    _sin_secretos(error.value)
    assert error.value.__cause__ is None and error.value.__suppress_context__
