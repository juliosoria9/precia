"""Histórico de precios y alertas."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from precia.almacen.sqlite import Almacen, DatoNoValido, ProductoNoEncontrado
from precia.nucleo.modelos import PuntoHistorico, SerieHistorico

PRODUCTO = "oem-1k0698451j"
NOMBRE = "Pastillas de freno delanteras Bosch"
URL_A = "https://recambiosnorte.example/p/pastillas-bosch"
URL_B = "https://piezasya.example/item/9981"


def _guardar(a: Almacen, tienda: str, precio: str, fecha: datetime, url: str = URL_A) -> None:
    a.guardar_precio(PRODUCTO, NOMBRE, "1K0698451J", tienda, url, Decimal(precio), fecha)


def test_historico_vacio(almacen: Almacen) -> None:
    assert almacen.historico(PRODUCTO) == []
    assert almacen.producto_nombre(PRODUCTO) is None


def test_una_serie_por_tienda_con_el_ultimo_precio_del_dia(almacen: Almacen) -> None:
    _guardar(almacen, "recambiosnorte.example", "38.90", datetime(2026, 10, 1, 9, 0))
    _guardar(almacen, "recambiosnorte.example", "37.50", datetime(2026, 10, 1, 18, 0))
    _guardar(almacen, "recambiosnorte.example", "39.00", datetime(2026, 10, 1, 12, 0))
    _guardar(almacen, "recambiosnorte.example", "36.10", datetime(2026, 10, 3, 8, 0))
    _guardar(almacen, "piezasya.example", "41.00", datetime(2026, 10, 2, 10, 0), URL_B)

    assert almacen.historico(PRODUCTO) == [
        SerieHistorico(
            "piezasya.example",
            (PuntoHistorico(date(2026, 10, 2), Decimal("41.00")),),
        ),
        SerieHistorico(
            "recambiosnorte.example",
            (
                PuntoHistorico(date(2026, 10, 1), Decimal("37.50")),
                PuntoHistorico(date(2026, 10, 3), Decimal("36.10")),
            ),
        ),
    ]


def test_mismo_instante_gana_el_ultimo_guardado(almacen: Almacen) -> None:
    instante = datetime(2026, 10, 1, 9, 0)
    _guardar(almacen, "a.example", "10.00", instante)
    _guardar(almacen, "a.example", "11.00", instante)
    (serie,) = almacen.historico(PRODUCTO)
    assert serie.puntos == (PuntoHistorico(date(2026, 10, 1), Decimal("11.00")),)


def test_los_decimales_no_pierden_precision(almacen: Almacen) -> None:
    # 0.1 + 0.2 en coma flotante no es 0.3: el precio se guarda como texto.
    _guardar(almacen, "a.example", "1234567890.123456789", datetime(2026, 10, 1))
    (serie,) = almacen.historico(PRODUCTO)
    assert serie.puntos[0].precio == Decimal("1234567890.123456789")
    assert str(serie.puntos[0].precio) == "1234567890.123456789"


def test_producto_nombre_es_el_ultimo_guardado(almacen: Almacen) -> None:
    _guardar(almacen, "a.example", "10", datetime(2026, 10, 1))
    fecha = datetime(2026, 10, 2)
    almacen.guardar_precio(PRODUCTO, "Pastillas Bosch", None, "b.example", URL_B, Decimal(9), fecha)
    assert almacen.producto_nombre(PRODUCTO) == "Pastillas Bosch"


def test_historico_de_otro_producto_no_se_mezcla(almacen: Almacen) -> None:
    _guardar(almacen, "a.example", "10", datetime(2026, 10, 1))
    fecha = datetime(2026, 10, 1)
    almacen.guardar_precio("txt-abc123", "Filtro", None, "a.example", URL_A, Decimal(5), fecha)
    assert len(almacen.historico("txt-abc123")) == 1
    assert almacen.historico("txt-abc123")[0].puntos[0].precio == Decimal("5")


def test_inyeccion_sql_en_la_tienda_del_historico(almacen: Almacen) -> None:
    malo = "x'); DROP TABLE precios; --"
    _guardar(almacen, malo, "10", datetime(2026, 10, 1))
    _guardar(almacen, "a.example", "12", datetime(2026, 10, 1))
    assert {s.tienda for s in almacen.historico(PRODUCTO)} == {malo, "a.example"}
    assert almacen.historico("' OR 1=1 --") == []


@pytest.mark.parametrize("precio", ["-1", "NaN", "Infinity", "sNaN", "9" * 51])
def test_rechaza_precios_no_validos(almacen: Almacen, precio: str) -> None:
    with pytest.raises(DatoNoValido):
        _guardar(almacen, "a.example", precio, datetime(2026, 10, 1))
    assert almacen.historico(PRODUCTO) == []


@pytest.mark.parametrize(
    ("producto", "nombre", "url"),
    [
        ("", NOMBRE, URL_A),
        (PRODUCTO, "n" * 501, URL_A),
        (PRODUCTO, NOMBRE, "u" * 2049),
        # `json.loads` del JSON-LD de una tienda convierte "\\ud800" en un sustituto suelto.
        (PRODUCTO, "Pastillas \ud800", URL_A),
    ],
)
def test_guardar_precio_valida_tamanos(
    almacen: Almacen, producto: str, nombre: str, url: str
) -> None:
    with pytest.raises(DatoNoValido):
        almacen.guardar_precio(
            producto, nombre, None, "a.example", url, Decimal("1"), datetime(2026, 10, 1)
        )


def test_crear_y_listar_alertas(almacen: Almacen) -> None:
    _guardar(almacen, "a.example", "38.90", datetime(2026, 10, 1))
    alerta = almacen.crear_alerta(PRODUCTO, Decimal("35.00"), "correo")
    assert alerta.id >= 1
    assert alerta.producto_id == PRODUCTO
    assert alerta.producto_nombre == NOMBRE
    assert alerta.umbral == Decimal("35.00")
    assert alerta.canal == "correo"
    assert alerta.activa is True
    assert alerta.ultima_activacion is None
    segunda = almacen.crear_alerta(PRODUCTO, Decimal("30"), "telegram")
    assert almacen.alertas() == [alerta, segunda]


def test_alerta_de_producto_desconocido(almacen: Almacen) -> None:
    with pytest.raises(ProductoNoEncontrado):
        almacen.crear_alerta("nada", Decimal("10"), "correo")


@pytest.mark.parametrize(
    ("umbral", "canal"),
    [("0", "correo"), ("-5", "correo"), ("NaN", "correo"), ("10", ""), ("10", "c" * 51)],
)
def test_alerta_valida_umbral_y_canal(almacen: Almacen, umbral: str, canal: str) -> None:
    _guardar(almacen, "a.example", "38.90", datetime(2026, 10, 1))
    with pytest.raises(DatoNoValido):
        almacen.crear_alerta(PRODUCTO, Decimal(umbral), canal)
    assert almacen.alertas() == []
