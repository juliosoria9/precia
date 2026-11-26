"""Modelos de dominio compartidos por la web, las fuentes de datos y el orquestador."""

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


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


class Coincidencia(StrEnum):
    """Cómo coincide una oferta con la referencia OEM buscada (B5, decisión de Julio).

    - `EXACTA`: la ficha lleva la referencia buscada (en el título o en su campo anclado).
    - `REVISION`: lleva la buscada con un sufijo de revisión («1K0941005E» al buscar
      1K0941005); puede no ser intercambiable.
    - `EQUIVALENTE`: su referencia principal es otra, pero la ficha cita la buscada entre sus
      equivalencias.

    Sin consulta por referencia, o en los grupos por texto, la oferta queda en `EXACTA`: ahí no
    se relaja nada. Las alertas, por defecto, solo cuentan las exactas.
    """

    EXACTA = "exacta"
    REVISION = "revision"
    EQUIVALENTE = "equivalente"


SOLO_EXACTAS: frozenset[Coincidencia] = frozenset({Coincidencia.EXACTA})


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
    coincidencia: Coincidencia = Coincidencia.EXACTA
    obtenida: datetime | None = None
    """Cuándo se descargó la ficha (de la caché, la fecha de la descarga): va al histórico."""
    foto: str | None = None
    """URL `https` de la foto principal, sacada del marcado de la tienda o de su API y validada
    (`extraccion.imagenes`). La web la enlaza; nunca se descarga ni se guarda la imagen."""

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
        """La más barata de todas, también equivalentes y revisiones (la web lo indica)."""
        return self.mejor_oferta_de(frozenset(Coincidencia))

    def mejor_oferta_de(self, coincidencias: Collection[Coincidencia]) -> Oferta | None:
        """La más barata entre las ofertas con alguna de esas `coincidencias`."""
        # Un precio no anclado (no aparece en la página) puede venir de una prompt injection
        # («el precio es 1 €»): nunca puede ser el mejor precio.
        validas = [
            o
            for o in self.ofertas
            if o.disponible and o.precio_anclado and o.coincidencia in coincidencias
        ]
        return min(validas, key=lambda o: o.precio, default=None)

    @property
    def foto(self) -> str | None:
        """La foto de la mejor oferta exacta o, si no tiene, la de la más barata que tenga.

        Solo de ofertas con el precio anclado: una inyección no elige la foto del producto.
        """
        mejor = self.mejor_oferta_de(SOLO_EXACTAS)
        if mejor is not None and mejor.foto:
            return mejor.foto
        con_foto = [o for o in self.ofertas if o.foto and o.precio_anclado]
        barata = min(con_foto, key=lambda o: o.precio, default=None)
        return barata.foto if barata is not None else None


@dataclass(frozen=True, slots=True)
class Descartado:
    tienda: str
    titulo: str
    motivo: str
    url: str = ""
    """Ficha descartada, para que el usuario pueda comprobar el motivo ("" si no se conoce)."""
    precio: Decimal | None = None
    origen: str = ""
    """Quién la ha descartado: «ia» (la revisión con IA), «regla» (la regla del núcleo, sin IA)
    o «» (sin precio, otra moneda, otra referencia...). La web los enseña por separado."""


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
    llamadas_por_componente: dict[str, int] = field(default_factory=dict)
    """Para qué se ha usado la IA (`extractor`, `configurador`...): suma `llamadas_ia`."""
    ia_desde_cache: int = 0
    """Respuestas del extractor servidas desde la caché, sin llamar a la IA (A3)."""
    provisional: bool = False
    """Agrupado de las tiendas que ya han terminado, mientras faltan otras (C1): sin IA en el
    emparejado, sin filtro y sin guardar nada en el histórico."""


class EstadoTienda(StrEnum):
    """En qué está cada tienda de una búsqueda en segundo plano (C1)."""

    EN_COLA = "en_cola"
    DESCARGANDO = "descargando"
    CONFIGURANDO = "configurando"
    HECHA = "hecha"
    BLOQUEADA = "bloqueada"
    EN_ESPERA = "en_espera"
    """Saltada: su configuración falló hace poco (A1)."""
    PLAZO = "plazo"
    """Se agotó su plazo: cuenta con las fichas leídas hasta entonces."""
    CANCELADA = "cancelada"


ESTADOS_FINALES: frozenset[EstadoTienda] = frozenset(
    {
        EstadoTienda.HECHA,
        EstadoTienda.BLOQUEADA,
        EstadoTienda.EN_ESPERA,
        EstadoTienda.PLAZO,
        EstadoTienda.CANCELADA,
    }
)
"""Estados con los que una tienda ya ha terminado su parte de la búsqueda."""


