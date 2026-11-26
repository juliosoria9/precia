"""Resultados del banco de evaluación: el JSON que lee la web y el informe en Markdown.

`guardar` escribe `datos/evaluacion.json` con un resumen por modo. Los modos que no se han
vuelto a ejecutar se conservan, para poder lanzar el clásico (gratis) y los de IA por separado.
`leer_resultados` nunca lanza: un fichero roto o tocado a mano es «sin mediciones».
"""

import json
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from precia.evaluacion.banco import CAMPOS_EVALUADOS, ETIQUETAS, MODOS, InformeModo
from precia.evaluacion.casos import Caso
from precia.evaluacion.metricas import Metricas
from precia.nucleo.formato import decimal, dolares, fecha_hora
from precia.nucleo.modelos import ResultadoEvaluacion

VERSION = 1
MAX_BYTES = 2_000_000
LINEA_BASE = "Línea base: Crawl4AI"
SIN_LINEA_BASE = "sin medir: `crawl4ai` sigue pendiente de aprobar como dependencia"


def _metricas(m: Metricas) -> dict[str, Any]:
    return {
        "correctos": m.correctos,
        "incorrectos": m.incorrectos,
        "ausentes": m.ausentes,
        "inventados": m.inventados,
        "bien_ausentes": m.bien_ausentes,
        "cobertura": round(m.cobertura, 4),
        "exactitud": round(m.exactitud, 4),
    }


def resumen(informe: InformeModo, fecha: datetime) -> dict[str, Any]:
    """Lo que se guarda de un modo: sus medidas y el detalle por campo y por caso."""
    return {
        "modo": informe.modo,
        "modelo": informe.modelo,
        "fecha": fecha.isoformat(),
        "casos": len(informe.casos),
        "total": _metricas(informe.total),
        "por_campo": {campo: _metricas(m) for campo, m in informe.por_campo.items()},
        "coste_usd": str(informe.coste_usd),
        "coste_por_caso_usd": str(informe.coste_por_caso_usd),
        "llamadas_ia": informe.llamadas_ia,
        "segundos_por_caso": round(informe.segundos_por_caso, 4),
        "no_anclados": sum(c.no_anclados for c in informe.casos),
        "detalle": [
            {
                "caso": c.caso_id,
                "juicios": {campo: juicio.value for campo, juicio in c.juicios.items()},
                "obtenido": c.obtenido,
                "error": c.error,
            }
            for c in informe.casos
        ],
    }


def _leer(ruta: Path) -> dict[str, dict[str, Any]]:
    """Los resúmenes guardados, por modo. Vacío si el fichero no existe o no vale."""
    try:
        if ruta.stat().st_size > MAX_BYTES:
            return {}
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(datos, dict) or datos.get("version") != VERSION:
        return {}
    modos = datos.get("modos")
    if not isinstance(modos, dict):
        return {}
    return {m: r for m, r in modos.items() if m in MODOS and isinstance(r, dict)}


def guardar(ruta: Path, informes: Sequence[InformeModo], *, fecha: datetime) -> None:
    """Guarda los modos ejecutados y conserva los demás que ya hubiera."""
    modos = _leer(ruta)
    for informe in informes:
        modos[informe.modo] = resumen(informe, fecha)
    ordenados = {m: modos[m] for m in MODOS if m in modos}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps({"version": VERSION, "modos": ordenados}, ensure_ascii=False, indent=2)
    ruta.write_text(texto + "\n", encoding="utf-8", newline="\n")


def leer_resultados(ruta: Path, *, tasa_bloqueo: float = 0.0) -> list[ResultadoEvaluacion]:
    """Una fila por modo medido, para la pantalla de Evaluación.

    `tasa_bloqueo` no la mide el banco (trabaja con páginas guardadas): la pone quien llama con
    los bloqueos reales de las tiendas.
    """
    resultados: list[ResultadoEvaluacion] = []
    for modo, datos in _leer(ruta).items():
        try:
            total = datos["total"]
            fecha = datetime.fromisoformat(datos["fecha"])
            modelo = str(datos.get("modelo") or "")
            resultados.append(
                ResultadoEvaluacion(
                    modo=ETIQUETAS[modo],
                    cobertura=float(total["cobertura"]),
                    exactitud=float(total["exactitud"]),
                    coste_usd=Decimal(str(datos["coste_por_caso_usd"])),
                    tiempo_s=float(datos["segundos_por_caso"]),
                    tasa_bloqueo=tasa_bloqueo,
                    casos=int(datos["casos"]),
                    detalle=f"{fecha_hora(fecha)}{', ' + modelo if modelo else ''}"[:200],
                )
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue  # Un modo con datos tocados a mano: se queda sin fila.
    return resultados


def _porcentaje(tasa: float) -> str:
    return f"{decimal(tasa * 100, 0)} %"


def a_markdown(informes: Sequence[InformeModo], *, fecha: datetime, casos: Sequence[Caso]) -> str:
    """El informe comparativo, para pegarlo en la memoria."""
    reales = sum(1 for c in casos if c.real)
    tiendas = len({c.tienda for c in casos})
    lineas = [
        "# Banco de evaluación de PrecIA",
        "",
        f"Ejecutado el {fecha_hora(fecha)} sobre {len(casos)} casos etiquetados "
        f"({reales} reales, {tiendas} tiendas). Las páginas son HTML guardado: no se accede a "
        "la red. Los campos evaluados son " + ", ".join(CAMPOS_EVALUADOS) + ".",
        "",
        "| Modo | Cobertura | Exactitud | Inventados | Coste por ficha | Tiempo por ficha | "
        "Llamadas de IA |",
        "|---|---|---|---|---|---|---|",
    ]
    for informe in informes:
        total = informe.total
        lineas.append(
            f"| {informe.etiqueta} | {_porcentaje(total.cobertura)} | "
            f"{_porcentaje(total.exactitud)} | {total.inventados} | "
            f"{dolares(informe.coste_por_caso_usd)} | "
            f"{decimal(informe.segundos_por_caso, 3)} s | {informe.llamadas_ia} |"
        )
    lineas.append(f"| {LINEA_BASE} | — | — | — | — | — | {SIN_LINEA_BASE} |")
    for informe in informes:
        modelo = f" ({informe.modelo})" if informe.modelo else ""
        lineas += [
            "",
            f"## {informe.etiqueta}{modelo}",
            "",
            "| Campo | Con valor en la etiqueta | Correctos | Incorrectos | Ausentes | "
            "Inventados | Cobertura | Exactitud |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for campo in CAMPOS_EVALUADOS:
            m = informe.por_campo.get(campo, Metricas())
            lineas.append(
                f"| {campo} | {m.esperados} | {m.correctos} | {m.incorrectos} | {m.ausentes} | "
                f"{m.inventados} | {_porcentaje(m.cobertura)} | {_porcentaje(m.exactitud)} |"
            )
        fallidos = [c for c in informe.casos if c.error]
        if fallidos:
            lineas += [
                "",
                "Casos con error: " + ", ".join(f"{c.caso_id} ({c.error})" for c in fallidos),
            ]
    lineas += [
        "",
        "Cobertura: de los campos que la etiqueta dice que la página tiene, cuántos se "
        "obtienen. Exactitud: de lo que se da por obtenido, cuánto coincide con la etiqueta; un "
        "valor sacado de una página que no lo tiene cuenta como inventado. Un valor que no "
        "aparece en la página (no anclado) no cuenta como obtenido.",
        "",
    ]
    return "\n".join(lineas)
