"""Banco de evaluación (H8): resuelve los mismos casos etiquetados en cada modo y los mide.

Cada caso es una ficha guardada. El banco le pasa la misma función que usa la aplicación
(`extraccion.extraer_ficha`), sin perfil de tienda, y compara lo que sale con la etiqueta:

- `clasico`: solo los datos estructurados de la página (JSON-LD, microdatos, OpenGraph).
- `local` y `api`: lo mismo, más el extractor de respaldo con IA (Ollama o la API de Claude)
  para los campos que falten.

Un valor **no anclado** en la página no cuenta como obtenido: es lo que hace la aplicación con
un precio que la IA devuelve y no aparece en la ficha (nota 05). Se cuenta aparte.

No hay red: las páginas salen de `tests/fixtures/`. La IA sí se llama de verdad en los modos
con IA (cuesta dinero en `api`); el gasto se apunta en un registro en memoria, no en la base
de datos de la aplicación.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from precia.evaluacion.casos import Caso
from precia.evaluacion.metricas import Juicio, Metricas, juzgar
from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.tipos import FichaExtraida
from precia.ia.extractor import ExtractorRespaldoIA
from precia.ia.servicio import ServicioIA
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import LlamadaIA

CAMPOS_EVALUADOS = ("precio", "marca", "referencia_oem", "estado")
"""Los campos por defecto de una búsqueda (`ConsultaBusqueda.campos`)."""
MODOS = ("clasico", "local", "api")
ETIQUETAS = {
    "clasico": "Clásico (sin IA)",
    "local": "IA local (Ollama)",
    "api": "IA por API (Claude)",
}
"""Nombre de cada modo en la web y en el informe."""
MAX_BYTES_FIXTURE = 5_000_000


class RegistroMemoria:
    """`ia.tipos.RegistroLlamadas` en memoria: el banco no escribe en la base de datos."""

    def __init__(self) -> None:
        self.llamadas: list[LlamadaIA] = []

    def registrar_llamada(self, llamada: LlamadaIA) -> None:
        self.llamadas.append(llamada)

    def gasto_mes_usd(self) -> Decimal:
        return sum((c.coste_usd for c in self.llamadas), Decimal(0))


@dataclass(frozen=True, slots=True)
class ResultadoCaso:
    caso_id: str
    juicios: dict[str, Juicio]
    """Solo los campos que el caso etiqueta."""
    obtenido: dict[str, str | None]
    segundos: float
    llamadas_ia: int = 0
    coste_usd: Decimal = Decimal(0)
    no_anclados: int = 0
    """Valores que se extrajeron pero no aparecen en la página: no cuentan como obtenidos."""
    error: str = ""


@dataclass(frozen=True, slots=True)
class InformeModo:
    modo: str
    modelo: str
    """«proveedor/modelo» de la IA; vacío en clásico."""
    casos: tuple[ResultadoCaso, ...]
    por_campo: dict[str, Metricas] = field(default_factory=dict)

    @property
    def etiqueta(self) -> str:
        return ETIQUETAS[self.modo]

    @property
    def total(self) -> Metricas:
        return sum(self.por_campo.values(), Metricas())

    @property
    def coste_usd(self) -> Decimal:
        return sum((c.coste_usd for c in self.casos), Decimal(0))

    @property
    def llamadas_ia(self) -> int:
        return sum(c.llamadas_ia for c in self.casos)

    @property
    def segundos_por_caso(self) -> float:
        return sum(c.segundos for c in self.casos) / len(self.casos) if self.casos else 0.0

    @property
    def coste_por_caso_usd(self) -> Decimal:
        return self.coste_usd / len(self.casos) if self.casos else Decimal(0)


async def ejecutar(
    casos: Sequence[Caso],
    *,
    modo: str,
    raiz_fixtures: Path,
    servicio: ServicioIA | None = None,
    cronometro: Callable[[], float] = time.perf_counter,
) -> InformeModo:
    """Resuelve `casos` en `modo` y devuelve sus medidas. Los modos con IA necesitan `servicio`."""
    if modo not in MODOS:
        raise ValueError(f"Modo desconocido: {modo}")
    if (modo == "clasico") != (servicio is None):
        raise ValueError("El modo clásico va sin servicio de IA y los demás lo necesitan")
    resultados: list[ResultadoCaso] = []
    por_campo = {campo: Metricas() for campo in CAMPOS_EVALUADOS}
    for caso in casos:
        resultado = await _resolver(caso, raiz_fixtures, servicio, cronometro)
        resultados.append(resultado)
        for campo, juicio in resultado.juicios.items():
            por_campo[campo].anotar(juicio)
    modelo = ""
    if servicio is not None:
        modelo = f"{servicio.proveedor.nombre}/{servicio.proveedor.modelo}"
    return InformeModo(modo, modelo, tuple(resultados), por_campo)


async def _resolver(
    caso: Caso,
    raiz: Path,
    servicio: ServicioIA | None,
    cronometro: Callable[[], float],
) -> ResultadoCaso:
    try:
        html = _leer(raiz / caso.fixture)
    except OSError:
        juicios = {c: juzgar(c, e, None) for c, e in caso.esperado.items()}
        vacio: dict[str, str | None] = dict.fromkeys(caso.esperado)
        return ResultadoCaso(caso.id, juicios, vacio, 0.0, error=f"no existe {caso.fixture}")
    pagina = Pagina(
        url=caso.url,
        url_final=caso.url,
        estado=200,
        html=html,
        tipo_contenido="text/html; charset=utf-8",
        obtenida=datetime.now(UTC),
    )
    sesion = servicio.nueva_busqueda() if servicio is not None else None
    extractor = ExtractorRespaldoIA(sesion) if sesion is not None else None
    inicio = cronometro()
    ficha = await extraer_ficha(pagina, CAMPOS_EVALUADOS, extractor_ia=extractor)
    segundos = max(cronometro() - inicio, 0.0)
    obtenido, no_anclados = _valores(ficha, caso.moneda)
    return ResultadoCaso(
        caso_id=caso.id,
        juicios={c: juzgar(c, e, obtenido.get(c)) for c, e in caso.esperado.items()},
        obtenido={c: obtenido.get(c) for c in caso.esperado},
        segundos=segundos,
        llamadas_ia=sesion.llamadas if sesion is not None else 0,
        coste_usd=sesion.coste_usd if sesion is not None else Decimal(0),
        no_anclados=no_anclados,
    )


def _leer(ruta: Path) -> str:
    if ruta.stat().st_size > MAX_BYTES_FIXTURE:
        raise OSError("fixture demasiado grande")
    return ruta.read_text(encoding="utf-8", errors="replace")


def _valores(ficha: FichaExtraida, moneda: str) -> tuple[dict[str, str | None], int]:
    """Lo que la ficha da por obtenido (solo lo anclado) y cuántos valores no lo estaban."""
    obtenido: dict[str, str | None] = {}
    no_anclados = 0
    for campo, valor in ficha.campos.items():
        if not valor.anclado:
            no_anclados += 1
        elif campo != "precio":
            obtenido[campo] = valor.valor
    precio = ficha.campos.get("precio")
    if ficha.precio is not None and (precio is None or precio.anclado):
        # El importe ya leído, no su texto («38,90 €»); en otra moneda no es el de la etiqueta.
        obtenido["precio"] = str(ficha.precio.importe) if ficha.precio.moneda == moneda else None
    return obtenido, no_anclados
