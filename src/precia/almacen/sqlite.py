"""Almacén de PrecIA sobre SQLite: tiendas, perfiles, histórico, alertas, ajustes y llamadas de IA.

También guarda el catálogo local de URL de las tiendas sin buscador, sacado de su `sitemap.xml`
(nota 16 §B): solo la URL, las palabras de su slug y el `lastmod`, nunca precios ni descripciones.

Todas las consultas son parametrizadas: los textos vienen de webs (no confiables) o de la IA y
nunca se pegan en el SQL. Los importes se guardan como TEXT con `str(Decimal)` para no pasar
nunca por coma flotante, y las fechas en ISO 8601.

El esquema evoluciona con `MIGRACIONES`: cada entrada es un script y `PRAGMA user_version`
guarda cuántas se han aplicado. Una migración se aplica entera o no se aplica.

Cada precio guarda cómo coincide su oferta con la referencia buscada (exacta, revisión o
equivalente, B5) y cada alerta si cuenta solo las exactas, que es lo que hace por defecto.

La caché del extractor de respaldo con IA (A3) guarda la huella sha256 del texto de la ficha, los
campos pedidos, el modelo y los valores ya limpios: nunca el texto de la página ni el prompt.
"""

import heapq
import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import zip_longest
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple, Self
from urllib.parse import unquote, urlsplit

from precia.emparejado.referencias import referencias_en
from precia.emparejado.texto import normalizar_titulo
from precia.nucleo.modelos import (
    CLAVES_CAMPOS,
    Ajustes,
    Alerta,
    Coincidencia,
    EstadoPerfil,
    FalloConfiguracion,
    GrupoProducto,
    InfoCatalogo,
    LlamadaIA,
    Modo,
    Oferta,
    OrigenDato,
    PuntoHistorico,
    SerieHistorico,
    Tienda,
    ValorCampo,
    VersionPerfil,
)
from precia.perfiles.tipos import TIPO_HTML, TIPO_LISTADO, TIPO_SITEMAP, TIPOS_PERFIL, Perfil

MIGRACIONES: tuple[str, ...] = (
    """
    CREATE TABLE tiendas (
        dominio TEXT PRIMARY KEY,
        nombre TEXT NOT NULL,
        estado TEXT NOT NULL,
        robots TEXT NOT NULL DEFAULT '',
        robots_permite INTEGER NOT NULL DEFAULT 1 CHECK (robots_permite IN (0, 1)),
        ultima_visita TEXT,
        bloqueos INTEGER NOT NULL DEFAULT 0 CHECK (bloqueos >= 0)
    ) STRICT;

    CREATE TABLE perfiles (
        dominio TEXT NOT NULL REFERENCES tiendas (dominio) ON DELETE CASCADE,
        version INTEGER NOT NULL CHECK (version >= 1),
        url_busqueda TEXT NOT NULL,
        sel_resultado TEXT NOT NULL,
        sel_titulo TEXT NOT NULL,
        sel_enlace TEXT NOT NULL,
        sel_precio_listado TEXT,
        sel_campos TEXT NOT NULL,
        necesita_js INTEGER NOT NULL CHECK (necesita_js IN (0, 1)),
        autor TEXT NOT NULL,
        revisado INTEGER NOT NULL DEFAULT 0 CHECK (revisado IN (0, 1)),
        creado TEXT NOT NULL,
        nota TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (dominio, version)
    ) STRICT;

    CREATE TABLE busquedas (
        id INTEGER PRIMARY KEY,
        texto TEXT NOT NULL,
        modo TEXT NOT NULL,
        fecha TEXT NOT NULL,
        llamadas_ia INTEGER NOT NULL DEFAULT 0,
        coste_ia_usd TEXT NOT NULL DEFAULT '0',
        duracion_s REAL NOT NULL DEFAULT 0
    ) STRICT;

    CREATE TABLE productos (
        id TEXT PRIMARY KEY,
        nombre TEXT NOT NULL,
        referencia_oem TEXT
    ) STRICT;

    CREATE TABLE precios (
        id INTEGER PRIMARY KEY,
        producto_id TEXT NOT NULL REFERENCES productos (id) ON DELETE CASCADE,
        tienda TEXT NOT NULL,
        url TEXT NOT NULL,
        precio TEXT NOT NULL,
        fecha TEXT NOT NULL,
        dia TEXT NOT NULL
    ) STRICT;
    CREATE INDEX precios_por_producto ON precios (producto_id, tienda, dia);

    CREATE TABLE llamadas_ia (
        id INTEGER PRIMARY KEY,
        fecha TEXT NOT NULL,
        mes TEXT NOT NULL,
        componente TEXT NOT NULL,
        proveedor TEXT NOT NULL,
        modelo TEXT NOT NULL,
        tokens_entrada INTEGER NOT NULL,
        tokens_salida INTEGER NOT NULL,
        coste_usd TEXT NOT NULL,
        resultado TEXT NOT NULL,
        duracion_ms INTEGER NOT NULL
    ) STRICT;
    CREATE INDEX llamadas_por_mes ON llamadas_ia (mes);

    CREATE TABLE alertas (
        id INTEGER PRIMARY KEY,
        producto_id TEXT NOT NULL REFERENCES productos (id) ON DELETE CASCADE,
        umbral TEXT NOT NULL,
        canal TEXT NOT NULL,
        activa INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
        ultima_activacion TEXT
    ) STRICT;

    CREATE TABLE ajustes (
        clave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    ) STRICT;
    """,
    # 2: la ficha de un producto en la web enseña la última oferta de cada tienda con sus campos
    # (origen y anclaje incluidos), no solo el precio. Las filas antiguas quedan con valores
    # vacíos y se leen igual.
    """
    ALTER TABLE precios ADD COLUMN titulo TEXT NOT NULL DEFAULT '';
    ALTER TABLE precios ADD COLUMN campos TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE precios ADD COLUMN disponible INTEGER NOT NULL DEFAULT 1
        CHECK (disponible IN (0, 1));
    ALTER TABLE productos ADD COLUMN criterio TEXT NOT NULL DEFAULT '';
    """,
    # 3: tipo de perfil (nota 15 §D). Los perfiles anteriores leían HTML con selectores; los
    # `api_*` leen la API pública de la plataforma de la tienda y pueden no tener selectores.
    """
    ALTER TABLE perfiles ADD COLUMN tipo TEXT NOT NULL DEFAULT 'html'
        CHECK (tipo IN ('html', 'api_woocommerce', 'api_shopify'));
    """,
    # 4: webs sin buscador (nota 16, ADR-018). La URL de entrada que da el usuario, los tipos
    # `listado` y `sitemap` y el catálogo local de URL del sitemap. SQLite no deja cambiar un
    # CHECK con ALTER: la tabla `perfiles` se reconstruye con el mismo esquema (columnas en el
    # mismo orden, clave primaria, clave foránea y STRICT) y se copian todas sus filas. Va dentro
    # de la transacción de `_migrar`, donde `PRAGMA foreign_keys` no se puede cambiar (SQLite lo
    # ignora): no hace falta, porque ninguna tabla apunta a `perfiles` (borrarla no dispara ningún
    # CASCADE) y sus filas ya cumplían la clave foránea.
    """
    ALTER TABLE tiendas ADD COLUMN entrada TEXT;

    CREATE TABLE perfiles_v4 (
        dominio TEXT NOT NULL REFERENCES tiendas (dominio) ON DELETE CASCADE,
        version INTEGER NOT NULL CHECK (version >= 1),
        url_busqueda TEXT NOT NULL,
        sel_resultado TEXT NOT NULL,
        sel_titulo TEXT NOT NULL,
        sel_enlace TEXT NOT NULL,
        sel_precio_listado TEXT,
        sel_campos TEXT NOT NULL,
        necesita_js INTEGER NOT NULL CHECK (necesita_js IN (0, 1)),
        autor TEXT NOT NULL,
        revisado INTEGER NOT NULL DEFAULT 0 CHECK (revisado IN (0, 1)),
        creado TEXT NOT NULL,
        nota TEXT NOT NULL DEFAULT '',
        tipo TEXT NOT NULL DEFAULT 'html'
            CHECK (tipo IN ('html', 'api_woocommerce', 'api_shopify', 'listado', 'sitemap')),
        PRIMARY KEY (dominio, version)
    ) STRICT;
    INSERT INTO perfiles_v4 (dominio, version, url_busqueda, sel_resultado, sel_titulo,
        sel_enlace, sel_precio_listado, sel_campos, necesita_js, autor, revisado, creado, nota,
        tipo)
    SELECT dominio, version, url_busqueda, sel_resultado, sel_titulo, sel_enlace,
        sel_precio_listado, sel_campos, necesita_js, autor, revisado, creado, nota, tipo
    FROM perfiles;
    DROP TABLE perfiles;
    ALTER TABLE perfiles_v4 RENAME TO perfiles;

    CREATE TABLE catalogo (
        dominio TEXT NOT NULL REFERENCES tiendas (dominio) ON DELETE CASCADE,
        url TEXT NOT NULL,
        palabras TEXT NOT NULL,
        lastmod TEXT,
        PRIMARY KEY (dominio, url)
    ) STRICT, WITHOUT ROWID;

    CREATE TABLE catalogos (
        dominio TEXT PRIMARY KEY REFERENCES tiendas (dominio) ON DELETE CASCADE,
        sitemap TEXT NOT NULL,
        actualizado TEXT NOT NULL,
        total INTEGER NOT NULL CHECK (total >= 0),
        parcial INTEGER NOT NULL CHECK (parcial IN (0, 1))
    ) STRICT;
    """,
    # 5: emparejado por OEM ampliado (B5): cómo coincide cada oferta con la referencia buscada y si
    # una alerta cuenta solo las exactas (decisión de Julio). Las filas antiguas quedan como
    # exactas: antes de B5 solo entraban exactas o grupos por texto, que no relajan nada.
    """
    ALTER TABLE precios ADD COLUMN coincidencia TEXT NOT NULL DEFAULT 'exacta'
        CHECK (coincidencia IN ('exacta', 'revision', 'equivalente'));
    ALTER TABLE alertas ADD COLUMN solo_exactas INTEGER NOT NULL DEFAULT 1
        CHECK (solo_exactas IN (0, 1));
    """,
    # 6: caché del extractor de respaldo con IA (A3). Solo la huella del texto, los campos
    # pedidos, el modelo y los valores ya limpios: nunca el texto de la página ni el prompt.
    """
    CREATE TABLE extracciones_ia (
        texto_sha256 TEXT NOT NULL CHECK (length(texto_sha256) = 64),
        campos TEXT NOT NULL,
        modelo TEXT NOT NULL,
        valores TEXT NOT NULL,
        creado TEXT NOT NULL,
        PRIMARY KEY (texto_sha256, campos, modelo)
    ) STRICT, WITHOUT ROWID;
    CREATE INDEX extracciones_ia_por_fecha ON extracciones_ia (creado);
    """,
    # 7: configuraciones fallidas de las tiendas sin perfil (A1, decisión de Julio S03): la
    # última, cuántas van seguidas y desde cuándo se puede reintentar (NULL: el usuario pulsó
    # «Reintentar»). La espera la decide el orquestador; aquí solo se guarda.
    """
    CREATE TABLE configuraciones_fallidas (
        dominio TEXT PRIMARY KEY REFERENCES tiendas (dominio) ON DELETE CASCADE,
        intentos INTEGER NOT NULL CHECK (intentos >= 1),
        fecha TEXT NOT NULL,
        motivo TEXT NOT NULL,
        reintentar_desde TEXT
    ) STRICT;
    """,
    # 8: la búsqueda que vio cada oferta por última vez (A1, con las ofertas que A3 sirve desde
    # la caché con su fecha de descarga). La ficha del producto enseña las ofertas de la última
    # visita a cada tienda: sin esto, una oferta de la caché (fecha antigua) desaparecía en
    # cuanto otra de la misma visita era nueva. Las filas antiguas: visita = fecha, como antes.
    """
    ALTER TABLE precios ADD COLUMN visita TEXT NOT NULL DEFAULT '';
    UPDATE precios SET visita = fecha;
    """,
    # 9: los sitemaps hijos de un índice que no cupieron en el tope de ficheros al hacer el
    # catálogo (R2-C14b), en el orden en que se leerían. Una búsqueda descarga los que nombran la
    # consulta y los quita de aquí. Solo URL de sitemaps de la tienda: ningún dato de producto.
    """
    CREATE TABLE catalogo_pendientes (
        dominio TEXT NOT NULL REFERENCES tiendas (dominio) ON DELETE CASCADE,
        url TEXT NOT NULL,
        orden INTEGER NOT NULL,
        PRIMARY KEY (dominio, url)
    ) STRICT, WITHOUT ROWID;
    """,
    # 10: el prefijo de idioma o país de la portada con que se filtró el catálogo (causa R5-C),
    # con sus tramos separados por «/» («es/es»; '' si no tiene). Al rehacer el catálogo cada 7
    # días se vuelve a aplicar: antes solo se conocía el sitemap y entraban todos los países. Los
    # catálogos ya guardados quedan sin prefijo, como se leían.
    """
    ALTER TABLE catalogos ADD COLUMN idioma TEXT NOT NULL DEFAULT '';
    """,
    # 11: la URL de la foto principal de la oferta (solo el enlace: la imagen nunca se descarga
    # ni se guarda). '' = sin foto, como quedan las filas antiguas.
    """
    ALTER TABLE precios ADD COLUMN foto TEXT NOT NULL DEFAULT '';
    """,
    # 12: caché de la revisión con IA de los resultados (nota 05): un veredicto por oferta,
    # con clave sha256 de modelo, consulta, filtro y lo enviado (sin el precio). TTL de 7 días.
    """
    CREATE TABLE revisiones_ia (
        clave TEXT PRIMARY KEY,
        clase TEXT NOT NULL,
        motivo TEXT NOT NULL,
        cumple_filtro INTEGER NOT NULL CHECK (cumple_filtro IN (0, 1)),
        creado TEXT NOT NULL
    ) STRICT;
    CREATE INDEX revisiones_ia_por_fecha ON revisiones_ia (creado);
    """,
    # 13: estado de cada alerta (H7): si ya se avisó de la bajada actual (no se repite hasta
    # que el precio vuelva a subir), por qué falló la última vez y cuándo se comprobó. Las
    # alertas guardadas quedan sin avisar: antes no se enviaba nada.
    """
    ALTER TABLE alertas ADD COLUMN avisada INTEGER NOT NULL DEFAULT 0
        CHECK (avisada IN (0, 1));
    ALTER TABLE alertas ADD COLUMN ultimo_error TEXT NOT NULL DEFAULT '';
    ALTER TABLE alertas ADD COLUMN ultima_comprobacion TEXT;
    """,
)

