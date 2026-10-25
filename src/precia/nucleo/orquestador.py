"""Orquestador de una búsqueda (memoria §5, nota 14): tiendas → perfiles → fichas → emparejado.

Por cada tienda elegida, en paralelo (la cortesía por dominio la pone el navegador):

1. Perfil vigente del almacén. Si no hay, en modo Auto o IA lo crea el configurador IA; se
   guarda pendiente de revisión (`revisado=False`) y se usa de forma provisional. Se configura
   con una consulta genérica de prueba (`CONSULTAS_CONFIGURACION`, A1) y luego se busca la del
   usuario. Si la configuración falla, se recuerda en el almacén y la tienda queda en espera
   (`ESPERAS_CONFIGURACION`: 1 h y luego 1 día): mientras tanto no se visita y la búsqueda lo
   avisa en un paso general «en espera», hasta que pasa la espera o el usuario pulsa
   «Reintentar».
2. Búsqueda con la URL del perfil y extracción del listado. Si los selectores no dan nada pero
   la página trae los resultados como datos (`extraccion.embebidos.listado_estructurado`:
   JSON-LD `ItemList`, `__NEXT_DATA__`...), se usan los que nombran la consulta (los
   «destacados» que trae cualquier página no son resultados) con un aviso y el perfil no se da
   por roto mientras así salgan ofertas con precio (nota 15 §D). Si no, y la página tiene productos
   con precio (o un selector no es válido), el perfil está roto: en clásico se marca como roto;
   en Auto o IA el reparador IA propone una versión nueva.
3. Fichas de los resultados que nombran la consulta (`_para_abrir`, A2), primero los que llevan
   la referencia y después los que nombran más palabras, como mucho `max_fichas`. Si ninguno la
   nombra (una tienda que ignora la búsqueda y enseña sus destacados), «sin resultados» sin
   abrir ninguna ficha. Con referencia OEM y ningún resultado que la nombre, los `max_fichas`
   primeros (`RESPALDO_OEM`): hay tiendas que solo la citan en la ficha. Con una consulta de
   texto, también los primeros si la página confirma que es su búsqueda (la repiten el campo de
   búsqueda y el `<title>` o el `<h1>`) y va a haber emparejado con IA (`RESPALDO_TEXTO`, R3-D):
   hay tiendas que titulan con la marca y el modelo, sin la categoría. Cada ficha: JSON-LD →
   metadatos → selectores → extractor de respaldo con IA (solo si se permite la IA) y anclaje de
   todo valor. El extractor (A3) guarda sus respuestas en el almacén con el TTL de la caché de
   páginas (`ttl_cache_ia`) y se apaga en la tienda si no deja ningún valor anclado en la
   primera ficha en que se usa: por eso las fichas de una tienda se extraen en serie. Deja de
   pedir un campo que no ha salido anclado en `MAX_FALLOS_CAMPO` fichas (A11, nunca el
   precio). Mientras se extrae una ficha ya se descarga la siguiente (A2), de una en una por la
   cortesía del navegador.

Un perfil `api_*` (WooCommerce, Shopify) busca en la API pública de la tienda, sin JavaScript, y
las ofertas salen directamente de su respuesta (`fuentes.apis.ofertas_api`): no se abre ninguna
ficha ni hay selectores que reparar. Se leen hasta `fuentes.apis.MAX_FICHAS` productos,
filtrados y ordenados igual que los resultados de un listado (la API busca también en la
descripción), y se quedan `max_fichas`. Una respuesta sin productos es «sin resultados».

Tiendas sin buscador (nota 16, ADR-018):

- Perfil `listado` (A2): se recorren como mucho `MAX_PAGINAS_LISTADO` páginas del listado que dio
  el usuario (solo la URL si no lleva `{pagina}`), parando en la primera que no trae resultados
  nuevos (vacía o repetida) o en cuanto hay `max_fichas` que nombran toda la consulta. Los
  resultados se filtran por la consulta como los datos estructurados (`_de_la_consulta`) y se
  abren como mucho `max_fichas` fichas. Un listado roto no lo repara la IA: se marca roto.
- Perfil `sitemap` (B): se busca en el catálogo local de URL de la tienda
  (`Almacen.buscar_catalogo`, por relevancia y con las páginas de marca o categoría al final:
  `fuentes.sitemap.es_listado`) y se abren esas fichas con la cascada de siempre. Si el catálogo
  tiene más de `REFRESCO_CATALOGO`, antes se rehace desde el sitemap
  (`perfiles.catalogo.leer_catalogo`, descargas grandes y sin caché); si eso falla, se avisa y
  se usa el anterior. Un bloqueo en un sitemap para la tienda como cualquier otro. Si el
  catálogo se quedó a medias por el tope de ficheros, antes de buscar se descargan los sitemaps
  hijos pendientes cuyo nombre nombra la consulta (como mucho `MAX_HIJOS_BUSQUEDA`, R2-C14b) y
  sus URL se añaden al catálogo; si el catálogo es parcial y nada casa, se avisa.

Después, el emparejado de todas las ofertas (como mucho `max_ofertas`) y el histórico, con la
fecha en que se descargó cada ficha (la de la caché si salió de ella, A3) y la búsqueda que la
ha visto (`visita`, A1).

Con un `Seguimiento` (C1), cada ficha leída manda un resultado provisional (A1 de velocidad,
S03): agrupado clásico, sin IA ni revisión, sin histórico y sin descartar nada, así que una
oferta vista no desaparece de un parcial al siguiente; la revisión con IA se hace una vez al
final. También recibe el detalle de en qué está cada tienda (`detalle`, A10).

Varias búsquedas a la vez (B6) comparten unos `TurnosDePerfil`: una tienda no se configura ni
se repara dos veces a la vez.

Modos (ADR-002): **clásico** nunca llama a la IA; **auto** solo cuando falla la vía clásica
(sin perfil, perfil roto, campos que faltan) o para aplicar el filtro; **IA**, además, empareja
siempre con IA. Sin servicio de IA (no hay clave) Auto e IA funcionan como el clásico y lo dicen.

Cada error se convierte en un paso legible del progreso de su tienda y nunca para las demás:
el robots.txt que prohíbe → aviso y se sigue (ADR-017); un bloqueo (CAPTCHA, anti-bot) → error,
se para y se cuenta, nunca se evade; un error de la IA → aviso y se sigue sin IA. Si el bloqueo
llega en las fichas, las que quedaban por abrir salen con el precio del listado ya descargado
(`DEL_LISTADO`, R3-H): solo con `sel_precio_listado`, si nombran la consulta y el precio sale
en la página, y nunca con un respaldo (ningún resultado nombra la consulta).

La IA no ejecuta nada: los selectores que propone los valida `perfiles` con el lenguaje
restringido de `extraccion.selectores`, y toda URL que se visita pasa por `validar_url`.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit

from precia.almacen.sqlite import (
    MAX_LIMITE_CATALOGO,
    MAX_MOTIVO_FALLO,
    TTL_REVISIONES_IA_S,
    Almacen,
    DatoNoValido,
    ErrorAlmacen,
)
from precia.emparejado.agrupador import InformeRevision, agrupar
from precia.emparejado.nucleo_nominal import pieza_de
from precia.emparejado.referencias import es_revision, referencias_de_consulta
from precia.emparejado.tipos import OfertaCandidata
from precia.extraccion.anclaje import normalizar
from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.embebidos import listado_estructurado
from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.listado import extraer_listado
from precia.extraccion.tipos import FichaExtraida, ResultadoListado, SelectorNoValido
from precia.fuentes.apis import AVISO_AGOTADO
from precia.fuentes.apis import MAX_FICHAS as MAX_FICHAS_API
from precia.fuentes.sitemap import (
    MAX_HIJOS_BUSQUEDA,
    es_listado,
    hijos_de_la_consulta,
    palabras_url,
)
from precia.ia.extractor import ExtractorRespaldoIA
from precia.ia.servicio import ServicioIA, SesionIA
from precia.ia.tipos import ErrorIA, PeticionIA, RespuestaIA
from precia.navegador.tipos import (
    Bloqueado,
    ErrorDescarga,
    Pagina,
    URLNoPermitida,
)
from precia.navegador.url import Validador, validar_url
from precia.nucleo.dominios import normalizar_dominio
from precia.nucleo.formato import fecha_hora
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    Descartado,
    EstadoPaso,
    EstadoPerfil,
    EstadoTienda,
    FalloConfiguracion,
    GrupoProducto,
    InfoCatalogo,
    Modo,
    OrigenDato,
    Paso,
    ProgresoTienda,
    ResultadoBusqueda,
    Seguimiento,
    Tienda,
    ValorCampo,
)
from precia.perfiles.catalogo import leer_catalogo, leer_hijos
from precia.perfiles.configurador import configurar
from precia.perfiles.descubrimiento import (
    MIN_PALABRA,
    NOMBRES_BUSQUEDA,
    consulta_en_url,
    nombra,
    palabras_de,
    parece_listado,
    solo_idioma,
)
from precia.perfiles.protocolos import Navegador as NavegadorDePerfiles
from precia.perfiles.reparador import reparar
from precia.perfiles.tipos import (
    TIPO_LISTADO,
    TIPO_SITEMAP,
    ConfiguracionFallida,
    Perfil,
    ReparacionFallida,
)
from precia.perfiles.validador import (
    NOMBRE_PLATAFORMA,
    PLATAFORMA_DE_TIPO,
    de_la_tienda,
    ofertas_de_api,
    url_de_busqueda,
)

MAX_FICHAS = 5
PLAZO_TIENDA_S = 120.0
"""Lo que puede tardar una tienda en una búsqueda (C1): luego cuenta con las fichas leídas.

Configurar una tienda nueva con la consulta genérica (A1) tiene su propio plazo, y si sale bien
la búsqueda del usuario empieza otro entero (R5-D): configurar ya lleva 80-90 s en tiendas
grandes. Una tienda nueva puede tardar así hasta dos plazos; las que ya tienen perfil, uno."""
MAX_PAGINAS_LISTADO = 5
"""Páginas de un perfil `listado` que se recorren como mucho en cada búsqueda (nota 16 §A2)."""
MAX_RESULTADOS_PAGINA = 200
"""Resultados que se leen de cada página de un listado (antes de filtrar por la consulta)."""
REFRESCO_CATALOGO = timedelta(days=7)
"""Un catálogo por sitemap más viejo que esto se rehace antes de buscar (nota 16 §B)."""
MARCA_PAGINA = "{pagina}"
LISTADO = "Listado de la tienda (sin buscador)"
CATALOGO = "Catálogo por sitemap"
MAX_CONSULTA = 500
"""Caracteres de la consulta que se miran al filtrar un listado (la web ya la recorta)."""
MAX_PALABRAS_CONSULTA = 12
MAX_OFERTAS = 60
"""Ofertas que llegan al agrupador: con más de 150 el emparejado ya es lento."""
GENERAL = "PrecIA"
"""Nombre del progreso con los avisos que no son de una tienda (IA, filtro, emparejado)."""
EN_ESPERA = "en espera"
"""Paso general con las tiendas saltadas porque su configuración falló hace poco (A1)."""
BLOQUEO = "Bloqueo (CAPTCHA o anti-bot): no se evade"
SIN_IA = "IA no disponible"
OTRA_CONFIGURO = (
    "Otra búsqueda simultánea estaba configurando la tienda: se usa el perfil que ha dejado"
)
"""Paso de una tienda sin perfil que otra búsqueda a la vez acababa de configurar (B6)."""
OTRA_REPARO = "otra búsqueda simultánea lo acaba de reparar; se usa"
OTRA_NO_REPARO = (
    "otra búsqueda simultánea acaba de intentar repararlo y no ha podido; no se repite ahora"
)
PRIMERA_VEZ = "Primera vez en esta tienda: suele tardar 40-90 s; las siguientes, unos 12 s"
"""Detalle del progreso (A10) mientras se configura una tienda sin perfil."""
ESPERANDO_OTRA = "Esperando a otra búsqueda que está configurando o reparando esta tienda"
DESCARGANDO_BUSQUEDA = "Descargando los resultados de la búsqueda"
LEYENDO_CATALOGO = "Leyendo el catálogo del sitemap"
REPARANDO = "La IA está reparando el perfil"
MAX_DETALLE = 300
MAX_FALLOS_CAMPO = 2
"""Fichas en que se pide un campo a la IA sin que salga anclado antes de dejar de pedirlo en
la tienda (A11)."""
CAMPOS_SIEMPRE = frozenset({"precio"})
"""Campos que se piden a la IA aunque no salgan anclados (A11): el precio es el de la oferta."""
PERMITE = "Permite la búsqueda"
IGNORADO = "Lo prohíbe; se ignora por decisión del usuario (ADR-017)"
INESPERADO = "Error inesperado: la tienda se salta en esta búsqueda (detalle en el registro)"
ESTRUCTURADOS = "Selectores sin resultados: se usan los datos estructurados de la página"
RESPALDO_OEM = (
    "Ningún resultado nombra la referencia en el título o el enlace: se abren los {n} primeros"
    " y el emparejado mira si la ficha la cita"
)
"""Paso de una búsqueda por referencia OEM en la que ningún resultado la nombra (A2): hay tiendas
que solo la citan en la ficha (Oscaro, entre sus equivalencias)."""
RESPALDO_TEXTO = (
    "Ningún resultado nombra la consulta en el título o el enlace, pero la página es su búsqueda"
    " (la repiten el campo de búsqueda y el título): se abren los {n} primeros y el emparejado"
    " con IA decide cuáles son lo buscado y cuáles accesorios u otra cosa"
)
"""Paso de una búsqueda de texto en la que ningún resultado la nombra, pero la página confirma
que es su búsqueda (`_confirma_la_busqueda`, R3-D): hay tiendas que titulan con la marca y el
modelo, sin la categoría (Thomann: «214ce», «CLG-70ASR CE NAT AllSolid»)."""
DEL_LISTADO = "{n} ofertas con el precio del listado (sin abrir la ficha: bloqueo)"
"""Paso de las ofertas que salen del listado porque sus fichas no se pudieron abrir por un
bloqueo (R3-H): título, enlace y precio de la página de resultados ya descargada."""
AVISO_LISTADO = "Precio del listado: la ficha no se pudo abrir por un bloqueo"
"""Aviso de cada oferta que sale del listado (R3-H): sin ficha, solo hay título y precio."""
CONFIANZA_LISTADO = 0.7
"""Confianza del precio de una oferta que sale del listado (R3-H): está anclado en la página de
resultados, pero no se ha comprobado en la ficha."""
CONSULTAS_CONFIGURACION = ("filtro", "faro", "freno")
"""Consultas de prueba con las que se configura una tienda sin perfil (A1, decisión de Julio S03).

