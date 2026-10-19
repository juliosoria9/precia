"""Arranca la interfaz web en local: `python -m precia.web`."""

import uvicorn


def main() -> None:
    # Solo en 127.0.0.1: es una herramienta personal y no se publica como servicio (nota legal).
    uvicorn.run("precia.web.app:app", host="127.0.0.1", port=8000, proxy_headers=False)


if __name__ == "__main__":
    main()
