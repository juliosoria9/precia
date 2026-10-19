"""Proveedor de la API de Anthropic (Messages) con un cliente propio sobre `urllib`.

No se usa el SDK oficial (decisión del proyecto): basta una petición POST y así el código que
toca la clave es corto y auditable. La clave solo va en la cabecera `x-api-key`: no aparece en
`repr`, en los mensajes de error ni en el registro, y cualquier texto que venga de la red se
limpia de ella por si la reflejase.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from precia.ia.costes import calcular_coste, tarifa
from precia.ia.defensas import construir_mensajes
from precia.ia.salida import (
    Consumo,
    RechazoDelModelo,
    SalidaNoValidaConConsumo,
    esquema_para_api,
    interpretar,
    leer_json,
)
from precia.ia.tipos import ErrorIA, IANoDisponible, PeticionIA, RespuestaIA, SalidaNoValida
from precia.ia.transporte import ErrorTransporte, Transporte, crear_transporte_urllib

registro = logging.getLogger(__name__)

URL_API = "https://api.anthropic.com/v1/messages"
VERSION_API = "2023-06-01"
MODELO_POR_DEFECTO = "claude-haiku-5-5"
MAX_BYTES_RESPUESTA = 2 * 1024 * 1024
ESTADOS_REINTENTABLES = frozenset({429, 500, 529})
_CLAVE_VALIDA = re.compile(r"[\x21-\x7e]+")


def _numero_no_negativo(valor: Any) -> int:
    if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0:
        return valor
    return 0


def _retry_after(cabeceras: dict[str, str]) -> float | None:
    # Solo la forma en segundos: es la que usa la API (la de fecha HTTP se ignora).
    try:
        segundos = float(cabeceras.get("retry-after", ""))
    except ValueError:
        return None
    return segundos if segundos >= 0 else None


class ProveedorClaude:
    """`ProveedorIA` para la API de Anthropic."""

    nombre = "api"

    def __init__(
        self,
        clave: str,
        *,
        modelo: str = MODELO_POR_DEFECTO,
        transporte: Transporte | None = None,
        reintentos: int = 3,
        espera_base_s: float = 1.0,
        espera_max_s: float = 30.0,
        timeout_s: float = 60.0,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
        azar: Callable[[], float] = random.random,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        if not clave.strip():
            raise IANoDisponible(
                "Falta la clave de la API de Anthropic: defínela en ANTHROPIC_API_KEY (.env)"
            )
        if not _CLAVE_VALIDA.fullmatch(clave.strip()):
            # http.client copiaría la cabecera (la clave) en su error; mejor no llegar a enviarla.
            raise IANoDisponible(
                "La clave de la API de Anthropic tiene caracteres no válidos (espacios, saltos "
                "de línea o caracteres no ASCII): revisa ANTHROPIC_API_KEY en el .env"
            )
        tarifa(self.nombre, modelo)  # falla ya si el modelo no tiene precio conocido
        self.__clave = clave.strip()
        self.modelo = modelo
        self._transporte = transporte or crear_transporte_urllib(
            timeout=timeout_s, max_bytes=MAX_BYTES_RESPUESTA
        )
        self._reintentos = reintentos
        self._espera_base_s = espera_base_s
        self._espera_max_s = espera_max_s
        self._dormir = dormir
        self._azar = azar
        self._reloj = reloj

    def __repr__(self) -> str:
        return f"ProveedorClaude(modelo={self.modelo!r}, clave=<oculta>)"

    def _sin_clave(self, texto: str) -> str:
        return texto.replace(self.__clave, "<clave oculta>")

    def _cuerpo(self, peticion: PeticionIA) -> bytes:
        sistema, usuario = construir_mensajes(peticion)
        cuerpo = {
            "model": self.modelo,
            "max_tokens": peticion.max_tokens,
            "system": sistema,
            "messages": [{"role": "user", "content": usuario}],
            "output_config": {
                "format": {"type": "json_schema", "schema": esquema_para_api(peticion.esquema)},
                # Tareas de extracción: no merece la pena pagar razonamiento extenso.
                "effort": "low",
            },
        }
        return json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")

    def _espera(self, intento: int, cabeceras: dict[str, str]) -> float | None:
        """Segundos antes del siguiente intento, o `None` si no merece la pena esperar."""
        exponencial = min(self._espera_max_s, self._espera_base_s * 2.0**intento)
        # Jitter entre el 50 % y el 100 %: evita que varias búsquedas reintenten a la vez.
        espera = exponencial * (0.5 + self._azar() / 2)
        pedida = _retry_after(cabeceras)
        if pedida is not None:
            if pedida > self._espera_max_s:
                return None
            espera = max(espera, pedida)
        return espera

    async def completar(self, peticion: PeticionIA) -> RespuestaIA:
        cuerpo = self._cuerpo(peticion)
        cabeceras = {
            "x-api-key": self.__clave,
            "anthropic-version": VERSION_API,
            "content-type": "application/json",
        }
        inicio = self._reloj()
        ultimo_error: ErrorIA = IANoDisponible("No se puede conectar con la API de Anthropic")
        for intento in range(self._reintentos + 1):
            try:
                estado, cab, datos = await asyncio.to_thread(
                    self._transporte, URL_API, cabeceras, cuerpo
                )
            except ErrorTransporte as error:
                registro.warning("API de Anthropic: fallo de red (%s)", type(error).__name__)
                ultimo_error = IANoDisponible("No se puede conectar con la API de Anthropic")
                cab = {}
            else:
                cab = {clave.lower(): valor for clave, valor in cab.items()}
                if estado == 200:
                    return self._respuesta(peticion, datos, inicio)
                if estado not in ESTADOS_REINTENTABLES:
                    raise self._error_definitivo(estado, datos)
                ultimo_error = self._error_reintentable(estado)
            if intento == self._reintentos:
                break
            espera = self._espera(intento, cab)
            if espera is None:
                break
            registro.info("API de Anthropic: reintento %d en %.1f s", intento + 1, espera)
            await self._dormir(espera)
        raise ultimo_error

    def _mensaje_error(self, datos: bytes) -> str:
        try:
            cuerpo = leer_json(datos)
            error = cuerpo["error"]
            texto = f"{error['type']}: {error['message']}"
        except (ValueError, KeyError, TypeError):
            return "sin detalle"
        return self._sin_clave(str(texto))[:300]

    def _error_reintentable(self, estado: int) -> ErrorIA:
        if estado == 429:
            return IANoDisponible("La API de Anthropic ha alcanzado el límite de peticiones (429)")
        if estado == 529:
            return IANoDisponible("La API de Anthropic está sobrecargada (529); prueba más tarde")
        return IANoDisponible(f"Error interno de la API de Anthropic ({estado})")

    def _error_definitivo(self, estado: int, datos: bytes) -> ErrorIA:
        if estado in (401, 403):
            return IANoDisponible(
                f"La API de Anthropic ha rechazado la clave ({estado}): revisa ANTHROPIC_API_KEY"
            )
        return ErrorIA(
            f"La API de Anthropic rechaza la petición ({estado}): {self._mensaje_error(datos)}"
        )

    def _respuesta(self, peticion: PeticionIA, datos: bytes, inicio: float) -> RespuestaIA:
        try:
            cuerpo = leer_json(datos)
        except ValueError:
            raise ErrorIA("La API de Anthropic ha devuelto una respuesta ilegible") from None
        if not isinstance(cuerpo, dict):
            raise ErrorIA("La API de Anthropic ha devuelto una respuesta ilegible")
        uso = cuerpo.get("usage")
        uso = uso if isinstance(uso, dict) else {}
        entrada = _numero_no_negativo(uso.get("input_tokens"))
        salida = _numero_no_negativo(uso.get("output_tokens"))
        consumo = Consumo(
            entrada, salida, calcular_coste(self.nombre, self.modelo, entrada, salida)
        )

        motivo = cuerpo.get("stop_reason")
        if motivo == "refusal":
            raise RechazoDelModelo("El modelo ha rechazado la petición", consumo)
        if motivo == "max_tokens":
            raise SalidaNoValidaConConsumo(
                "Salida incompleta: se ha alcanzado el máximo de tokens", consumo
            )
        if motivo not in ("end_turn", "stop_sequence"):
            raise SalidaNoValidaConConsumo("Motivo de parada inesperado", consumo)

        bloques = cuerpo.get("content")
        texto = "".join(
            b["text"]
            for b in (bloques if isinstance(bloques, list) else [])
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
        try:
            resultado = interpretar(texto, peticion.esquema)
        except SalidaNoValida as error:
            raise SalidaNoValidaConConsumo(str(error), consumo) from None
        return RespuestaIA(
            datos=resultado,
            tokens_entrada=entrada,
            tokens_salida=salida,
            coste_usd=consumo.coste_usd,
            duracion_ms=round((self._reloj() - inicio) * 1000),
            proveedor=self.nombre,
            modelo=self.modelo,
        )
