"""Arranca la interfaz web en local: `python -m precia.web` (datos reales) o `--demo`."""

import argparse
from collections.abc import Sequence
from dataclasses import replace

import uvicorn

from precia.nucleo.config import Config
from precia.web.app import aplicacion_desde_config

HOST = "127.0.0.1"
PUERTO = 8000


def main(argumentos: Sequence[str] | None = None) -> None:
    lector = argparse.ArgumentParser(prog="python -m precia.web", description=__doc__)
    lector.add_argument(
        "--demo",
        action="store_true",
        help="usa los datos de ejemplo (no visita tiendas ni usa la IA)",
    )
    opciones = lector.parse_args(argumentos)
    config = Config.cargar()
    if opciones.demo:
        config = replace(config, demo=True)
    # Solo en 127.0.0.1: es una herramienta personal y no se publica como servicio (nota legal).
    # Se pasa el objeto (no «modulo:app») para que la fuente elegida sea la que se sirve.
    uvicorn.run(aplicacion_desde_config(config), host=HOST, port=PUERTO, proxy_headers=False)


if __name__ == "__main__":
    main()