Con la del usuario, una OEM que la tienda no vende dejaba la configuración fallida
(`descubrimiento.MIN_PRODUCTOS`): se configura con una genérica y luego se busca la del usuario.
Cada intento prueba la siguiente (`consulta_de_configuracion`)."""
ESPERAS_CONFIGURACION = (timedelta(hours=1), timedelta(days=1))
"""Cuánto se espera para volver a configurar una tienda tras 1, 2 o más fallos seguidos (A1)."""
_DE_TIPO = {
    TIPO_LISTADO: " con el listado de la URL de entrada (sin buscador)",
    TIPO_SITEMAP: " con el catálogo del sitemap (sin buscador)",
}
"""Cómo se cuenta en el progreso un perfil nuevo de una tienda sin buscador (nota 16)."""

_TROZO = re.compile(r"[0-9a-z]+")
"""Trozos de un texto normalizado: una referencia se compara sin lo que hay entre ellos
(«5k1-941-005-e», «5k1 941 005 e»)."""
_ESPACIOS = re.compile(r"\s+")

registro = logging.getLogger(__name__)


def _ahora() -> datetime:
    return datetime.now(UTC)


def _recortar(texto: str, maximo: int = MAX_DETALLE) -> str:
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def espera_configuracion(intentos: int) -> timedelta:
    """La espera tras `intentos` configuraciones fallidas seguidas: 1 h y luego 1 día (A1)."""
    return ESPERAS_CONFIGURACION[min(max(intentos, 1), len(ESPERAS_CONFIGURACION)) - 1]


def consulta_de_configuracion(consultas: Sequence[str], intentos_previos: int) -> str:
    """La consulta de prueba de este intento: cada intento fallido pasa a la siguiente (A1)."""
    return consultas[intentos_previos % len(consultas)]


def estado_de(perfil: Perfil) -> EstadoPerfil:
    """Estado de la tienda según su perfil vigente (memoria §4: sin revisión no se fija)."""
    if not perfil.revisado:
        return EstadoPerfil.PENDIENTE_REVISION
    return EstadoPerfil.REPARADO if "reparador" in perfil.autor else EstadoPerfil.ACTIVO


class _NavegadorVigilado:
    """Envuelve el navegador del configurador para contar sus peticiones (progreso fino, A10)."""

    def __init__(self, navegador: "Navegador", al_pedir: Callable[[], None] | None = None) -> None:
        """`al_pedir` se llama en cada petición: el progreso fino cuenta las páginas (A10)."""
        self._navegador = navegador
        self._al_pedir = al_pedir
        self.intentos = 0

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        self.intentos += 1
        if self._al_pedir is not None:
            self._al_pedir()
        if grande:
            return await self._navegador.obtener(url, js=js, grande=True)
        return await self._navegador.obtener(url, js=js)

    async def sitemaps(self, url: str) -> tuple[str, ...]:
        """Las líneas `Sitemap:` del robots.txt (nota 16 §B). No cuenta como intento: el
        robots.txt se lee aunque se ignore (ADR-017)."""
        return await self._navegador.sitemaps(url)


class _ExtractorVigilado:
    """El extractor de respaldo de una tienda, que se apaga tras el primer error de la IA.

    `extraer_ficha` se traga el `ErrorIA` (lo deja como aviso de la ficha) y seguiría llamando
    en cada ficha: con la API caída, un timeout por ficha; con salidas no válidas, dos llamadas
    de pago por ficha. Aquí se recuerda el error para que la tienda siga sin IA (ADR-002).

    También se apaga si no aporta nada (ningún valor anclado) en la primera ficha en la que se
    usa: el resto de fichas de la tienda en esta búsqueda ya no lo llaman (A3). Hay uno por
    tienda y búsqueda, porque las fichas de una tienda se pueden leer en dos pasadas.

    Y deja de pedir un campo que ha pedido en `MAX_FALLOS_CAMPO` fichas sin que saliera anclado
    en ninguna (A11: la `referencia_oem` de unas zapatillas): `anotar` lo mira tras cada ficha.
    Un campo que ha salido anclado alguna vez se sigue pidiendo, y el precio siempre
    (`CAMPOS_SIEMPRE`): es el de la oferta. Si no queda ningún campo que pedir, no se llama.
    """

    def __init__(self, extractor: ExtractorRespaldoIA) -> None:
        self._extractor = extractor
        self.error: ErrorIA | None = None
        self.usos = 0
        self.inutil = False
        self.descartados: list[str] = []
        """Campos que ya no se piden en esta tienda y búsqueda (A11), en el orden en que salen."""
        self._pedidos: tuple[str, ...] = ()
        self._fallos: dict[str, int] = {}
        self._anclados: set[str] = set()

    @property
    def activo(self) -> bool:
        return self.error is None and not self.inutil

    @property
    def desde_cache(self) -> int:
        return self._extractor.desde_cache

    async def extraer(self, texto_pagina: str, campos: tuple[str, ...]) -> dict[str, str]:
        self._pedidos = tuple(c for c in campos if c not in self.descartados)
        if not self._pedidos:
            return {}
        self.usos += 1
        try:
            return await self._extractor.extraer(texto_pagina, self._pedidos)
        except ErrorIA as error:
            self.error = error
            raise

    def anotar(self, ficha: FichaExtraida) -> None:
        """Tras extraer una ficha: cuenta los campos pedidos a la IA que no salieron anclados."""
        anclados = {
            campo
            for campo, valor in ficha.campos.items()
            if valor.origen is OrigenDato.IA and valor.anclado
        }
        self._anclados |= anclados
        for campo in self._pedidos:
            if campo in self._anclados or campo in CAMPOS_SIEMPRE:
                continue
            self._fallos[campo] = self._fallos.get(campo, 0) + 1
            if self._fallos[campo] >= MAX_FALLOS_CAMPO and campo not in self.descartados:
                self.descartados.append(campo)
        self._pedidos = ()


class _CacheExtractor:
    """`ia.tipos.CacheExtracciones` sobre el almacén, con el TTL de la caché de páginas (A3).

    Un error del almacén no para la búsqueda: cuenta como fallo de caché.
    """

    def __init__(self, almacen: Almacen, ttl_s: float) -> None:
        self._almacen = almacen
        self._ttl_s = ttl_s

    def leer(
        self, texto_sha256: str, campos: tuple[str, ...], modelo: str
    ) -> dict[str, str] | None:
        try:
            return self._almacen.leer_extraccion_ia(
                texto_sha256, campos, modelo, max_edad_s=self._ttl_s
            )
        except ErrorAlmacen:
            return None

    def guardar(
        self, texto_sha256: str, campos: tuple[str, ...], modelo: str, valores: Mapping[str, str]
    ) -> None:
        with contextlib.suppress(ErrorAlmacen):
            self._almacen.guardar_extraccion_ia(texto_sha256, campos, modelo, valores)


class _CacheRevisiones:
    """`emparejado.tipos.CacheRevisiones` sobre el almacén, con un TTL de 7 días.

    Un error del almacén no para la búsqueda: cuenta como fallo de caché.
    """

    def __init__(self, almacen: Almacen) -> None:
        self._almacen = almacen

    def leer(self, clave: str) -> tuple[str, str, bool] | None:
        try:
            return self._almacen.leer_revision_ia(clave, max_edad_s=TTL_REVISIONES_IA_S)
        except ErrorAlmacen:
            return None

    def guardar(self, clave: str, clase: str, motivo: str, cumple_filtro: bool) -> None:
        with contextlib.suppress(ErrorAlmacen):
            self._almacen.guardar_revision_ia(clave, clase, motivo, cumple_filtro)


class _SesionContada:
    """La sesión de IA que se le da al configurador de una tienda, contando sus llamadas.

    La sesión de la búsqueda la comparten todas las tiendas en paralelo: su contador no dice si
    la IA ha intervenido en *este* perfil.
    """

    def __init__(self, sesion: SesionIA, al_pedir: Callable[[], None] | None = None) -> None:
        self._sesion = sesion
        self._al_pedir = al_pedir
        self.llamadas = 0

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA:
        self.llamadas += 1
        if self._al_pedir is not None:
            self._al_pedir()
        return await self._sesion.pedir(peticion)


class Navegador(NavegadorDePerfiles, Protocol):
    """Lo que necesita el orquestador del navegador: lo de `perfiles`, si se ignoró robots y,
    para las tiendas sin buscador (nota 16 §B), las descargas grandes (`grande=True`) y las
    líneas `Sitemap:` del robots.txt, que usa el configurador."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina: ...

    async def sitemaps(self, url: str) -> tuple[str, ...]: ...

    def robots_ignorado(self, dominio: str) -> bool: ...


class TurnosDePerfil:
    """Un candado por tienda para crear o reparar su perfil (B6).

    Las búsquedas simultáneas comparten uno (`FuenteReal`): si dos llegan a la vez a una tienda
    sin perfil, o con el perfil roto, solo la primera llama al configurador o al reparador; la
    otra espera su turno y usa lo que haya dejado en el almacén.

    Una reparación que falla no deja nada en el almacén: se cuenta aquí, por versión del perfil,
    para que la búsqueda que la esperaba no la repita en el acto (otra vez la IA y las peticiones
    de validación). Una búsqueda posterior sí lo vuelve a intentar.
    """

    def __init__(self) -> None:
        self._candados: dict[str, asyncio.Lock] = {}
        self._reparaciones_fallidas: dict[tuple[str, int], int] = {}

    def de(self, dominio: str) -> asyncio.Lock:
        return self._candados.setdefault(dominio, asyncio.Lock())

    def reparaciones_fallidas(self, dominio: str, version: int) -> int:
        return self._reparaciones_fallidas.get((dominio, version), 0)

    def apuntar_reparacion_fallida(self, dominio: str, version: int) -> None:
        clave = (dominio, version)
        self._reparaciones_fallidas[clave] = self._reparaciones_fallidas.get(clave, 0) + 1


@dataclass
class _Salida:
    progreso: ProgresoTienda
    candidatas: list[OfertaCandidata] = field(default_factory=list)
    en_espera: bool = False
    """La tienda se ha saltado porque su configuración falló hace poco (A1)."""


