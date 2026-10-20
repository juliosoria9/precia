"""Almacén de PrecIA sobre SQLite: tiendas, perfiles, histórico, alertas, ajustes y llamadas de IA.

También guarda el catálogo local de URL de las tiendas sin buscador, sacado de su `sitemap.xml`
(nota 16 §B): solo la URL, las palabras de su slug y el `lastmod`, nunca precios ni descripciones.

Todas las consultas son parametrizadas: los textos vienen de webs (no confiables) o de la IA y
nunca se pegan en el SQL. Los importes se guardan como TEXT con `str(Decimal)` para no pasar
nunca por coma flotante, y las fechas en ISO 8601.

El esquema evoluciona con `MIGRACIONES`: cada entrada es un script y `PRAGMA user_version`
guarda cuántas se han aplicado. Una migración se aplica entera o no se aplica.
"""

import json
import math
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple, Self
from urllib.parse import urlsplit

from precia.emparejado.referencias import referencias_en
from precia.emparejado.texto import normalizar_titulo
from precia.nucleo.modelos import (
    CLAVES_CAMPOS,
    Ajustes,
    Alerta,
    EstadoPerfil,
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
MAX_ETIQUETA_IA = 100
MAX_RESULTADO_IA = 500
MAX_AJUSTE_TEXTO = 500
MAX_LIMITE_LLAMADAS = 10_000
MAX_VALOR_CAMPO = 5_000
"""Lo que admite `extraccion.ficha` en un valor (`MAX_VALOR`)."""
MAX_CRITERIO = 200
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
_PUERTOS = {"http": 80, "https": 443}


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


def _url_de_la_tienda(valor: object, campo: str, dominio: str, maximo: int) -> str:
    """`valor` es una URL http(s) absoluta del host de la tienda (con o sin `www.`).

    El almacén no guarda nada que apunte a otro servidor: las URL del catálogo vienen de un
    sitemap (dato de la web) y se descargan después. No sustituye a `validar_url` (anti-SSRF),
    que se aplica siempre al descargar.
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
        or partes.hostname not in (dominio, f"www.{dominio}")
    ):
        raise error
    return texto


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
            filas = self._consultar("SELECT * FROM tiendas ORDER BY dominio")
            versiones: dict[str, list[VersionPerfil]] = {}
            for perfil in self._perfiles("SELECT * FROM perfiles ORDER BY dominio, version"):
                versiones.setdefault(perfil.dominio, []).append(_version_perfil(perfil))
        return [_tienda(f, versiones.get(f["dominio"], [])) for f in filas]

    def tienda(self, dominio: str) -> Tienda | None:
        with self._cerrojo:
            filas = self._consultar("SELECT * FROM tiendas WHERE dominio = ?", (dominio,))
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
    ) -> InfoCatalogo:
        """Sustituye todo el catálogo de la tienda, en una transacción, y devuelve su resumen.

        `entradas` son `EntradaCatalogo` o tuplas `(url, palabras, lastmod)`. Cada URL (y el
        `sitemap`) tiene que ser http(s) del host de la tienda, y `palabras` se normaliza igual
        que la consulta de `buscar_catalogo`. Una entrada no válida es `DatoNoValido` y no
        cambia nada. Las URL repetidas cuentan una vez. Se guardan como mucho `MAX_CATALOGO`:
        si `entradas` trae más, el resto no se lee y el catálogo queda `parcial`.
        """
        _texto(dominio, "dominio", MAX_DOMINIO)
        _url_de_la_tienda(sitemap, "sitemap", dominio, MAX_URL)
        parcial = bool(parcial)
        filas: list[tuple[str, str, str, str | None]] = []
        vistas: set[str] = set()
        for entrada in entradas:
            if len(filas) >= MAX_CATALOGO:
                parcial = True
                break
            if not isinstance(entrada, tuple) or len(entrada) != 3:
                raise DatoNoValido("entradas: se espera (url, palabras, lastmod)")
            url, palabras, lastmod = entrada
            _url_de_la_tienda(url, "url", dominio, MAX_URL)
            normalizadas = _palabras_catalogo(palabras)
            if lastmod is not None:
                _texto(lastmod, "lastmod", MAX_LASTMOD)
            if url not in vistas:
                vistas.add(url)
                filas.append((dominio, url, normalizadas, lastmod))
        info = InfoCatalogo(dominio, sitemap, self._reloj(), len(filas), parcial)
        actualizado = _fecha(info.actualizado, "actualizado")
        with self._transaccion() as con:
            if not con.execute("SELECT 1 FROM tiendas WHERE dominio = ?", (dominio,)).fetchone():
                raise TiendaNoEncontrada(dominio)
            con.execute("DELETE FROM catalogo WHERE dominio = ?", (dominio,))
            con.executemany(
                "INSERT INTO catalogo (dominio, url, palabras, lastmod) VALUES (?, ?, ?, ?)", filas
            )
            con.execute(
                "INSERT INTO catalogos (dominio, sitemap, actualizado, total, parcial)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (dominio) DO UPDATE SET"
                " sitemap = excluded.sitemap, actualizado = excluded.actualizado,"
                " total = excluded.total, parcial = excluded.parcial",
                (dominio, sitemap, actualizado, info.total, int(parcial)),
            )
        return info

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
        )

    def buscar_catalogo(self, dominio: str, consulta: str, *, limite: int) -> list[str]:
        """Las URL del catálogo que encajan con la consulta, como mucho `limite`.

        Encajan las que tienen en sus palabras todas las de la consulta (normalizadas, de 3
        letras o más, sin las vacías como «para») o alguna de sus referencias OEM, comparada sin
        espacios («1k0 698 151» encaja con «1K0698151»). Si no hay ninguna, las que tienen más
        palabras de la consulta (las empatadas en el máximo). Una consulta sin palabras ni
        referencias no devuelve nada. Orden: primero las de referencia y después por URL.
        """
        if _entero(limite, "limite") == 0 or limite > MAX_LIMITE_CATALOGO:
            raise DatoNoValido(f"limite: entre 1 y {MAX_LIMITE_CATALOGO}")
        if not isinstance(consulta, str):
            raise DatoNoValido("consulta: se espera un texto")
        palabras, referencias = _terminos_catalogo(consulta)
        if not palabras and not referencias:
            return []
        # Las expresiones solo repiten texto fijo; las palabras van siempre como parámetros.
        aciertos = " + ".join(["(palabras LIKE ? ESCAPE '\\')"] * len(palabras)) or "0"
        oem = " OR ".join(["(REPLACE(palabras, ' ', '') LIKE ? ESCAPE '\\')"] * len(referencias))
        filas = self._consultar(
            f"SELECT url, aciertos, oem FROM (SELECT url, {aciertos} AS aciertos,"
            f" {oem or '0'} AS oem FROM catalogo WHERE dominio = ?)"
            " WHERE aciertos > 0 OR oem ORDER BY oem DESC, aciertos DESC, url LIMIT ?",
            (
                *(_patron_like(p) for p in palabras),
                *(_patron_like(r) for r in referencias),
                dominio,
                limite,
            ),
        )
        completas = [f["url"] for f in filas if f["oem"] or f["aciertos"] == len(palabras)]
        if completas or not filas:
            return completas
        maximo = filas[0]["aciertos"]
        return [f["url"] for f in filas if f["aciertos"] == maximo]

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
    ) -> None:
        """Una oferta en el histórico. `titulo`, `campos` y `criterio` son los que enseña la web.

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
        _texto(titulo, "titulo", MAX_NOMBRE_PRODUCTO, vacio=True)
        _texto(criterio, "criterio", MAX_CRITERIO, vacio=True)
        texto_campos = _codificar_campos(campos or {})
        with self._transaccion() as con:
            con.execute(
                "INSERT INTO productos (id, nombre, referencia_oem, criterio) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET nombre = excluded.nombre,"
                " referencia_oem = COALESCE(excluded.referencia_oem, productos.referencia_oem),"
                " criterio = CASE WHEN excluded.criterio = '' THEN productos.criterio"
                " ELSE excluded.criterio END",
                (producto_id, nombre, referencia_oem, criterio),
            )
            con.execute(
                "INSERT INTO precios (producto_id, tienda, url, precio, fecha, dia, titulo,"
                " campos, disponible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    producto_id,
                    tienda,
                    url,
                    importe,
                    instante,
                    fecha.date().isoformat(),
                    titulo,
                    texto_campos,
                    int(bool(disponible)),
                ),
            )

    def ultimas_ofertas(self, producto_id: str) -> list[Oferta]:
        """Las ofertas de la última visita a cada tienda para el producto, por orden de tienda.

        Un grupo puede tener varias ofertas de la misma tienda (dos fichas parecidas): se
        devuelven todas las de su último guardado, una por URL, y no las de visitas anteriores.
        """
        filas = self._consultar(
            "SELECT p.tienda, p.url, p.precio, p.titulo, p.campos, p.disponible,"
            " o.nombre AS producto FROM precios p JOIN productos o ON o.id = p.producto_id"
            " WHERE p.producto_id = ? AND p.fecha = ("
            " SELECT MAX(q.fecha) FROM precios q WHERE q.producto_id = p.producto_id"
            " AND q.tienda = p.tienda) AND p.id = ("
            " SELECT MAX(r.id) FROM precios r WHERE r.producto_id = p.producto_id"
            " AND r.tienda = p.tienda AND r.fecha = p.fecha AND r.url = p.url)"
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
        """Una serie por tienda (por orden alfabético) con el último precio de cada día."""
        filas = self._consultar(
            "SELECT tienda, dia, precio FROM precios WHERE producto_id = ?"
            " ORDER BY tienda, dia, fecha, id",
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

    def crear_alerta(self, producto_id: str, umbral: Decimal, canal: str) -> Alerta:
        texto_umbral = _importe(umbral, "umbral", positivo=True)
        _texto(canal, "canal", MAX_CANAL)
        with self._transaccion() as con:
            fila = con.execute("SELECT nombre FROM productos WHERE id = ?", (producto_id,))
            producto = fila.fetchone()
            if producto is None:
                raise ProductoNoEncontrado(producto_id)
            cursor = con.execute(
                "INSERT INTO alertas (producto_id, umbral, canal) VALUES (?, ?, ?)",
                (producto_id, texto_umbral, canal),
            )
            identificador = cursor.lastrowid
        assert identificador is not None
        return Alerta(identificador, producto_id, str(producto[0]), Decimal(texto_umbral), canal)

    def alertas(self) -> list[Alerta]:
        filas = self._consultar(
            "SELECT a.id, a.producto_id, p.nombre, a.umbral, a.canal, a.activa,"
            " a.ultima_activacion FROM alertas a JOIN productos p ON p.id = a.producto_id"
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
            )
            for f in filas
        ]

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


def _tienda(fila: sqlite3.Row, versiones: Sequence[VersionPerfil]) -> Tienda:
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
