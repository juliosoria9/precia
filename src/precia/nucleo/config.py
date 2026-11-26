"""Configuración de la aplicación: rutas de datos, clave de la API y modo demo.

La clave se lee de la variable de entorno `ANTHROPIC_API_KEY` o, si no está, del fichero `.env`
de la raíz del repositorio (ignorado por git). El `.env` se lee con un parser propio: no hace
falta `python-dotenv` para algo de veinte líneas.

El proxy de salida (`navegador.proxy`) se configura igual: `PRECIA_PROXY` (una URL
`http://usuario:contraseña@servidor:puerto`) o `EVOMI_API_KEY` (las credenciales se piden a su
API), con `EVOMI_PRODUCTO`, `PRECIA_PROXY_PAIS` y `PRECIA_PROXY_MODO`. Si están las dos,
manda `PRECIA_PROXY`. Sin ninguna, no hay proxy. Aquí solo se leen: la URL se interpreta y la API
se consulta al crear el navegador, para que un error no impida arrancar la web.

Los avisos de las alertas (H7) también: `PRECIA_SMTP_*` y `PRECIA_ALERTAS_EMAIL` para el correo
y `PRECIA_TELEGRAM_TOKEN` y `PRECIA_TELEGRAM_CHAT` para Telegram (`seguimiento.avisos` los
valida). `PRECIA_ALERTAS_CADA_H` dice cada cuántas horas se comprueban las alertas (12 por
defecto; 0 lo desactiva; como poco 1, para no repetir búsquedas en las tiendas más a menudo).

`PRECIA_HOST` es la dirección en la que escucha la web: `127.0.0.1` (por defecto) o `0.0.0.0`,
que solo tiene sentido dentro de un contenedor (H9, ver el README).
"""

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from precia.navegador.proxy import ModoProxy, modo_proxy
from precia.seguimiento.avisos import VARIABLES as VARIABLES_AVISOS

RAIZ_REPO = Path(__file__).resolve().parents[3]
ALERTAS_CADA_H = 12.0
MIN_ALERTAS_CADA_H = 1.0
MAX_ALERTAS_CADA_H = 24.0 * 30
HOST_LOCAL = "127.0.0.1"
HOST_CONTENEDOR = "0.0.0.0"
"""Todas las interfaces: solo para un contenedor cuyo puerto se publica en el 127.0.0.1 del
servidor. La web no tiene usuarios ni contraseña."""
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


def horas_entre_comprobaciones(texto: str | None) -> float:
    """`PRECIA_ALERTAS_CADA_H`: 0 desactiva la comprobación; un valor raro, el de por defecto."""
    if texto is None or not texto.strip():
        return ALERTAS_CADA_H
    try:
        horas = float(texto.strip().replace(",", "."))
    except ValueError:
        return ALERTAS_CADA_H
    if not math.isfinite(horas) or horas < 0 or horas > MAX_ALERTAS_CADA_H:
        return ALERTAS_CADA_H
    return 0.0 if horas == 0 else max(horas, MIN_ALERTAS_CADA_H)


def direccion_de_escucha(texto: str | None) -> str:
    """`PRECIA_HOST`: `127.0.0.1` (por defecto) o `0.0.0.0` (contenedor). Nada más vale."""
    return HOST_CONTENEDOR if (texto or "").strip() == HOST_CONTENEDOR else HOST_LOCAL


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
    # repr=False: lleva la contraseña de SMTP y el token de Telegram.
    avisos: Mapping[str, str] = field(default_factory=dict, repr=False)
    """Las variables de los avisos de las alertas que están puestas (`seguimiento.avisos`)."""
    alertas_cada_h: float = ALERTAS_CADA_H
    """Cada cuántas horas se comprueban las alertas; 0 = solo a mano."""
    host: str = HOST_LOCAL
    """Dónde escucha el servidor web (`direccion_de_escucha`)."""

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
            avisos={n: v for n in VARIABLES_AVISOS if (v := valor(n)) is not None},
            alertas_cada_h=horas_entre_comprobaciones(valor("PRECIA_ALERTAS_CADA_H")),
            host=direccion_de_escucha(valor("PRECIA_HOST")),
        )
