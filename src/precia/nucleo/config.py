"""Configuración de la aplicación: rutas de datos, clave de la API y modo demo.

La clave se lee de la variable de entorno `ANTHROPIC_API_KEY` o, si no está, del fichero `.env`
de la raíz del repositorio (ignorado por git). El `.env` se lee con un parser propio: no hace
falta `python-dotenv` para algo de veinte líneas.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[3]
_LINEA = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")


def leer_env(ruta: Path) -> dict[str, str]:
    """Lee `CLAVE=valor` por línea; admite comentarios, comillas y `export`."""
    if not ruta.is_file():
        return {}
    valores: dict[str, str] = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        coincidencia = _LINEA.fullmatch(linea.strip())
        if not coincidencia or linea.lstrip().startswith("#"):
            continue
        clave, valor = coincidencia.groups()
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        else:
            valor = valor.split(" #", 1)[0].strip()
        valores[clave] = valor
    return valores


@dataclass(frozen=True, slots=True)
class Config:
    raiz: Path
    ruta_bd: Path
    ruta_cache: Path
    demo: bool
    # repr=False: la clave nunca aparece al imprimir la configuración ni en un error.
    clave_api: str | None = field(default=None, repr=False)

    @classmethod
    def cargar(cls, raiz: Path = RAIZ_REPO) -> "Config":
        fichero = leer_env(raiz / ".env")

        def valor(nombre: str) -> str | None:
            return os.environ.get(nombre) or fichero.get(nombre) or None

        return cls(
            raiz=raiz,
            ruta_bd=raiz / "datos" / "precia.sqlite3",
            ruta_cache=raiz / "cache",
            demo=(valor("PRECIA_DEMO") or "").lower() in ("1", "true", "si", "sí"),
            clave_api=valor("ANTHROPIC_API_KEY"),
        )