# Solo para diagnóstico y tests: los nombres de PRAGMA no se pueden parametrizar.
PRAGMAS_CONSULTABLES = frozenset({"journal_mode", "foreign_keys", "user_version"})

MAX_DOMINIO = 253
MAX_NOMBRE_TIENDA = 200
MAX_ROBOTS = 2000
MAX_URL = 2048
MAX_SELECTOR = 1000
MAX_AUTOR = 100
MAX_NOTA = 2000
MAX_PRODUCTO_ID = 200
MAX_NOMBRE_PRODUCTO = 500
MAX_REFERENCIA = 100
MAX_CANAL = 50
MAX_ERROR_ALERTA = 500
"""Caracteres del último error de una alerta: lo que pase se recorta."""
MAX_ETIQUETA_IA = 100
MAX_RESULTADO_IA = 500
MAX_AJUSTE_TEXTO = 500
MAX_LIMITE_LLAMADAS = 10_000
MAX_VALOR_CAMPO = 5_000
TTL_REVISIONES_IA_S = 7 * 24 * 3600
"""Cuánto vale un veredicto de la revisión con IA (el producto puede cambiar con el título)."""
CLASES_REVISION = frozenset({"misma_pieza", "accesorio", "variante", "kit", "otra"})
"""Las de `emparejado.semantico.ClaseOferta` (el almacén no depende de `emparejado`)."""
MAX_MOTIVO_REVISION = 140
_CLAVE_REVISION = re.compile(r"[0-9a-f]{64}")
"""Lo que admite `extraccion.ficha` en un valor (`MAX_VALOR`)."""
MAX_CRITERIO = 200
MAX_FOTO = 2000
"""Lo que admite `extraccion.imagenes` en la URL de una foto (`MAX_URL_FOTO`)."""
_NO_EN_FOTO = frozenset("\"'<>`\\")
MAX_MOTIVO_FALLO = 1000
"""Caracteres del motivo de una configuración fallida (A1)."""
_ORIGENES = frozenset(o.value for o in OrigenDato)
MAX_TEXTO_BUSQUEDA = 500
# Caracteres de `str(Decimal)`: sobra para cualquier precio o coste real (28 cifras de precisión).
MAX_IMPORTE = 50
MAX_ENTERO = 2**63 - 1
"""El mayor INTEGER de SQLite: más allá, `sqlite3` lanza `OverflowError`."""
MAX_ENTRADA = 500
"""Lo que admite `perfiles.entrada.normalizar_entrada` en la URL de entrada (nota 16 §A)."""
MAX_CATALOGO = 100_000
"""URL del catálogo de una tienda (nota 16 §B): si hay más, se guardan estas y queda `parcial`."""
MAX_PENDIENTES_CATALOGO = 5_000
"""Sitemaps hijos sin leer que se recuerdan por tienda (R2-C14b); el resto se olvida."""
MAX_TRAMOS_IDIOMA = 2
"""Tramos que puede tener el prefijo de idioma del catálogo (`/es/es/`, R5-C)."""
_TRAMO_IDIOMA = re.compile(r"[a-z]{2}(?:[-_][a-z]{2})?")
"""Un tramo de prefijo de idioma o país (`es`, `es-es`, `en_gb`), como `perfiles.catalogo`."""
MAX_PALABRAS_URL = 300
"""Lo que da como mucho `fuentes.sitemap.palabras_url`."""
MAX_LASTMOD = 64
MAX_LIMITE_CATALOGO = 100
MIN_PALABRA_CATALOGO = 3
"""Las palabras más cortas de la consulta no cuentan (como en `orquestador._de_la_consulta`)."""
MAX_PALABRAS_CONSULTA = 12
MAX_REFERENCIAS_CONSULTA = 4
# Palabras de 3 letras o más que casi nunca salen en un slug: si contaran, «disco de freno para
# el Golf» exigiría «para» en la URL.
_PALABRAS_VACIAS_CATALOGO = frozenset({"con", "del", "las", "los", "para", "por", "una", "uno"})
MIN_PALABRA_RAIZ = 6
"""Una palabra de la consulta que no casa con ninguna URL se prueba por su raíz si tiene al
menos estas letras (R5-J)."""
MIN_RAIZ = 5
# Terminaciones flexivas comunes (español e inglés), de la más larga a la más corta: así
# «polarizadas» da «polariz» (y no «polarizad») y casa con «polarized» (que también da «polariz»).
_TERMINACIONES_CATALOGO = ("adas", "ados", "idas", "idos", "ing", "as", "os", "es", "ed", "s")
_PUERTOS = {"http": 80, "https": 443}
_ALFANUMERICO = re.compile(r"[0-9a-z]+")
"""Las palabras de un texto en minúsculas, como las deja `normalizar_titulo` (si es ASCII)."""


class ErrorAlmacen(Exception):
    """Base de los errores del almacén."""


class DatoNoValido(ErrorAlmacen, ValueError):
    """Un valor no cumple los límites de tipo o tamaño antes de guardarlo."""


class TiendaDuplicada(ErrorAlmacen):
    """Ya hay una tienda con ese dominio."""


class TiendaNoEncontrada(ErrorAlmacen, LookupError):
    """No hay ninguna tienda con ese dominio."""


class PerfilNoEncontrado(ErrorAlmacen, LookupError):
    """La tienda no tiene esa versión de perfil."""


class AlertaNoEncontrada(ErrorAlmacen, LookupError):
    """No hay ninguna alerta con ese identificador."""


class ProductoNoEncontrado(ErrorAlmacen, LookupError):
    """No se ha guardado nunca un precio de ese producto."""


class EntradaCatalogo(NamedTuple):
    """Una URL del catálogo. `guardar_catalogo` admite también tuplas `(url, palabras, lastmod)`."""

    url: str
    palabras: str
    """Palabras del slug y de la ruta (`fuentes.sitemap.palabras_url`)."""
    lastmod: str | None = None


class BusquedaGuardada(NamedTuple):
    texto: str
    modo: Modo
    fecha: datetime
    llamadas_ia: int
    coste_ia_usd: Decimal
    duracion_s: float


def _texto(valor: object, campo: str, maximo: int, *, vacio: bool = False) -> str:
    if not isinstance(valor, str) or len(valor) > maximo or (not vacio and not valor):
        raise DatoNoValido(f"{campo}: se espera un texto no vacío de hasta {maximo} caracteres")
    try:
        # `json.loads` convierte "\ud800" del JSON-LD de una web en un sustituto suelto, que
        # SQLite no puede guardar (UnicodeEncodeError al enlazar el parámetro).
        valor.encode("utf-8")
    except UnicodeEncodeError as e:
        raise DatoNoValido(f"{campo}: el texto no es Unicode válido") from e
    return valor


def _importe(valor: object, campo: str, *, positivo: bool = False) -> str:
    if not isinstance(valor, Decimal) or not valor.is_finite() or valor < 0:
        raise DatoNoValido(f"{campo}: se espera un importe Decimal finito y no negativo")
    if positivo and valor == 0:
        raise DatoNoValido(f"{campo}: debe ser mayor que cero")
    texto = str(valor)
    if len(texto) > MAX_IMPORTE:
        raise DatoNoValido(f"{campo}: importe de más de {MAX_IMPORTE} caracteres")
    return texto


def _entero(valor: object, campo: str) -> int:
    if not isinstance(valor, int) or isinstance(valor, bool) or not 0 <= valor <= MAX_ENTERO:
        raise DatoNoValido(f"{campo}: se espera un entero entre 0 y {MAX_ENTERO}")
    return valor


def _fecha(valor: object, campo: str) -> str:
    if not isinstance(valor, datetime):
        raise DatoNoValido(f"{campo}: se espera un datetime")
    return valor.isoformat()


def _url_de_sitemap_de_la_tienda(valor: object, campo: str, dominio: str, maximo: int) -> str:
    """`valor` es la URL de un fichero sitemap de la tienda: como `_url_de_la_tienda`, pero el
    host puede ser también un subdominio estricto del dominio (`static.xtralife.com`).

    La misma regla con la que se descargan (`fuentes.sitemap.url_de_sitemap`, causa R5-B): el
    sitemap del catálogo y los hijos pendientes o leídos de un índice. Las fichas, no.
    """
    return _url_de_la_tienda(valor, campo, dominio, maximo, subdominio=True)


def _foto(valor: object) -> str:
    """La URL de la foto para guardarla ('' sin foto), o `DatoNoValido` si no es segura."""
    if valor is None:
        return ""
    if not foto_guardable(valor):
        raise DatoNoValido(
            f"foto: se espera una URL https de hasta {MAX_FOTO} caracteres, sin espacios ni "
            "comillas"
        )
    return str(valor)


