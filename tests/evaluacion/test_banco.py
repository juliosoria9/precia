"""Banco de evaluación (H8): casos etiquetados, ejecución por modo e informe. Sin red."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from precia.evaluacion.banco import CAMPOS_EVALUADOS, RegistroMemoria, ejecutar
from precia.evaluacion.casos import Caso, ErrorCasos, cargar_casos
from precia.evaluacion.informe import (
    a_markdown,
    guardar,
    leer_resultados,
)
from precia.evaluacion.metricas import Juicio
from precia.ia.servicio import ServicioIA
from precia.ia.simulado import Guion, ProveedorSimulado

AHORA = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
CON_JSONLD = (
    '<html><head><title>Pastillas Brembo P 85 075</title><script type="application/ld+json">'
    '{"@type": "Product", "name": "Pastillas Brembo P 85 075", "mpn": "P 85 075",'
    ' "brand": {"@type": "Brand", "name": "Brembo"},'
    ' "offers": {"@type": "Offer", "price": "38.90", "priceCurrency": "EUR",'
    ' "itemCondition": "https://schema.org/NewCondition"}}</script></head>'
    "<body><h1>Pastillas Brembo P 85 075</h1><p>Marca: Brembo</p><p>Ref. P 85 075</p>"
    "<p>38,90 €</p></body></html>"
)
SIN_DATOS = (
    "<html><head><title>Disco de freno</title></head><body><h1>Disco de freno delantero</h1>"
    "<p>Fabricante: Zimmermann</p><p>Referencia: 100.3234.20</p>"
    "<p>Precio: 52,10 €</p></body></html>"
)


def escribir(raiz: Path, nombre: str, html: str) -> None:
    (raiz / nombre).write_text(html, encoding="utf-8")


def casos_de_prueba(raiz: Path) -> list[Caso]:
    escribir(raiz, "con_jsonld.html", CON_JSONLD)
    escribir(raiz, "sin_datos.html", SIN_DATOS)
    return [
        Caso(
            id="brembo",
            fixture="con_jsonld.html",
            url="https://tienda.example/p/brembo-p85075",
            tienda="tienda.example",
            sector="recambios",
            real=False,
            esperado={
                "precio": "38.90",
                "marca": "Brembo",
                "referencia_oem": "P 85 075",
                "estado": "Nuevo",
            },
        ),
        Caso(
            id="zimmermann",
            fixture="sin_datos.html",
            url="https://otra.example/disco-100323420",
            tienda="otra.example",
            sector="recambios",
            real=False,
            esperado={
                "precio": "52.10",
                "marca": "Zimmermann",
                "referencia_oem": "100.3234.20",
                "estado": None,
            },
        ),
    ]


def servicio(*respuestas: Guion) -> tuple[ServicioIA, ProveedorSimulado, RegistroMemoria]:
    proveedor = ProveedorSimulado({"extractor": respuestas}, coste_usd=Decimal("0.002"))
    registro = RegistroMemoria()
    return (
        ServicioIA(
            proveedor,
            registro,
            presupuesto_mes_usd=Decimal(1),
            tope_llamadas=3,
            reloj=lambda: AHORA,
        ),
        proveedor,
        registro,
    )


# --- Casos ------------------------------------------------------------------------------------


def _json_de_casos(**cambios: Any) -> dict[str, Any]:
    caso = {
        "id": "brembo",
        "fixture": "con_jsonld.html",
        "url": "https://tienda.example/p/1",
        "tienda": "tienda.example",
        "sector": "recambios",
        "real": True,
        "esperado": {"precio": "38.90", "marca": "Brembo", "estado": None},
        "evidencia": {"precio": "38,90 €"},
        **cambios,
    }
    return {"version": 1, "descripcion": "prueba", "casos": [caso]}


def test_cargar_casos(tmp_path: Path) -> None:
    ruta = tmp_path / "casos.json"
    ruta.write_text(json.dumps(_json_de_casos()), encoding="utf-8")
    [caso] = cargar_casos(ruta)
    assert caso.id == "brembo" and caso.real is True
    assert caso.esperado == {"precio": "38.90", "marca": "Brembo", "estado": None}
    assert caso.moneda == "EUR"


@pytest.mark.parametrize(
    "cambios",
    [
        {"id": ""},
        {"fixture": "../../etc/passwd"},
        {"fixture": "C:/Windows/win.ini"},
        {"fixture": "/absoluto.html"},
        {"url": "javascript:alert(1)"},
        {"esperado": {"precio": "38,90"}},
        {"esperado": {"precio": "-1.00"}},
        {"esperado": {"campo_raro": "x"}},
        {"esperado": {"marca": 7}},
        {"esperado": {}},
        {"real": "sí"},
    ],
)
def test_un_caso_mal_formado_no_se_carga(tmp_path: Path, cambios: dict[str, Any]) -> None:
    ruta = tmp_path / "casos.json"
    ruta.write_text(json.dumps(_json_de_casos(**cambios)), encoding="utf-8")
    with pytest.raises(ErrorCasos):
        cargar_casos(ruta)


def test_ids_repetidos_o_json_roto(tmp_path: Path) -> None:
    ruta = tmp_path / "casos.json"
    datos = _json_de_casos()
    datos["casos"] *= 2
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    with pytest.raises(ErrorCasos):
        cargar_casos(ruta)
    ruta.write_text("{no es json", encoding="utf-8")
    with pytest.raises(ErrorCasos):
        cargar_casos(ruta)
    with pytest.raises(ErrorCasos):
        cargar_casos(tmp_path / "no-existe.json")


# --- Ejecución --------------------------------------------------------------------------------


def test_modo_clasico_saca_lo_que_dan_los_datos_estructurados(tmp_path: Path) -> None:
    casos = casos_de_prueba(tmp_path)
    informe = asyncio.run(ejecutar(casos, modo="clasico", raiz_fixtures=tmp_path))

    assert informe.modo == "clasico" and len(informe.casos) == 2
    brembo, zimmermann = informe.casos
    assert brembo.juicios == dict.fromkeys(CAMPOS_EVALUADOS, Juicio.CORRECTO)
    # Sin JSON-LD ni perfil, el clásico no saca nada de la segunda ficha.
    assert zimmermann.juicios["precio"] is Juicio.AUSENTE
    assert zimmermann.juicios["estado"] is Juicio.BIEN_AUSENTE
    # 7 campos con valor en las etiquetas, 4 obtenidos y los 4 bien.
    assert informe.total.esperados == 7
    assert informe.total.cobertura == pytest.approx(4 / 7)
    assert informe.total.exactitud == 1.0
    assert informe.coste_usd == 0 and informe.llamadas_ia == 0
    assert informe.segundos_por_caso >= 0


def test_modo_con_ia_completa_lo_que_falta_y_cuenta_el_coste(tmp_path: Path) -> None:
    casos = casos_de_prueba(tmp_path)
    ia, proveedor, registro = servicio(
        {
            "precio": "52,10 €",
            "marca": "Zimmermann",
            "referencia_oem": "100.3234.20",
            "estado": None,
        }
    )
    informe = asyncio.run(ejecutar(casos, modo="api", raiz_fixtures=tmp_path, servicio=ia))

    zimmermann = informe.casos[1]
    assert zimmermann.juicios["precio"] is Juicio.CORRECTO
    assert zimmermann.juicios["marca"] is Juicio.CORRECTO
    assert zimmermann.juicios["referencia_oem"] is Juicio.CORRECTO
    assert informe.total.cobertura == 1.0
    # La primera ficha no necesitó IA: una sola llamada en total.
    assert len(proveedor.peticiones) == 1 and informe.llamadas_ia == 1
    assert informe.coste_usd == Decimal("0.002")
    assert informe.modelo == "simulado/simulado"
    assert len(registro.llamadas) == 1


def test_un_valor_que_la_ia_inventa_no_cuenta_como_obtenido(tmp_path: Path) -> None:
    casos = casos_de_prueba(tmp_path)
    ia, _, _ = servicio(
        {"precio": "1,00 €", "marca": "Brembo", "referencia_oem": "INVENTADA", "estado": "Nuevo"}
    )
    informe = asyncio.run(ejecutar(casos, modo="api", raiz_fixtures=tmp_path, servicio=ia))
    zimmermann = informe.casos[1]
    # Nada de eso está en la página: no anclado, luego no se da por obtenido.
    assert zimmermann.juicios["precio"] is Juicio.AUSENTE
    assert zimmermann.juicios["referencia_oem"] is Juicio.AUSENTE
    assert zimmermann.juicios["estado"] is Juicio.BIEN_AUSENTE
    assert zimmermann.no_anclados >= 2


def test_si_falta_el_fixture_el_caso_queda_con_error_y_sigue(tmp_path: Path) -> None:
    casos = casos_de_prueba(tmp_path)
    (tmp_path / "con_jsonld.html").unlink()
    informe = asyncio.run(ejecutar(casos, modo="clasico", raiz_fixtures=tmp_path))
    assert "no existe" in informe.casos[0].error
    assert informe.casos[0].juicios["precio"] is Juicio.AUSENTE
    assert informe.casos[1].error == ""


def test_un_modo_con_ia_sin_servicio_es_un_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        asyncio.run(ejecutar(casos_de_prueba(tmp_path), modo="api", raiz_fixtures=tmp_path))
    with pytest.raises(ValueError):
        asyncio.run(ejecutar(casos_de_prueba(tmp_path), modo="raro", raiz_fixtures=tmp_path))


# --- Informe ----------------------------------------------------------------------------------


def test_guardar_y_leer_los_resultados_para_la_web(tmp_path: Path) -> None:
    casos = casos_de_prueba(tmp_path)
    clasico = asyncio.run(ejecutar(casos, modo="clasico", raiz_fixtures=tmp_path))
    ruta = tmp_path / "datos" / "evaluacion.json"
    guardar(ruta, [clasico], fecha=AHORA)

    [resultado] = leer_resultados(ruta)
    assert resultado.modo == "Clásico (sin IA)"
    # El fichero guarda las tasas con cuatro decimales.
    assert resultado.cobertura == pytest.approx(4 / 7, abs=1e-4)
    assert resultado.exactitud == 1.0
    assert resultado.coste_usd == 0
    assert resultado.casos == 2
    assert "25 nov" in resultado.detalle


def test_guardar_conserva_los_modos_que_no_se_han_vuelto_a_ejecutar(tmp_path: Path) -> None:
    casos = casos_de_prueba(tmp_path)
    ruta = tmp_path / "evaluacion.json"
    guardar(
        ruta, [asyncio.run(ejecutar(casos, modo="clasico", raiz_fixtures=tmp_path))], fecha=AHORA
    )
    ia, _, _ = servicio(
        {
            "precio": "52,10 €",
            "marca": "Zimmermann",
            "referencia_oem": "100.3234.20",
            "estado": None,
        }
    )
    api = asyncio.run(ejecutar(casos, modo="api", raiz_fixtures=tmp_path, servicio=ia))
    guardar(ruta, [api], fecha=AHORA)
    assert [r.modo for r in leer_resultados(ruta)] == ["Clásico (sin IA)", "IA por API (Claude)"]


@pytest.mark.parametrize(
    "contenido", ["", "{", "[]", '{"version": 99}', '{"version": 1, "modos": 7}']
)
def test_un_fichero_de_resultados_roto_no_rompe_la_web(tmp_path: Path, contenido: str) -> None:
    ruta = tmp_path / "evaluacion.json"
    ruta.write_text(contenido, encoding="utf-8")
    assert leer_resultados(ruta) == []
    assert leer_resultados(tmp_path / "no-existe.json") == []


def test_el_informe_en_markdown_trae_la_tabla_por_modo_y_por_campo(tmp_path: Path) -> None:
    casos = casos_de_prueba(tmp_path)
    clasico = asyncio.run(ejecutar(casos, modo="clasico", raiz_fixtures=tmp_path))
    texto = a_markdown([clasico], fecha=AHORA, casos=casos)
    assert "| Clásico (sin IA) |" in texto
    assert "57 %" in texto and "100 %" in texto
    for campo in CAMPOS_EVALUADOS:
        assert campo in texto
    assert "Crawl4AI" in texto  # la línea base prometida, aún sin medir
    assert "2 casos" in texto
