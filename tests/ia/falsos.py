"""Dobles de prueba compartidos por los tests de `ia`: transporte HTTP, reloj y espera falsos."""

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from precia.nucleo.modelos import LlamadaIA

RespuestaHTTP = tuple[int, dict[str, str], bytes]


@dataclass
class LlamadaHTTP:
    url: str
    cabeceras: dict[str, str]
    cuerpo: bytes
    hilo: int

    @property
    def json(self) -> Any:
        return json.loads(self.cuerpo)


@dataclass
class TransporteFalso:
    """Devuelve las respuestas guionizadas en orden (o lanza la excepción) y anota las llamadas."""

    respuestas: list[RespuestaHTTP | Exception]
    llamadas: list[LlamadaHTTP] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], cuerpo: bytes) -> RespuestaHTTP:
        self.llamadas.append(LlamadaHTTP(url, dict(cabeceras), cuerpo, threading.get_ident()))
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente


@dataclass
class EsperaFalsa:
    esperas: list[float] = field(default_factory=list)

    async def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)


@dataclass
class RelojFalso:
    """Avanza `paso` segundos cada vez que se consulta."""

    ahora: float = 1000.0
    paso: float = 0.25

    def __call__(self) -> float:
        valor = self.ahora
        self.ahora += self.paso
        return valor


def respuesta_json(
    estado: int, cuerpo: Any, cabeceras: dict[str, str] | None = None
) -> RespuestaHTTP:
    return (
        estado,
        {"Content-Type": "application/json", **(cabeceras or {})},
        json.dumps(cuerpo).encode(),
    )


class RegistroFalso:
    """`RegistroLlamadas` en memoria: el gasto del mes es el previo más lo registrado."""

    def __init__(self, gasto_previo: Decimal = Decimal(0)) -> None:
        self.gasto_previo = gasto_previo
        self.llamadas: list[LlamadaIA] = []

    def registrar_llamada(self, llamada: LlamadaIA) -> None:
        self.llamadas.append(llamada)

    def gasto_mes_usd(self) -> Decimal:
        return self.gasto_previo + sum((ll.coste_usd for ll in self.llamadas), Decimal(0))