def foto_guardable(valor: object) -> bool:
    """Si `valor` es una URL `https` con host, sin espacios, comillas ni `<>`, y no muy larga.

    La regla se repite a propósito aquí (sin importar `extraccion`): el almacén no se fía de quien
    lo llama, y al leer se vuelve a aplicar por si la base de datos se ha tocado a mano.
    """
    if not isinstance(valor, str) or len(valor) > MAX_FOTO or not valor.startswith("https://"):
        return False
    if any(c.isspace() or not c.isprintable() or c in _NO_EN_FOTO for c in valor):
        return False
    try:
        return bool(urlsplit(valor).hostname)
    except ValueError:
        return False


def _url_de_la_tienda(
    valor: object, campo: str, dominio: str, maximo: int, *, subdominio: bool = False
) -> str:
    """`valor` es una URL http(s) absoluta del host de la tienda (con o sin `www.`).

    El almacén no guarda nada que apunte a otro servidor: las URL del catálogo vienen de un
    sitemap (dato de la web) y se descargan después. No sustituye a `validar_url` (anti-SSRF),
    que se aplica siempre al descargar. Con `subdominio`, vale también un subdominio estricto
    del dominio, sin etiquetas vacías (solo para ficheros sitemap).
    """
    texto = _texto(valor, campo, maximo)
    error = DatoNoValido(f"{campo}: se espera una URL http(s) de {dominio}")
    # `urlsplit` quita en silencio tabuladores y saltos de línea: se rechazan antes, igual que la
    # barra invertida, que los navegadores leen como `/`.
    if any(c.isspace() or not c.isprintable() or c == "\\" for c in texto):
        raise error
    try:
        # Unos corchetes de IPv6 sin cerrar hacen que ya `urlsplit` lance `ValueError`.
        partes = urlsplit(texto)
        puerto = partes.port
    except ValueError as e:
        raise error from e
    if (
        partes.scheme not in _PUERTOS
        or "@" in partes.netloc
        or puerto not in (None, _PUERTOS[partes.scheme])
        or not _host_de_la_tienda(partes.hostname, dominio, subdominio=subdominio)
    ):
        raise error
    return texto


def _host_de_la_tienda(host: str | None, dominio: str, *, subdominio: bool) -> bool:
    if host in (dominio, f"www.{dominio}"):
        return True
    return subdominio and host is not None and host.endswith("." + dominio) and all(host.split("."))


def _patron_like(texto: str) -> str:
    """Patrón de `LIKE ... ESCAPE '\\'` que busca `texto` literal en cualquier sitio."""
    escapado = texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escapado}%"


def _terminos_catalogo(consulta: str) -> tuple[list[str], list[str]]:
    """Palabras (normalizadas, de 3 letras o más) y referencias OEM de la consulta.

    La normalización es `emparejado.texto.normalizar_titulo`, la misma que `guardar_catalogo`
    aplica a las palabras de cada URL: minúsculas, sin acentos y separando por cualquier signo.
    Las referencias van en minúsculas y sin separadores (`normalizar_referencia`).
    """
    texto = consulta[:MAX_TEXTO_BUSQUEDA]
    palabras: list[str] = []
    for palabra in normalizar_titulo(texto).split():
        if (
            len(palabra) >= MIN_PALABRA_CATALOGO
            and palabra not in _PALABRAS_VACIAS_CATALOGO
            and palabra not in palabras
        ):
            palabras.append(palabra)
            if len(palabras) == MAX_PALABRAS_CONSULTA:
                break
    referencias = sorted(r.lower() for r in referencias_en(texto))[:MAX_REFERENCIAS_CONSULTA]
    return palabras, referencias


def _raiz_catalogo(palabra: str) -> str | None:
    """La raíz de `palabra` sin su terminación flexiva (la más larga que deje al menos
    `MIN_RAIZ` letras), o `None` si es más corta que `MIN_PALABRA_RAIZ` o no tiene ninguna."""
    if len(palabra) < MIN_PALABRA_RAIZ:
        return None
    for terminacion in _TERMINACIONES_CATALOGO:
        if palabra.endswith(terminacion) and len(palabra) - len(terminacion) >= MIN_RAIZ:
            return palabra[: -len(terminacion)]
    return None


def _posicion_en_el_nombre(url: str, palabras: tuple[str, ...]) -> int:
    """Lugar de la primera palabra del nombre del producto que empieza por una de `palabras`.

    El nombre es el tramo de la ruta con más palabras con letras (a igualdad, el último): en
    `/recambio-automovil-segunda-mano/faro-derecho/seat/ibiza/90652_faro-derecho-seat….html` es
    el último, y en `/audiolibro-harry-potter-y-el-caliz…/9781781105054/17916831`, el primero.
    Se cuentan solo las palabras con letras («2027» o el id de delante no cuentan). Si el nombre
    no tiene ninguna, `MAX_PALABRAS_URL` (después de todas las que la tienen).
    """
    # La URL ya es http(s) y de la tienda (`guardar_catalogo`): se corta a mano, sin `urlsplit`,
    # que en una búsqueda que casa con todo el catálogo se nota.
    ruta = url.partition("://")[2].partition("#")[0].partition("?")[0].partition("/")[2]
    if "%" in ruta:
        ruta = unquote(ruta)
    if ruta.isascii():  # Lo mismo que `normalizar_titulo` en cada tramo, pero de una vez.
        tramos = [_ALFANUMERICO.findall(tramo) for tramo in ruta.lower().split("/")]
    else:
        tramos = [normalizar_titulo(tramo).split() for tramo in ruta.split("/")]
    nombre: list[str] = []
    for tramo in tramos:
        suyas = [p for p in tramo if not p.isdigit()]
        if suyas and len(suyas) >= len(nombre):
            nombre = suyas
    for lugar, suya in enumerate(nombre):
        if suya.startswith(palabras):
            return lugar
    return MAX_PALABRAS_URL


class _UrlsGuardadas:
    """Las URL del catálogo de una tienda que ya están en la base más las que se van añadiendo,
    para no repetirlas al ampliar. `in` pregunta a la base: el catálogo puede tener 100 000."""

    def __init__(self, con: sqlite3.Connection, dominio: str) -> None:
        self._con = con
        self._dominio = dominio
        self._nuevas: set[str] = set()

    def __contains__(self, url: str) -> bool:
        if url in self._nuevas:
            return True
        consulta = "SELECT 1 FROM catalogo WHERE dominio = ? AND url = ?"
        return self._con.execute(consulta, (self._dominio, url)).fetchone() is not None

    def add(self, url: str) -> None:
        self._nuevas.add(url)


def _filas_catalogo(
    dominio: str,
    entradas: Iterable[tuple[str, str, str | None]],
    maximo: int,
    vistas: set[str] | _UrlsGuardadas,
) -> tuple[list[tuple[str, str, str, str | None]], bool]:
    """Las filas de `entradas` que no están en `vistas`, como mucho `maximo`, y si sobraba
    alguna (el resto no se lee). Una entrada no válida es `DatoNoValido`."""
    filas: list[tuple[str, str, str, str | None]] = []
    for entrada in entradas:
        if len(filas) >= maximo:
            return filas, True
        if not isinstance(entrada, tuple) or len(entrada) != 3:
            raise DatoNoValido("entradas: se espera (url, palabras, lastmod)")
        url, palabras, lastmod = entrada
        _url_de_la_tienda(url, "url", dominio, MAX_URL)
        normalizadas = _palabras_catalogo(palabras)
        if lastmod is not None:
            _texto(lastmod, "lastmod", MAX_LASTMOD)
        if url in vistas:
            continue
        vistas.add(url)
        filas.append((dominio, url, normalizadas, lastmod))
    return filas, False


def _idioma_catalogo(idioma: object) -> str:
    """El prefijo de idioma del catálogo como se guarda (`("es", "es")` → `"es/es"`, R5-C). Uno
    que no son como mucho `MAX_TRAMOS_IDIOMA` tramos con forma de idioma o país es
    `DatoNoValido`."""
    if isinstance(idioma, str) or not isinstance(idioma, Sequence):
        raise DatoNoValido("idioma: tiene que ser una secuencia de tramos")
    tramos = list(idioma)
    if len(tramos) > MAX_TRAMOS_IDIOMA or not all(
        isinstance(t, str) and _TRAMO_IDIOMA.fullmatch(t) for t in tramos
    ):
        raise DatoNoValido(f"idioma: {tramos!r} no es un prefijo de idioma o país")
    return "/".join(tramos)


def _idioma_guardado(texto: str) -> tuple[str, ...]:
    return tuple(texto.split("/")) if texto else ()


def _hijos_pendientes(
    dominio: str, hijos: Iterable[str], vistos: set[str], maximo: int
) -> list[str]:
    """Los sitemaps hijos sin leer que no están en `vistos`, sin repetir y como mucho `maximo`
    (los que no caben se olvidan). Uno que no es de la tienda es `DatoNoValido`."""
    nuevos: list[str] = []
    for hijo in hijos:
        if len(nuevos) >= maximo:
            break
        _url_de_sitemap_de_la_tienda(hijo, "pendiente", dominio, MAX_URL)
        if hijo not in vistos:
            vistos.add(hijo)
            nuevos.append(hijo)
    return nuevos


def _palabras_catalogo(palabras: object) -> str:
    texto = _texto(palabras, "palabras", MAX_PALABRAS_URL, vacio=True)
    # Se normalizan aquí también, con la misma función que la consulta: así las dos comparan
    # igual aunque cambie cómo las saca `fuentes.sitemap.palabras_url`.
    return normalizar_titulo(texto)[:MAX_PALABRAS_URL]


def _mes(instante: datetime) -> str:
    # El mes se toma de la hora de pared de cada fecha: las llamadas y el reloj deben usar la
    # misma convención (ambas locales o ambas con la misma zona).
    return f"{instante.year:04d}-{instante.month:02d}"


def _leer_fecha(texto: str | None) -> datetime | None:
    return None if texto is None else datetime.fromisoformat(texto)


