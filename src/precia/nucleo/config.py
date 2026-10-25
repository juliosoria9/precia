"""Configuración de la aplicación: rutas de datos, clave de la API y modo demo.

La clave se lee de la variable de entorno `ANTHROPIC_API_KEY` o, si no está, del fichero `.env`
de la raíz del repositorio (ignorado por git). El `.env` se lee con un parser propio: no hace
falta `python-dotenv` para algo de veinte líneas.

El proxy de salida (`navegador.proxy`) se configura igual: `PRECIA_PROXY` (una URL
`http://usuario:contraseña@servidor:puerto`) o `EVOMI_API_KEY` (las credenciales se piden a su
API), con `EVOMI_PRODUCTO`, `PRECIA_PROXY_PAIS` y `PRECIA_PROXY_MODO`. Si están las dos,
manda `PRECIA_PROXY`. Sin ninguna, no hay proxy. Aquí solo se leen: la URL se interpreta y la API
se consulta al crear el navegador, para que un error no impida arrancar la web.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from precia.navegador.proxy import ModoProxy, modo_proxy

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
    proxy_url: str | None = field(default=None, repr=False)
    """`PRECIA_PROXY`: lleva la contraseña del proxy."""
    clave_evomi: str | None = field(default=None, repr=False)
    evomi_producto: str = "rpc"
    proxy_pais: str = "ES"
    proxy_modo: ModoProxy = ModoProxy.BLOQUEO

    @property
    def hay_proxy(self) -> bool:
        """Si se ha configurado algún proxy (aún sin comprobar que funcione)."""
        return bool(self.proxy_url or self.clave_evomi)

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
            proxy_url=valor("PRECIA_PROXY"),
            clave_evomi=valor("EVOMI_API_KEY"),
            evomi_producto=(valor("EVOMI_PRODUCTO") or "rpc").strip().lower(),
            proxy_pais=(valor("PRECIA_PROXY_PAIS") or "ES").strip().upper(),
            proxy_modo=modo_proxy(valor("PRECIA_PROXY_MODO")),
        )
