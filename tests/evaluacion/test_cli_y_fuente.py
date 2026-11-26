"""El banco desde la línea de órdenes, en la fuente real y en la pantalla de Evaluación."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.evaluacion.__main__ import main
from precia.evaluacion.casos import CASOS, cargar_casos
from precia.nucleo.config import Config
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import ResultadoEvaluacion
from precia.web.app import crear_app
from tests.asgi import ClienteASGI
from tests.evaluacion.test_banco import CON_JSONLD, SIN_DATOS

RAIZ_FIXTURES = Path(__file__).parent.parent / "fixtures"


def preparar(tmp_path: Path) -> Path:
    (tmp_path / "con_jsonld.html").write_text(CON_JSONLD, encoding="utf-8")
    (tmp_path / "sin_datos.html").write_text(SIN_DATOS, encoding="utf-8")
    casos = {
        "version": 1,
        "descripcion": "prueba",
        "casos": [
            {
                "id": "brembo",
                "fixture": "con_jsonld.html",
                "url": "https://tienda.example/p/brembo",
                "tienda": "tienda.example",
                "sector": "recambios",
                "real": False,
                "esperado": {"precio": "38.90", "marca": "Brembo"},
            },
            {
                "id": "zimmermann",
                "fixture": "sin_datos.html",
                "url": "https://otra.example/disco",
                "tienda": "otra.example",
                "sector": "recambios",
                "real": False,
                "esperado": {"precio": "52.10", "marca": "Zimmermann"},
            },
        ],
    }
    ruta = tmp_path / "casos.json"
    ruta.write_text(json.dumps(casos), encoding="utf-8")
    return ruta


def argumentos(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--casos",
        str(preparar(tmp_path)),
        "--fixtures",
        str(tmp_path),
        "--salida",
        str(tmp_path / "datos" / "evaluacion.json"),
        "--informe",
        str(tmp_path / "informe.md"),
        *extra,
    ]


# --- CLI --------------------------------------------------------------------------------------


def test_el_modo_clasico_escribe_los_resultados_y_el_informe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argumentos(tmp_path)) == 0
    datos = json.loads((tmp_path / "datos" / "evaluacion.json").read_text(encoding="utf-8"))
    assert list(datos["modos"]) == ["clasico"]
    assert datos["modos"]["clasico"]["casos"] == 2
    assert datos["modos"]["clasico"]["total"]["cobertura"] == 0.5
    informe = (tmp_path / "informe.md").read_text(encoding="utf-8")
    assert "| Clásico (sin IA) | 50 % | 100 % |" in informe
    assert "Clásico (sin IA)" in capsys.readouterr().out


def test_el_modo_api_sin_clave_no_hace_nada_y_lo_dice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("precia.evaluacion.__main__.RAIZ_REPO", tmp_path)
    assert main(argumentos(tmp_path, "--modos", "api")) == 2
    assert not (tmp_path / "datos" / "evaluacion.json").exists()
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_un_fichero_de_casos_malo_es_un_error_claro(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ruta = tmp_path / "casos.json"
    ruta.write_text("{", encoding="utf-8")
    assert main(["--casos", str(ruta), "--fixtures", str(tmp_path)]) == 2
    assert "casos.json" in capsys.readouterr().err


def test_un_modo_desconocido_no_se_acepta(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(argumentos(tmp_path, "--modos", "magia"))


# --- Casos reales del repositorio -------------------------------------------------------------


def test_los_casos_del_repositorio_se_cargan_y_sus_paginas_existen() -> None:
    casos = cargar_casos(CASOS)
    assert len(casos) >= 10
    for caso in casos:
        assert (RAIZ_FIXTURES / caso.fixture).is_file(), caso.fixture
    assert sum(1 for c in casos if c.real) >= 10
    assert len({c.tienda for c in casos}) >= 5


# --- Fuente real y web ------------------------------------------------------------------------


def _config(raiz: Path) -> Config:
    return Config(
        raiz=raiz, ruta_bd=raiz / "datos" / "precia.sqlite3", ruta_cache=raiz / "cache", demo=False
    )


def test_la_fuente_real_lee_los_resultados_y_pone_la_tasa_de_bloqueo(tmp_path: Path) -> None:
    assert main(argumentos(tmp_path)) == 0
    fuente = FuenteReal(_config(tmp_path))
    try:
        for dominio in ("a.example", "b.example", "c.example", "d.example"):
            fuente.almacen.alta_tienda(dominio, dominio)
        fuente.almacen.actualizar_tienda("a.example", bloqueos=3)
        [resultado] = fuente.evaluacion()
    finally:
        asyncio.run(fuente.cerrar())
    assert resultado.modo == "Clásico (sin IA)"
    assert resultado.cobertura == 0.5 and resultado.casos == 2
    assert resultado.tasa_bloqueo == 0.25


def test_sin_fichero_de_resultados_no_hay_mediciones(tmp_path: Path) -> None:
    fuente = FuenteReal(_config(tmp_path))
    try:
        assert fuente.evaluacion() == []
    finally:
        asyncio.run(fuente.cerrar())


class FuenteMedida(FuenteDemo):
    def evaluacion(self) -> list[ResultadoEvaluacion]:
        return [
            ResultadoEvaluacion(
                "Clásico (sin IA)", 0.62, 0.97, Decimal(0), 0.012, 0.13, 21, "25 nov 10:00"
            ),
            ResultadoEvaluacion(
                "IA por API (Claude)",
                0.91,
                0.95,
                Decimal("0.0031"),
                1.8,
                0.13,
                21,
                "25 nov 10:05, api/claude-haiku-5-5",
            ),
        ]


def test_la_pantalla_ensena_los_modos_medidos_y_los_que_faltan() -> None:
    html = ClienteASGI(crear_app(FuenteMedida())).get("/evaluacion").texto
    assert "62 %" in html and "97 %" in html and "91 %" in html
    assert "21 casos" in html and "claude-haiku-5-5" in html
    # Los modos sin medir siguen a la vista, vacíos.
    assert "IA local (Ollama)" in html and "Crawl4AI" in html
    assert "python -m precia.evaluacion" in html


def test_la_pantalla_sin_mediciones_dice_como_lanzar_el_banco() -> None:
    class Vacia(FuenteDemo):
        def evaluacion(self) -> list[ResultadoEvaluacion]:
            return []

    html = ClienteASGI(crear_app(Vacia())).get("/evaluacion").texto
    assert "Sin mediciones todavía" in html and "python -m precia.evaluacion" in html


def test_la_fecha_de_la_medicion_no_depende_de_la_zona(tmp_path: Path) -> None:
    assert main(argumentos(tmp_path)) == 0
    datos = json.loads((tmp_path / "datos" / "evaluacion.json").read_text(encoding="utf-8"))
    fecha = datetime.fromisoformat(datos["modos"]["clasico"]["fecha"])
    assert fecha.tzinfo is not None and fecha.astimezone(UTC) <= datetime.now(UTC)