class Orquestador:
    def __init__(
        self,
        almacen: Almacen,
        navegador: Navegador,
        servicio_ia: ServicioIA | None,
        *,
        max_fichas: int = MAX_FICHAS,
        max_ofertas: int = MAX_OFERTAS,
        validador: Validador = validar_url,
        reloj: Callable[[], datetime] = _ahora,
        cronometro: Callable[[], float] = time.monotonic,
        motivo_sin_ia: str = "",
        ttl_cache_ia: float = 0.0,
        consultas_configuracion: Sequence[str] = CONSULTAS_CONFIGURACION,
        plazo_tienda_s: float = PLAZO_TIENDA_S,
        revisar_resultados: bool = False,
        turnos_perfil: TurnosDePerfil | None = None,
    ) -> None:
        """`servicio_ia` es `None` si no hay IA (sin clave): Auto e IA van como el clásico.

        `revisar_resultados` (ajuste del mismo nombre, activado por defecto en la web) hace que
        en Auto la IA revise los resultados en una llamada (nota 05, propuesta ADR-024). En modo
        IA se revisan siempre y en clásico nunca.

        `motivo_sin_ia` se añade al aviso «IA no disponible» (p. ej. «falta la clave»).
        `ttl_cache_ia` es cuánto vale lo que guarda el extractor de respaldo (A3); 0, sin caché.
        `consultas_configuracion` son las consultas de prueba del configurador (A1).
        `plazo_tienda_s` es lo que puede tardar cada tienda (C1), y otro tanto la búsqueda si
        antes ha habido que configurarla (R5-D).
        """
        if max_fichas < 1 or max_ofertas < 1:
            raise ValueError("max_fichas y max_ofertas tienen que ser al menos 1")
        if plazo_tienda_s <= 0:
            raise ValueError("El plazo por tienda tiene que ser positivo")
        if not consultas_configuracion or not all(c.strip() for c in consultas_configuracion):
            raise ValueError("consultas_configuracion necesita al menos una consulta no vacía")
        self._almacen = almacen
        self._navegador = navegador
        self._servicio = servicio_ia
        self._max_fichas = max_fichas
        self._max_ofertas = max_ofertas
        self._validador = validador
        self._reloj = reloj
        self._cronometro = cronometro
        self._motivo_sin_ia = motivo_sin_ia
        self._ttl_cache_ia = ttl_cache_ia
        self._cache_ia = _CacheExtractor(almacen, ttl_cache_ia) if ttl_cache_ia > 0 else None
        self._consultas_configuracion = tuple(consultas_configuracion)
        self._plazo_tienda_s = plazo_tienda_s
        self._revisar = revisar_resultados
        self._cache_revisiones = _CacheRevisiones(almacen)
        self._turnos_perfil = turnos_perfil if turnos_perfil is not None else TurnosDePerfil()

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        """Busca en las tiendas, empareja y guarda el histórico.

        `seguimiento` (C1) recibe el estado de cada tienda según avanza y, cada vez que termina
        una, un resultado provisional con las que ya han terminado (`_parcial`).
        """
        inicio = self._cronometro()
        generales: list[Paso] = []
        sesion: SesionIA | None = None
        if consulta.modo is not Modo.CLASICO:
            if self._servicio is not None:
                sesion = self._servicio.nueva_busqueda()
            else:
                motivo = f" ({self._motivo_sin_ia})" if self._motivo_sin_ia else ""
                generales.append(
                    Paso("IA", EstadoPaso.AVISO, f"{SIN_IA}{motivo}: se busca como en modo clásico")
                )
                if consulta.filtro:
                    generales.append(
                        Paso("filtro", EstadoPaso.AVISO, "El filtro necesita la IA: no se aplica")
                    )

        if self._cache_ia is not None:
            with contextlib.suppress(ErrorAlmacen):
                self._almacen.borrar_extracciones_ia(max_edad_s=self._ttl_cache_ia)
        if sesion is not None:
            with contextlib.suppress(ErrorAlmacen):
                self._almacen.borrar_revisiones_ia(max_edad_s=TTL_REVISIONES_IA_S)
        tiendas = self._tiendas(consulta, generales)
        hechas: dict[int, _Salida] = {}
        en_tiendas: list[_EnTienda] = []

        async def parcial() -> None:
            """Un parcial con las tiendas terminadas y las fichas leídas de las que siguen (A1)."""
            if seguimiento is None:
                return
            candidatas: list[OfertaCandidata] = []
            for indice, busqueda in enumerate(en_tiendas):
                if indice in hechas:
                    candidatas.extend(hechas[indice].candidatas)
                else:
                    # Solo las que ya tienen precio: una ficha sin él no aporta nada a la tabla
                    # y, si los datos estructurados no valen, la tienda las vuelve a leer.
                    candidatas.extend(c for c in busqueda.leidas if _valida(c))
            terminadas = [hechas[i] for i in sorted(hechas)]
            seguimiento.parcial(
                await self._parcial(consulta, terminadas, candidatas, sesion, inicio)
            )

        en_tiendas.extend(
            _EnTienda(self, t, consulta, sesion, seguimiento, al_leer=parcial) for t in tiendas
        )

        async def en_tienda(indice: int, busqueda: _EnTienda) -> _Salida:
            salida = await busqueda.ejecutar()
            if seguimiento is not None:
                hechas[indice] = salida
                await parcial()
            return salida

        salidas = await asyncio.gather(*(en_tienda(i, e) for i, e in enumerate(en_tiendas)))
        if esperando := [s.progreso.tienda for s in salidas if s.en_espera]:
            generales.append(
                Paso(EN_ESPERA, EstadoPaso.AVISO, _recortar(aviso_en_espera(esperando)))
            )
        candidatas = [c for s in salidas for c in s.candidatas]
        if len(candidatas) > self._max_ofertas:
            generales.append(
                Paso(
                    "emparejado",
                    EstadoPaso.AVISO,
                    f"Hay {len(candidatas)} ofertas: solo se comparan las {self._max_ofertas} "
                    "primeras",
                )
            )
            candidatas = candidatas[: self._max_ofertas]
        grupos, descartados = await self._agrupar(consulta, candidatas, sesion, generales)
        self._guardar_historico(grupos, generales)

        progreso = [s.progreso for s in salidas]
        if generales:
            progreso.insert(0, ProgresoTienda(GENERAL, tuple(generales)))
        if consulta.descubrir:
            progreso.insert(
                0,
                ProgresoTienda(
                    "Google",
                    (
                        Paso(
                            "descubrir",
                            EstadoPaso.AVISO,
                            "El descubrimiento con Google aún no está disponible: no se consulta",
                        ),
                    ),
                ),
            )
        llamadas = sesion.llamadas if sesion is not None else 0
        coste = sesion.coste_usd if sesion is not None else Decimal(0)
        duracion = max(self._cronometro() - inicio, 0.0)
        # Solo son métricas para la evaluación: si no se pueden guardar, la búsqueda vale igual.
        with contextlib.suppress(ErrorAlmacen):
            self._almacen.registrar_busqueda(
                consulta.texto, consulta.modo, self._reloj(), llamadas, coste, duracion
            )
        return ResultadoBusqueda(
            consulta=consulta,
            grupos=tuple(grupos),
            descartados=tuple(descartados),
            progreso=tuple(progreso),
            llamadas_ia=llamadas,
            coste_ia_usd=coste,
            duracion_s=duracion,
            es_demo=False,
            llamadas_por_componente=sesion.por_componente if sesion is not None else {},
            ia_desde_cache=sum(e.ia_desde_cache for e in en_tiendas),
        )

    async def _parcial(
        self,
        consulta: ConsultaBusqueda,
        salidas: Sequence[_Salida],
        candidatas: Sequence[OfertaCandidata],
        sesion: SesionIA | None,
        inicio: float,
    ) -> ResultadoBusqueda:
        """Resultado provisional (C1): las ofertas de las tiendas que ya han terminado y las
        fichas leídas de las que siguen (A1); el progreso, solo de las terminadas.

        Agrupado clásico: sin IA (el emparejado con IA, la revisión y el filtro se hacen una
        vez, al final) y sin guardar nada en el histórico ni en las métricas, que son de la
        búsqueda completa. Tampoco descarta la regla del núcleo: así una oferta que se ha visto
        no desaparece de un parcial al siguiente; lo que descarte la revisión del final se
        enseña aparte, con su motivo.
        """
        candidatas = list(candidatas)[: self._max_ofertas]
        grupos, descartados = await agrupar(consulta, candidatas, sesion_ia=None, regla=False)
        return ResultadoBusqueda(
            consulta=consulta,
            grupos=tuple(grupos),
            descartados=tuple(descartados),
            progreso=tuple(s.progreso for s in salidas),
            llamadas_ia=sesion.llamadas if sesion is not None else 0,
            coste_ia_usd=sesion.coste_usd if sesion is not None else Decimal(0),
            duracion_s=max(self._cronometro() - inicio, 0.0),
            es_demo=False,
            llamadas_por_componente=sesion.por_componente if sesion is not None else {},
            provisional=True,
        )

    def _tiendas(self, consulta: ConsultaBusqueda, generales: list[Paso]) -> list[Tienda]:
        """Solo tiendas dadas de alta: un dominio cualquiera de la consulta no se visita nunca."""
        todas = self._almacen.tiendas()
        if not consulta.tiendas:
            return todas
        pedidas = {normalizar_dominio(t) for t in consulta.tiendas}
        elegidas = [t for t in todas if t.dominio in pedidas]
        if len(elegidas) < len(set(consulta.tiendas)):
            generales.append(
                Paso(
                    "tiendas",
                    EstadoPaso.AVISO,
                    "Se ignoran las tiendas que no están dadas de alta.",
                )
            )
        return elegidas

    async def _agrupar(
        self,
        consulta: ConsultaBusqueda,
        candidatas: Sequence[OfertaCandidata],
        sesion: SesionIA | None,
        generales: list[Paso],
    ) -> tuple[list[GrupoProducto], list[Descartado]]:
        """Agrupa con la revisión de los resultados y apunta en `generales` lo que ha hecho.

        Si la IA falla, se repite sin sesión: no se pierde ninguna oferta y descarta la regla
        del núcleo, con su aviso.
        """
        informe = InformeRevision()
        try:
            grupos, descartados = await agrupar(
                consulta,
                candidatas,
                sesion_ia=sesion,
                revisar=self._revisar,
                cache=self._cache_revisiones,
                modelo=sesion.modelo if sesion is not None else "",
                informe=informe,
            )
        except ErrorIA as error:
            revisaba = consulta.modo is Modo.IA or self._revisar
            texto = (
                f"Revisión sin IA ({error}): se aplica la regla del núcleo"
                if revisaba
                else f"Emparejado sin IA ({error}): se repite en modo clásico"
            )
            generales.append(Paso("emparejado", EstadoPaso.AVISO, _recortar(texto)))
            if consulta.filtro:
                generales.append(
                    Paso("filtro", EstadoPaso.AVISO, "Sin la IA, el filtro no se ha aplicado")
                )
            informe = InformeRevision()
            grupos, descartados = await agrupar(
                consulta, candidatas, sesion_ia=None, informe=informe
            )
        generales.extend(_pasos_revision(informe))
        return grupos, descartados

    def _guardar_historico(self, grupos: Sequence[GrupoProducto], generales: list[Paso]) -> None:
        ahora = self._reloj()
        fallidas = 0
        for grupo in grupos:
            for oferta in grupo.ofertas:
                if not oferta.precio_anclado:
                    continue  # Un precio que no está en la página nunca entra en el histórico.
                fecha = _fecha_historico(oferta.obtenida, ahora)
                try:
                    self._almacen.guardar_precio(
                        grupo.id,
                        grupo.nombre,
                        grupo.referencia_oem,
                        oferta.tienda,
                        oferta.url,
                        oferta.precio,
                        fecha,
                        titulo=oferta.titulo,
                        campos=oferta.campos,
                        disponible=oferta.disponible,
                        criterio=grupo.criterio,
                        coincidencia=oferta.coincidencia,
                        # La búsqueda que la ha visto, aunque saliera de la caché (A1).
                        visita=ahora,
                        foto=oferta.foto,
                    )
                except ErrorAlmacen:
                    fallidas += 1
        if fallidas:
            generales.append(
                Paso(
                    "histórico",
                    EstadoPaso.AVISO,
                    f"{fallidas} ofertas no se han podido guardar en el histórico",
                )
            )


def _pasos_revision(informe: InformeRevision) -> list[Paso]:
    """Lo que ha hecho la revisión de los resultados, para «Qué ha hecho»."""
    pasos: list[Paso] = []
    if informe.anomala:
        pasos.append(
            Paso(
                "revisión",
                EstadoPaso.AVISO,
                f"IA · Revisión anómala (descarta {informe.descartadas_ia} de {informe.revisadas}):"
                " no se aplica; se usa la regla del núcleo",
            )
        )
    elif informe.revisadas:
        detalle = f"IA · Revisión: {informe.descartadas_ia} de {informe.revisadas} descartadas"
        if informe.por_motivo:
            motivos = ", ".join(f"{n} {m.lower()}" for m, n in informe.por_motivo.most_common())
            detalle += f" ({motivos})"
        if informe.desde_cache:
            detalle += f"; {informe.desde_cache} desde la caché"
        pasos.append(Paso("revisión", EstadoPaso.OK, _recortar(detalle)))
    if informe.descartadas_regla:
        pasos.append(
            Paso(
                "revisión",
                EstadoPaso.OK,
                f"Regla del núcleo (sin IA): {informe.descartadas_regla} descartadas por parecer "
                "una pieza o un kit de lo buscado",
            )
        )
    return pasos


