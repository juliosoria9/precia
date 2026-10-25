"""Gráficas SVG generadas en Python, sin librerías de terceros (memoria §7).

El SVG se escribe como texto y todo dato variable pasa por `html.escape`, porque los nombres de
tienda vienen de webs externas. Los colores no van aquí: cada serie lleva la clase `serie-N` y la
hoja de estilos decide el color (así funciona en modo claro y oscuro).
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from html import escape

from precia.nucleo.formato import euros, fecha_corta
from precia.nucleo.modelos import SerieHistorico

N_COLORES = 6
_MARGEN_IZQ, _MARGEN_DER, _MARGEN_SUP, _MARGEN_INF = 72, 16, 16, 32


def grafica_historico(
    series: Sequence[SerieHistorico], *, ancho: int = 720, alto: int = 280
) -> str:
    cabecera = (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="grafica" role="img" '
        f'viewBox="0 0 {ancho} {alto}" aria-labelledby="grafica-titulo">'
    )
    puntos = [p for s in series for p in s.puntos]
    if not puntos:
        return (
            f'{cabecera}<title id="grafica-titulo">Histórico de precio: sin datos</title>'
            f'<text class="grafica-vacia" x="{ancho / 2}" y="{alto / 2}" text-anchor="middle">'
            "Sin datos de histórico todavía</text></svg>"
        )

    f_min = min(p.fecha for p in puntos)
    f_max = max(p.fecha for p in puntos)
    p_min = min(p.precio for p in puntos)
    p_max = max(p.precio for p in puntos)
    dias = max((f_max - f_min).days, 1)
    rango = (p_max - p_min) or Decimal(1)
    area_x = ancho - _MARGEN_IZQ - _MARGEN_DER
    area_y = alto - _MARGEN_SUP - _MARGEN_INF

    def x(fecha: date) -> float:
        return _MARGEN_IZQ + area_x * (fecha - f_min).days / dias

    def y(precio: Decimal) -> float:
        return _MARGEN_SUP + area_y * float((p_max - precio) / rango)

    con_datos = sum(1 for s in series if s.puntos)
    tiendas = "1 tienda" if con_datos == 1 else f"{con_datos} tiendas"
    partes = [
        cabecera,
        f'<title id="grafica-titulo">Histórico de precio de {tiendas}, '
        f"entre {euros(p_min)} y {euros(p_max)}</title>",
    ]
    # Con un precio constante, las tres líneas de rejilla caerían en el mismo sitio.
    valores_rejilla = (p_max,) if p_min == p_max else (p_max, (p_max + p_min) / 2, p_min)
    for valor in valores_rejilla:
        alto_linea = f"{y(valor):.1f}"
        partes.append(
            f'<line class="rejilla" x1="{_MARGEN_IZQ}" x2="{ancho - _MARGEN_DER}" '
            f'y1="{alto_linea}" y2="{alto_linea}"/>'
            f'<text class="eje" x="{_MARGEN_IZQ - 8}" y="{alto_linea}" text-anchor="end" '
            f'dominant-baseline="middle">{escape(euros(valor))}</text>'
        )
    partes.append(
        f'<text class="eje" x="{_MARGEN_IZQ}" y="{alto - 8}">{fecha_corta(f_min)}</text>'
        f'<text class="eje" x="{ancho - _MARGEN_DER}" y="{alto - 8}" text-anchor="end">'
        f"{fecha_corta(f_max)}</text>"
    )
    for i, serie in enumerate(series):
        if not serie.puntos:
            continue
        # Ordenados por fecha: la línea no retrocede y el círculo marca el precio más reciente.
        ordenados = sorted(serie.puntos, key=lambda p: p.fecha)
        coordenadas = " ".join(f"{x(p.fecha):.1f},{y(p.precio):.1f}" for p in ordenados)
        ultimo = ordenados[-1]
        partes.append(
            f'<polyline class="serie serie-{i % N_COLORES}" points="{coordenadas}">'
            f"<title>{escape(serie.tienda)}</title></polyline>"
            f'<circle class="serie-punto serie-{i % N_COLORES}" r="3.5" '
            f'cx="{x(ultimo.fecha):.1f}" cy="{y(ultimo.precio):.1f}"/>'
        )
    partes.append("</svg>")
    return "".join(partes)
