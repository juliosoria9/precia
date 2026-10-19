import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal

from precia.nucleo.modelos import PuntoHistorico, SerieHistorico
from precia.seguimiento.graficas import grafica_historico

SVG = "{http://www.w3.org/2000/svg}"


def _serie(tienda: str, precios: list[str]) -> SerieHistorico:
    inicio = date(2026, 10, 1)
    return SerieHistorico(
        tienda,
        tuple(
            PuntoHistorico(inicio + timedelta(days=i), Decimal(p)) for i, p in enumerate(precios)
        ),
    )


def test_genera_svg_valido_con_una_linea_por_tienda() -> None:
    svg = grafica_historico([_serie("a.example", ["10", "12", "11"]), _serie("b.example", ["9"])])
    raiz = ET.fromstring(svg)
    assert raiz.tag == f"{SVG}svg"
    lineas = raiz.findall(f".//{SVG}polyline")
    assert len(lineas) == 2
    assert lineas[0].get("class") == "serie serie-0"


def test_escapa_el_nombre_de_la_tienda() -> None:
    svg = grafica_historico([_serie("<script>alert(1)</script>", ["10", "11"])])
    assert "<script>" not in svg
    ET.fromstring(svg)


def test_incluye_titulo_accesible_y_rango_de_precios() -> None:
    svg = grafica_historico([_serie("a.example", ["35.20", "44.00"])])
    raiz = ET.fromstring(svg)
    assert raiz.get("role") == "img"
    titulo = raiz.find(f"{SVG}title")
    assert titulo is not None and titulo.text
    assert "35,20 €" in svg and "44,00 €" in svg


def test_los_puntos_quedan_dentro_del_lienzo() -> None:
    svg = grafica_historico([_serie("a.example", ["1", "1000", "500"])], ancho=600, alto=200)
    linea = ET.fromstring(svg).find(f".//{SVG}polyline")
    assert linea is not None
    for par in (linea.get("points") or "").split():
        x, y = (float(v) for v in par.split(","))
        assert 0 <= x <= 600 and 0 <= y <= 200


def test_precio_constante_no_divide_por_cero() -> None:
    svg = grafica_historico([_serie("a.example", ["20", "20", "20"])])
    ET.fromstring(svg)


def test_sin_datos_devuelve_mensaje() -> None:
    svg = grafica_historico([])
    raiz = ET.fromstring(svg)
    assert raiz.findall(f".//{SVG}polyline") == []
    assert "Sin datos" in svg


def test_ordena_por_fecha_y_marca_el_punto_mas_reciente() -> None:
    inicio = date(2026, 10, 1)
    desordenada = SerieHistorico(
        "a.example",
        (
            PuntoHistorico(inicio + timedelta(days=2), Decimal("30")),
            PuntoHistorico(inicio, Decimal("10")),
            PuntoHistorico(inicio + timedelta(days=1), Decimal("20")),
        ),
    )
    raiz = ET.fromstring(grafica_historico([desordenada], ancho=600, alto=200))
    linea = raiz.find(f".//{SVG}polyline")
    circulo = raiz.find(f".//{SVG}circle")
    assert linea is not None and circulo is not None
    xs = [float(p.split(",")[0]) for p in (linea.get("points") or "").split()]
    assert xs == sorted(xs)
    assert float(circulo.get("cx") or 0) == max(xs)


def test_precio_unico_dibuja_una_sola_linea_de_rejilla() -> None:
    raiz = ET.fromstring(grafica_historico([_serie("a.example", ["30"])]))
    assert len(raiz.findall(f".//{SVG}line")) == 1


def test_el_titulo_no_cuenta_series_vacias() -> None:
    svg = grafica_historico([_serie("a.example", ["10", "11"]), SerieHistorico("b.example", ())])
    titulo = ET.fromstring(svg).find(f"{SVG}title")
    assert titulo is not None
    assert "1 tienda," in (titulo.text or "")
