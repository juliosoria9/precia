"""Caché de páginas en disco: evita volver a pedir a la tienda lo que se descargó hace poco.

Un fichero JSON por URL, con nombre `sha256(url).json`: el nombre no depende de nada que controle
la web (ni rutas ni caracteres raros). La versión renderizada con Chromium (`js=True`) se guarda
aparte, porque no es el mismo HTML que la descarga directa.

Lo que se lee del disco se valida igual que si viniera de fuera; un fichero dañado o ajeno cuenta
como fallo de caché.
"""

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from precia.navegador.tipos import Pagina

VERSION = 1
MAXIMO_BYTES = 16 * 1024 * 1024


def _ahora() -> datetime:
    return datetime.now(UTC)


class CachePaginas:
    def __init__(
        self,
        directorio: Path | str,
        ttl: float,
        *,
        reloj: Callable[[], datetime] = _ahora,
        max_bytes: int = MAXIMO_BYTES,
    ) -> None:
        """`ttl` en segundos, contados desde que se descargó la página."""
        self._directorio = Path(directorio)
        self._ttl = ttl
        self._reloj = reloj
        self._max_bytes = max_bytes

    def obtener(self, url: str, *, js: bool = False) -> Pagina | None:
        ruta = self._ruta(url, js)
        try:
            if ruta.stat().st_size > self._max_bytes:
                return None
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        pagina = _desde_json(datos)
        if pagina is None or pagina.url != url:
            return None
        edad = (self._reloj() - pagina.obtenida).total_seconds()
        if not 0 <= edad <= self._ttl:
            return None
        return pagina

    def guardar(self, pagina: Pagina, *, js: bool = False) -> None:
        self._directorio.mkdir(parents=True, exist_ok=True)
        datos = {
            "version": VERSION,
            "url": pagina.url,
            "url_final": pagina.url_final,
            "estado": pagina.estado,
            "html": pagina.html,
            "tipo_contenido": pagina.tipo_contenido,
            "obtenida": pagina.obtenida.isoformat(),
        }
        # Escritura atómica: un lector a la vez nunca ve un JSON a medias.
        descriptor, temporal = tempfile.mkstemp(dir=self._directorio, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as fichero:
                json.dump(datos, fichero, ensure_ascii=False)
            os.replace(temporal, self._ruta(pagina.url, js))
        except BaseException:
            Path(temporal).unlink(missing_ok=True)
            raise

    def _ruta(self, url: str, js: bool) -> Path:
        clave = f"js:{url}" if js else url
        return self._directorio / f"{hashlib.sha256(clave.encode('utf-8')).hexdigest()}.json"


def _desde_json(datos: Any) -> Pagina | None:
    if not isinstance(datos, dict) or datos.get("version") != VERSION:
        return None
    textos = ("url", "url_final", "html", "tipo_contenido", "obtenida")
    if not all(isinstance(datos.get(c), str) for c in textos):
        return None
    estado = datos.get("estado")
    if not isinstance(estado, int) or isinstance(estado, bool):
        return None
    try:
        obtenida = datetime.fromisoformat(datos["obtenida"])
    except ValueError:
        return None
    if obtenida.tzinfo is None:
        return None
    return Pagina(
        url=datos["url"],
        url_final=datos["url_final"],
        estado=estado,
        html=datos["html"],
        tipo_contenido=datos["tipo_contenido"],
        obtenida=obtenida,
        desde_cache=True,
    )
