"""Proveedor local con Ollama (`POST /api/chat` con salida estructurada por esquema).

Sin coste y sin enviar datos fuera del equipo. No se reintenta: el servidor es local y, si no
responde, reintentar solo alarga la búsqueda.
"""

import asyncio
import json
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from precia.ia.costes import calcular_coste
from precia.ia.defensas import construir_mensajes
from precia.ia.salida import (
    Consumo,
    SalidaNoValidaConConsumo,
    esquema_para_api,
    interpretar,
    leer_json,
)
from precia.ia.tipos import ErrorIA, IANoDisponible, PeticionIA, RespuestaIA, SalidaNoValida
from precia.ia.transporte import ErrorTransporte, Transporte, crear_transporte_urllib

URL_POR_DEFECTO = "http://127.0.0.1:11434"
MAX_BYTES_RESPUESTA = 2 * 1024 * 1024


def _url_base(url: str) -> str:
    partes = urlsplit(url.strip())
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise ValueError("La URL de Ollama tiene que ser http(s)://host:puerto")
    if partes.username or partes.password or partes.query or partes.fragment:
        raise ValueError("La URL de Ollama no puede llevar credenciales, consulta ni fragmento")
    try:
        partes.port  # noqa: B018 - solo se lee para que urlsplit valide el puerto
    except ValueError:
        raise ValueError("La URL de Ollama tiene un puerto no válido") from None
    return url.strip().rstrip("/")


def _contador(valor: object) -> int:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0 else 0


class ProveedorOllama:
    """`ProveedorIA` para un servidor de Ollama (normalmente en el propio equipo)."""

    nombre = "ollama"

    def __init__(
        self,
        *,
        url: str = URL_POR_DEFECTO,
        modelo: str,
        transporte: Transporte | None = None,
        timeout_s: float = 120.0,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = _url_base(url)
        self.modelo = modelo
        self._transporte = transporte or crear_transporte_urllib(
            timeout=timeout_s, max_bytes=MAX_BYTES_RESPUESTA
        )
        self._reloj = reloj

    def __repr__(self) -> str:
        return f"ProveedorOllama(url={self.url!r}, modelo={self.modelo!r})"

    async def completar(self, peticion: PeticionIA) -> RespuestaIA:
        sistema, usuario = construir_mensajes(peticion)
        cuerpo = {
            "model": self.modelo,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "format": esquema_para_api(peticion.esquema),
            "stream": False,
            "options": {"temperature": 0, "num_predict": peticion.max_tokens},
        }
        inicio = self._reloj()
        try:
            estado, _, datos = await asyncio.to_thread(
                self._transporte,
                f"{self.url}/api/chat",
                {"content-type": "application/json"},
                json.dumps(cuerpo, ensure_ascii=False).encode("utf-8"),
            )
        except ErrorTransporte:
            raise IANoDisponible(f"No se puede conectar con Ollama en {self.url}") from None
        if estado == 404:
            raise IANoDisponible(
                f"Ollama no tiene el modelo «{self.modelo}»: "
                f"instálalo con `ollama pull {self.modelo}`"
            )
        if estado != 200:
            raise ErrorIA(f"Ollama ha respondido con un error ({estado})")
        return self._respuesta(peticion, datos, inicio)

    def _respuesta(self, peticion: PeticionIA, datos: bytes, inicio: float) -> RespuestaIA:
        try:
            cuerpo = leer_json(datos)
            contenido = cuerpo["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise ErrorIA("Ollama ha devuelto una respuesta ilegible") from None
        if not isinstance(contenido, str):
            raise ErrorIA("Ollama ha devuelto una respuesta ilegible")
        entrada = _contador(cuerpo.get("prompt_eval_count"))
        salida = _contador(cuerpo.get("eval_count"))
        consumo = Consumo(
            entrada, salida, calcular_coste(self.nombre, self.modelo, entrada, salida)
        )
        if cuerpo.get("done_reason") == "length":
            raise SalidaNoValidaConConsumo(
                "Salida incompleta: se ha alcanzado el máximo de tokens", consumo
            )
        try:
            resultado = interpretar(contenido, peticion.esquema)
        except SalidaNoValida as error:
            raise SalidaNoValidaConConsumo(str(error), consumo) from None
        return RespuestaIA(
            datos=resultado,
            tokens_entrada=consumo.tokens_entrada,
            tokens_salida=consumo.tokens_salida,
            coste_usd=consumo.coste_usd,
            duracion_ms=round((self._reloj() - inicio) * 1000),
            proveedor=self.nombre,
            modelo=self.modelo,
        )