def aviso_en_espera(dominios: Sequence[str]) -> str:
    """El aviso general de las tiendas saltadas porque su configuración falló hace poco (A1).

    «Reintentar» está en la página de cada tienda, no en la lista de Tiendas.
    """
    if len(dominios) == 1:
        return (
            f"Se ha saltado 1 tienda cuya configuración falló hace poco: {dominios[0]}. Puedes "
            "reintentarla desde su página"
        )
    return (
        f"Se han saltado {len(dominios)} tiendas cuya configuración falló hace poco: "
        f"{', '.join(dominios)}. Puedes reintentarlas desde la página de cada una"
    )


def _detalle_espera(fallo: FalloConfiguracion) -> str:
    """El paso «perfil» de una tienda en espera tras una configuración fallida (A1)."""
    assert fallo.reintentar_desde is not None
    veces = "1 vez" if fallo.intentos == 1 else f"{fallo.intentos} veces"
    return _recortar(
        f"Sin perfil: la configuración ha fallado {veces} (la última, el "
        f"{fecha_hora(fallo.fecha)}) y no se reintenta hasta el "
        f"{fecha_hora(fallo.reintentar_desde)}. Pulsa «Reintentar» en la tienda para probar antes"
    )


def _fecha_historico(obtenida: datetime | None, ahora: datetime) -> datetime:
    """La fecha de una oferta en el histórico: cuando se descargó su ficha, en la zona del reloj.

    Una ficha servida desde la caché lleva la fecha de su descarga (A3, decisión de Julio): así
    repetir la búsqueda no añade un precio «de hoy» que no se ha vuelto a mirar. Sin zona (no se
    puede comparar) o en el futuro (otro reloj adelantado), se usa la de ahora.
    """
    if obtenida is None or obtenida.tzinfo is None or ahora.tzinfo is None:
        return ahora
    return min(obtenida.astimezone(ahora.tzinfo), ahora)


