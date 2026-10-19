"""Arranque de la web: demo o datos reales según la configuración, y cierre de la fuente."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

import precia.web.app as modulo_app
from precia.nucleo.config import Config
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.fuente_real import FuenteReal
from precia.web import __main__ as principal
from precia.web.app import aplicacion_desde_config, crear_app
from tests.asgi import ClienteASGI


def configuracion(raiz: Path, *, demo: bool) -> Config:
    return Config(
        raiz=raiz,
        ruta_bd=raiz / "datos" / "precia.sqlite3",
        ruta_cache=raiz / "cache",
        demo=demo,
        clave_api=None,
    )


async def _ciclo_de_vida(app: Any) -> list[str]:
    """Arranca y apaga la aplicación con el protocolo `lifespan` de ASGI."""
    mensajes = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    enviados: list[str] = []

    async def receive() -> dict[str, Any]:
        return mensajes.pop(0)

    async def send(mensaje: dict[str, Any]) -> None:
        enviados.append(mensaje["type"])

    await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)
    return enviados


class FuenteQueSeCierra(FuenteDemo):
    def __init__(self) -> None:
        super().__init__()
        self.cerrada = False

    async def cerrar(self) -> None:
        self.cerrada = True


def test_crear_app_sin_fuente_sigue_siendo_la_demo() -> None:
    assert "DEMO" in ClienteASGI(crear_app()).get("/").texto


def test_la_fuente_se_cierra_al_apagar() -> None:
    fuente = FuenteQueSeCierra()
    pasos = asyncio.run(_ciclo_de_vida(crear_app(fuente)))
    assert pasos == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert fuente.cerrada


def test_desde_config_en_demo(tmp_path: Path) -> None:
    app = aplicacion_desde_config(configuracion(tmp_path, demo=True))
    assert isinstance(app.state.fuente, FuenteDemo)
    assert "DEMO" in ClienteASGI(app).get("/").texto
    assert not (tmp_path / "datos").exists()


def test_desde_config_con_datos_reales(tmp_path: Path) -> None:
    app = aplicacion_desde_config(configuracion(tmp_path, demo=False))
    fuente = app.state.fuente
    assert isinstance(fuente, FuenteReal)
    try:
        html = ClienteASGI(app).get("/").texto
        assert "DEMO" not in html and "aviso-demo" not in html
        # Sin tiendas, la pantalla de búsqueda invita a añadir una.
        assert 'href="/tiendas"' in html and "Añade una tienda" in html
        assert 'class="ejemplos"' not in html
    finally:
        asyncio.run(fuente.cerrar())


def test_la_fuente_real_se_cierra_al_apagar(tmp_path: Path) -> None:
    app = aplicacion_desde_config(configuracion(tmp_path, demo=False))
    asyncio.run(_ciclo_de_vida(app))
    with pytest.raises(Exception, match="closed"):
        app.state.fuente.tiendas()


def test_ya_no_hay_una_aplicacion_creada_al_importar() -> None:
    # Al importar el módulo no se abre ninguna base de datos ni se decide la fuente.
    assert not hasattr(modulo_app, "app")


class Uvicorn:
    def __init__(self) -> None:
        self.llamadas: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, app: Any, **opciones: Any) -> None:
        self.llamadas.append((app, opciones))


@pytest.mark.parametrize(
    ("argumentos", "demo_en_entorno", "es_demo"),
    [(["--demo"], False, True), ([], False, False), ([], True, True)],
)
def test_main_arranca_uvicorn_solo_en_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argumentos: list[str],
    demo_en_entorno: bool,
    es_demo: bool,
) -> None:
    falso = Uvicorn()
    monkeypatch.setattr(uvicorn, "run", falso)
    # Nunca se lee el .env de verdad en un test.
    monkeypatch.setattr(
        Config,
        "cargar",
        classmethod(lambda cls: configuracion(tmp_path, demo=demo_en_entorno)),
    )
    principal.main(argumentos)
    ((app, opciones),) = falso.llamadas
    assert isinstance(app, FastAPI)
    assert opciones["host"] == "127.0.0.1"
    assert opciones.get("proxy_headers") is False
    assert app.state.fuente.es_demo is es_demo
    asyncio.run(app.state.fuente.cerrar())
