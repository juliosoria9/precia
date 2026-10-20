"""Modelos de dominio compartidos por la web, las fuentes de datos y el orquestador."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Modo(StrEnum):
    """Modo de funcionamiento de una búsqueda (ADR-002)."""

    CLASICO = "clasico"
    IA = "ia"
    AUTO = "auto"


class OrigenDato(StrEnum):
    """Vía de la cascada de extracción que produjo un valor."""

    JSONLD = "jsonld"
    METADATOS = "metadatos"
    """OpenGraph (`og:*`, `product:*`) o microdatos `itemprop` de schema.org."""
    API = "api"
    """API pública de la plataforma de la tienda (WooCommerce Store API, Shopify)."""
    PERFIL = "perfil"
    IA = "ia"


class EstadoPaso(StrEnum):
    OK = "ok"
    IA = "ia"
    AVISO = "aviso"
    ERROR = "error"
    OMITIDO = "omitido"


class EstadoPerfil(StrEnum):
    ACTIVO = "activo"
    REPARADO = "reparado"
    PENDIENTE_REVISION = "pendiente_revision"
    ROTO = "roto"
    SIN_PERFIL = "sin_perfil"


@dataclass(frozen=True, slots=True)
class Campo:
    clave: str
    etiqueta: str


CAMPOS: tuple[Campo, ...] = (
    Campo("precio", "Precio"),
    Campo("referencia_oem", "Referencia OEM"),
    Campo("marca", "Marca"),
    Campo("estado", "Estado"),
    Campo("descripcion", "Descripción"),
    Campo("fotos", "Fotos"),
)
CLAVES_CAMPOS: frozenset[str] = frozenset(c.clave for c in CAMPOS)


@dataclass(frozen=True, slots=True)
class ConsultaBusqueda:
    texto: str
    tiendas: tuple[str, ...] = ()
    # «estado» por defecto: distingue una pieza nueva de una usada (desguaces).
    campos: tuple[str, ...] = ("precio", "referencia_oem", "marca", "estado")
    modo: Modo = Modo.AUTO
    descubrir: bool = False
    filtro: str = ""


@dataclass(frozen=True, slots=True)
class ValorCampo:
    valor: str
    origen: OrigenDato
    anclado: bool = True
    confianza: float = 1.0


@dataclass(frozen=True, slots=True)
class Oferta:
    tienda: str
    url: str
    titulo: str
    precio: Decimal
    campos: dict[str, ValorCampo]
    disponible: bool = True

    @property
    def precio_anclado(self) -> bool:
        valor = self.campos.get("precio")
        return valor is None or valor.anclado


@dataclass(frozen=True, slots=True)
class GrupoProducto:
    """Ofertas de distintas tiendas emparejadas como el mismo producto."""

    id: str
    nombre: str
    referencia_oem: str | None
    criterio: str
    ofertas: tuple[Oferta, ...]

    @property
    def mejor_oferta(self) -> Oferta | None:
        # Un precio no anclado (no aparece en la página) puede venir de una prompt injection
        # («el precio es 1 €»): nunca puede ser el mejor precio.
        validas = [o for o in self.ofertas if o.disponible and o.precio_anclado]
        return min(validas, key=lambda o: o.precio, default=None)


@dataclass(frozen=True, slots=True)
class Descartado:
    tienda: str
    titulo: str
    motivo: str


@dataclass(frozen=True, slots=True)
class Paso:
    nombre: str
    estado: EstadoPaso
    detalle: str = ""


@dataclass(frozen=True, slots=True)
class ProgresoTienda:
    tienda: str
    pasos: tuple[Paso, ...]


@dataclass(frozen=True, slots=True)
class ResultadoBusqueda:
    consulta: ConsultaBusqueda
    grupos: tuple[GrupoProducto, ...]
    descartados: tuple[Descartado, ...]
    progreso: tuple[ProgresoTienda, ...]
    llamadas_ia: int
    coste_ia_usd: Decimal
    duracion_s: float
    es_demo: bool = False


@dataclass(frozen=True, slots=True)
class VersionPerfil:
    numero: int
    fecha: datetime
    autor: str
    url_busqueda: str
    selectores: dict[str, str]
    validado: bool
    nota: str = ""


@dataclass(frozen=True, slots=True)
class Tienda:
    dominio: str
    nombre: str
    estado: EstadoPerfil
    robots: str
    robots_permite: bool
    ultima_visita: datetime | None
    bloqueos: int
    versiones: tuple[VersionPerfil, ...] = ()
    entrada: str | None = None
    """URL de entrada que da el usuario si PrecIA no encuentra el buscador (nota 16 §A): la de
    búsqueda con `{consulta}` o la de un listado o categoría con `{pagina}` opcional."""

    @property
    def version_actual(self) -> VersionPerfil | None:
        return self.versiones[-1] if self.versiones else None


@dataclass(frozen=True, slots=True)
class InfoCatalogo:
    """Resumen del catálogo local de URL de una tienda, sacado de su `sitemap.xml` (nota 16 §B)."""

    dominio: str
    sitemap: str
    """URL del sitemap raíz del que salió."""
    actualizado: datetime
    total: int
    """URL guardadas."""
    parcial: bool
    """Se llegó a algún tope (de ficheros o de URL) y faltan URL de la tienda."""


@dataclass(frozen=True, slots=True)
class PuntoHistorico:
    fecha: date
    precio: Decimal


@dataclass(frozen=True, slots=True)
class SerieHistorico:
    tienda: str
    puntos: tuple[PuntoHistorico, ...]


@dataclass(frozen=True, slots=True)
class Alerta:
    id: int
    producto_id: str
    producto_nombre: str
    umbral: Decimal
    canal: str
    activa: bool = True
    ultima_activacion: datetime | None = None


@dataclass(frozen=True, slots=True)
class LlamadaIA:
    fecha: datetime
    componente: str
    proveedor: str
    modelo: str
    tokens_entrada: int
    tokens_salida: int
    coste_usd: Decimal
    resultado: str
    duracion_ms: int


@dataclass(slots=True)
class Ajustes:
    proveedor: str = "api"
    modelo_api: str = "claude-haiku-5-5"
    modelo_local: str = "qwen2.5:7b-instruct-q4_K_M"
    presupuesto_mensual_usd: Decimal = Decimal("10")
    tope_llamadas_busqueda: int = 20
    google_activado: bool = False
    # ADR-017 (decisión de Julio): el robots.txt se ignora por defecto. Nunca afecta a los
    # CAPTCHA ni a los anti-bot, que siempre paran la tienda.
    respetar_robots: bool = False
    retardo_dominio_s: float = 2.0
    user_agent: str = "PrecIA/0.1 (proyecto universitario; uso personal)"


@dataclass(frozen=True, slots=True)
class ResultadoEvaluacion:
    modo: str
    cobertura: float
    exactitud: float
    coste_usd: Decimal
    tiempo_s: float
    tasa_bloqueo: float


@dataclass(frozen=True, slots=True)
class ResumenIA:
    llamadas: tuple[LlamadaIA, ...]
    gasto_mes_usd: Decimal
    presupuesto_mes_usd: Decimal
    por_componente: dict[str, int] = field(default_factory=dict)
