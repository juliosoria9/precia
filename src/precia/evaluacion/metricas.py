"""Métricas del banco de evaluación: comparar lo extraído con la etiqueta manual y contar.

Definiciones (nota 13):

- **Cobertura:** de los campos que la etiqueta dice que la página tiene, cuántos se obtienen
  (bien o mal).
- **Exactitud:** de lo que se da por obtenido, cuánto coincide con la etiqueta. Un valor sacado
  de una página que no lo tiene (etiqueta `null`) es un **inventado** y cuenta como fallo.

La comparación es por campo: el precio como importe exacto; la referencia sin separadores ni
mayúsculas; marca y estado sin mayúsculas, tildes ni espacios de más.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

_NO_ALFANUMERICO = re.compile(r"[^0-9A-Z]")
_ESPACIOS = re.compile(r"\s+")


class Juicio(StrEnum):
    CORRECTO = "correcto"
    INCORRECTO = "incorrecto"
    AUSENTE = "ausente"
    """La página lo tiene y no se ha obtenido."""
    INVENTADO = "inventado"
    """La página no lo tiene y se ha dado un valor."""
    BIEN_AUSENTE = "bien_ausente"
    """La página no lo tiene y no se ha dado nada."""


def _texto(valor: str) -> str:
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", valor) if not unicodedata.combining(c)
    )
    return _ESPACIOS.sub(" ", sin_tildes).strip().casefold()


def _importe(valor: str) -> Decimal | None:
    try:
        importe = Decimal(valor.strip())
    except InvalidOperation:
        return None
    return importe if importe.is_finite() else None


def coincide(campo: str, esperado: str, obtenido: str) -> bool:
    """Si `obtenido` es el valor de la etiqueta para ese campo."""
    if campo == "precio":
        a, b = _importe(esperado), _importe(obtenido)
        return a is not None and a == b
    if campo == "referencia_oem":
        a_ref = _NO_ALFANUMERICO.sub("", esperado.upper())
        return bool(a_ref) and a_ref == _NO_ALFANUMERICO.sub("", obtenido.upper())
    return bool(_texto(esperado)) and _texto(esperado) == _texto(obtenido)


def juzgar(campo: str, esperado: str | None, obtenido: str | None) -> Juicio:
    """`esperado` es la etiqueta (`None`: la página no da el dato) y `obtenido`, lo extraído."""
    hay = obtenido is not None and bool(obtenido.strip())
    if esperado is None:
        return Juicio.INVENTADO if hay else Juicio.BIEN_AUSENTE
    if not hay or obtenido is None:
        return Juicio.AUSENTE
    return Juicio.CORRECTO if coincide(campo, esperado, obtenido) else Juicio.INCORRECTO


@dataclass(slots=True)
class Metricas:
    correctos: int = 0
    incorrectos: int = 0
    ausentes: int = 0
    inventados: int = 0
    bien_ausentes: int = 0

    def anotar(self, juicio: Juicio) -> None:
        match juicio:
            case Juicio.CORRECTO:
                self.correctos += 1
            case Juicio.INCORRECTO:
                self.incorrectos += 1
            case Juicio.AUSENTE:
                self.ausentes += 1
            case Juicio.INVENTADO:
                self.inventados += 1
            case Juicio.BIEN_AUSENTE:
                self.bien_ausentes += 1

    def __add__(self, otra: "Metricas") -> "Metricas":
        return Metricas(
            self.correctos + otra.correctos,
            self.incorrectos + otra.incorrectos,
            self.ausentes + otra.ausentes,
            self.inventados + otra.inventados,
            self.bien_ausentes + otra.bien_ausentes,
        )

    @property
    def esperados(self) -> int:
        """Campos que la etiqueta dice que la página tiene."""
        return self.correctos + self.incorrectos + self.ausentes

    @property
    def cobertura(self) -> float:
        return (self.correctos + self.incorrectos) / self.esperados if self.esperados else 0.0

    @property
    def exactitud(self) -> float:
        dados = self.correctos + self.incorrectos + self.inventados
        return self.correctos / dados if dados else 0.0
