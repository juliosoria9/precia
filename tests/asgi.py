"""Cliente ASGI mínimo para los tests.

Sustituye al `TestClient` de Starlette, que exigiría la dependencia `httpx`. Una aplicación ASGI
es una corrutina `app(scope, receive, send)`: basta con construir el `scope` de una petición HTTP,
entregar el cuerpo por `receive` y recoger por `send` el estado, las cabeceras y el cuerpo.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

Mensaje = dict[str, Any]


@dataclass(frozen=True)
class Respuesta:
    estado: int
    cabeceras: dict[str, str]
    cuerpo: bytes

    @property
    def texto(self) -> str:
        return self.cuerpo.decode("utf-8")


class ClienteASGI:
    def __init__(self, app: Any, host: str = "127.0.0.1:8000") -> None:
        self._app = app
        self._host = host

    def get(self, ruta: str, cabeceras: Mapping[str, str] | None = None) -> Respuesta:
        return self.peticion("GET", ruta, cabeceras=cabeceras)

    def post(
        self,
        ruta: str,
        datos: Mapping[str, str | list[str]] | None = None,
        cabeceras: Mapping[str, str] | None = None,
    ) -> Respuesta:
        cuerpo = urlencode(datos or {}, doseq=True).encode()
        todas = {"content-type": "application/x-www-form-urlencoded", **(cabeceras or {})}
        return self.peticion("POST", ruta, cuerpo=cuerpo, cabeceras=todas)

    def peticion(
        self,
        metodo: str,
        ruta: str,
        cuerpo: bytes = b"",
        cabeceras: Mapping[str, str] | None = None,
    ) -> Respuesta:
        return asyncio.run(self._peticion(metodo, ruta, cuerpo, cabeceras or {}))

    async def _peticion(
        self, metodo: str, ruta: str, cuerpo: bytes, cabeceras: Mapping[str, str]
    ) -> Respuesta:
        partes = urlsplit(ruta)
        todas = {"host": self._host, **{k.lower(): v for k, v in cabeceras.items()}}
        if cuerpo:
            # `setdefault`: un test puede declarar una longitud falsa a propósito.
            todas.setdefault("content-length", str(len(cuerpo)))
        scope: Mensaje = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": metodo,
            "scheme": "http",
            "path": partes.path,
            "raw_path": partes.path.encode(),
            "query_string": partes.query.encode(),
            "root_path": "",
            "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in todas.items()],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
        }
        enviado = False

        async def receive() -> Mensaje:
            nonlocal enviado
            if enviado:
                # Un cliente real sigue conectado mientras recibe la respuesta. Si aquí se
                # devolviera `http.disconnect`, Starlette dejaría de enviar ficheros.
                await asyncio.Future()
            enviado = True
            return {"type": "http.request", "body": cuerpo, "more_body": False}

        estado = 0
        cabeceras_resp: dict[str, str] = {}
        trozos: list[bytes] = []

        async def send(mensaje: Mensaje) -> None:
            nonlocal estado
            if mensaje["type"] == "http.response.start":
                estado = mensaje["status"]
                for k, v in mensaje.get("headers", []):
                    cabeceras_resp[k.decode("latin-1").lower()] = v.decode("latin-1")
            elif mensaje["type"] == "http.response.body":
                trozos.append(mensaje.get("body", b""))

        await self._app(scope, receive, send)
        return Respuesta(estado, cabeceras_resp, b"".join(trozos))