class Almacen:
    """Persistencia de PrecIA en un fichero SQLite (o `":memory:"` para los tests).

    Implementa `ia.tipos.RegistroLlamadas`. Es seguro usarlo desde varios hilos del mismo
    proceso (p. ej. `asyncio.to_thread`): una sola conexión protegida por un cerrojo.
    """

    def __init__(
        self,
        ruta: Path | str,
        *,
        reloj: Callable[[], datetime] = datetime.now,
        migraciones: Sequence[str] = MIGRACIONES,
    ) -> None:
        self._reloj = reloj
        self._cerrojo = threading.RLock()
        # Sin transacciones implícitas del módulo: se abren a mano en `_transaccion`. Con
        # `autocommit=True`, `executescript` tampoco hace COMMIT por su cuenta (ver `_migrar`).
        self._con = sqlite3.connect(str(ruta), autocommit=True, check_same_thread=False)
        try:
            self._con.execute("PRAGMA foreign_keys = ON")
            self._con.execute("PRAGMA busy_timeout = 5000")
            # En `:memory:` el modo queda en «memory»; WAL solo aplica a ficheros.
            self._con.execute("PRAGMA journal_mode = WAL")
            self._migrar(migraciones)
            # Un almacén abierto con menos migraciones (los tests de migración) no tiene la
            # tabla de A1: sus tiendas se leen sin fallo.
            con_fallos = self._con.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table'"
                " AND name = 'configuraciones_fallidas'"
            ).fetchone()
            self._select_tiendas = _SELECT_TIENDAS if con_fallos else _SELECT_TIENDAS_SIN_FALLOS
        except BaseException:
            self._con.close()
            raise

    def _migrar(self, migraciones: Sequence[str]) -> None:
        # Una transacción por migración, y la versión se lee ya con el cerrojo de escritura: si
        # otro proceso está migrando el mismo fichero, se espera y se parte de lo que dejó hecho.
        while True:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                actual = int(self._con.execute("PRAGMA user_version").fetchone()[0])
                if actual > len(migraciones):
                    raise ErrorAlmacen(
                        f"La base de datos tiene la versión {actual} del esquema y esta versión"
                        f" de PrecIA solo conoce hasta la {len(migraciones)}."
                    )
                if actual < len(migraciones):
                    # `actual + 1` es un entero nuestro: PRAGMA no admite parámetros.
                    self._con.executescript(
                        f"{migraciones[actual]}\nPRAGMA user_version = {actual + 1:d};"
                    )
            except BaseException:
                if self._con.in_transaction:
                    self._con.execute("ROLLBACK")
                raise
            self._con.execute("COMMIT")
            if actual >= len(migraciones):
                return

    def cerrar(self) -> None:
        with self._cerrojo:
            self._con.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        error: BaseException | None,
        traza: TracebackType | None,
    ) -> None:
        self.cerrar()

    def pragma(self, nombre: str) -> Any:
        """Valor de un PRAGMA de diagnóstico (solo los de `PRAGMAS_CONSULTABLES`)."""
        if nombre not in PRAGMAS_CONSULTABLES:
            raise ErrorAlmacen(f"PRAGMA no consultable: {nombre!r}")
        with self._cerrojo:
            return self._con.execute(f"PRAGMA {nombre}").fetchone()[0]

    @contextmanager
    def _transaccion(self) -> Iterator[sqlite3.Connection]:
        with self._cerrojo:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                yield self._con
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            self._con.execute("COMMIT")

    def _consultar(self, sql: str, parametros: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._cerrojo:
            cursor = self._con.execute(sql, parametros)
            cursor.row_factory = sqlite3.Row
            return cursor.fetchall()

    # --- Tiendas ---

    def alta_tienda(self, dominio: str, nombre: str, *, entrada: str | None = None) -> Tienda:
        """Da de alta la tienda con la URL de entrada del usuario, si la hay (nota 16 §A).

        `entrada` llega ya normalizada por `perfiles.entrada.normalizar_entrada`; el almacén
        solo comprueba que sea una URL http(s) del host de la tienda.
        """
        _texto(dominio, "dominio", MAX_DOMINIO)
        _texto(nombre, "nombre", MAX_NOMBRE_TIENDA)
        if entrada is not None:
            _url_de_la_tienda(entrada, "entrada", dominio, MAX_ENTRADA)
        with self._transaccion() as con:
            if con.execute("SELECT 1 FROM tiendas WHERE dominio = ?", (dominio,)).fetchone():
                raise TiendaDuplicada(dominio)
            con.execute(
                "INSERT INTO tiendas (dominio, nombre, estado, entrada) VALUES (?, ?, ?, ?)",
                (dominio, nombre, EstadoPerfil.SIN_PERFIL.value, entrada),
            )
        return self._tienda_o_error(dominio)

    def cambiar_entrada(self, dominio: str, entrada: str | None) -> Tienda:
        """Pone (o quita, con `None`) la URL de entrada de la tienda (nota 16 §A)."""
        if entrada is not None:
            _url_de_la_tienda(entrada, "entrada", dominio, MAX_ENTRADA)
        with self._transaccion() as con:
            cursor = con.execute(
                "UPDATE tiendas SET entrada = ? WHERE dominio = ?", (entrada, dominio)
            )
            if cursor.rowcount == 0:
                raise TiendaNoEncontrada(dominio)
        return self._tienda_o_error(dominio)

    def tiendas(self) -> list[Tienda]:
        with self._cerrojo:
            filas = self._consultar(f"{self._select_tiendas} ORDER BY t.dominio")
            versiones: dict[str, list[VersionPerfil]] = {}
            for perfil in self._perfiles("SELECT * FROM perfiles ORDER BY dominio, version"):
                versiones.setdefault(perfil.dominio, []).append(_version_perfil(perfil))
        return [_tienda(f, versiones.get(f["dominio"], [])) for f in filas]

    def tienda(self, dominio: str) -> Tienda | None:
        with self._cerrojo:
            filas = self._consultar(f"{self._select_tiendas} WHERE t.dominio = ?", (dominio,))
            if not filas:
                return None
            return _tienda(filas[0], [_version_perfil(p) for p in self.versiones(dominio)])

    def _tienda_o_error(self, dominio: str) -> Tienda:
        tienda = self.tienda(dominio)
        if tienda is None:
            raise TiendaNoEncontrada(dominio)
        return tienda

    def actualizar_tienda(
        self,
        dominio: str,
        *,
        estado: EstadoPerfil | None = None,
        robots: str | None = None,
        robots_permite: bool | None = None,
        ultima_visita: datetime | None = None,
        bloqueos: int | None = None,
    ) -> Tienda:
        """Cambia solo los campos indicados (los `None` se dejan como están)."""
        # Los nombres de columna salen de esta lista fija; los valores van siempre como parámetros.
        cambios: list[tuple[str, object]] = []
        if estado is not None:
            cambios.append(("estado", EstadoPerfil(estado).value))
        if robots is not None:
            cambios.append(("robots", _texto(robots, "robots", MAX_ROBOTS, vacio=True)))
        if robots_permite is not None:
            cambios.append(("robots_permite", int(bool(robots_permite))))
        if ultima_visita is not None:
            cambios.append(("ultima_visita", _fecha(ultima_visita, "ultima_visita")))
        if bloqueos is not None:
            cambios.append(("bloqueos", _entero(bloqueos, "bloqueos")))
        with self._transaccion() as con:
            if not con.execute("SELECT 1 FROM tiendas WHERE dominio = ?", (dominio,)).fetchone():
                raise TiendaNoEncontrada(dominio)
            if cambios:
                asignaciones = ", ".join(f"{columna} = ?" for columna, _ in cambios)
                con.execute(
                    f"UPDATE tiendas SET {asignaciones} WHERE dominio = ?",
                    (*(valor for _, valor in cambios), dominio),
                )
        return self._tienda_o_error(dominio)

    def registrar_fallo_configuracion(
        self,
        dominio: str,
        motivo: str,
        fecha: datetime,
        *,
        intentos: int,
        reintentar_desde: datetime | None,
    ) -> Tienda:
        """Guarda la última configuración fallida de la tienda (A1); sustituye a la anterior.

        `intentos` son los fallos seguidos, contando este. Hasta `reintentar_desde` el
        orquestador no vuelve a configurar la tienda.
        """
        _texto(motivo, "motivo", MAX_MOTIVO_FALLO)
        texto_fecha = _fecha(fecha, "fecha")
        if _entero(intentos, "intentos") < 1:
            raise DatoNoValido("intentos: al menos 1")
        desde = None if reintentar_desde is None else _fecha(reintentar_desde, "reintentar_desde")
        with self._transaccion() as con:
            if not con.execute("SELECT 1 FROM tiendas WHERE dominio = ?", (dominio,)).fetchone():
                raise TiendaNoEncontrada(dominio)
            con.execute(
                "INSERT INTO configuraciones_fallidas (dominio, intentos, fecha, motivo,"
                " reintentar_desde) VALUES (?, ?, ?, ?, ?) ON CONFLICT (dominio) DO UPDATE SET"
                " intentos = excluded.intentos, fecha = excluded.fecha,"
                " motivo = excluded.motivo, reintentar_desde = excluded.reintentar_desde",
                (dominio, intentos, texto_fecha, motivo, desde),
            )
        return self._tienda_o_error(dominio)

    def borrar_fallo_configuracion(self, dominio: str) -> None:
        """Olvida la configuración fallida de la tienda (ya tiene perfil). Sin fallo, nada."""
        with self._transaccion() as con:
            con.execute("DELETE FROM configuraciones_fallidas WHERE dominio = ?", (dominio,))

    def levantar_espera(self, dominio: str) -> Tienda:
        """Quita la espera de una configuración fallida (botón «Reintentar», A1).

        Conserva los intentos y el motivo: si vuelve a fallar, la espera sigue creciendo.
        """
        with self._transaccion() as con:
            if not con.execute("SELECT 1 FROM tiendas WHERE dominio = ?", (dominio,)).fetchone():
                raise TiendaNoEncontrada(dominio)
            con.execute(
                "UPDATE configuraciones_fallidas SET reintentar_desde = NULL WHERE dominio = ?",
                (dominio,),
            )
        return self._tienda_o_error(dominio)

    # --- Perfiles ---

    def guardar_perfil(self, perfil: Perfil) -> Perfil:
        """Guarda el perfil como una versión nueva y lo devuelve con el número asignado.

        El número es siempre el siguiente al último guardado: una versión anterior nunca se
        sobrescribe, aunque el llamante repita su número.
        """
        _validar_perfil(perfil)
        with self._transaccion() as con:
            existe = con.execute(
                "SELECT 1 FROM tiendas WHERE dominio = ?", (perfil.dominio,)
            ).fetchone()
            if not existe:
                raise TiendaNoEncontrada(perfil.dominio)
            (ultima,) = con.execute(
                "SELECT COALESCE(MAX(version), 0) FROM perfiles WHERE dominio = ?",
                (perfil.dominio,),
            ).fetchone()
            guardado = replace(perfil, version=int(ultima) + 1)
            con.execute(
                "INSERT INTO perfiles (dominio, version, url_busqueda, sel_resultado, sel_titulo,"
                " sel_enlace, sel_precio_listado, sel_campos, necesita_js, autor, revisado,"
                " creado, nota, tipo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    guardado.dominio,
                    guardado.version,
                    guardado.url_busqueda,
                    guardado.sel_resultado,
                    guardado.sel_titulo,
                    guardado.sel_enlace,
                    guardado.sel_precio_listado,
                    json.dumps(guardado.sel_campos, ensure_ascii=False),
                    int(guardado.necesita_js),
                    guardado.autor,
                    int(guardado.revisado),
                    guardado.creado.isoformat(),
                    guardado.nota,
                    guardado.tipo,
                ),
            )
        return guardado

    def perfil_vigente(self, dominio: str) -> Perfil | None:
        """La última versión guardada; su campo `revisado` dice si una persona la ha revisado."""
        perfiles = self._perfiles(
            "SELECT * FROM perfiles WHERE dominio = ? ORDER BY version DESC LIMIT 1", (dominio,)
        )
        return perfiles[0] if perfiles else None

    def versiones(self, dominio: str) -> list[Perfil]:
        return self._perfiles(
            "SELECT * FROM perfiles WHERE dominio = ? ORDER BY version", (dominio,)
        )

    def marcar_revisado(self, dominio: str, version: int) -> None:
        if not isinstance(version, int) or not 1 <= version <= MAX_ENTERO:
            # Fuera del rango de INTEGER `sqlite3` lanzaría OverflowError: no existe y punto.
            raise PerfilNoEncontrado(f"{dominio} v{version}")
        with self._transaccion() as con:
            cursor = con.execute(
                "UPDATE perfiles SET revisado = 1 WHERE dominio = ? AND version = ?",
                (dominio, version),
            )
            if cursor.rowcount == 0:
                raise PerfilNoEncontrado(f"{dominio} v{version}")

    def _perfiles(self, sql: str, parametros: Sequence[object] = ()) -> list[Perfil]:
        return [_perfil(f) for f in self._consultar(sql, parametros)]

    # --- Catálogo por sitemap (nota 16 §B) ---

    def guardar_catalogo(
        self,
        dominio: str,
        sitemap: str,
        entradas: Iterable[tuple[str, str, str | None]],
        *,
        parcial: bool,
        pendientes: Iterable[str] = (),
        idioma: Sequence[str] = (),
    ) -> InfoCatalogo:
        """Sustituye todo el catálogo de la tienda, en una transacción, y devuelve su resumen.

        `entradas` son `EntradaCatalogo` o tuplas `(url, palabras, lastmod)`. Cada URL (y el
        `sitemap`) tiene que ser http(s) del host de la tienda, y `palabras` se normaliza igual
        que la consulta de `buscar_catalogo`. Una entrada no válida es `DatoNoValido` y no
        cambia nada. Las URL repetidas cuentan una vez. Se guardan como mucho `MAX_CATALOGO`:
        si `entradas` trae más, el resto no se lee y el catálogo queda `parcial`.

        `pendientes` son los sitemaps hijos que no se leyeron (R2-C14b), también de la tienda:
        sustituyen a los anteriores (como mucho `MAX_PENDIENTES_CATALOGO`, sin repetir).

        `idioma` es el prefijo de idioma o país con que se filtró (`perfiles.catalogo.Catalogo
        .idioma`, R5-C): se guarda para rehacer el catálogo con el mismo filtro.
        """
        _texto(dominio, "dominio", MAX_DOMINIO)
        _url_de_sitemap_de_la_tienda(sitemap, "sitemap", dominio, MAX_URL)
        prefijo = _idioma_catalogo(idioma)
        filas, desbordado = _filas_catalogo(dominio, entradas, MAX_CATALOGO, set())
        hijos = _hijos_pendientes(dominio, pendientes, set(), MAX_PENDIENTES_CATALOGO)
        info = InfoCatalogo(
            dominio,
            sitemap,
            self._reloj(),
            len(filas),
            bool(parcial) or desbordado,
            _idioma_guardado(prefijo),
        )
        actualizado = _fecha(info.actualizado, "actualizado")
        with self._transaccion() as con:
            if not con.execute("SELECT 1 FROM tiendas WHERE dominio = ?", (dominio,)).fetchone():
                raise TiendaNoEncontrada(dominio)
            con.execute("DELETE FROM catalogo WHERE dominio = ?", (dominio,))
            con.executemany(
                "INSERT INTO catalogo (dominio, url, palabras, lastmod) VALUES (?, ?, ?, ?)", filas
            )
            con.execute("DELETE FROM catalogo_pendientes WHERE dominio = ?", (dominio,))
            con.executemany(
                "INSERT INTO catalogo_pendientes (dominio, url, orden) VALUES (?, ?, ?)",
                [(dominio, hijo, orden) for orden, hijo in enumerate(hijos)],
            )
            con.execute(
                "INSERT INTO catalogos (dominio, sitemap, actualizado, total, parcial, idioma)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (dominio) DO UPDATE SET"
                " sitemap = excluded.sitemap, actualizado = excluded.actualizado,"
                " total = excluded.total, parcial = excluded.parcial, idioma = excluded.idioma",
                (dominio, sitemap, actualizado, info.total, int(info.parcial), prefijo),
            )
        return info

    def ampliar_catalogo(
        self,
        dominio: str,
        leidos: Iterable[str],
        entradas: Iterable[tuple[str, str, str | None]],
        *,
        parcial: bool,
        pendientes: Iterable[str] = (),
    ) -> InfoCatalogo:
        """Añade al catálogo las URL de unos sitemaps hijos leídos en una búsqueda (R2-C14b).

        `leidos` salen de los pendientes (aunque no hayan dado nada: no se vuelven a descargar
        hasta que se rehaga el catálogo) y `pendientes` (los hijos de un índice dentro del
        índice) se añaden al final. Las `entradas` se validan como en `guardar_catalogo` y las
        que ya estaban no se repiten; si no caben en `MAX_CATALOGO`, el catálogo queda `parcial`.
        La fecha no cambia: el refresco lo rehace entero. Sin catálogo es `DatoNoValido`.
        """
        _texto(dominio, "dominio", MAX_DOMINIO)
        quitar = [
            (dominio, _url_de_sitemap_de_la_tienda(u, "leido", dominio, MAX_URL)) for u in leidos
        ]
        with self._transaccion() as con:
            anterior = con.execute(
                "SELECT sitemap, actualizado, parcial, idioma FROM catalogos WHERE dominio = ?",
                (dominio,),
            ).fetchone()
            if anterior is None:
                raise DatoNoValido(f"{dominio} no tiene catálogo que ampliar")
            total = int(
                con.execute(
                    "SELECT COUNT(*) FROM catalogo WHERE dominio = ?", (dominio,)
                ).fetchone()[0]
            )
            filas, desbordado = _filas_catalogo(
                dominio, entradas, MAX_CATALOGO - total, _UrlsGuardadas(con, dominio)
            )
            con.executemany("DELETE FROM catalogo_pendientes WHERE dominio = ? AND url = ?", quitar)
            ya = {
                fila[0]
                for fila in con.execute(
                    "SELECT url FROM catalogo_pendientes WHERE dominio = ?", (dominio,)
                )
            }
            hijos = _hijos_pendientes(
                dominio, pendientes, ya | {u for _, u in quitar}, MAX_PENDIENTES_CATALOGO - len(ya)
            )
            siguiente = int(
                con.execute(
                    "SELECT COALESCE(MAX(orden), -1) + 1 FROM catalogo_pendientes"
                    " WHERE dominio = ?",
                    (dominio,),
                ).fetchone()[0]
            )
            con.executemany(
                "INSERT INTO catalogo_pendientes (dominio, url, orden) VALUES (?, ?, ?)",
                [(dominio, hijo, siguiente + n) for n, hijo in enumerate(hijos)],
            )
            con.executemany(
                "INSERT INTO catalogo (dominio, url, palabras, lastmod) VALUES (?, ?, ?, ?)", filas
            )
            total += len(filas)
            parcial = bool(anterior[2]) or bool(parcial) or desbordado
            con.execute(
                "UPDATE catalogos SET total = ?, parcial = ? WHERE dominio = ?",
                (total, int(parcial), dominio),
            )
        return InfoCatalogo(
            dominio,
            anterior[0],
            datetime.fromisoformat(anterior[1]),
            total,
            parcial,
            _idioma_guardado(anterior[3]),
        )

    def hijos_pendientes(self, dominio: str) -> list[str]:
        """Los sitemaps hijos sin leer del catálogo de la tienda, en orden (R2-C14b)."""
        filas = self._consultar(
            "SELECT url FROM catalogo_pendientes WHERE dominio = ? ORDER BY orden", (dominio,)
        )
        return [f["url"] for f in filas]

    def catalogo(self, dominio: str) -> InfoCatalogo | None:
        """El resumen del catálogo de la tienda, o `None` si no se ha guardado nunca."""
        filas = self._consultar("SELECT * FROM catalogos WHERE dominio = ?", (dominio,))
        if not filas:
            return None
        fila = filas[0]
        return InfoCatalogo(
            dominio=fila["dominio"],
            sitemap=fila["sitemap"],
            actualizado=datetime.fromisoformat(fila["actualizado"]),
            total=fila["total"],
            parcial=bool(fila["parcial"]),
            idioma=_idioma_guardado(fila["idioma"]),
        )

    def buscar_catalogo(
        self,
        dominio: str,
        consulta: str,
        *,
        limite: int,
        es_listado: Callable[[str], bool] | None = None,
    ) -> list[str]:
        """Las URL del catálogo que encajan con la consulta, las `limite` más relevantes.

        Encajan las que tienen en sus palabras todas las de la consulta (normalizadas, de 3
        letras o más, sin las vacías como «para»; basta con que aparezcan: «faro» encaja con
        «faros» y con «farola») o alguna de sus referencias OEM, comparada sin espacios («1k0 698
        151» encaja con «1K0698151»). Una consulta sin palabras ni referencias no devuelve nada.

        Una palabra de `MIN_PALABRA_RAIZ` letras o más que no casa con ninguna URL de la tienda
        se cambia por su raíz sin la terminación flexiva (R5-J, `_raiz_catalogo`): «polarizadas»
        pasa a «polariz» y casa con «polarized»; «pilotos», a «pilot» y casa con «piloto». Las que
        sí casan no se tocan. Cuesta una segunda consulta, y solo cuando hay alguna así.

        Si no encaja ninguna, se agrupan las que tienen alguna palabra por el conjunto de las
        que casan y se quedan los grupos que no están dentro de otro (R5-I): con «vino tinto
        rioja», {rioja} y {vino, tinto}, pero no {tinto}. Los grupos se turnan, empezando por
        el de la palabra más rara del catálogo (la que casan menos URL). Así, en una tienda de
        vinos, los riojas no se quedan fuera por los kits de «vino tinto»: que las palabras de
        la categoría salgan en el slug no hace a la URL más relevante. Dentro de cada grupo
        manda el orden de abajo; con un solo grupo, es el de siempre.

        Orden (R2-C14a): primero las de referencia; después, las que no son páginas de listado
        (las que `es_listado` da por marca, categoría…; sin `es_listado`, ninguna lo es); luego
        las que tienen más palabras de la consulta al principio de una palabra («faros» sí,
        «antifaro» no) y más como palabra entera («faro» antes que «faros»); luego las que la
        nombran antes en el nombre del producto (`_posicion_en_el_nombre`); y por último la URL,
        para que el orden sea estable. El límite se aplica después de ordenar.
        """
        if _entero(limite, "limite") == 0 or limite > MAX_LIMITE_CATALOGO:
            raise DatoNoValido(f"limite: entre 1 y {MAX_LIMITE_CATALOGO}")
        if not isinstance(consulta, str):
            raise DatoNoValido("consulta: se espera un texto")
        palabras, referencias = _terminos_catalogo(consulta)
        if not palabras and not referencias:
            return []
        filas = self._filas_que_casan(dominio, palabras, referencias)
        # Una palabra que no casa con ninguna URL de la tienda se prueba por su raíz (R5-J):
        # «polarizadas» no está en ningún slug, pero «polariz» casa con «polarized».
        raices = {
            p: r
            for p in palabras
            if (r := _raiz_catalogo(p)) and not any(p in f["palabras"] for f in filas)
        }
        if raices:
            palabras = list(dict.fromkeys(raices.get(p, p) for p in palabras))
            filas = self._filas_que_casan(dominio, palabras, referencias)
        elegidas = [f for f in filas if f["oem"] or f["aciertos"] == len(palabras)]
        prefijos = tuple(palabras)

        def clave(fila: sqlite3.Row) -> tuple[int, int, bool, int, int, int, str]:
            url: str = fila["url"]
            suyas = fila["palabras"].split()
            inicios = sum(any(s.startswith(p) for s in suyas) for p in palabras)
            enteras = len(set(palabras).intersection(suyas))
            listado = es_listado is not None and es_listado(url)
            posicion = _posicion_en_el_nombre(url, prefijos)
            return (-fila["oem"], -fila["aciertos"], listado, -inicios, -enteras, posicion, url)

        if elegidas or not filas:
            return [f["url"] for f in heapq.nsmallest(limite, elegidas, key=clave)]
        # Sin ninguna con todas (ni con referencia): grupos maximales, por turnos (R5-I). Se casa
        # como el LIKE de arriba, por subcadena (las palabras ya están normalizadas).
        grupos: dict[frozenset[str], list[sqlite3.Row]] = {}
        for fila in filas:
            casadas = frozenset(p for p in palabras if p in fila["palabras"])
            grupos.setdefault(casadas, []).append(fila)
        cuantas = {p: sum(len(v) for g, v in grupos.items() if p in g) for p in palabras}
        maximales = sorted(
            (g for g in grupos if not any(g < otro for otro in grupos)),
            key=lambda g: (min(cuantas[p] for p in g), -len(g), sorted(g)),
        )
        turnos = zip_longest(*(heapq.nsmallest(limite, grupos[g], key=clave) for g in maximales))
        return [f["url"] for vuelta in turnos for f in vuelta if f is not None][:limite]

    def _filas_que_casan(
        self, dominio: str, palabras: list[str], referencias: list[str]
    ) -> list[sqlite3.Row]:
        """Las URL de la tienda con alguna de `palabras` o de `referencias`, con cuántas palabras
        casan (`aciertos`) y si casa alguna referencia (`oem`)."""
        # Las expresiones solo repiten texto fijo; las palabras van siempre como parámetros.
        aciertos = " + ".join(["(palabras LIKE ? ESCAPE '\\')"] * len(palabras)) or "0"
        oem = " OR ".join(["(REPLACE(palabras, ' ', '') LIKE ? ESCAPE '\\')"] * len(referencias))
        return self._consultar(
            f"SELECT url, palabras, aciertos, oem FROM (SELECT url, palabras, {aciertos} AS"
            f" aciertos, {oem or '0'} AS oem FROM catalogo WHERE dominio = ?)"
            " WHERE aciertos > 0 OR oem",
            (
                *(_patron_like(p) for p in palabras),
                *(_patron_like(r) for r in referencias),
                dominio,
            ),
        )

    # --- Histórico de precios ---

    def guardar_precio(
        self,
        producto_id: str,
        nombre: str,
        referencia_oem: str | None,
        tienda: str,
        url: str,
        precio: Decimal,
        fecha: datetime,
        *,
        titulo: str = "",
        campos: Mapping[str, ValorCampo] | None = None,
        disponible: bool = True,
        criterio: str = "",
        coincidencia: Coincidencia = Coincidencia.EXACTA,
        visita: datetime | None = None,
        foto: str | None = None,
    ) -> None:
        """Una oferta en el histórico. `titulo`, `campos` y `criterio` son los que enseña la web.

        `foto` es la URL `https` de la foto principal (`foto_guardable`); `None`, sin foto. Solo se
        guarda el enlace, nunca la imagen.

        `coincidencia` dice cómo coincide la oferta con la referencia buscada (B5): las alertas
        por defecto solo cuentan las exactas.

        `fecha` es cuándo se descargó la oferta (la de la caché si salió de ella, A3) y `visita`,
        la búsqueda que la ha visto (por defecto, `fecha`). La misma oferta con la misma fecha y
        el mismo precio no crea otra fila: se apunta en esa visita (A1).

        Solo se admiten los campos declarados en `nucleo.modelos.CAMPOS` (nota 08: no se guarda
        nada que el usuario no haya pedido, como el nombre de un vendedor particular).
        """
        _texto(producto_id, "producto_id", MAX_PRODUCTO_ID)
        _texto(nombre, "nombre", MAX_NOMBRE_PRODUCTO)
        if referencia_oem is not None:
            _texto(referencia_oem, "referencia_oem", MAX_REFERENCIA)
        _texto(tienda, "tienda", MAX_DOMINIO)
        _texto(url, "url", MAX_URL)
        importe = _importe(precio, "precio")
        instante = _fecha(fecha, "fecha")
        visto = instante if visita is None else _fecha(visita, "visita")
        _texto(titulo, "titulo", MAX_NOMBRE_PRODUCTO, vacio=True)
        _texto(criterio, "criterio", MAX_CRITERIO, vacio=True)
        texto_campos = _codificar_campos(campos or {})
        texto_foto = _foto(foto)
        try:
            texto_coincidencia = Coincidencia(coincidencia).value
        except ValueError:
            raise DatoNoValido("coincidencia: exacta, revision o equivalente") from None
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO productos (id, nombre, referencia_oem, criterio) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET nombre = excluded.nombre,"
                " referencia_oem = COALESCE(excluded.referencia_oem, productos.referencia_oem),"
                " criterio = CASE WHEN excluded.criterio = '' THEN productos.criterio"
                " ELSE excluded.criterio END",
                (producto_id, nombre, referencia_oem, criterio),
            )
            # La misma oferta con la misma fecha y el mismo precio ya está: es una ficha servida
            # desde la caché en una búsqueda repetida (A3). No se duplica: se apunta en esta
            # visita (A1). Sin índice UNIQUE, que fallaría con los duplicados que ya hay en las
            # bases de datos guardadas: se actualiza la última de ellas.
            parametros = (
                producto_id,
                tienda,
                url,
                importe,
                instante,
                fecha.date().isoformat(),
                titulo,
                texto_campos,
                int(bool(disponible)),
                texto_coincidencia,
                visto,
                texto_foto,
            )
            vista = con.execute(
                "UPDATE precios SET titulo = ?7, campos = ?8, disponible = ?9,"
                " coincidencia = ?10, visita = ?11, foto = ?12"
                " WHERE id = (SELECT MAX(id) FROM precios"
                " WHERE producto_id = ?1 AND tienda = ?2 AND url = ?3 AND precio = ?4"
                # Como instante y no como texto: con el cambio de hora, la misma descarga de la
                # caché llega con otro desfase («01:00+02:00» y «00:00+01:00»).
                " AND julianday(fecha) = julianday(?5))",
                parametros,
            )
            if vista.rowcount == 0:
                con.execute(
                    "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia, titulo,"
                    " campos, disponible, coincidencia, visita, foto)"
                    " VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                    parametros,
                )

    def ultimas_ofertas(self, producto_id: str) -> list[Oferta]:
        """Las ofertas de la última visita a cada tienda para el producto, por orden de tienda.

        Un grupo puede tener varias ofertas de la misma tienda (dos fichas parecidas): se
        devuelven todas las de su última visita, una por URL, y no las de visitas anteriores.
        Cuenta la visita y no la fecha: una oferta servida desde la caché lleva la fecha de su
        descarga (A3), pero la ha visto esta búsqueda (A1).
        """
        filas = self._consultar(
            "SELECT p.tienda, p.url, p.precio, p.titulo, p.campos, p.disponible,"
            " p.coincidencia, p.foto, o.nombre AS producto"
            " FROM precios p JOIN productos o ON o.id = p.producto_id"
            # La última visita como instante (`julianday`): como texto, en la hora repetida del
            # cambio de hora «02:10+01:00» iría antes que «02:30+02:00».
            " WHERE p.producto_id = ? AND julianday(p.visita) = ("
            " SELECT MAX(julianday(q.visita)) FROM precios q WHERE q.producto_id = p.producto_id"
            " AND q.tienda = p.tienda) AND p.id = ("
            " SELECT MAX(r.id) FROM precios r WHERE r.producto_id = p.producto_id"
            " AND r.tienda = p.tienda AND r.visita = p.visita AND r.url = p.url)"
            " ORDER BY p.tienda, p.id",
            (producto_id,),
        )
        return [
            Oferta(
                tienda=f["tienda"],
                url=f["url"],
                # Las filas de antes de la migración 2 no tienen título: el del producto.
                titulo=f["titulo"] or f["producto"],
                precio=Decimal(f["precio"]),
                campos=_decodificar_campos(f["campos"]),
                disponible=bool(f["disponible"]),
                coincidencia=Coincidencia(f["coincidencia"]),
                # Se revalida al leer: un valor tocado a mano que no sea seguro no llega a la web.
                foto=f["foto"] if foto_guardable(f["foto"]) else None,
            )
            for f in filas
        ]

    def producto(self, producto_id: str) -> GrupoProducto | None:
        """El producto con la última oferta de cada tienda, o `None` si no se ha guardado."""
        with self._cerrojo:
            filas = self._consultar(
                "SELECT nombre, referencia_oem, criterio FROM productos WHERE id = ?",
                (producto_id,),
            )
            ofertas = self.ultimas_ofertas(producto_id)
        if not filas or not ofertas:
            return None
        fila = filas[0]
        return GrupoProducto(
            id=producto_id,
            nombre=fila["nombre"],
            referencia_oem=fila["referencia_oem"],
            criterio=fila["criterio"],
            ofertas=tuple(ofertas),
        )

    def historico(self, producto_id: str) -> list[SerieHistorico]:
        """Una serie por tienda (por orden alfabético) con el último precio de cada día.

        Solo las ofertas exactas (B5): las equivalentes y las revisiones son otras piezas, con
        otro precio, y el punto del día dependería de qué ficha se descargó la última.
        """
        filas = self._consultar(
            "SELECT tienda, dia, precio FROM precios WHERE producto_id = ?"
            " AND coincidencia = 'exacta' ORDER BY tienda, dia, julianday(fecha), id",
            (producto_id,),
        )
        por_tienda: dict[str, dict[date, Decimal]] = {}
        for fila in filas:
            # Las filas llegan en orden cronológico: la última de cada día sobrescribe al resto.
            dias = por_tienda.setdefault(fila["tienda"], {})
            dias[date.fromisoformat(fila["dia"])] = Decimal(fila["precio"])
        return [
            SerieHistorico(tienda, tuple(PuntoHistorico(d, p) for d, p in dias.items()))
            for tienda, dias in por_tienda.items()
        ]

    def producto_nombre(self, producto_id: str) -> str | None:
        filas = self._consultar("SELECT nombre FROM productos WHERE id = ?", (producto_id,))
        return str(filas[0]["nombre"]) if filas else None

    # --- Búsquedas ---

    def registrar_busqueda(
        self,
        texto: str,
        modo: Modo,
        fecha: datetime,
        llamadas_ia: int,
        coste_ia_usd: Decimal,
        duracion_s: float,
    ) -> None:
        """Métricas de una búsqueda (para la evaluación): nunca los datos de las tiendas."""
        if (
            not isinstance(duracion_s, int | float)
            or isinstance(duracion_s, bool)
            or not math.isfinite(duracion_s)
            or duracion_s < 0
        ):
            raise DatoNoValido("duracion_s: se espera un número finito y no negativo")
        fila = (
            _texto(texto, "texto", MAX_TEXTO_BUSQUEDA),
            Modo(modo).value,
            _fecha(fecha, "fecha"),
            _entero(llamadas_ia, "llamadas_ia"),
            _importe(coste_ia_usd, "coste_ia_usd"),
            float(duracion_s),
        )
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO busquedas (texto, modo, fecha, llamadas_ia, coste_ia_usd,"
                " duracion_s) VALUES (?, ?, ?, ?, ?, ?)",
                fila,
            )

    def busquedas(self, limite: int = 50) -> list[BusquedaGuardada]:
        """Las últimas `limite` búsquedas, de la más reciente a la más antigua."""
        if _entero(limite, "limite") == 0 or limite > MAX_LIMITE_LLAMADAS:
            raise DatoNoValido(f"limite: entre 1 y {MAX_LIMITE_LLAMADAS}")
        filas = self._consultar(
            "SELECT * FROM busquedas ORDER BY fecha DESC, id DESC LIMIT ?", (limite,)
        )
        return [
            BusquedaGuardada(
                texto=f["texto"],
                modo=Modo(f["modo"]),
                fecha=datetime.fromisoformat(f["fecha"]),
                llamadas_ia=f["llamadas_ia"],
                coste_ia_usd=Decimal(f["coste_ia_usd"]),
                duracion_s=f["duracion_s"],
            )
            for f in filas
        ]

    # --- Alertas ---

    def crear_alerta(
        self, producto_id: str, umbral: Decimal, canal: str, *, solo_exactas: bool = True
    ) -> Alerta:
        """Alerta de precio; con `solo_exactas` (por defecto), solo cuentan las ofertas exactas."""
        texto_umbral = _importe(umbral, "umbral", positivo=True)
        _texto(canal, "canal", MAX_CANAL)
        with self._transaccion() as con:
            fila = con.execute("SELECT nombre FROM productos WHERE id = ?", (producto_id,))
            producto = fila.fetchone()
            if producto is None:
                raise ProductoNoEncontrado(producto_id)
            cursor = con.execute(
                "INSERT INTO alertas (producto_id, umbral, canal, solo_exactas)"
                " VALUES (?, ?, ?, ?)",
                (producto_id, texto_umbral, canal, int(bool(solo_exactas))),
            )
            identificador = cursor.lastrowid
        assert identificador is not None
        return Alerta(
            identificador,
            producto_id,
            str(producto[0]),
            Decimal(texto_umbral),
            canal,
            solo_exactas=bool(solo_exactas),
        )

    def alertas(self) -> list[Alerta]:
        filas = self._consultar(
            "SELECT a.id, a.producto_id, p.nombre, a.umbral, a.canal, a.activa,"
            " a.ultima_activacion, a.solo_exactas, a.avisada, a.ultimo_error,"
            " a.ultima_comprobacion"
            " FROM alertas a JOIN productos p ON p.id = a.producto_id"
            " ORDER BY a.id"
        )
        return [
            Alerta(
                id=f["id"],
                producto_id=f["producto_id"],
                producto_nombre=f["nombre"],
                umbral=Decimal(f["umbral"]),
                canal=f["canal"],
                activa=bool(f["activa"]),
                ultima_activacion=_leer_fecha(f["ultima_activacion"]),
                solo_exactas=bool(f["solo_exactas"]),
                avisada=bool(f["avisada"]),
                ultimo_error=f["ultimo_error"],
                ultima_comprobacion=_leer_fecha(f["ultima_comprobacion"]),
            )
            for f in filas
        ]

    def anotar_alerta(
        self,
        id_alerta: int,
        *,
        comprobada: datetime,
        avisada: bool | None = None,
        error: str = "",
    ) -> None:
        """Apunta una comprobación de la alerta (H7).

        `avisada=True`: se acaba de enviar el aviso (queda también como última activación).
        `avisada=False`: el precio volvió a subir y la alerta se rearma. `None`: no cambia.
        `error` es el motivo de un fallo ("" si fue bien); se recorta a `MAX_ERROR_ALERTA`.
        """
        instante = _fecha(comprobada, "comprobada")
        if not isinstance(error, str):
            raise DatoNoValido("error: se espera un texto")
        texto = error[:MAX_ERROR_ALERTA]
        fila = self._id_alerta(id_alerta)
        with self._transaccion() as con:
            if avisada is None:
                cursor = con.execute(
                    "UPDATE alertas SET ultima_comprobacion = ?, ultimo_error = ? WHERE id = ?",
                    (instante, texto, fila),
                )
            elif avisada:
                cursor = con.execute(
                    "UPDATE alertas SET ultima_comprobacion = ?1, ultimo_error = ?2,"
                    " avisada = 1, ultima_activacion = ?1 WHERE id = ?3",
                    (instante, texto, fila),
                )
            else:
                cursor = con.execute(
                    "UPDATE alertas SET ultima_comprobacion = ?, ultimo_error = ?, avisada = 0"
                    " WHERE id = ?",
                    (instante, texto, fila),
                )
            if cursor.rowcount == 0:
                raise AlertaNoEncontrada(id_alerta)

    def cambiar_alerta(self, id_alerta: int, *, activa: bool) -> None:
        """Pausa o reanuda la alerta. Al reanudarla se rearma: vuelve a avisar aunque el precio
        siga por debajo, que es lo que espera quien la reactiva."""
        fila = self._id_alerta(id_alerta)
        with self._transaccion() as con:
            if activa:
                cursor = con.execute(
                    "UPDATE alertas SET activa = 1, avisada = 0, ultimo_error = '' WHERE id = ?",
                    (fila,),
                )
            else:
                cursor = con.execute("UPDATE alertas SET activa = 0 WHERE id = ?", (fila,))
            if cursor.rowcount == 0:
                raise AlertaNoEncontrada(id_alerta)

    def borrar_alerta(self, id_alerta: int) -> None:
        fila = self._id_alerta(id_alerta)
        with self._transaccion() as con:
            if con.execute("DELETE FROM alertas WHERE id = ?", (fila,)).rowcount == 0:
                raise AlertaNoEncontrada(id_alerta)

    @staticmethod
    def _id_alerta(id_alerta: object) -> int:
        """El id como entero de SQLite; uno imposible (negativo, enorme) no existe."""
        if isinstance(id_alerta, bool) or not isinstance(id_alerta, int):
            raise AlertaNoEncontrada(id_alerta)
        if not 1 <= id_alerta <= MAX_ENTERO:
            raise AlertaNoEncontrada(id_alerta)
        return id_alerta

    # --- Ajustes ---

    def leer_ajustes(self) -> Ajustes:
        """Los ajustes guardados sobre los valores por defecto. Un valor corrupto es un error."""
        guardados = {f["clave"]: f["valor"] for f in self._consultar("SELECT * FROM ajustes")}
        ajustes = Ajustes()
        for campo in fields(Ajustes):
            if campo.name in guardados:
                defecto = getattr(ajustes, campo.name)
                valor = _decodificar_ajuste(campo.name, guardados[campo.name], defecto)
                setattr(ajustes, campo.name, valor)
        return ajustes

    def guardar_ajustes(self, ajustes: Ajustes) -> None:
        """Guarda todos los ajustes. Un valor que luego no se podría leer es `DatoNoValido`."""
        defectos = Ajustes()
        pares = [
            (
                campo.name,
                _codificar_ajuste(
                    campo.name, getattr(ajustes, campo.name), getattr(defectos, campo.name)
                ),
            )
            for campo in fields(Ajustes)
        ]
        with self._transaccion() as con:
            con.executemany(
                "INSERT INTO ajustes (clave, valor) VALUES (?, ?)"
                " ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor",
                pares,
            )

    # --- Llamadas de IA (RegistroLlamadas) ---

    def registrar_llamada(self, llamada: LlamadaIA) -> None:
        """Guarda las métricas de una llamada (nunca el contenido de la petición)."""
        fila = (
            _fecha(llamada.fecha, "fecha"),
            _mes(llamada.fecha),
            _texto(llamada.componente, "componente", MAX_ETIQUETA_IA),
            _texto(llamada.proveedor, "proveedor", MAX_ETIQUETA_IA),
            _texto(llamada.modelo, "modelo", MAX_ETIQUETA_IA),
            _entero(llamada.tokens_entrada, "tokens_entrada"),
            _entero(llamada.tokens_salida, "tokens_salida"),
            _importe(llamada.coste_usd, "coste_usd"),
            _texto(llamada.resultado, "resultado", MAX_RESULTADO_IA, vacio=True),
            _entero(llamada.duracion_ms, "duracion_ms"),
        )
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO llamadas_ia (fecha, mes, componente, proveedor, modelo,"
                " tokens_entrada, tokens_salida, coste_usd, resultado, duracion_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                fila,
            )

    # --- Caché del extractor de IA (A3) ---

    def leer_extraccion_ia(
        self, texto_sha256: str, campos: Sequence[str], modelo: str, *, max_edad_s: float
    ) -> dict[str, str] | None:
        """Valores guardados del extractor, o `None` si no hay, han caducado o están dañados."""
        clave = _clave_extraccion(texto_sha256, campos, modelo)
        filas = self._consultar(
            "SELECT valores, creado FROM extracciones_ia"
            " WHERE texto_sha256 = ? AND campos = ? AND modelo = ?",
            clave,
        )
        if not filas:
            return None
        try:
            # La edad se mide en Python, exacta (como `CachePaginas`); un reloj que mezcle
            # fechas con y sin zona da TypeError y cuenta como fallo de caché.
            edad = (self._reloj() - datetime.fromisoformat(filas[0]["creado"])).total_seconds()
            valores = json.loads(filas[0]["valores"])
        except (TypeError, ValueError):
            return None
        if not 0 <= edad <= max_edad_s or not isinstance(valores, dict):
            return None
        pedidos = set(clave[1].split(","))
        if not all(
            isinstance(v, str) and k in pedidos and len(v) <= MAX_VALOR_CAMPO
            for k, v in valores.items()
        ):
            return None
        return valores

    def guardar_extraccion_ia(
        self, texto_sha256: str, campos: Sequence[str], modelo: str, valores: Mapping[str, str]
    ) -> None:
        clave = _clave_extraccion(texto_sha256, campos, modelo)
        pedidos = set(clave[1].split(","))
        for campo, valor in valores.items():
            if campo not in pedidos:
                raise DatoNoValido(f"Campo no pedido en la extracción: {campo!r}")
            _texto(valor, campo, MAX_VALOR_CAMPO)
        texto = json.dumps(dict(valores), ensure_ascii=False, sort_keys=True)
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO extracciones_ia (texto_sha256, campos, modelo, valores, creado)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (texto_sha256, campos, modelo)"
                " DO UPDATE SET valores = excluded.valores, creado = excluded.creado",
                (*clave, texto, self._reloj().isoformat()),
            )

    def borrar_extracciones_ia(self, *, max_edad_s: float) -> int:
        """Borra las caducadas y dice cuántas.

        Compara el texto ISO: es aproximado si el reloj cambia de zona (horario de verano), pero
        la lectura comprueba la edad exacta.
        """
        limite = (self._reloj() - timedelta(seconds=max_edad_s)).isoformat()
        with self._transaccion() as con:
            return con.execute("DELETE FROM extracciones_ia WHERE creado < ?", (limite,)).rowcount

    # --- Caché de la revisión con IA de los resultados ---

    def leer_revision_ia(self, clave: str, *, max_edad_s: float) -> tuple[str, str, bool] | None:
        """`(clase, motivo, cumple_filtro)` guardado, o `None` si no hay, caducó o está dañado."""
        filas = self._consultar(
            "SELECT clase, motivo, cumple_filtro, creado FROM revisiones_ia WHERE clave = ?",
            (clave,),
        )
        if not filas:
            return None
        fila = filas[0]
        try:
            edad = (self._reloj() - datetime.fromisoformat(fila["creado"])).total_seconds()
        except (TypeError, ValueError):
            return None
        if not 0 <= edad <= max_edad_s or fila["clase"] not in CLASES_REVISION:
            return None
        return fila["clase"], fila["motivo"], bool(fila["cumple_filtro"])

    def guardar_revision_ia(self, clave: str, clase: str, motivo: str, cumple_filtro: bool) -> None:
        if not isinstance(clave, str) or not _CLAVE_REVISION.fullmatch(clave):
            raise DatoNoValido("clave: sha256 en hexadecimal")
        if clase not in CLASES_REVISION:
            raise DatoNoValido(f"Clase de revisión no válida: {clase!r}")
        _texto(motivo, "motivo", MAX_MOTIVO_REVISION, vacio=True)
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO revisiones_ia (clave, clase, motivo, cumple_filtro, creado)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (clave) DO UPDATE SET"
                " clase = excluded.clase, motivo = excluded.motivo,"
                " cumple_filtro = excluded.cumple_filtro, creado = excluded.creado",
                (clave, clase, motivo, int(bool(cumple_filtro)), self._reloj().isoformat()),
            )

    def borrar_revisiones_ia(self, *, max_edad_s: float) -> int:
        """Borra las caducadas y dice cuántas (aproximado, como `borrar_extracciones_ia`)."""
        limite = (self._reloj() - timedelta(seconds=max_edad_s)).isoformat()
        with self._transaccion() as con:
            return con.execute("DELETE FROM revisiones_ia WHERE creado < ?", (limite,)).rowcount

    def gasto_mes_usd(self) -> Decimal:
        """Coste de las llamadas del mes en curso según el reloj del almacén."""
        filas = self._consultar(
            "SELECT coste_usd FROM llamadas_ia WHERE mes = ?", (_mes(self._reloj()),)
        )
        # Suma en Python con Decimal: SUM() de SQLite convertiría el texto a coma flotante.
        return sum((Decimal(f["coste_usd"]) for f in filas), Decimal("0"))

    def llamadas(self, limite: int = 50) -> list[LlamadaIA]:
        """Las últimas `limite` llamadas, de la más reciente a la más antigua."""
        if _entero(limite, "limite") == 0 or limite > MAX_LIMITE_LLAMADAS:
            raise DatoNoValido(f"limite: entre 1 y {MAX_LIMITE_LLAMADAS}")
        filas = self._consultar(
            "SELECT * FROM llamadas_ia ORDER BY fecha DESC, id DESC LIMIT ?", (limite,)
        )
        return [
            LlamadaIA(
                fecha=datetime.fromisoformat(f["fecha"]),
                componente=f["componente"],
                proveedor=f["proveedor"],
                modelo=f["modelo"],
                tokens_entrada=f["tokens_entrada"],
                tokens_salida=f["tokens_salida"],
                coste_usd=Decimal(f["coste_usd"]),
                resultado=f["resultado"],
                duracion_ms=f["duracion_ms"],
            )
            for f in filas
        ]


