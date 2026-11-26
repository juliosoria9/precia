"""Arranca la interfaz web en local: `python -m precia.web` (datos reales) o `--demo`."""

import argparse
import logging
from collections.abc import Sequence
from dataclasses import replace

import uvicorn

from precia.nucleo.config import HOST_LOCAL, Config
from precia.web.app import aplicacion_desde_config

PUERTO = 8000

registro = logging.getLogger(__name__)


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
    # Por defecto solo en 127.0.0.1: es una herramienta personal y no se publica como servicio
    # (nota legal). En un contenedor (`PRECIA_HOST=0.0.0.0`) escucha en todas sus interfaces y es
    # el servidor quien publica el puerto solo en su 127.0.0.1; la web sigue respondiendo
    # únicamente a `127.0.0.1` y `localhost` (`TrustedHostMiddleware`).
    if config.host != HOST_LOCAL:
        registro.warning(
            "PrecIA escucha en %s y no tiene usuarios ni contraseña: publica el puerto solo en "
            "el 127.0.0.1 del servidor y entra por un túnel SSH.",
            config.host,
        )
    # Se pasa el objeto (no «modulo:app») para que la fuente elegida sea la que se sirve.
    uvicorn.run(aplicacion_desde_config(config), host=config.host, port=PUERTO, proxy_headers=False)


if __name__ == "__main__":
    main()
