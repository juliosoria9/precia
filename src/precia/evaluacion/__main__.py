"""Ejecuta el banco de evaluación: `python -m precia.evaluacion [--modos clasico api local]`.

Por defecto solo el modo clásico, que no cuesta nada. `api` llama de verdad a la API de Claude
(hace falta `ANTHROPIC_API_KEY`; cuesta céntimos y tiene su propio presupuesto, `--presupuesto`)
y `local`, al Ollama del equipo. Los resultados van a `datos/evaluacion.json`, que es lo que
enseña la pantalla de Evaluación, y el informe para la memoria, a `datos/evaluacion.md`.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from precia.evaluacion.banco import MODOS, InformeModo, RegistroMemoria, ejecutar
from precia.evaluacion.casos import CASOS, Caso, ErrorCasos, cargar_casos
from precia.evaluacion.informe import a_markdown, guardar
from precia.ia.claude import ProveedorClaude
from precia.ia.ollama import ProveedorOllama
from precia.ia.servicio import ServicioIA
from precia.ia.tipos import ErrorIA, ProveedorIA
from precia.nucleo.config import RAIZ_REPO, Config
from precia.nucleo.modelos import Ajustes

PRESUPUESTO_USD = Decimal("0.50")
"""Tope de gasto de una ejecución del banco con la API (aparte del presupuesto de la web)."""
TOPE_LLAMADAS_CASO = 3


class _SinIA(Exception):
    """El modo pedido no se puede ejecutar: falta la clave, el modelo no tiene tarifa..."""


def _proveedor(modo: str, config: Config, ajustes: Ajustes) -> ProveedorIA:
    if modo == "local":
        return ProveedorOllama(modelo=ajustes.modelo_local)
    if not config.clave_api:
        raise _SinIA("El modo api necesita ANTHROPIC_API_KEY en el entorno o en .env.")
    try:
        return ProveedorClaude(config.clave_api, modelo=ajustes.modelo_api)
    except ErrorIA as error:
        # Sus mensajes son propios: no llevan la clave.
        raise _SinIA(f"No se puede usar la API: {error}") from None


def _servicio(proveedor: ProveedorIA, presupuesto: Decimal) -> ServicioIA:
    return ServicioIA(
        proveedor,
        RegistroMemoria(),
        presupuesto_mes_usd=presupuesto,
        tope_llamadas=TOPE_LLAMADAS_CASO,
    )


async def _ejecutar(
    casos: Sequence[Caso], modos: Sequence[str], fixtures: Path, presupuesto: Decimal
) -> list[InformeModo]:
    config = Config.cargar(RAIZ_REPO)
    ajustes = Ajustes()
    # Antes de gastar nada: que todos los modos pedidos se puedan montar.
    servicios = {
        modo: None
        if modo == "clasico"
        else _servicio(_proveedor(modo, config, ajustes), presupuesto)
        for modo in modos
    }
    return [
        await ejecutar(casos, modo=modo, raiz_fixtures=fixtures, servicio=servicio)
        for modo, servicio in servicios.items()
    ]


def _importe(texto: str) -> Decimal:
    try:
        valor = Decimal(texto.replace(",", "."))
    except InvalidOperation:
        raise argparse.ArgumentTypeError("se espera un importe, por ejemplo 0.50") from None
    if not valor.is_finite() or not 0 < valor <= 20:
        raise argparse.ArgumentTypeError("el presupuesto tiene que estar entre 0 y 20 $")
    return valor


def main(argumentos: Sequence[str] | None = None) -> int:
    lector = argparse.ArgumentParser(prog="python -m precia.evaluacion", description=__doc__)
    lector.add_argument("--modos", nargs="+", choices=MODOS, default=["clasico"])
    lector.add_argument("--casos", type=Path, default=CASOS, help="fichero de casos etiquetados")
    lector.add_argument("--fixtures", type=Path, default=RAIZ_REPO / "tests" / "fixtures")
    lector.add_argument("--salida", type=Path, default=RAIZ_REPO / "datos" / "evaluacion.json")
    lector.add_argument("--informe", type=Path, default=RAIZ_REPO / "datos" / "evaluacion.md")
    lector.add_argument(
        "--presupuesto",
        type=_importe,
        default=PRESUPUESTO_USD,
        help="tope de gasto en dólares de los modos con IA (por defecto 0.50)",
    )
    opciones = lector.parse_args(argumentos)
    modos = list(dict.fromkeys(opciones.modos))
    try:
        casos = cargar_casos(opciones.casos)
        informes = asyncio.run(_ejecutar(casos, modos, opciones.fixtures, opciones.presupuesto))
    except (ErrorCasos, _SinIA) as error:
        print(f"No se ejecuta el banco: {error}", file=sys.stderr)
        return 2
    fecha = datetime.now().astimezone()
    guardar(opciones.salida, informes, fecha=fecha)
    opciones.informe.parent.mkdir(parents=True, exist_ok=True)
    texto = a_markdown(informes, fecha=fecha, casos=casos)
    opciones.informe.write_text(texto, encoding="utf-8", newline="\n")
    print(texto)
    print(f"Resultados en {opciones.salida} e informe en {opciones.informe}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