def _clave_extraccion(
    texto_sha256: object, campos: Sequence[str], modelo: object
) -> tuple[str, str, str]:
    """La clave de la caché del extractor: sha256 en hexadecimal, campos ordenados y modelo."""
    if not isinstance(texto_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", texto_sha256):
        raise DatoNoValido("texto_sha256: se espera un sha256 en hexadecimal")
    pedidos = sorted(set(campos))
    if not pedidos or any(c not in CLAVES_CAMPOS for c in pedidos):
        raise DatoNoValido("campos: se esperan campos de `CAMPOS`")
    return texto_sha256, ",".join(pedidos), _texto(modelo, "modelo", MAX_ETIQUETA_IA)


def _codificar_campos(campos: Mapping[str, ValorCampo]) -> str:
    datos: dict[str, dict[str, object]] = {}
    for clave, valor in campos.items():
        if clave not in CLAVES_CAMPOS:
            raise DatoNoValido(f"campos: campo desconocido {clave!r}")
        if not isinstance(valor, ValorCampo):
            raise DatoNoValido(f"campos[{clave}]: se espera un ValorCampo")
        confianza = valor.confianza
        if (
            not isinstance(confianza, int | float)
            or isinstance(confianza, bool)
            or not math.isfinite(confianza)
            or not 0 <= confianza <= 1
        ):
            raise DatoNoValido(f"campos[{clave}]: la confianza va de 0 a 1")
        datos[clave] = {
            "valor": _texto(valor.valor, f"campos[{clave}]", MAX_VALOR_CAMPO),
            "origen": OrigenDato(valor.origen).value,
            "anclado": bool(valor.anclado),
            "confianza": float(confianza),
        }
    return json.dumps(datos, ensure_ascii=False)


def _decodificar_campos(texto: str) -> dict[str, ValorCampo]:
    error = ErrorAlmacen("Campos de una oferta corruptos en la base de datos")
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as e:
        raise error from e
    if not isinstance(datos, dict):
        raise error
    campos: dict[str, ValorCampo] = {}
    for clave, valor in datos.items():
        if clave not in CLAVES_CAMPOS or not isinstance(valor, dict):
            raise error
        texto_valor = valor.get("valor")
        anclado = valor.get("anclado")
        confianza = valor.get("confianza")
        origen = valor.get("origen")
        if (
            not isinstance(texto_valor, str)
            or not isinstance(origen, str)
            or origen not in _ORIGENES
            or not isinstance(anclado, bool)
            or not isinstance(confianza, int | float)
            or isinstance(confianza, bool)
        ):
            raise error
        campos[clave] = ValorCampo(texto_valor, OrigenDato(origen), anclado, float(confianza))
    return campos


def _validar_perfil(perfil: Perfil) -> None:
    if not isinstance(perfil.tipo, str) or perfil.tipo not in TIPOS_PERFIL:
        raise DatoNoValido(f"tipo: se espera uno de {', '.join(TIPOS_PERFIL)}")
    _texto(perfil.dominio, "dominio", MAX_DOMINIO)
    url = _texto(perfil.url_busqueda, "url_busqueda", MAX_URL)
    if perfil.es_listado:
        # Nota 16 §A2: la URL del listado, sin `{consulta}` y con `{pagina}` como mucho una vez.
        if url.count("{pagina}") > 1 or _llaves(url.replace("{pagina}", "", 1)):
            raise DatoNoValido("url_busqueda: un listado no lleva {consulta} y {pagina} va una vez")
    elif perfil.es_sitemap and _llaves(url):
        raise DatoNoValido("url_busqueda: la URL del sitemap no lleva plantilla")
    if perfil.es_sitemap:
        # Nota 16 §B: se busca en el catálogo local y no hay listado que leer.
        if perfil.sel_resultado or perfil.sel_titulo or perfil.sel_enlace:
            raise DatoNoValido("sel_resultado, sel_titulo y sel_enlace: un sitemap no los usa")
        if perfil.sel_precio_listado is not None:
            raise DatoNoValido("sel_precio_listado: un sitemap no lo usa")
    # Un perfil `api_*` o `sitemap` no lee un listado HTML: sus selectores pueden ir vacíos. Uno
    # `html` o `listado` sin selectores no encontraría nunca nada.
    sin_selectores = perfil.es_api or perfil.es_sitemap
    _texto(perfil.sel_resultado, "sel_resultado", MAX_SELECTOR, vacio=sin_selectores)
    _texto(perfil.sel_titulo, "sel_titulo", MAX_SELECTOR, vacio=sin_selectores)
    _texto(perfil.sel_enlace, "sel_enlace", MAX_SELECTOR, vacio=sin_selectores)
    if perfil.sel_precio_listado is not None:
        _texto(perfil.sel_precio_listado, "sel_precio_listado", MAX_SELECTOR)
    if not isinstance(perfil.sel_campos, dict):
        raise DatoNoValido("sel_campos: se espera un dict")
    for clave, selector in perfil.sel_campos.items():
        # Solo campos de `CAMPOS`: una clave como «titulo» taparía en `Tienda.versiones` el
        # selector real del listado, que es lo que revisa una persona (memoria §4).
        if clave not in CLAVES_CAMPOS:
            raise DatoNoValido(f"sel_campos: campo desconocido {clave!r}")
        _texto(selector, f"sel_campos[{clave}]", MAX_SELECTOR)
    _texto(perfil.autor, "autor", MAX_AUTOR)
    _texto(perfil.nota, "nota", MAX_NOTA, vacio=True)
    _fecha(perfil.creado, "creado")


def _llaves(texto: str) -> bool:
    return "{" in texto or "}" in texto


def _perfil(fila: sqlite3.Row) -> Perfil:
    campos = json.loads(fila["sel_campos"])
    if not isinstance(campos, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in campos.items()
    ):
        raise ErrorAlmacen(f"sel_campos corrupto en el perfil {fila['dominio']}")
    return Perfil(
        dominio=fila["dominio"],
        version=fila["version"],
        url_busqueda=fila["url_busqueda"],
        sel_resultado=fila["sel_resultado"],
        sel_titulo=fila["sel_titulo"],
        sel_enlace=fila["sel_enlace"],
        sel_precio_listado=fila["sel_precio_listado"],
        sel_campos=campos,
        necesita_js=bool(fila["necesita_js"]),
        autor=fila["autor"],
        revisado=bool(fila["revisado"]),
        creado=datetime.fromisoformat(fila["creado"]),
        nota=fila["nota"],
        tipo=fila["tipo"],
    )


_TIPOS_LEGIBLES = {
    TIPO_LISTADO: "listado (sin buscador: se recorre el listado de la URL)",
    TIPO_SITEMAP: "sitemap (sin buscador: catálogo de URL del sitemap)",
}


def _version_perfil(perfil: Perfil) -> VersionPerfil:
    # La web enseña estos pares a quien revisa el perfil: en uno `api_*` o `sitemap` lo que hay
    # que revisar es el tipo (y la URL de la API o del sitemap), no unos selectores vacíos.
    selectores: dict[str, str] = {}
    if perfil.tipo != TIPO_HTML:
        selectores["tipo"] = _TIPOS_LEGIBLES.get(perfil.tipo, perfil.tipo)
    for nombre, selector in (
        ("resultado", perfil.sel_resultado),
        ("titulo", perfil.sel_titulo),
        ("enlace", perfil.sel_enlace),
    ):
        if selector or not (perfil.es_api or perfil.es_sitemap):
            selectores[nombre] = selector
    if perfil.sel_precio_listado is not None:
        selectores["precio_listado"] = perfil.sel_precio_listado
    selectores.update(perfil.sel_campos)
    return VersionPerfil(
        numero=perfil.version,
        fecha=perfil.creado,
        autor=f"{perfil.autor} · revisado" if perfil.revisado else perfil.autor,
        url_busqueda=perfil.url_busqueda,
        selectores=selectores,
        # Solo se guardan perfiles que han pasado el validador (configurador y reparador
        # validan antes de devolver), así que toda versión guardada está validada.
        validado=True,
        nota=perfil.nota,
    )


_SELECT_TIENDAS = (
    "SELECT t.*, f.intentos AS fallo_intentos, f.fecha AS fallo_fecha, f.motivo AS fallo_motivo,"
    " f.reintentar_desde AS fallo_reintentar FROM tiendas t"
    " LEFT JOIN configuraciones_fallidas f ON f.dominio = t.dominio"
)
"""Las tiendas con su última configuración fallida, si la hay (A1)."""
_SELECT_TIENDAS_SIN_FALLOS = "SELECT t.*, NULL AS fallo_intentos FROM tiendas t"


def _tienda(fila: sqlite3.Row, versiones: Sequence[VersionPerfil]) -> Tienda:
    fallo = None
    if fila["fallo_intentos"] is not None:
        fecha = _leer_fecha(fila["fallo_fecha"])
        assert fecha is not None
        fallo = FalloConfiguracion(
            intentos=fila["fallo_intentos"],
            fecha=fecha,
            motivo=fila["fallo_motivo"],
            reintentar_desde=_leer_fecha(fila["fallo_reintentar"]),
        )
    return Tienda(
        dominio=fila["dominio"],
        nombre=fila["nombre"],
        estado=EstadoPerfil(fila["estado"]),
        robots=fila["robots"],
        robots_permite=bool(fila["robots_permite"]),
        ultima_visita=_leer_fecha(fila["ultima_visita"]),
        bloqueos=fila["bloqueos"],
        versiones=tuple(versiones),
        entrada=fila["entrada"],
        fallo=fallo,
    )


def _codificar_ajuste(clave: str, valor: object, defecto: object) -> str:
    # El tipo lo marca el valor por defecto, igual que en `_decodificar_ajuste`: si se aceptara
    # otro (p. ej. 5.0 en un entero, desde un formulario), `leer_ajustes` fallaría para siempre.
    if isinstance(defecto, Decimal):
        return json.dumps(_importe(valor, clave))
    if isinstance(defecto, bool):
        if not isinstance(valor, bool):
            raise DatoNoValido(f"{clave}: se espera un booleano")
        return json.dumps(valor)
    if isinstance(defecto, int):
        return json.dumps(_entero(valor, clave))
    if isinstance(defecto, float):
        if (
            not isinstance(valor, int | float)
            or isinstance(valor, bool)
            or not math.isfinite(valor)
            or valor < 0
        ):
            raise DatoNoValido(f"{clave}: se espera un número finito y no negativo")
        return json.dumps(float(valor))
    return json.dumps(_texto(valor, clave, MAX_AJUSTE_TEXTO, vacio=True), ensure_ascii=False)


def _decodificar_ajuste(clave: str, texto: str, defecto: object) -> object:
    error = ErrorAlmacen(f"Ajuste {clave} corrupto en la base de datos")
    try:
        valor = json.loads(texto)
    except json.JSONDecodeError as e:
        raise error from e
    # El tipo esperado es el del valor por defecto de `Ajustes`.
    if isinstance(defecto, Decimal):
        if not isinstance(valor, str):
            raise error
        try:
            importe = Decimal(valor)
        except InvalidOperation as e:
            raise error from e
        if not importe.is_finite():
            raise error
        return importe
    if isinstance(defecto, bool):
        if not isinstance(valor, bool):
            raise error
        return valor
    if isinstance(defecto, int):
        if not isinstance(valor, int) or isinstance(valor, bool):
            raise error
        return valor
    if isinstance(defecto, float):
        if not isinstance(valor, int | float) or isinstance(valor, bool):
            raise error
        return float(valor)
    if not isinstance(valor, str):
        raise error
    return valor
