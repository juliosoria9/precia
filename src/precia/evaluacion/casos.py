"""Casos etiquetados del banco de evaluación: una ficha guardada y lo que una persona lee en ella.

Las etiquetas están en `evaluacion/casos/*.json` y las páginas, en `tests/fixtures/` (HTML real
guardado: el banco no accede a la red). El fichero se valida entero al cargarlo: un caso mal
escrito es un error, no un caso que se salta en silencio y cambia las cifras.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

CARPETA = Path(__file__).parent / "casos"
CASOS = CARPETA / "recambios.json"
VERSION = 1
CAMPOS_ETIQUETABLES = ("precio", "marca", "referencia_oem", "estado")
MAX_CASOS = 1_000
MAX_VALOR = 500
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")
_PRECIO = re.compile(r"[0-9]{1,9}\.[0-9]{2}")
_MONEDA = re.compile(r"[A-Z]{3}")
_TRAMO = re.compile(r"[A-Za-z0-9._-]+")


class ErrorCasos(ValueError):
    """El fichero de casos no existe, no es JSON o tiene un caso mal formado."""


@dataclass(frozen=True, slots=True)
class Caso:
    id: str
    fixture: str
    """Ruta de la página guardada, relativa a `tests/fixtures/` y con barras `/`."""
    url: str
    """URL real de la página (el extractor la usa para saber cuál es su producto)."""
    tienda: str
    sector: str
    real: bool
    """Si la página es real (descargada de la tienda) o sintética (escrita para un test)."""
    esperado: Mapping[str, str | None]
    """Campo → valor de la etiqueta; `None` si la página no da ese dato. Un campo que no está
    no se evalúa en este caso."""
    moneda: str = "EUR"
    nota: str = field(default="", compare=False)


def cargar_casos(ruta: Path = CASOS) -> list[Caso]:
    """Los casos del fichero, validados. `ErrorCasos` si algo no cuadra."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except OSError as error:
        raise ErrorCasos(f"No se puede leer {ruta.name}: {type(error).__name__}") from None
    except ValueError as error:
        raise ErrorCasos(f"{ruta.name} no es JSON válido: {error}") from None
    if not isinstance(datos, dict) or datos.get("version") != VERSION:
        raise ErrorCasos(f"{ruta.name}: se espera un objeto con «version»: {VERSION}")
    lista = datos.get("casos")
    if not isinstance(lista, list) or not lista or len(lista) > MAX_CASOS:
        raise ErrorCasos(f"{ruta.name}: «casos» tiene que ser una lista de 1 a {MAX_CASOS} casos")
    casos = [_caso(c, n) for n, c in enumerate(lista, 1)]
    repetidos = {c.id for c in casos if sum(1 for o in casos if o.id == c.id) > 1}
    if repetidos:
        raise ErrorCasos(f"{ruta.name}: id repetidos: {', '.join(sorted(repetidos))}")
    return casos


def _caso(dato: Any, numero: int) -> Caso:
    donde = f"caso {numero}"
    if not isinstance(dato, dict):
        raise ErrorCasos(f"{donde}: se espera un objeto")
    identificador = dato.get("id")
    if not isinstance(identificador, str) or not _ID.fullmatch(identificador):
        raise ErrorCasos(f"{donde}: «id» en minúsculas, cifras y guiones")
    donde = f"caso «{identificador}»"
    fixture = dato.get("fixture")
    if not isinstance(fixture, str) or not _ruta_relativa(fixture):
        raise ErrorCasos(f"{donde}: «fixture» tiene que ser una ruta relativa dentro de fixtures")
    url = dato.get("url")
    if not isinstance(url, str) or not _url_web(url):
        raise ErrorCasos(f"{donde}: «url» tiene que ser una URL http(s)")
    textos = {}
    for clave in ("tienda", "sector"):
        valor = dato.get(clave)
        if not isinstance(valor, str) or not valor.strip() or len(valor) > 100:
            raise ErrorCasos(f"{donde}: falta «{clave}»")
        textos[clave] = valor.strip()
    real = dato.get("real")
    if not isinstance(real, bool):
        raise ErrorCasos(f"{donde}: «real» tiene que ser true o false")
    moneda = dato.get("moneda", "EUR")
    if not isinstance(moneda, str) or not _MONEDA.fullmatch(moneda):
        raise ErrorCasos(f"{donde}: «moneda» tiene que ser un código de tres letras")
    nota = dato.get("nota", "")
    return Caso(
        id=identificador,
        fixture=fixture,
        url=url,
        tienda=textos["tienda"],
        sector=textos["sector"],
        real=real,
        esperado=_esperado(dato.get("esperado"), donde),
        moneda=moneda,
        nota=nota if isinstance(nota, str) else "",
    )


def _esperado(dato: Any, donde: str) -> dict[str, str | None]:
    if not isinstance(dato, dict) or not dato:
        raise ErrorCasos(f"{donde}: «esperado» necesita al menos un campo")
    esperado: dict[str, str | None] = {}
    for campo, valor in dato.items():
        if campo not in CAMPOS_ETIQUETABLES:
            raise ErrorCasos(f"{donde}: «{campo}» no es un campo que se etiquete")
        if valor is None:
            esperado[campo] = None
            continue
        if not isinstance(valor, str) or not valor.strip() or len(valor) > MAX_VALOR:
            raise ErrorCasos(f"{donde}: «{campo}» tiene que ser un texto o null")
        if campo == "precio" and not _PRECIO.fullmatch(valor):
            raise ErrorCasos(f"{donde}: el precio se escribe como «38.90»")
        esperado[campo] = valor.strip()
    return esperado


def _ruta_relativa(texto: str) -> bool:
    """Una ruta con barras `/`, sin `..`, sin unidad ni raíz: no sale de `tests/fixtures/`."""
    ruta = PurePosixPath(texto)
    if ruta.is_absolute() or "\\" in texto or ":" in texto or not ruta.parts:
        return False
    return all(_TRAMO.fullmatch(parte) and parte not in (".", "..") for parte in ruta.parts)


def _url_web(texto: str) -> bool:
    try:
        partes = urlsplit(texto)
    except ValueError:
        return False
    return partes.scheme in ("http", "https") and bool(partes.hostname)