class _EnTienda:
    """La búsqueda en una tienda: sus pasos de progreso y lo que hay que guardar de ella."""

    def __init__(
        self,
        orquestador: Orquestador,
        tienda: Tienda,
        consulta: ConsultaBusqueda,
        sesion: SesionIA | None,
        seguimiento: Seguimiento | None = None,
        *,
        al_leer: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """`al_leer` se llama cada vez que se lee una ficha: el orquestador manda un parcial."""
        self.o = orquestador
        self.tienda = tienda
        self.dominio = tienda.dominio
        self.consulta = consulta
        self.sesion = sesion
        # Las OEM de la consulta, para ver qué fichas las citan entre sus equivalencias (B5).
        self.referencias = tuple(sorted(referencias_de_consulta(consulta.texto)))
        self.pasos: list[Paso] = []
        self.fase = "perfil"
        self.estado: EstadoPerfil | None = None
        self.robots: tuple[str, bool] | None = None
        self.bloqueado = False
        self.visitada = False
        self.en_espera = False
        self.espera = ""
        """Hasta cuándo no se reintenta la configuración que acaba de fallar (A1)."""
        self._vigilado: _ExtractorVigilado | None = None
        self._seguimiento = seguimiento
        self._al_leer = al_leer
        self._estado_avisado: EstadoTienda | None = None
        self.leidas: list[OfertaCandidata] = []
        """Las fichas leídas hasta ahora en `_fichas`: con ellas se queda si se agota el plazo."""
        self.plazo_agotado = False
        self.configurando = False
        """El configurador está en marcha: si el plazo lo corta, cuenta como fallo (A1)."""
        self._plazo: asyncio.Timeout | None = None
        """El plazo en curso de `ejecutar`: al acabar de configurar vuelve a empezar (R5-D)."""
        self.por_respaldo = False
        """Los resultados elegidos salen de un respaldo (OEM o de texto): ninguno nombra la
        consulta y solo la ficha dice si es lo buscado, así que el listado no basta (R3-H)."""

    @property
    def con_ia(self) -> bool:
        return self.sesion is not None

    @property
    def campos(self) -> tuple[str, ...]:
        return tuple(self.consulta.campos)

    @property
    def ia_desde_cache(self) -> int:
        return self._vigilado.desde_cache if self._vigilado is not None else 0

    def extractor(self) -> _ExtractorVigilado | None:
        """El extractor de respaldo de la tienda: uno por búsqueda, para recordar si se apagó."""
        if self.sesion is None:
            return None
        if self._vigilado is None:
            self._vigilado = _ExtractorVigilado(
                ExtractorRespaldoIA(self.sesion, cache=self.o._cache_ia)
            )
        return self._vigilado

    def paso(self, nombre: str, estado: EstadoPaso, detalle: str = "") -> None:
        self.pasos.append(Paso(nombre, estado, _recortar(detalle)))

    def detallar(self, texto: str) -> None:
        """Cuenta al seguimiento el detalle de en qué está la tienda (A10): «Ficha 2 de 5»..."""
        if self._seguimiento is not None:
            self._seguimiento.detalle(self.dominio, texto)

    def avisar(self, estado: EstadoTienda) -> None:
        """Cuenta al seguimiento (C1) en qué está la tienda; no repite el mismo estado."""
        if self._seguimiento is None or estado is self._estado_avisado:
            return
        self._estado_avisado = estado
        self._seguimiento.tienda(self.dominio, estado)

    async def _ficha_leida(self) -> None:
        """Avisa al orquestador para que mande un parcial (A1). Es solo para enseñarlo antes:
        un fallo al montarlo se registra y no para la tienda."""
        if self._al_leer is None:
            return
        try:
            await self._al_leer()
        except Exception:
            registro.exception("No se ha podido mandar el resultado parcial (%s)", self.dominio)

    def sin_ia(self, error: ErrorIA, que: str) -> None:
        """Un error de la IA no para la búsqueda: se avisa y la tienda sigue sin IA."""
        self.paso("IA", EstadoPaso.AVISO, f"{que}: {error}. Se sigue sin IA")
        self.sesion = None

    async def ejecutar(self) -> _Salida:
        plazo = self.o._plazo_tienda_s
        try:
            async with asyncio.timeout(plazo) as self._plazo:
                candidatas = await self._ejecutar_con_errores()
        except TimeoutError:
            self.plazo_agotado = True
            if self.configurando:
                # Sin apuntarlo, cada búsqueda volvería a configurar la tienda desde cero (y a
                # pagar la IA) sin entrar nunca en la espera de A1.
                agotado = f"Plazo de {plazo:g} s agotado configurando la tienda"
                espera = self._recordar_fallo(self.tienda.fallo, agotado)
                self.paso("perfil", EstadoPaso.ERROR, f"{agotado}: {espera}")
            else:
                self.paso(
                    self.fase,
                    EstadoPaso.ERROR,
                    f"Plazo de {plazo:g} s agotado: se usan las {len(self.leidas)} fichas leídas",
                )
            candidatas = list(self.leidas)
        except asyncio.CancelledError:
            # Cancelada desde fuera (el usuario o el cierre de la aplicación; la del plazo ya la
            # ha convertido `asyncio.timeout` en `TimeoutError`): se apunta la visita y se sigue
            # cancelando, sin agrupar ni guardar nada en el histórico.
            self._actualizar_tienda()
            self.avisar(EstadoTienda.CANCELADA)
            raise
        self._actualizar_tienda()
        self.avisar(self._estado_final())
        progreso = ProgresoTienda(self.dominio, tuple(self.pasos))
        return _Salida(progreso, candidatas, en_espera=self.en_espera)

    async def _ejecutar_con_errores(self) -> list[OfertaCandidata]:
        """`_ejecutar`, con cada error convertido en un paso del progreso de la tienda."""
        candidatas: list[OfertaCandidata] = []
        try:
            candidatas = await self._ejecutar()
        except Bloqueado as error:
            self.visitada = True
            self.bloqueado = True
            espera = f". {self.espera}" if self.espera else ""
            self.paso(self.fase, EstadoPaso.ERROR, f"{BLOQUEO} ({error.motivo}){espera}")
        except ErrorAlmacen as error:
            self.paso(self.fase, EstadoPaso.ERROR, f"No se pudo guardar en el almacén: {error}")
        except Exception:
            # Último recurso: un fallo que nadie espera (Playwright, la librería HTTP, un dato
            # raro de la página...) es un error de esta tienda. Sin esto, `asyncio.gather`
            # tumbaría la búsqueda entera (un 500) y dejaría a las demás tiendas sin recoger.
            # El detalle va al registro y no a la página: puede llevar rutas o datos internos.
            registro.exception("Error inesperado en %s (%s)", self.dominio, self.fase)
            self.paso(self.fase, EstadoPaso.ERROR, INESPERADO)
        return candidatas

    def _estado_final(self) -> EstadoTienda:
        if self.en_espera:
            return EstadoTienda.EN_ESPERA
        if self.plazo_agotado:
            return EstadoTienda.PLAZO
        return EstadoTienda.BLOQUEADA if self.bloqueado else EstadoTienda.HECHA

    async def _ejecutar(self) -> list[OfertaCandidata]:
        perfil = self.o._almacen.perfil_vigente(self.dominio)
        nuevo = perfil is None
        if perfil is None:
            perfil, nuevo = await self._configurar_en_turno()
            if perfil is None:
                return []
        if perfil.es_listado:
            return await self._de_listado(perfil, nuevo)
        if perfil.es_sitemap:
            return await self._de_sitemap(perfil, nuevo)
        pagina = await self._buscar(perfil, nuevo)
        if pagina is None:
            return []
        if perfil.es_api:
            return await self._de_la_api(perfil, pagina)
        listado = await self._listado(perfil, pagina)
        if listado is None:
            return []
        perfil, resultados, estructurado = listado
        resultados = self._seleccionar(resultados, pagina)
        if not resultados:
            return []
        # Si una ficha se bloquea, el precio que ya trae el listado (R3-H); no con los datos
        # estructurados ni cuando ningún resultado nombra la consulta.
        listado_de_respaldo = None if estructurado or self.por_respaldo else pagina
        candidatas = await self._fichas(perfil, resultados, listado_de_respaldo)
        if not estructurado or self.bloqueado or any(_valida(c) for c in candidatas):
            return candidatas
        # Los datos estructurados no han dado ninguna oferta con precio: el perfil sigue roto.
        roto = await self._perfil_roto(perfil, pagina, "")
        if roto is None:
            return candidatas
        perfil, resultados = roto
        if not resultados:
            return candidatas
        resultados = self._seleccionar(resultados, pagina)
        return await self._fichas(perfil, resultados) if resultados else candidatas

    @property
    def emparejado_con_ia(self) -> bool:
        """Las ofertas pasarán por el emparejado con IA (modo IA con servicio), que clasifica
        cada una como la pieza buscada, un accesorio u otra cosa."""
        return self.sesion is not None and self.consulta.modo is Modo.IA

    def _seleccionar(
        self, resultados: Sequence[ResultadoListado], pagina: Pagina | None = None
    ) -> list[ResultadoListado]:
        """Los resultados cuyas fichas se abren, en orden (`_para_abrir`), con su paso.

        Si ninguno nombra una consulta de texto (sin referencia OEM) pero `pagina` confirma que
        es su búsqueda (`_confirma_la_busqueda`), se abren los primeros en el orden de la tienda
        con el aviso `RESPALDO_TEXTO` (R3-D), solo si va a haber emparejado con IA: es quien
        descarta los accesorios y lo que no tiene relación. Si no, «sin resultados», diciendo
        que en modo IA se abrirían.
        """
        self.por_respaldo = False
        if not resultados:
            self._sin_resultados()
            return []
        abrir, respaldo = _para_abrir(resultados, self.consulta.texto)
        if not abrir:
            confirmada = (
                not self.referencias
                and pagina is not None
                and _confirma_la_busqueda(pagina, self.consulta.texto)
            )
            if confirmada and self.emparejado_con_ia:
                n = min(len(resultados), self.o._max_fichas)
                self.paso("búsqueda", EstadoPaso.AVISO, RESPALDO_TEXTO.format(n=n))
                self.por_respaldo = True
                return list(resultados)
            leidos = len(resultados)
            donde = f"en esta tienda: ninguno de los {leidos} resultados de la página la nombra"
            if confirmada:
                donde += " (la página sí es su búsqueda: en modo IA se abrirían los primeros)"
            self._sin_resultados(donde)
        elif respaldo:
            n = min(len(abrir), self.o._max_fichas)
            self.paso("búsqueda", EstadoPaso.AVISO, RESPALDO_OEM.format(n=n))
            self.por_respaldo = True
        return abrir

    def _sin_resultados(self, donde: str = "en esta tienda") -> None:
        self.paso(
            "extracción", EstadoPaso.AVISO, f"Sin resultados para «{self.consulta.texto}» {donde}"
        )

    # --- Perfil -----------------------------------------------------------------------------

    async def _configurar_en_turno(self) -> tuple[Perfil | None, bool]:
        """`_configurar` con el turno de la tienda (B6), y si el perfil es nuevo de esta búsqueda.

        Si otra búsqueda simultánea la estaba configurando, se espera a que termine y se vuelven
        a leer el perfil y el fallo de configuración: se usa el perfil que haya dejado (con su
        plazo entero, como tras configurar, R5-D) o, si falló, la tienda queda en espera (A1).
        """
        if not self.con_ia:
            return await self._configurar(), True
        candado = self.o._turnos_perfil.de(self.dominio)
        if candado.locked():
            self.detallar(ESPERANDO_OTRA)
        async with candado:
            perfil = self.o._almacen.perfil_vigente(self.dominio)
            if perfil is not None:
                self.paso("perfil", EstadoPaso.AVISO, OTRA_CONFIGURO)
                self._nuevo_plazo()
                return perfil, False
            with contextlib.suppress(ErrorAlmacen):
                if (tienda := self.o._almacen.tienda(self.dominio)) is not None:
                    self.tienda = tienda  # Con el fallo que haya dejado otra búsqueda.
            return await self._configurar(), True

    def _nuevo_plazo(self) -> None:
        """La búsqueda del usuario tiene su propio plazo entero tras configurar (R5-D)."""
        if self._plazo is not None:
            self._plazo.reschedule(asyncio.get_running_loop().time() + self.o._plazo_tienda_s)

    async def _configurar(self) -> Perfil | None:
        if not self.con_ia:
            detalle = (
                "Sin perfil (en modo clásico no se configura)"
                if self.consulta.modo is Modo.CLASICO
                else f"Sin perfil: {SIN_IA.lower()} para configurarlo"
            )
            self.paso("perfil", EstadoPaso.OMITIDO, detalle)
            return None
        assert self.sesion is not None
        fallo = self.tienda.fallo
        if fallo is not None and fallo.en_espera(self.o._reloj()):
            # Ni se configura ni se visita (A1): cada intento cuesta IA y peticiones a la tienda.
            self.en_espera = True
            self.paso("perfil", EstadoPaso.OMITIDO, _detalle_espera(fallo))
            return None
        # Una consulta genérica y no la del usuario (A1): una OEM que la tienda no vende no
        # deja la tienda sin perfil. Después se busca la del usuario con el perfil nuevo.
        prueba = consulta_de_configuracion(
            self.o._consultas_configuracion, fallo.intentos if fallo is not None else 0
        )
        de_prueba = f" (consulta de prueba «{prueba}»)"

        def detalle_configurando() -> None:
            # A10: la primera vez tarda (40-90 s); se va viendo que avanza.
            paginas = navegador.intentos
            llamadas = sesion.llamadas
            probadas = "página probada" if paginas == 1 else "páginas probadas"
            texto = f"{PRIMERA_VEZ} · {paginas} {probadas}"
            if llamadas:
                texto += f" · {llamadas} {'llamada' if llamadas == 1 else 'llamadas'} a la IA"
            self.detallar(texto)

        navegador = _NavegadorVigilado(self.o._navegador, detalle_configurando)
        sesion = _SesionContada(self.sesion, detalle_configurando)
        self.visitada = True
        self.avisar(EstadoTienda.CONFIGURANDO)
        detalle_configurando()
        try:
            perfil = await self._configurador(navegador, sesion, prueba)
        except ConfiguracionFallida as error:
            espera = self._recordar_fallo(fallo, str(error))
            self.paso(
                "perfil", EstadoPaso.ERROR, f"IA · Configurador{de_prueba}: {espera}. {error}"
            )
            return None
        except Bloqueado as error:
            # Lo sigue contando `ejecutar` como cualquier bloqueo, con la espera al final.
            self.espera = self._recordar_fallo(fallo, f"{BLOQUEO} ({error.motivo})")
            raise
        except ErrorIA as error:
            self.paso(
                "perfil",
                EstadoPaso.AVISO,
                f"Configurador sin IA: {error}. La tienda sigue sin perfil",
            )
            return None
        except (ErrorDescarga, URLNoPermitida) as error:
            espera = self._recordar_fallo(fallo, str(error))
            self.paso(
                "perfil", EstadoPaso.ERROR, f"IA · Configurador{de_prueba}: {espera}. {error}"
            )
            return None
        if fallo is not None:
            # Si no se puede borrar no pasa nada: con perfil vigente el fallo ya no se mira.
            with contextlib.suppress(ErrorAlmacen):
                self.o._almacen.borrar_fallo_configuracion(self.dominio)
        guardado = self.o._almacen.guardar_perfil(perfil)
        self.estado = EstadoPerfil.PENDIENTE_REVISION
        if guardado.es_api:
            # Sin IA: el perfil sale de la API pública de la plataforma (coste 0).
            nombre = NOMBRE_PLATAFORMA[PLATAFORMA_DE_TIPO[guardado.tipo]]
            self.paso(
                "perfil",
                EstadoPaso.AVISO,
                f"Configurador: perfil nuevo v{guardado.version} con la API pública de {nombre}, "
                f"sin IA{de_prueba}; pendiente de revisión (se usa de forma provisional)",
            )
        elif sesion.llamadas == 0:
            # Sin IA: p. ej. un perfil `sitemap` cuyas fichas traen JSON-LD con el precio.
            self.paso(
                "perfil",
                EstadoPaso.AVISO,
                f"Configurador: perfil nuevo v{guardado.version}{_DE_TIPO.get(guardado.tipo, '')}"
                f", sin IA{de_prueba}; pendiente de revisión (se usa de forma provisional)",
            )
        else:
            self.paso(
                "perfil",
                EstadoPaso.IA,
                f"IA · Configurador: perfil nuevo v{guardado.version}"
                f"{_DE_TIPO.get(guardado.tipo, '')}{de_prueba}, pendiente de revisión (se usa de "
                "forma provisional)",
            )
        return guardado

    async def _configurador(
        self, navegador: _NavegadorVigilado, sesion: _SesionContada, prueba: str
    ) -> Perfil:
        """`configurar`, con `configurando` puesto mientras dura (lo mira `ejecutar`)."""
        self.configurando = True
        try:
            perfil = await configurar(
                self.dominio,
                navegador,
                sesion,
                prueba,
                validador=self.o._validador,
                reloj=self.o._reloj,
                # Nota 16: la URL de entrada del usuario (A1 o A2) y dónde guardar el catálogo
                # si el perfil sale del sitemap (B).
                entrada=self.tienda.entrada,
                catalogo=self.o._almacen,
            )
        except Exception:
            self.configurando = False
            raise
        # Una cancelación (el plazo o el usuario) no pasa por aquí: `configurando` sigue puesto.
        self.configurando = False
        # Configurar una tienda nueva ya lleva casi todo el primer plazo. Lo peor para una
        # tienda nueva son dos plazos.
        self._nuevo_plazo()
        return perfil

    def _recordar_fallo(self, previo: FalloConfiguracion | None, motivo: str) -> str:
        """Guarda la configuración fallida con su espera (A1) y dice hasta cuándo dura.

        Solo cuentan los fallos de la tienda (no encuentra el buscador, no responde, bloquea, o
        tarda tanto que se agota el plazo de la tienda configurándola): un error de la IA o la
        cancelación del usuario no se guardan.
        """
        ahora = self.o._reloj()
        intentos = (previo.intentos if previo is not None else 0) + 1
        desde = ahora + espera_configuracion(intentos)
        try:
            self.o._almacen.registrar_fallo_configuracion(
                self.dominio,
                _recortar(motivo or "Sin detalle", MAX_MOTIVO_FALLO),
                ahora,
                intentos=intentos,
                reintentar_desde=desde,
            )
        except ErrorAlmacen as error:
            self.paso("tienda", EstadoPaso.AVISO, f"No se pudo guardar el fallo: {error}")
            return "se reintentará en la próxima búsqueda"
        return (
            f"no se reintenta hasta el {fecha_hora(desde)}; pulsa «Reintentar» en la tienda para "
            "probar antes"
        )

    def _paso_perfil(self, perfil: Perfil) -> None:
        if perfil.revisado:
            self.paso("perfil", EstadoPaso.OK, f"v{perfil.version} ({perfil.autor}, revisado)")
        else:
            self.paso(
                "perfil",
                EstadoPaso.AVISO,
                f"v{perfil.version} ({perfil.autor}) pendiente de revisión: se usa de forma "
                "provisional",
            )

    # --- Búsqueda y listado -----------------------------------------------------------------

    async def _buscar(self, perfil: Perfil, nuevo: bool) -> Pagina | None:
        self.fase = "búsqueda"
        self.visitada = True
        self.avisar(EstadoTienda.DESCARGANDO)
        self.detallar(DESCARGANDO_BUSQUEDA)
        try:
            url = await self.o._validador(url_de_busqueda(perfil.url_busqueda, self.consulta.texto))
            # Una API devuelve JSON: Chromium no pinta nada ahí.
            js = perfil.necesita_js and not perfil.es_api
            pagina = await self.o._navegador.obtener(url, js=js)
        except (ErrorDescarga, URLNoPermitida, Bloqueado) as error:
            if not nuevo:
                self._paso_perfil(perfil)
            if isinstance(error, Bloqueado):
                raise
            self.paso("búsqueda", EstadoPaso.ERROR, str(error))
            return None
        if not nuevo:
            self._paso_perfil(perfil)
        self._paso_robots(url)
        if not 200 <= pagina.estado < 300:
            self.paso(
                "búsqueda", EstadoPaso.ERROR, f"La tienda responde con el estado {pagina.estado}"
            )
            return None
        if not de_la_tienda(pagina, self.dominio):
            self.paso("búsqueda", EstadoPaso.ERROR, "La búsqueda redirige fuera de la tienda")
            return None
        detalle = "Resultados descargados"
        if perfil.es_api:
            detalle += " de la API pública de la tienda"
        elif perfil.necesita_js:
            detalle += " con Chromium"
        if pagina.via_proxy:
            # La tienda bloqueó la conexión directa (o el modo es «siempre»): se enseña.
            detalle += " por el proxy"
        if pagina.desde_cache:
            detalle += " (caché)"
        self.paso("búsqueda", EstadoPaso.OK, detalle)
        return pagina

    def _paso_robots(self, url: str) -> None:
        """Primer paso del progreso: si el robots.txt permitía la visita o se ha ignorado.

        Se llama después de la primera descarga de la tienda (antes no se sabe) y solo una vez.
        """
        if self.robots is not None:
            return
        if self.o._navegador.robots_ignorado(urlsplit(url).hostname or self.dominio):
            # ADR-017: el usuario decidió ignorar el robots.txt; se visita, pero se enseña.
            self.robots = (IGNORADO, False)
            self.pasos.insert(0, Paso("robots.txt", EstadoPaso.AVISO, IGNORADO))
        else:
            self.robots = (PERMITE, True)
            self.pasos.insert(0, Paso("robots.txt", EstadoPaso.OK, PERMITE))

    # --- Sin buscador: listado de la tienda (nota 16 §A2) -----------------------------------

    async def _de_listado(self, perfil: Perfil, nuevo: bool) -> list[OfertaCandidata]:
        """Recorre unas pocas páginas del listado, filtra por la consulta y abre las fichas.

        Como mucho `MAX_PAGINAS_LISTADO` (solo la URL tal cual si no lleva `{pagina}`). Se para
        en la primera página que no responde, que no trae resultados o que los repite (la
        tienda no entiende el número de página), y en cuanto hay `max_fichas` resultados que
        nombran toda la consulta: no se pide ni una página más de las necesarias.
        """
        self.fase = "búsqueda"
        self.visitada = True
        self.avisar(EstadoTienda.DESCARGANDO)
        self.detallar(DESCARGANDO_BUSQUEDA)
        leidos: list[ResultadoListado] = []
        vistas: set[str] = set()
        paginas = 0
        for numero, plantilla in enumerate(_paginas_listado(perfil.url_busqueda), start=1):
            primera = numero == 1
            try:
                url = await self.o._validador(plantilla)
                pagina = await self.o._navegador.obtener(url, js=perfil.necesita_js)
            except Bloqueado:
                if primera and not nuevo:
                    self._paso_perfil(perfil)
                raise  # Se para: ni una petición más.
            except (ErrorDescarga, URLNoPermitida) as error:
                if not primera:
                    break
                if not nuevo:
                    self._paso_perfil(perfil)
                self.paso("búsqueda", EstadoPaso.ERROR, str(error))
                return []
            if primera:
                if not nuevo:
                    self._paso_perfil(perfil)
                self._paso_robots(url)
            paginas += 1
            if not 200 <= pagina.estado < 300 or not de_la_tienda(pagina, self.dominio):
                if not primera:
                    break  # Una página que no existe es el final del listado.
                detalle = (
                    f"La tienda responde con el estado {pagina.estado}"
                    if not 200 <= pagina.estado < 300
                    else "El listado redirige fuera de la tienda"
                )
                self.paso("búsqueda", EstadoPaso.ERROR, detalle)
                return []
            try:
                resultados = extraer_listado(pagina, perfil, maximo=MAX_RESULTADOS_PAGINA)
            except SelectorNoValido as error:
                self._listado_roto(f"selector no válido: {error}")
                return []
            nuevos = [r for r in resultados if r.url not in vistas]
            if not nuevos:
                if primera and parece_listado(pagina, self.dominio, "", portada=None):
                    # La página tiene productos con precio y los selectores no los ven.
                    self._listado_roto("el listado sale vacío")
                    return []
                break
            vistas.update(r.url for r in nuevos)
            leidos.extend(nuevos)
            if _completos(leidos, self.consulta.texto) >= self.o._max_fichas:
                break
        self.estado = self.estado or estado_de(perfil)
        coincidencias = _de_la_consulta(leidos, self.consulta.texto, ordenar=True)
        self.paso(
            "búsqueda",
            EstadoPaso.OK,
            f"{LISTADO}: {paginas} páginas, {len(coincidencias)} coincidencias de {len(leidos)}",
        )
        if not coincidencias:
            self._sin_resultados("en el listado de esta tienda")
            return []
        return await self._fichas(perfil, coincidencias)

    def _listado_roto(self, motivo: str) -> None:
        """Un perfil `listado` roto no se repara: el reparador valida una búsqueda con
        `{consulta}`, y un listado se arregla volviendo a configurar la tienda."""
        self.fase = "perfil"
        self.estado = EstadoPerfil.ROTO
        self.paso(
            "perfil",
            EstadoPaso.ERROR,
            f"Perfil roto ({motivo}): un perfil de listado no se repara solo; hay que "
            "revisarlo o configurar la tienda de nuevo",
        )

    # --- Sin buscador: catálogo por sitemap (nota 16 §B) ------------------------------------

    async def _de_sitemap(self, perfil: Perfil, nuevo: bool) -> list[OfertaCandidata]:
        """Busca en el catálogo local de URL de la tienda y abre las fichas que encajan.

        Si el catálogo no existe o tiene más de `REFRESCO_CATALOGO`, antes se rehace desde el
        sitemap (con los mismos topes que al configurar). Si eso falla y hay un catálogo
        anterior, se avisa y se usa; un bloqueo, en cambio, para la tienda.
        """
        self.fase = "búsqueda"
        self.avisar(EstadoTienda.DESCARGANDO)
        self.detallar(LEYENDO_CATALOGO)
        if not nuevo:
            self._paso_perfil(perfil)
        info = self.o._almacen.catalogo(self.dominio)
        if info is None or self.o._reloj() - info.actualizado > REFRESCO_CATALOGO:
            self.visitada = True
            rehecho = await self._rehacer_catalogo(perfil, info.idioma if info else ())
            if isinstance(rehecho, InfoCatalogo):
                info = rehecho
            elif info is None:
                self.paso("búsqueda", EstadoPaso.ERROR, f"Sin catálogo: {rehecho}")
                return []
            else:
                self.paso(
                    "catálogo",
                    EstadoPaso.AVISO,
                    f"No se ha podido rehacer el catálogo ({rehecho}): se usa el anterior, del "
                    f"{info.actualizado:%d/%m/%Y}",
                )
        info = await self._hijos_de_la_consulta(info)
        limite = min(self.o._max_fichas, MAX_LIMITE_CATALOGO)
        # Ordenar por relevancia puntúa en Python todas las URL que casan (decenas de miles si la
        # consulta lleva una palabra de todas las rutas): en un hilo, para no parar el bucle.
        urls = await asyncio.to_thread(
            self.o._almacen.buscar_catalogo,
            self.dominio,
            self.consulta.texto,
            limite=limite,
            es_listado=es_listado,
        )
        parcial = ", parcial" if info.parcial else ""
        self.estado = self.estado or estado_de(perfil)
        self.paso(
            "búsqueda",
            EstadoPaso.OK,
            f"{CATALOGO} ({info.total} URL{parcial}): {len(urls)} coincidencias",
        )
        if not urls:
            if info.parcial:
                self.paso(
                    "catálogo",
                    EstadoPaso.AVISO,
                    f"El catálogo está incompleto (parcial, {info.total} URL): puede que la tienda "
                    "tenga lo que buscas en sitemaps que no se han leído",
                )
            self._sin_resultados("en el catálogo de esta tienda")
            return []
        self.visitada = True
        # El catálogo solo guarda URL: el título de cada resultado son las palabras del slug.
        resultados = [ResultadoListado(palabras_url(u), u) for u in urls]
        candidatas = await self._fichas(perfil, resultados)
        self._paso_robots(urls[0])
        return candidatas

    async def _hijos_de_la_consulta(self, info: InfoCatalogo) -> InfoCatalogo:
        """El catálogo con los sitemaps hijos pendientes que nombran la consulta (R2-C14b).

        Al hacer el catálogo, los hijos que no cupieron en el tope de ficheros quedaron
        pendientes (PcComponentes tiene 424 feeds por categoría). Se descargan los que nombran la
        consulta (`fuentes.sitemap.hijos_de_la_consulta`, como mucho `MAX_HIJOS_BUSQUEDA`) con
        `perfiles.catalogo.leer_hijos`, sus URL se añaden al catálogo y salen de la lista aunque
        no den nada, para no repetir la descarga. Un bloqueo se propaga.
        """
        pendientes = self.o._almacen.hijos_pendientes(self.dominio)
        hijos = hijos_de_la_consulta(pendientes, self.consulta.texto, maximo=MAX_HIJOS_BUSQUEDA)
        if not hijos:
            return info
        self.fase = "catálogo"
        self.visitada = True
        try:
            leido = await leer_hijos(
                info.sitemap,
                hijos,
                self.dominio,
                self.o._navegador,
                validador=self.o._validador,
                idioma=info.idioma,
            )
            if leido is None:
                return info
            self._paso_robots(hijos[0])
            nuevo = await asyncio.to_thread(
                self.o._almacen.ampliar_catalogo,
                self.dominio,
                hijos,
                leido.entradas,
                parcial=leido.parcial,
                pendientes=leido.pendientes,
            )
        except DatoNoValido as error:
            self.paso("catálogo", EstadoPaso.AVISO, f"No se ha podido ampliar el catálogo: {error}")
            return info
        finally:
            self.fase = "búsqueda"
        cuantos = (
            "1 sitemap más del índice que nombra"
            if len(hijos) == 1
            else f"{len(hijos)} sitemaps más del índice que nombran"
        )
        self.paso(
            "catálogo",
            EstadoPaso.OK,
            f"{cuantos} la consulta: {nuevo.total - info.total} URL nuevas",
        )
        return nuevo

    async def _rehacer_catalogo(
        self, perfil: Perfil, idioma: tuple[str, ...]
    ) -> InfoCatalogo | str:
        """El catálogo nuevo, ya guardado, o el motivo por el que no se ha podido rehacer.

        Lo lee `perfiles.catalogo.leer_catalogo` (el mismo código y los mismos topes que al
        configurar), con el prefijo de idioma del catálogo anterior (`idioma`, R5-C). Un bloqueo
        se propaga: la tienda se para ahí, sin ninguna petición más.
        """
        self.fase = "catálogo"
        try:
            leido = await leer_catalogo(
                perfil.url_busqueda,
                self.dominio,
                self.o._navegador,
                validador=self.o._validador,
                idioma=idioma,
            )
            if leido is None:
                return "el sitemap no responde o no tiene URL de la tienda"
            self._paso_robots(leido.sitemap)
            return self.o._almacen.guardar_catalogo(
                self.dominio,
                leido.sitemap,
                leido.entradas,
                parcial=leido.parcial,
                pendientes=leido.pendientes,
                idioma=leido.idioma,
            )
        except DatoNoValido as error:
            return f"el catálogo no es válido: {error}"
        finally:
            self.fase = "búsqueda"

    async def _listado(
        self, perfil: Perfil, pagina: Pagina
    ) -> tuple[Perfil, list[ResultadoListado], bool] | None:
        """El listado con el perfil (reparado si hace falta), o `None` si está roto.

        El tercer valor dice si los resultados salen de los datos estructurados de la página y
        no de los selectores: entonces aún hay que ver si dan ofertas válidas.
        """
        self.fase = "perfil"
        try:
            resultados = extraer_listado(pagina, perfil)
        except SelectorNoValido as error:
            # Un selector fuera del subconjunto es un perfil roto aunque haya datos estructurados.
            roto = await self._perfil_roto(perfil, pagina, f"selector no válido: {error}")
            return None if roto is None else (*roto, False)
        if resultados:
            self.estado = self.estado or estado_de(perfil)
            return perfil, resultados, False
        estructurados = _de_la_consulta(
            listado_estructurado(pagina, self.dominio), self.consulta.texto
        )
        if estructurados:
            self.paso("perfil", EstadoPaso.AVISO, ESTRUCTURADOS)
            self.estado = self.estado or estado_de(perfil)
            return perfil, estructurados, True
        roto = await self._perfil_roto(perfil, pagina, "")
        return None if roto is None else (*roto, False)

    async def _perfil_roto(
        self, perfil: Perfil, pagina: Pagina, motivo: str
    ) -> tuple[Perfil, list[ResultadoListado]] | None:
        """Listado vacío con el perfil: sin resultados de verdad, reparado o `None` (roto).

        Sin `motivo` (no hay un selector inválido), solo está roto si la página tiene productos
        con precio y la búsqueda no ha redirigido a otra página de la tienda que ya no es una
        búsqueda (`_busqueda_redirigida`, R2-C12): entonces se avisa y no se repara.
        """
        self.fase = "perfil"
        if not motivo:
            # Una página sin productos con precio no es un perfil roto: la tienda no tiene lo
            # buscado. Así tampoco se gasta una llamada al reparador cada vez que pasa (ni si
            # la tienda vacía el listado a propósito para provocarlas).
            if not parece_listado(pagina, self.dominio, self.consulta.texto, portada=None):
                self.estado = self.estado or estado_de(perfil)
                return perfil, []
            if _busqueda_redirigida(pagina, self.consulta.texto):
                # Una página de marca o categoría no son los resultados de la consulta, y sus
                # «productos con precio» pueden ser la cabecera (carrito, envío gratis). El perfil
                # sigue valiendo para otras consultas: ni reparador ni ROTO.
                self.paso(
                    "perfil",
                    EstadoPaso.AVISO,
                    f"La tienda redirige la búsqueda a {pagina.url_final} (no es una página de "
                    "resultados): no se repara el perfil",
                )
                self.estado = self.estado or estado_de(perfil)
                return perfil, []
            motivo = "el listado sale vacío"
        reparado = await self._reparar(perfil, pagina, motivo)
        if reparado is None:
            self.estado = EstadoPerfil.ROTO
            return None
        return reparado, extraer_listado(pagina, reparado)

    async def _reparar(self, perfil: Perfil, pagina: Pagina, motivo: str) -> Perfil | None:
        if not self.con_ia:
            if self.consulta.modo is Modo.CLASICO:
                detalle = f"Perfil roto ({motivo}): en modo clásico no se repara"
            else:
                detalle = f"Perfil roto ({motivo}): sin IA no se puede reparar"
            self.paso("perfil", EstadoPaso.ERROR, detalle)
            return None
        assert self.sesion is not None
        turnos = self.o._turnos_perfil
        candado = turnos.de(self.dominio)
        fallidas = turnos.reparaciones_fallidas(self.dominio, perfil.version)
        if candado.locked():
            self.detallar(ESPERANDO_OTRA)
        async with candado:
            vigente = self.o._almacen.perfil_vigente(self.dominio)
            if vigente is not None and vigente.version != perfil.version:
                # Otra búsqueda simultánea lo acaba de reparar (B6): no se paga otra reparación.
                self.estado = estado_de(vigente)
                self.paso(
                    "perfil",
                    EstadoPaso.AVISO,
                    f"Perfil roto ({motivo}): {OTRA_REPARO} v{vigente.version}",
                )
                return vigente
            if turnos.reparaciones_fallidas(self.dominio, perfil.version) > fallidas:
                # Otra búsqueda simultánea lo intentó mientras se esperaba y falló (B6).
                self.paso("perfil", EstadoPaso.ERROR, f"Perfil roto ({motivo}): {OTRA_NO_REPARO}")
                return None
            reparado = await self._reparar_con_ia(perfil, pagina, motivo)
            if reparado is None:
                turnos.apuntar_reparacion_fallida(self.dominio, perfil.version)
            return reparado

    async def _reparar_con_ia(self, perfil: Perfil, pagina: Pagina, motivo: str) -> Perfil | None:
        assert self.sesion is not None
        self.avisar(EstadoTienda.CONFIGURANDO)
        self.detallar(REPARANDO)
        try:
            nuevo = await reparar(
                perfil,
                pagina,
                self.o._navegador,
                self.sesion,
                self.consulta.texto,
                validador=self.o._validador,
                reloj=self.o._reloj,
            )
        except ReparacionFallida as error:
            self.paso(
                "perfil", EstadoPaso.ERROR, f"Perfil roto ({motivo}). IA · Reparador: {error}"
            )
            return None
        except ErrorIA as error:
            self.sin_ia(error, "Reparador")
            self.paso("perfil", EstadoPaso.ERROR, f"Perfil roto ({motivo}): no se ha reparado")
            return None
        except (ErrorDescarga, URLNoPermitida) as error:
            self.paso(
                "perfil",
                EstadoPaso.ERROR,
                f"Perfil roto ({motivo}). No se pudo validar la reparación: {error}",
            )
            return None
        guardado = self.o._almacen.guardar_perfil(nuevo)
        self.estado = EstadoPerfil.PENDIENTE_REVISION
        self.paso(
            "perfil",
            EstadoPaso.IA,
            f"IA · Reparador: el perfil v{perfil.version} estaba roto ({motivo}); propone "
            f"v{guardado.version}, pendiente de revisión",
        )
        return guardado

    # --- API pública ------------------------------------------------------------------------

    async def _de_la_api(self, perfil: Perfil, pagina: Pagina) -> list[OfertaCandidata]:
        """Ofertas de la respuesta de la API de un perfil `api_*`, sin abrir ninguna ficha."""
        self.fase = "extracción"
        self.estado = self.estado or estado_de(perfil)
        fichas = await ofertas_de_api(
            perfil,
            pagina,
            self.o._navegador,
            self.campos,
            validador=self.o._validador,
            # La API no cuesta más peticiones por traer más productos: se eligen después.
            maximo=max(self.o._max_fichas, MAX_FICHAS_API),
        )
        if not fichas:
            self._sin_resultados("en la API de esta tienda")
            return []
        # La API busca también en la descripción: solo los que nombran la consulta (bug 2).
        leidas = len(fichas)
        elegidas, _ = _para_abrir(fichas, self.consulta.texto)
        ajenas = leidas - len(elegidas)
        fichas = elegidas[: self.o._max_fichas]
        if not fichas:
            self._sin_resultados(
                f"en la API de esta tienda: ninguno de sus {leidas} productos la nombra"
            )
            return []
        nombre = NOMBRE_PLATAFORMA[PLATAFORMA_DE_TIPO[perfil.tipo]]
        sin_precio = sum(f.precio is None for f in fichas)
        detalle = f"{len(fichas)} productos de la API pública de {nombre}, sin abrir cada ficha"
        if ajenas:
            detalle += f" · {ajenas} no nombran la búsqueda"
        if sin_precio:
            detalle += f" · {sin_precio} sin precio"
        self.paso("extracción", EstadoPaso.AVISO if sin_precio else EstadoPaso.OK, detalle)
        avisos = [a for f in fichas for a in f.avisos if a != AVISO_AGOTADO]
        self.paso(
            *self._paso_verificacion(0, avisos)
            if avisos
            else ("verificación", EstadoPaso.OK, "Datos de la propia tienda (su API pública)")
        )
        return [
            OfertaCandidata(self.dominio, f, f.url, disponible=AVISO_AGOTADO not in f.avisos)
            for f in fichas
        ]

    # --- Fichas -----------------------------------------------------------------------------

    async def _fichas(
        self,
        perfil: Perfil,
        resultados: Sequence[ResultadoListado],
        listado: Pagina | None = None,
    ) -> list[OfertaCandidata]:
        """Abre las fichas de los `max_fichas` primeros `resultados` y extrae sus campos.

        Un bloqueo para la tienda: ni una petición más. Si `listado` es la página de resultados
        de la que salen, las fichas que el bloqueo deja sin abrir dan ofertas con el precio del
        listado (`_del_listado`, R3-H).

        La extracción va en serie (el extractor de respaldo se apaga según la primera ficha,
        A3), pero en cuanto una ficha se ha descargado bien se pide la siguiente en segundo
        plano mientras se extrae la actual (A2): la red no espera a la IA. Son las mismas
        peticiones que en serie (el bucle abre todas las elegidas), de una en una por la
        cortesía del navegador, y un bloqueo en la actual se ve antes de pedir la siguiente. Si
        la tienda acaba antes (plazo, cancelación o un error), la pendiente se cancela.
        """
        self.fase = "extracción"
        self.avisar(EstadoTienda.DESCARGANDO)
        candidatas: list[OfertaCandidata] = []
        self.leidas = candidatas
        fallidas = sin_precio = con_ia = no_anclados = 0
        avisos: list[str] = []
        bloqueo: Bloqueado | None = None
        vigilado = self.extractor()
        usos = vigilado.usos if vigilado is not None else 0
        cache = vigilado.desde_cache if vigilado is not None else 0
        elegidos = resultados[: self.o._max_fichas]
        sin_abrir: Sequence[ResultadoListado] = ()
        siguiente: asyncio.Task[Pagina] | None = None
        try:
            for numero, resultado in enumerate(elegidos):
                self.detallar(f"Ficha {numero + 1} de {len(elegidos)}")
                try:
                    if siguiente is not None:
                        pendiente, siguiente = siguiente, None
                        pagina = await pendiente
                    else:
                        pagina = await self._abrir_ficha(resultado, perfil)
                except Bloqueado as error:
                    bloqueo = error
                    sin_abrir = elegidos[numero:]
                    break  # Se para: ni una petición más a una tienda que nos ha bloqueado.
                except (ErrorDescarga, URLNoPermitida):
                    fallidas += 1
                    continue
                if not 200 <= pagina.estado < 300 or not de_la_tienda(pagina, self.dominio):
                    fallidas += 1
                    continue
                if numero + 1 < len(elegidos):
                    # A2: la siguiente se descarga mientras se extrae esta.
                    siguiente = asyncio.create_task(
                        self._abrir_ficha(elegidos[numero + 1], perfil),
                        name=f"ficha-{self.dominio}-{numero + 2}",
                    )
                # Si la IA ha fallado o no ha aportado nada en una ficha anterior, sigue sin IA.
                extractor = vigilado if vigilado is not None and vigilado.activo else None
                primera = vigilado is not None and vigilado.usos == 0
                ficha = await extraer_ficha(
                    pagina,
                    self.campos,
                    perfil=perfil,
                    extractor_ia=extractor,
                    referencias=self.referencias,
                )
                if ficha.foto is None and resultado.foto is not None:
                    # La ficha no da foto: la de su tarjeta del listado, ya validada contra la
                    # tienda.
                    ficha = replace(ficha, foto=resultado.foto)
                # Un valor no anclado no cuenta como aporte: es inventado o una posible inyección.
                aporta = any(v.origen is OrigenDato.IA and v.anclado for v in ficha.campos.values())
                if vigilado is not None and primera and vigilado.usos and not aporta:
                    vigilado.inutil = True
                if vigilado is not None:
                    vigilado.anotar(ficha)
                con_ia += any(v.origen is OrigenDato.IA for v in ficha.campos.values())
                no_anclados += sum(not v.anclado for v in ficha.campos.values())
                sin_precio += ficha.precio is None
                avisos.extend(ficha.avisos)
                candidatas.append(OfertaCandidata(self.dominio, ficha, ficha.url))
                await self._ficha_leida()
        finally:
            if siguiente is not None:
                siguiente.cancel()
                siguiente.add_done_callback(_recoger)

        if vigilado is not None and vigilado.error is not None:
            self.sin_ia(vigilado.error, "Extractor de respaldo")
        if bloqueo is not None:
            self.bloqueado = True
            self.paso(
                "extracción",
                EstadoPaso.ERROR,
                f"{BLOQUEO} ({bloqueo.motivo}); {len(candidatas)} fichas leídas antes del bloqueo",
            )
            del_listado = self._del_listado(perfil, listado, sin_abrir)
            if del_listado:
                candidatas.extend(del_listado)
                self.paso("listado", EstadoPaso.AVISO, DEL_LISTADO.format(n=len(del_listado)))
        elif not candidatas:
            self.paso("extracción", EstadoPaso.ERROR, "No se ha podido abrir ninguna ficha")
        else:
            uso = _UsoExtractor(
                vigilado.usos - usos if vigilado else 0,
                vigilado.desde_cache - cache if vigilado else 0,
                bool(vigilado and vigilado.inutil),
                tuple(vigilado.descartados) if vigilado and not vigilado.inutil else (),
            )
            self.paso(*self._paso_extraccion(len(candidatas), fallidas, sin_precio, con_ia, uso))
        if candidatas:
            self.paso(*self._paso_verificacion(no_anclados, avisos))
        return candidatas

    async def _abrir_ficha(self, resultado: ResultadoListado, perfil: Perfil) -> Pagina:
        url = await self.o._validador(resultado.url)
        return await self.o._navegador.obtener(url, js=perfil.necesita_js)

    def _del_listado(
        self, perfil: Perfil, listado: Pagina | None, sin_abrir: Sequence[ResultadoListado]
    ) -> list[OfertaCandidata]:
        """Ofertas con lo que el listado ya trae de las fichas que un bloqueo dejó sin abrir.

        Solo con un perfil que lee el precio en el listado (`sel_precio_listado`) y con los
        resultados que nombran la consulta y cuyo precio sale tal cual en el texto visible de
        la página de resultados (sin lo tachado): uno leído de un atributo no está anclado. No
        se pide ninguna página más. Cada oferta lleva solo título, enlace y precio, con
        `CONFIANZA_LISTADO` y el aviso `AVISO_LISTADO`, y la fecha de descarga del listado.
        """
        if listado is None or not perfil.sel_precio_listado:
            return []
        con_precio = [r for r in sin_abrir if r.precio is not None]
        if not con_precio:
            return []
        visible = _ESPACIOS.sub(" ", analizar(listado.html).texto(sin_tachados=True))
        ofertas: list[OfertaCandidata] = []
        for resultado in _de_la_consulta(con_precio, self.consulta.texto):
            precio = resultado.precio
            if precio is None or not _escrito_en(precio.texto, visible):
                continue
            valor = ValorCampo(precio.texto, OrigenDato.PERFIL, True, CONFIANZA_LISTADO)
            ficha = FichaExtraida(
                resultado.url,
                resultado.titulo,
                precio,
                {"precio": valor},
                avisos=(AVISO_LISTADO,),
                obtenida=listado.obtenida,
                foto=resultado.foto,
            )
            ofertas.append(OfertaCandidata(self.dominio, ficha, resultado.url))
        return ofertas

    @staticmethod
    def _paso_extraccion(
        leidas: int,
        fallidas: int,
        sin_precio: int,
        con_ia: int,
        uso: "_UsoExtractor | None" = None,
    ) -> tuple[str, EstadoPaso, str]:
        partes = [f"{leidas} fichas: JSON-LD y selectores del perfil"]
        estado = EstadoPaso.OK
        uso = uso or _UsoExtractor()
        if uso.usos or con_ia:
            texto = f"extractor de respaldo con IA en {max(uso.usos, con_ia)}"
            if uso.usos > con_ia:
                texto += f" (aportó en {con_ia})"
            if uso.desde_cache:
                texto += f", {uso.desde_cache} desde la caché"
            partes.append(texto)
            estado = EstadoPaso.IA
        if uso.inutil:
            partes.append("la IA no aportó nada en la 1.ª ficha: no se usa en el resto")
        if uso.descartados:
            campos = ", ".join(f"«{c}»" for c in uso.descartados)
            partes.append(
                f"la IA deja de pedir {campos}: no salió anclado en {MAX_FALLOS_CAMPO} fichas"
            )
        if fallidas:
            partes.append(f"{fallidas} no se han podido abrir")
            estado = EstadoPaso.AVISO
        if sin_precio:
            partes.append(f"{sin_precio} sin precio")
            estado = EstadoPaso.AVISO
        return "extracción", estado, " · ".join(partes)

    @staticmethod
    def _paso_verificacion(no_anclados: int, avisos: Sequence[str]) -> tuple[str, EstadoPaso, str]:
        if no_anclados:
            detalle = f"{no_anclados} valores descartados: no aparecen en la página"
            if avisos:
                detalle += f" · {avisos[0]}"
            return "verificación", EstadoPaso.AVISO, detalle
        if avisos:
            return "verificación", EstadoPaso.AVISO, f"{len(avisos)} avisos: {avisos[0]}"
        return "verificación", EstadoPaso.OK, "Todos los valores anclados en la página"

    # --- Almacén ----------------------------------------------------------------------------

    def _actualizar_tienda(self) -> None:
        robots, permite = self.robots if self.robots is not None else (None, None)
        try:
            self.o._almacen.actualizar_tienda(
                self.dominio,
                estado=self.estado,
                robots=robots,
                robots_permite=permite,
                ultima_visita=self.o._reloj() if self.visitada else None,
                bloqueos=self.tienda.bloqueos + 1 if self.bloqueado else None,
            )
        except ErrorAlmacen as error:
            self.paso("tienda", EstadoPaso.AVISO, f"No se pudo actualizar la tienda: {error}")


@dataclass(frozen=True, slots=True)
class _UsoExtractor:
    """Lo que hizo el extractor de respaldo en las fichas de un paso (para el progreso)."""

    usos: int = 0
    desde_cache: int = 0
    inutil: bool = False
    descartados: tuple[str, ...] = ()
    """Campos que el extractor ha dejado de pedir en la tienda (A11)."""


class _Nombrable(Protocol):
    """Lo que se mira para saber si un resultado (del listado o de la API) nombra la consulta."""

    @property
    def titulo(self) -> str: ...

    @property
    def url(self) -> str: ...


def _recoger(tarea: "asyncio.Task[Pagina]") -> None:
    """Recoge el final de una ficha adelantada que ya no se usa (A2), para que asyncio no avise
    de una excepción sin recoger."""
    if not tarea.cancelled():
        tarea.exception()


def _escrito_en(texto: str, visible: str) -> bool:
    """`texto` (un precio, «17,22 €») sale tal cual en `visible`, sin una cifra o un separador
    pegados delante ni una cifra detrás: «113,69 €» o «3,695» no anclan «3,69».

    Es lo que se pide a un precio del listado (R3-H). Basta con que salga, aunque vaya pegado a
    una etiqueta («Precio17,22 €», con «Precio» oculto para los lectores de pantalla).
    """
    texto = _ESPACIOS.sub(" ", texto).strip()
    if not texto:
        return False
    return re.search(rf"(?<![\d.,]){re.escape(texto)}(?!\d)", visible) is not None


def _para_abrir[R: _Nombrable](resultados: Sequence[R], consulta: str) -> tuple[list[R], bool]:
    """Los resultados cuyas fichas se abren, en orden, y si salen del respaldo por referencia.

    Los que nombran la consulta, primero los que llevan la referencia (`_de_la_consulta` con
    `ordenar`): así no se abren fichas de otras piezas (los destacados de una tienda que ignora
    la búsqueda, bug 10).

    Respaldo: si la consulta lleva una referencia OEM y ningún título ni enlace la nombra, todos,
    primero los que nombran alguna palabra y después el resto en el orden de la tienda (se
    abren los `max_fichas` primeros, como antes de A2). Hay tiendas que encuentran la
    referencia en la ficha sin ponerla en el listado (Oscaro solo la cita entre las
    equivalencias), y es el emparejado el que decide con la ficha si es exacta, una revisión o
    una equivalente (B5).
    """
    elegidos = _de_la_consulta(resultados, consulta, ordenar=True)
    palabras, referencias = _terminos(consulta)
    if not referencias or any(_encaje(r, palabras, referencias)[0] for r in elegidos):
        return elegidos, False
    vistos = {id(r) for r in elegidos}
    return elegidos + [r for r in resultados if id(r) not in vistos], True


def _de_la_consulta[R: _Nombrable](
    resultados: Sequence[R], consulta: str, *, ordenar: bool = False
) -> list[R]:
    """Los resultados (de los datos estructurados, de un listado o de la API) que la nombran.

    El JSON embebido de una página sin resultados suele traer igualmente productos (los
    «destacados» o «más vendidos» del diseño de la tienda), con título, enlace y precio: si se
    usaran, cada búsqueda sin resultados abriría fichas de otras piezas y las daría como
    ofertas. Por eso cada resultado tiene que nombrar alguna palabra de la consulta (como en
    `parece_listado`) o alguna de sus referencias OEM (`_encaje`: sin separadores,
    «5k1-941-005-e», y con el criterio de revisión de B5) en su título o en la ruta de su
    enlace, que suele llevar el nombre o la referencia. Todo lineal: los títulos y rutas ya
    vienen recortados.

    Con `ordenar`, primero los que tienen la referencia exacta, luego los que la tienen con
    sufijo de revisión y después los que nombran más palabras (un listado trae de todo, y solo
    se abren unas pocas fichas); a igualdad, los que no parecen una pieza de lo buscado
    (`nucleo_nominal.pieza_de`: «cerradura de la puerta…» se abre después de la puerta) y
    después el orden de la tienda. No descarta nada por eso.
    """
    palabras, referencias = _terminos(consulta)
    if not palabras and not referencias:
        return list(resultados)
    puntuados: list[tuple[tuple[int, int, int], R]] = []
    for resultado in resultados:
        referencia, nombradas = _encaje(resultado, palabras, referencias)
        if referencia or nombradas:
            directo = 1 if not ordenar or pieza_de(resultado.titulo, consulta) is None else 0
            puntuados.append(((referencia, nombradas, directo), resultado))
    if ordenar:
        # `sorted` es estable: a igualdad se conserva el orden de la tienda.
        puntuados.sort(key=lambda par: par[0], reverse=True)
    return [resultado for _, resultado in puntuados]


def _terminos(consulta: str) -> tuple[list[str], list[str]]:
    """Palabras de la consulta (normalizadas, de `MIN_PALABRA` letras o más, sin repetir) y sus
    referencias OEM en minúsculas y sin separadores.

    La referencia y sus trozos con dígitos («941» de «1K0 941 005») no cuentan como palabras: se
    buscarían como subcadenas y nombraría la consulta cualquier otra referencia que los lleve.
    """
    consulta = consulta[:MAX_CONSULTA]
    referencias = sorted(r.lower() for r in referencias_de_consulta(consulta))
    palabras = list(
        dict.fromkeys(
            _singular(p)
            for p in normalizar(consulta).split()
            if len(p) >= MIN_PALABRA and not _de_referencia(p, referencias)
        )
    )
    return palabras[:MAX_PALABRAS_CONSULTA], referencias[:MAX_PALABRAS_CONSULTA]


def _singular(palabra: str) -> str:
    """La palabra (normalizada) sin la marca de plural, para buscarla como subcadena.

    «faros» → «faro», «amortiguadores» → «amortiguador», «luces» → «luz»: una consulta en plural
    no es subcadena de un título en singular, y sin esto se descartaba todo el listado. El
    singular sí lo es del plural, así que «faro» sigue nombrando «Faros delanteros». Quitar «es»
    de más no estorba («cables» → «cabl» sigue dentro de «cable»). Si quedaría más corta que
    `MIN_PALABRA`, se deja como está.
    """
    if palabra.endswith("ces"):
        raiz = palabra[:-3] + "z"
    elif palabra.endswith("es"):
        raiz = palabra[:-2]
    elif palabra.endswith("s"):
        raiz = palabra[:-1]
    else:
        return palabra
    return raiz if len(raiz) >= MIN_PALABRA else palabra


def _de_referencia(palabra: str, referencias: Sequence[str]) -> bool:
    """Si `palabra` es una de las `referencias` o un trozo con dígitos de alguna."""
    compacta = "".join(_TROZO.findall(palabra))
    return any(c.isdigit() for c in compacta) and any(compacta in r for r in referencias)


EXACTA, REVISION, NINGUNA = 2, 1, 0
"""Cómo lleva un resultado la referencia de la consulta (`_encaje`): la exacta se abre antes."""


def _encaje(
    resultado: _Nombrable, palabras: Sequence[str], referencias: Sequence[str]
) -> tuple[int, int]:
    """Cómo lleva `resultado` alguna de las referencias (`EXACTA`, `REVISION` o `NINGUNA`) y
    cuántas palabras nombra.

    La referencia cuenta con el criterio del emparejado (B5): entera o con un sufijo de revisión
    (`es_revision`), en trozos seguidos del título o de la ruta («1K0 941 005 E»,
    «faro-1k0-941-005-e»), pero no dentro de otra más larga («11K0941005», «1K09410051»).
    """
    texto = normalizar(f"{resultado.titulo} {urlsplit(resultado.url).path}")
    trozos = _TROZO.findall(texto)
    compacto = "".join(trozos)
    # La subcadena descarta deprisa (un listado hostil trae miles de trozos): los bordes solo se
    # miran en las referencias que salen.
    referencia = max((_nombra(trozos, r) for r in referencias if r in compacto), default=NINGUNA)
    return referencia, sum(p in texto for p in palabras)


def _nombra(trozos: Sequence[str], referencia: str) -> int:
    """Si `referencia` sale en `trozos` seguidos, de borde a borde: `EXACTA`, como revisión
    (`REVISION`) o `NINGUNA`.

    Desde cada trozo se van juntando los siguientes mientras sean el principio de la referencia:
    como mucho tantos como letras tiene, así que es lineal en el número de trozos (por cada
    referencia que sale como subcadena, que son pocas).
    """
    mejor = NINGUNA
    for inicio in range(len(trozos)):
        junto = ""
        for fin in range(inicio, len(trozos)):
            junto += trozos[fin]
            if junto == referencia:
                return EXACTA
            if es_revision(junto, referencia):
                mejor = REVISION
            if not referencia.startswith(junto):
                break
    return mejor


def _completos(resultados: Sequence[ResultadoListado], consulta: str) -> int:
    """Cuántos resultados nombran toda la consulta (o su referencia): con `max_fichas` de
    ellos no hace falta pedir más páginas del listado."""
    palabras, referencias = _terminos(consulta)
    if not palabras and not referencias:
        return len(resultados)
    total = 0
    for resultado in resultados:
        referencia, nombradas = _encaje(resultado, palabras, referencias)
        total += bool(referencia) or (bool(palabras) and nombradas == len(palabras))
    return total


def _busqueda_redirigida(pagina: Pagina, consulta: str) -> bool:
    """La búsqueda redirigió a otra página de la tienda que ya no es una búsqueda (R2-C12).

    Juguetilandia manda `/buscador.htm?search=lego` a su página de marca `/lego__s3.htm`: el
    perfil no saca resultados porque no los hay, no porque esté roto. Lo es si la ruta final no
    es la pedida (`pagina.url`, la plantilla rellenada), no es la portada (ni solo un prefijo de
    idioma) y la consulta no sigue en la URL final: ni en su parte de consulta (`nombra`, como
    inicio de palabra) ni como un tramo entero de la ruta. Así un buscador que la tienda ha
    movido (`/buscar?q=x` → `/search?q=x` o `/es/buscar/x`) y una plantilla obsoleta que manda
    a la portada siguen siendo un perfil roto y se reparan. La consulta en la URL la mira
    `descubrimiento.consulta_en_url`, la misma que usa el descubrimiento (causa R3-F2).
    """
    ruta = urlsplit(pagina.url_final).path.rstrip("/")
    if ruta == urlsplit(pagina.url).path.rstrip("/") or solo_idioma(ruta):
        return False
    return not consulta_en_url(pagina.url_final, consulta)


def _confirma_la_busqueda(pagina: Pagina, consulta: str) -> bool:
    """La página dice ser la búsqueda de `consulta` (R3-D): todas sus palabras (`palabras_de`,
    cada una como inicio de palabra con `nombra`) salen en el `value` de un campo de búsqueda
    (`type=search` o un texto con un nombre de `NOMBRES_BUSQUEDA`) **y** en el `<title>` o en un
    `<h1>`.

    Thomann titula sus resultados con la marca y el modelo («214ce»), pero su página repite
    «guitarra acústica» en el `<title>`, el `<h1>` y el buscador. Hacen falta las dos señales:
    una página de «0 resultados para X» con destacados suele repetir la consulta en el título y
    el `<h1>` (bug 10), y el campo lo puede rellenar la tienda con lo último que se buscó.
    """
    palabras = palabras_de(consulta[:MAX_CONSULTA])[:MAX_PALABRAS_CONSULTA]
    if not palabras:
        return False
    campo = titulo = False
    for nodo in analizar(pagina.html).descendientes():
        if nodo.etiqueta == "input" and not campo and _campo_de_busqueda(nodo):
            campo = _nombra_todas((nodo.atributo("value") or "")[:MAX_CONSULTA], palabras)
        elif nodo.etiqueta in ("title", "h1") and not titulo:
            titulo = _nombra_todas(nodo.texto()[:MAX_CONSULTA], palabras)
        if campo and titulo:
            return True
    return False


def _campo_de_busqueda(entrada: Nodo) -> bool:
    """Un `<input>` de búsqueda: `type=search` o un texto con un nombre típico (como en el
    descubrimiento)."""
    tipo = (entrada.atributo("type") or "text").strip().lower()
    nombre = (entrada.atributo("name") or "").strip().lower()
    return tipo == "search" or (tipo == "text" and nombre in NOMBRES_BUSQUEDA)


def _nombra_todas(texto: str, palabras: Sequence[str]) -> bool:
    return all(nombra((texto,), (palabra,), en_palabra=True) for palabra in palabras)


def _paginas_listado(plantilla: str) -> list[str]:
    """Las URL de las páginas que se recorren de un listado: la 1..`MAX_PAGINAS_LISTADO` si
    lleva `{pagina}` y, si no, solo la propia URL."""
    if MARCA_PAGINA not in plantilla:
        return [plantilla]
    return [plantilla.replace(MARCA_PAGINA, str(n), 1) for n in range(1, MAX_PAGINAS_LISTADO + 1)]


def _valida(candidata: OfertaCandidata) -> bool:
    """Oferta con un precio leído y anclado en la página (el único que cuenta como precio)."""
    return candidata.ficha.precio is not None
