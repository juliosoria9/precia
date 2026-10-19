"""Formato de cifras y fechas en español."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def euros(importe: Decimal, decimales: int = 2) -> str:
    """`Decimal("1234.5")` → `"1.234,50 €"`."""
    cuantizado = importe.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)
    entero, _, fraccion = f"{abs(cuantizado):,.{decimales}f}".partition(".")
    texto = entero.replace(",", ".") + ("," + fraccion if fraccion else "")
    return f"{'-' if cuantizado < 0 else ''}{texto} €"


def miles(numero: int) -> str:
    """`19400` → `"19.400"`."""
    return f"{numero:,}".replace(",", ".")


def dolares(importe: Decimal) -> str:
    """Costes de IA: cuatro decimales por debajo de 1 $, porque una llamada cuesta céntimos."""
    if importe == 0:
        return "0 $"
    return euros(importe, 4 if abs(importe) < 1 else 2).replace(" €", " $")


def decimal(numero: float, decimales: int = 1) -> str:
    """`4.8` → `"4,8"`."""
    return f"{numero:.{decimales}f}".replace(".", ",")


def fecha_corta(valor: date | datetime) -> str:
    return f"{valor.day} {_MESES[valor.month - 1]}"


def fecha_hora(valor: datetime) -> str:
    return f"{fecha_corta(valor)} {valor:%H:%M}"