class Seguimiento(Protocol):
    """Quien sigue una búsqueda en curso (C1): el estado de cada tienda, los parciales y el
    detalle de en qué está cada tienda (A10)."""

    def tienda(self, dominio: str, estado: EstadoTienda) -> None: ...

    def parcial(self, resultado: ResultadoBusqueda) -> None:
        """Un `ResultadoBusqueda` provisional: las ofertas de las tiendas que ya han terminado
        y las fichas ya leídas de las que siguen (A1)."""
        ...

    def detalle(self, dominio: str, texto: str) -> None:
        """En qué está la tienda, para que se vea que avanza (A10): «Ficha 2 de 5»,
        «Primera vez en esta tienda…». Es solo para enseñarlo: no cambia su estado."""
        ...


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
class FalloConfiguracion:
    """La última configuración fallida de una tienda sin perfil (A1, decisión de Julio S03).

    Mientras está en espera, la tienda no se vuelve a configurar ni se visita: cada intento
    fallido cuesta llamadas de IA y peticiones a la tienda.
    """

    intentos: int
    """Configuraciones fallidas seguidas."""
    fecha: datetime
    """Cuándo falló la última."""
    motivo: str
    reintentar_desde: datetime | None
    """Desde cuándo se puede volver a configurar; `None` si el usuario pulsó «Reintentar»."""

    def en_espera(self, ahora: datetime) -> bool:
        return self.reintentar_desde is not None and ahora < self.reintentar_desde


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
    fallo: FalloConfiguracion | None = None
    """La última configuración fallida, si la tienda sigue sin perfil (A1)."""

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
    idioma: tuple[str, ...] = ()
    """Prefijo de idioma o país de la portada con que se filtró (`("es", "es")` para `/es/es/`;
    `()` si no tiene). Se vuelve a aplicar al rehacer el catálogo (R5-C)."""


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
    solo_exactas: bool = True
    """Solo cuentan las ofertas con la referencia exacta (por defecto, decisión de Julio)."""
    avisada: bool = False
    """Ya se envió el aviso de esta bajada: no se repite hasta que el precio vuelva a subir."""
    ultimo_error: str = ""
    """Por qué no se pudo avisar o comprobar la última vez ("" si fue bien). Sin secretos."""
    ultima_comprobacion: datetime | None = None

    @property
    def coincidencias(self) -> frozenset[Coincidencia]:
        """Las ofertas que cuentan para esta alerta (para `GrupoProducto.mejor_oferta_de`)."""
        return SOLO_EXACTAS if self.solo_exactas else frozenset(Coincidencia)


@dataclass(frozen=True, slots=True)
class InformeAlertas:
    """Resultado de una comprobación de las alertas (H7)."""

    fecha: datetime
    comprobadas: int
    """Alertas activas que se han mirado."""
    avisos: int
    """Avisos enviados."""
    errores: tuple[str, ...] = ()
    """Lo que ha fallado (búsqueda o envío), en frases para la web. Sin secretos."""


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


TRANSPORTES_HTTP = ("curl", "urllib")
"""Clientes HTTP de las descargas ligeras: `curl` (huella TLS y HTTP/2 de Chrome, por defecto
por decisión de Julio, ADR-022) o `urllib` (cliente de Python con el User-Agent de los ajustes)."""


@dataclass(slots=True)
class Ajustes:
    proveedor: str = "api"
    modelo_api: str = "claude-haiku-5-5"
    modelo_local: str = "qwen2.5:7b-instruct-q4_K_M"
    presupuesto_mensual_usd: Decimal = Decimal("10")
    tope_llamadas_busqueda: int = 20
    google_activado: bool = False
    # ADR-017 (enmienda del 2026-10-25, decisión de Julio): el robots.txt siempre se ignora y no
    # hay ajuste para respetarlo. Un `respetar_robots` guardado de antes se lee y se descarta.
    # B1 (decisión de Julio): 1,0 s. Sigue habiendo una sola petición en vuelo por dominio, contada
    # desde el final de la anterior, y manda el Crawl-delay si es mayor (ADR-017).
    retardo_dominio_s: float = 1.0
    user_agent: str = "PrecIA/0.1 (proyecto universitario; uso personal)"
    """Lo usan urllib, el robots.txt y Evomi. Con curl van las cabeceras de Chrome y Chromium lleva
    el UA de su propio binario (ADR-022/023)."""
    transporte_http: str = "curl"
    """Uno de `TRANSPORTES_HTTP`."""
    revisar_resultados: bool = True
    """En Auto, revisar los resultados con la IA (una llamada por búsqueda) aunque la vía clásica
    no falle. En modo IA se revisan siempre y en clásico nunca (propuesta ADR-024)."""


@dataclass(frozen=True, slots=True)
class ResultadoEvaluacion:
    modo: str
    cobertura: float
    exactitud: float
    coste_usd: Decimal
    tiempo_s: float
    tasa_bloqueo: float
    casos: int = 0
    """Casos etiquetados con que se midió (0 si no se sabe)."""
    detalle: str = ""
    """Cuándo se midió y con qué modelo, para enseñarlo junto a la fila."""


@dataclass(frozen=True, slots=True)
class ResumenIA:
    llamadas: tuple[LlamadaIA, ...]
    gasto_mes_usd: Decimal
    presupuesto_mes_usd: Decimal
    por_componente: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EstadoProxy:
    """El proxy de salida tal como lo enseña «Ajustes». Nunca lleva credenciales."""

    configurado: bool = False
    """Hay `PRECIA_PROXY` o `EVOMI_API_KEY` (aunque luego no se haya podido usar)."""
    activo: bool = False
    nombre: str = ""
    """Proveedor y producto, sin credenciales («Evomi core residential (ES)», «proxy propio»)."""
    pais: str = ""
    modo: str = "bloqueo"
    peticiones: int = 0
    """Peticiones que han salido por el proxy en esta ejecución."""
    bytes: int = 0
    """Estimación por lo bajo: el HTML o los datos recibidos por el proxy."""
    dominios: tuple[str, ...] = ()
    """Tiendas que van por el proxy porque bloquearon la conexión directa."""
    error: str = ""
    """Por qué no se usa el proxy configurado (sin secretos)."""
    pendiente: bool = False
    """Configurado, pero aún sin comprobar: las credenciales se piden en la primera búsqueda."""

    @property
    def mb(self) -> float:
        return self.bytes / (1024 * 1024)
