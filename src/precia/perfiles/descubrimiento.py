"""Descubrimiento de la búsqueda de una tienda: qué URL da su página de resultados (nota 15 §D).

El configurador lo usa antes de pedir selectores a la IA. Las plantillas candidatas (con
`{consulta}`, como `Perfil.url_busqueda`) se prueban en este orden, de la más fiable a la menos:

0. La **plantilla del usuario** (A1, nota 16): la URL de entrada de la tienda, si lleva
   `{consulta}`. La ha pegado una persona, pero se filtra igual que una propuesta de la IA (del
   dominio de la tienda, `{consulta}` una vez, fuera del host y del fragmento, sin otras llaves).
1. El descriptor **OpenSearch** que enlaza la portada (`<link rel="search"
   type="application/opensearchdescription+xml">`): es la propia tienda diciendo cómo se busca.
2. Los **formularios** de búsqueda GET de la portada.
3. Los **enlaces de búsqueda** de la portada (rutas con `search`, `buscar`, `busqueda`,
   `catalogsearch` o `recherche`). Si el enlace ya lleva un parámetro de búsqueda conocido se usa
   ese; si no, se prueba con `q`, `s`, `query`, `search`, `keyword` y `text`. Es lo que hay en
   las tiendas hechas con Next.js o Vue, que pintan el buscador con JavaScript y no tienen
   `<form>` en el HTML.
4. Las rutas de búsqueda de la **plataforma** detectada (`fuentes.plataformas`).
5. Los **patrones fijos** de siempre (`PATRONES`), sin los propios de otra plataforma si la
   portada delata una (`patrones_para`, A4 del plan de velocidad).
5b. Si un formulario de búsqueda no tiene `action`, el nombre de su campo en las rutas de
   búsqueda habituales (`plantillas_sin_accion`): su JavaScript suele llevar a otra página.
5c. Si una candidata redirige a otra ruta de búsqueda y pierde la consulta por el camino
   (`ruta_tras_redirigir`; El Corte Inglés: `/search?q=` → `/search-nwx/`), esa ruta con los
   `PARAMETROS_ENLACE`, detrás de todo lo anterior (causa R3-G de la campaña multi-sector).
6. La **IA** propone hasta `MAX_PLANTILLAS_IA` plantillas a partir de un resumen de la
   portada (sus formularios, con el id, placeholder y aria-label de cada campo, y sus enlaces).
   Lo que devuelve es un dato no
   confiable más: solo se aceptan plantillas del dominio de la tienda, con `{consulta}` una vez y
   fuera del fragmento, y cada una pasa por `validar_url` y por `parece_listado` antes de usarla.
   Si ni la URL de entrada ni la portada dan candidatas propias (pasos 0-3), se le pregunta
   **en paralelo** con la ronda HTTP de las demás y sus plantillas se prueban por HTTP en cuanto
   esa ronda acaba, **antes de la ronda de Chromium** (B7 del plan de velocidad, decisión de
   Julio): la IA no pide nada a la tienda y una llamada cuesta de 0,0007 a 0,0015 USD. Si gana
   antes una candidata, se deja de esperar a la IA (`_descartar`; la llamada se cobra igual). Si
   la portada sí da candidatas, la IA sigue siendo el último recurso, después de Chromium.

Si la portada por HTTP no trae ni formularios ni enlaces de búsqueda (ni OpenSearch), se mira la
versión renderizada con Chromium, que es donde están en una tienda que se pinta con JavaScript.
Cada candidata se prueba primero por HTTP y, solo si ninguna da un listado, con Chromium; las que
por HTTP dieron un estado de `ESTADOS_DEFINITIVOS` (un 404, por ejemplo) no se repiten. Tampoco
las que por HTTP redirigieron a una URL que ya no lleva la consulta (`perdio_la_consulta`), que no
se aceptan nunca, ni las que acabaron así en la portada o en un prefijo de idioma
(`_redirigio_sin_la_consulta`, A3 del plan de velocidad: esas sí se evalúan por HTTP), ni más de
una de las que acaban en la misma URL final (causa R3-F2: en
desguacesdli, siete rutas que acaban en el catálogo general se repetían todas con Chromium y se
agotaba el plazo de la tienda).

Una candidata vale si `parece_listado` en modo `estricto`: la consulta tiene que salir en el
`<title>` o en los resultados (no en los menús ni en las facetas) y la página no puede ser la
portada con unos pocos enlaces cambiados, comparada con la portada renderizada igual que ella
(`_Portadas`). Es la causa C2 de la campaña multi-sector: se aceptaban la portada de Ecooparts
con su carrusel, el cascarón JavaScript de PcComponentes y una búsqueda que ignora la consulta.

Todo sale de páginas no confiables: hay topes de formularios, enlaces y plantillas (una portada no
puede multiplicar las descargas), solo se aceptan URL `http(s)` de la propia tienda
(`extraccion.listado.url_de_la_tienda`) y no se usa ninguna expresión regular sobre el HTML que
pueda retroceder de forma cuadrática. El descriptor OpenSearch se lee con el analizador de HTML
propio (`extraccion.dom`), que no expande entidades externas ni definidas en el documento.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import (
    parse_qsl,
    quote_plus,
    unquote,
    unquote_plus,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from precia.extraccion.anclaje import normalizar
from precia.extraccion.dom import Nodo, analizar, oculto
from precia.extraccion.listado import url_de_la_tienda
from precia.extraccion.precios import buscar_precios
from precia.fuentes.plataformas import (
    Plataforma,
    detectar_plataforma,
    origen_de,
    plantillas_html,
)
from precia.ia.tipos import ErrorIA, PeticionIA, PresupuestoAgotado, max_tokens_para
from precia.navegador.tipos import ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import Validador, validar_url
from precia.perfiles.protocolos import Navegador, SesionIA
from precia.perfiles.tipos import ConfiguracionFallida
from precia.perfiles.validador import (
    MARCADOR,
    consulta_fuera_del_host,
    de_la_tienda,
    url_de_busqueda,
)

COMPONENTE = "configurador"
"""La propuesta de plantillas es parte de configurar la tienda: cuenta como el configurador."""
PATRONES = (
    "/busqueda?controller=search&s={consulta}",  # PrestaShop
    "/buscar?q={consulta}",
    "/search?q={consulta}",
    "/?s={consulta}&post_type=product",  # WooCommerce
    "/catalogsearch/result/?q={consulta}",  # Magento
    "/buscar/{consulta}",
)
"""Rutas de búsqueda habituales, en el orden en que se prueban si nada de lo anterior vale."""
PATRONES_DE_PLATAFORMA: dict[str, Plataforma] = {
    PATRONES[0]: Plataforma.PRESTASHOP,
    PATRONES[3]: Plataforma.WOOCOMMERCE,
    PATRONES[4]: Plataforma.MAGENTO,
}
"""Los `PATRONES` propios de una plataforma: si la portada delata otra de estas, no se prueban
(A4 del plan de velocidad; ver `patrones_para`)."""
MAX_FORMULARIOS = 3
"""Plantillas de formulario que se prueban como mucho: cada una cuesta dos descargas."""
MAX_OCULTOS = 5
MAX_VALOR_OCULTO = 100
RUTAS_DE_BUSQUEDA = ("search", "buscar", "busqueda", "catalogsearch", "recherche")
"""Palabras con las que empieza algún tramo de una ruta de búsqueda (`_es_ruta_de_busqueda`)."""
PARAMETROS_ENLACE = ("q", "s", "query", "search", "keyword", "text")
"""Nombres del parámetro de búsqueda que se prueban con un enlace que no lleva ninguno."""
NOMBRES_BUSQUEDA = frozenset(PARAMETROS_ENLACE) | {
    "buscar",
    "busqueda",
    "k",
    "keywords",
    "search_query",
    "searchterm",
    "term",
    "texto",
}
"""Nombres (en minúsculas) del campo de texto de un formulario que lo hacen el de búsqueda.

Siempre contiene `PARAMETROS_ENLACE` (todos tus libros usa `keyword`). Reconocer un nombre más
no cuesta descargas; añadirlo a `PARAMETROS_ENLACE` sí (una plantilla más por enlace).
"""
MAX_ENLACES = 2
"""Enlaces de búsqueda distintos de la portada que se prueban como mucho."""
RUTAS_SIN_ACCION = ("/buscar/", "/search", "/busqueda")
MAX_RUTAS_SIN_ACCION = len(RUTAS_SIN_ACCION)
MAX_RUTAS_REDIRIGIDAS = 1
"""Rutas de búsqueda que pierden la consulta al redirigir de las que se sacan plantillas (cada
una da `len(PARAMETROS_ENLACE)` candidatas más, ver `ruta_tras_redirigir`)."""
MAX_PLANTILLAS_ENLACES = len(PARAMETROS_ENLACE)
"""Plantillas que salen de los enlaces en total: cada una cuesta dos descargas."""
MAX_ENLACES_REVISADOS = 5_000
"""Enlaces de la portada que se miran como mucho (una portada enorme no puede costar segundos)."""
MAX_HREF = 2_000
MAX_PLANTILLA = 500
MAX_PLANTILLAS_IA = 3
MAX_URLS_OPENSEARCH = 20
MAX_FORMULARIOS_RESUMEN = 10
MAX_CAMPOS_RESUMEN = 10
MAX_ATRIBUTO_RESUMEN = 40
MAX_ENLACES_RESUMEN = 150
MAX_TEXTO_ENLACE = 80
RECHAZOS = frozenset({401, 429, 451})
"""La tienda dice que no y otro cliente no lo cambia: 401, pide credenciales (nunca se inicia
sesión); 429, limita las peticiones; 451, motivo legal o geográfico. El 403 no está (ADR-023):
suele ser el WAF rechazando la huella del cliente HTTP, y con Chromium puede pasar. Un 403 pequeño
ya lo convierte el navegador en `Bloqueado` y lo rescata él con Chromium; lo que ya vino de
Chromium (`Pagina.por_chromium`) no se vuelve a pedir con `js=True` (ver `_probar`). Incluye
`navegador.bloqueo.ESTADOS_SIN_RESCATE`."""
ESTADOS_DEFINITIVOS = frozenset({400, 404, 410, 414, 501}) | RECHAZOS
"""Estados por HTTP con los que una candidata no se repite con Chromium (ver `_probar`)."""
MIN_PRODUCTOS = 2
"""Enlaces a productos con precio para que una página parezca un listado."""
MIN_PROPORCION_NUEVOS = 0.05
"""Parte de los enlaces de la tienda que no tienen que estar en la portada (ver `parece_listado`).
Medido en la ronda 1 de la campaña multi-sector: Ecooparts, que devolvía la portada, 1 %;
PcComponentes por HTTP, 0 %; búsquedas buenas, del 14 % (Foot Locker) al 23 % (Primor)."""
MAX_TEXTO_CONTENEDOR = 600
NIVELES_CONTENEDOR = 5
"""Contenedores que se miran desde el enlace (él incluido) buscando el precio de su «tarjeta».
Causa R4-G de la campaña multi-sector: en El Corte Inglés el precio está en el 5.º
(`div.product_preview-ppal`, hermano de `div.title > div.product_preview-info > h2 > a`). No se
sube a 6: el límite también es una guarda, porque en una ficha de Coolmod el enlace de marca llega
con 6 al precio del producto principal sin pasar de `MAX_TEXTO_CONTENEDOR` ni cruzar otro enlace."""
MIN_PALABRA = 3
TIPO_OPENSEARCH = "application/opensearchdescription+xml"
TERMINOS = "{searchTerms}"

ORIGEN_OPENSEARCH = "OpenSearch"
ORIGEN_FORMULARIO = "formulario"
ORIGEN_ENLACE = "enlace de la portada"
ORIGEN_PLATAFORMA = "plataforma"
ORIGEN_PATRON = "patrón habitual"
ORIGEN_IA = "propuesta de la IA"
ORIGEN_USUARIO = "URL de entrada del usuario"
ORIGEN_REDIRECCION = "ruta de búsqueda tras redirigir"

# Un parámetro opcional de OpenSearch (`{startPage?}`, `{os:count?}`): se quita de la plantilla.
_OPCIONAL = re.compile(r"\{[A-Za-z0-9:_.-]{1,50}\?\}")
_ESPACIOS = re.compile(r"\s+")
_IDIOMA = re.compile(r"[a-z]{2}(?:[-_][a-z]{2})?")
"""Un tramo de ruta que es solo un prefijo de idioma (`es`, `es-es`, `en_GB`)."""
_NO_ALFANUMERICOS = re.compile(r"[^a-z0-9]")
# Caracteres que no pueden ir en una plantilla propuesta por la IA: espacios, comillas, `<`, `>`,
# barra invertida, acento grave, `|` y `^` (cada analizador de URL los entiende a su manera).
_PROHIBIDOS = frozenset(" \t\r\n\"'<>\\`|^")

ESQUEMA_PLANTILLAS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plantillas": {
            "type": "array",
            "description": "URL de la página de resultados de búsqueda, con {consulta}",
            "items": {"type": "string", "maxLength": MAX_PLANTILLA},
            "maxItems": MAX_PLANTILLAS_IA,
        }
    },
    "required": ["plantillas"],
    "additionalProperties": False,
}
INSTRUCCIONES_PLANTILLAS = (
    "Eres el configurador de perfiles de PrecIA. Los datos son un resumen de la portada de una "
    "tienda online de recambios: la URL de la portada, sus formularios y sus enlaces. No se ha "
    "encontrado la página de resultados de búsqueda de la tienda. Propón como mucho "
    f"{MAX_PLANTILLAS_IA} plantillas de URL de esa página, de la más probable a la menos, con "
    "{consulta} en el lugar del texto buscado (por ejemplo https://tienda.es/buscar?q={consulta})"
    ". Solo URL del mismo dominio que la portada. Si no lo puedes deducir de los datos, devuelve "
    "una lista vacía: no inventes."
)


@dataclass(frozen=True, slots=True)
class Busqueda:
    """La búsqueda descubierta: su plantilla, la primera página de resultados y de dónde salió."""

    plantilla: str
    pagina: Pagina
    necesita_js: bool
    origen: str
    """Uno de los `ORIGEN_*`: la persona que revisa el perfil sabe así si la URL la puso la IA."""


async def abrir(
    navegador: Navegador, url: str, *, js: bool, validador: Validador = validar_url
) -> Pagina | None:
    """La página, o `None` si no se puede abrir. Los bloqueos sí se propagan: hay que parar."""
    try:
        return await navegador.obtener(await validador(url), js=js)
    except (ErrorDescarga, URLNoPermitida):
        return None


async def abrir_portada(
    host: str, dominio: str, navegador: Navegador, *, validador: Validador = validar_url
) -> Pagina | None:
    """La portada de la tienda por HTTP, o `None` si no responde bien o redirige fuera."""
    portada = await abrir(navegador, f"https://{host}/", js=False, validador=validador)
    return portada if portada is not None and _de_la_tienda_y_bien(portada, dominio) else None


def _de_la_tienda_y_bien(pagina: Pagina, dominio: str) -> bool:
    """Responde 2xx y sigue en la tienda tras las redirecciones."""
    return correcta(pagina) and de_la_tienda(pagina, dominio)


async def descubrir(
    portada: Pagina | None,
    host: str,
    dominio: str,
    navegador: Navegador,
    consulta: str,
    *,
    sesion: SesionIA | None = None,
    validador: Validador = validar_url,
    entrada: str | None = None,
) -> Busqueda:
    """La búsqueda de la tienda, o `ConfiguracionFallida`. Ver el docstring del módulo.

    `portada` es la de `abrir_portada` (`None` si no se pudo abrir). Sin `sesion` no se pide
    nada a la IA. `PresupuestoAgotado` se propaga; cualquier otro error de la IA solo significa
    que no ha propuesto nada. `entrada` es la URL de entrada del usuario: si es una plantilla con
    `{consulta}` válida, es la primera candidata (`ORIGEN_USUARIO`).
    """
    base = _base(portada, host)
    candidatas: list[tuple[str, str]] = []
    motivo = ""
    if entrada is not None and MARCADOR in entrada:
        plantilla = plantilla_de_entrada(entrada, dominio)
        if plantilla is None:
            motivo = " La URL de entrada no es una plantilla de búsqueda válida de la tienda."
        else:
            candidatas.append((plantilla, ORIGEN_USUARIO))
            motivo = " Tampoco da resultados la URL de entrada."
    resumida = portada
    portadas = _Portadas(portada, dominio, navegador, validador)
    if portada is not None:
        descriptores: set[str] = set()
        de_portada = await _de_la_portada(portada, dominio, navegador, validador, descriptores)
        if not portada.por_chromium and _solo_por_rol(de_portada, portada, dominio):
            renderizada = await portadas.renderizada()
            if renderizada is not None:
                # Las de la renderizada primero, como antes del criterio por rol.
                de_portada = (
                    await _de_la_portada(renderizada, dominio, navegador, validador, descriptores)
                    + de_portada
                )
                resumida = renderizada
        candidatas.extend(de_portada)
        sin_candidatas = not candidatas
        plataforma = detectar_plataforma(portada)
        if plataforma is not None:
            candidatas.extend((p, ORIGEN_PLATAFORMA) for p in plantillas_html(plataforma, base))
    else:
        plataforma = None
        sin_candidatas = False
    candidatas.extend((base + patron, ORIGEN_PATRON) for patron in patrones_para(plataforma))
    if resumida is not None:
        # Detrás de los patrones: no retrasan a las tiendas que ya se configuraban.
        sin_accion = plantillas_sin_accion(resumida, dominio)
        candidatas.extend((p, ORIGEN_FORMULARIO) for p in sin_accion)

    anticipada: asyncio.Task[list[str]] | None = None
    # B7: la IA no pide nada a la tienda; responde mientras se prueban las demás. Una portada sin
    # pistas no (amazon.es a medias): la IA verá después la página con el buscador, si sale.
    if (
        sesion is not None
        and resumida is not None
        and sin_candidatas
        and _resumen(resumida, dominio)[1]
    ):
        anticipada = asyncio.create_task(plantillas_de_la_ia(resumida, dominio, sesion))

    async def de_la_ia_anticipada() -> list[tuple[str, str]]:
        nonlocal motivo
        assert anticipada is not None
        try:
            propuestas = await anticipada
        except PresupuestoAgotado:
            raise
        except ErrorIA as error:
            motivo += f" La IA tampoco ha podido proponer una: {error}"
            return []
        return [(p, ORIGEN_IA) for p in propuestas]

    unicas = _sin_repetir(candidatas)
    probadas = _Probadas()
    try:
        encontrada = await _probar(
            unicas,
            dominio,
            navegador,
            consulta,
            portadas,
            validador,
            probadas,
            antes_de_chromium=de_la_ia_anticipada if anticipada is not None else None,
        )
    finally:
        if anticipada is not None:
            _descartar(anticipada)
    if encontrada is not None:
        return encontrada
    if probadas.con_buscador is not None and (
        resumida is None or not _resumen(resumida, dominio)[1]
    ):
        # La IA ve la página con el buscador, no una portada sin pistas (amazon.es, a medias).
        resumida = probadas.con_buscador
    if sesion is not None and resumida is not None and anticipada is None:
        try:
            propuestas = await plantillas_de_la_ia(resumida, dominio, sesion)
        except PresupuestoAgotado:
            raise
        except ErrorIA as error:
            propuestas = []
            motivo += f" La IA tampoco ha podido proponer una: {error}"
        nuevas = [(p, ORIGEN_IA) for p in propuestas if p not in probadas.plantillas]
        encontrada = await _probar(
            nuevas, dominio, navegador, consulta, portadas, validador, probadas
        )
        if encontrada is not None:
            return encontrada
    raise ConfiguracionFallida(
        f"No se encuentra la página de búsqueda de {dominio}: ni la portada tiene un "
        "buscador que funcione ni responde ninguna de las rutas habituales." + motivo
    )


def _descartar(tarea: "asyncio.Task[list[str]]") -> None:
    """Deja de esperar a la propuesta anticipada de la IA (B7) sin perder nada.

    Si sigue en marcha se cancela: `ServicioIA` deja terminar la llamada al proveedor y la
    registra igual (el gasto del mes no se queda corto). Su resultado o su error se leen para que
    `asyncio` no avise de una excepción sin recoger.
    """
    if not tarea.done():
        tarea.cancel()
        tarea.add_done_callback(_recoger)
    else:
        _recoger(tarea)


def _recoger(tarea: "asyncio.Task[list[str]]") -> None:
    if not tarea.cancelled():
        tarea.exception()


def patrones_para(plataforma: Plataforma | None) -> tuple[str, ...]:
    """Los `PATRONES` que se prueban en una tienda de `plataforma` (A4 del plan de velocidad).

    Si es una de `PATRONES_DE_PLATAFORMA`, sin las rutas propias de las otras: cada una cuesta una
    descarga y su cortesía (druni, PrestaShop, gastaba así 9 s de 52). Las genéricas se quedan, y
    la ruta de la propia plataforma ya entra por `plantillas_html`. Sin plataforma detectada, o con
    otra (WordPress puede ser un WooCommerce que no se delata), todas: ecooparts ganó con la de
    WooCommerce sin que se detectara.
    """
    if plataforma not in PATRONES_DE_PLATAFORMA.values():
        return PATRONES
    return tuple(p for p in PATRONES if PATRONES_DE_PLATAFORMA.get(p, plataforma) == plataforma)


def _solo_por_rol(candidatas: Sequence[tuple[str, str]], portada: Pagina, dominio: str) -> bool:
    """No hay candidatas, o todas son formularios cuyo campo solo se reconoce por su rol.

    Entonces se mira también la portada renderizada. Encontrado por un revisor del arreglo de
    amazon.es: un `<form role="search">` sin `action` y con un nombre de campo poco típico (su
    JavaScript intercepta el envío) daba una plantilla, y con ella ya no se renderizaba la
    portada, que es donde estaba el formulario de verdad.
    """
    seguras = set(plantillas_de_formularios(portada, dominio, por_rol=False))
    return all(origen == ORIGEN_FORMULARIO and p not in seguras for p, origen in candidatas)


async def _de_la_portada(
    portada: Pagina,
    dominio: str,
    navegador: Navegador,
    validador: Validador,
    descriptores: set[str],
) -> list[tuple[str, str]]:
    """Candidatas que salen de la propia portada: OpenSearch, formularios y enlaces.

    `descriptores` son los descriptores OpenSearch ya pedidos: la portada renderizada con Chromium
    suele traer el mismo `<link>` en la cabecera, y si no dio una plantilla la primera vez tampoco
    la dará la segunda (se piden siempre por HTTP).
    """
    candidatas: list[tuple[str, str]] = []
    descriptor = enlace_opensearch(portada, dominio)
    if descriptor is not None and descriptor not in descriptores:
        descriptores.add(descriptor)
        pagina = await abrir(navegador, descriptor, js=False, validador=validador)
        if pagina is not None and _de_la_tienda_y_bien(pagina, dominio):
            plantilla = plantilla_opensearch(pagina, dominio)
            if plantilla is not None:
                candidatas.append((plantilla, ORIGEN_OPENSEARCH))
    candidatas.extend((p, ORIGEN_FORMULARIO) for p in plantillas_de_formularios(portada, dominio))
    candidatas.extend((p, ORIGEN_ENLACE) for p in plantillas_de_enlaces(portada, dominio))
    return candidatas


@dataclass(slots=True)
class _Portadas:
    """La portada con la que se compara cada candidata, renderizada igual que ella.

    Una candidata que va por Chromium trae lo que pinta el JavaScript de la tienda (destacados,
    menús): comparada con la portada por HTTP, todo eso parecería nuevo. La portada renderizada
    se pide una sola vez y solo si hace falta (o ya la pidió `descubrir`); si no se puede abrir,
    se compara con la de HTTP.
    """

    http: Pagina | None
    dominio: str
    navegador: Navegador
    validador: Validador
    _chromium: Pagina | None = None
    _pedida: bool = False

    async def renderizada(self) -> Pagina | None:
        """La portada renderizada con Chromium (la misma si ya vino por Chromium), o `None`."""
        if self.http is None or self.http.por_chromium:
            return self.http
        if not self._pedida:
            self._pedida = True
            pagina = await abrir(self.navegador, _url(self.http), js=True, validador=self.validador)
            if pagina is not None and _de_la_tienda_y_bien(pagina, self.dominio):
                self._chromium = pagina
        return self._chromium

    async def para(self, pagina: Pagina, *, js: bool) -> Pagina | None:
        """La portada con la que se compara `pagina` (`None` si no hay portada)."""
        if js or pagina.por_chromium:
            return await self.renderizada() or self.http
        return self.http


@dataclass(slots=True)
class _Probadas:
    """Lo ya probado en un descubrimiento, compartido por la ronda normal y la de la IA."""

    plantillas: set[str] = field(default_factory=set)
    derivadas: int = 0
    """Rutas de las que ya se han sacado plantillas (`ruta_tras_redirigir`)."""
    finales: dict[str, str] = field(default_factory=dict)
    """URL pedida por HTTP → la clave de su URL final (`_clave_final`)."""
    evaluadas: set[str] = field(default_factory=set)
    """Claves de URL final que ya se han evaluado por HTTP."""
    sin_chromium: set[str] = field(default_factory=set)
    """Claves de URL final que ya fueron por Chromium o que no tienen que ir (causa R3-F2)."""
    con_buscador: Pagina | None = None
    """La página de la tienda (no la portada) de cuyos formularios se han sacado plantillas."""


async def _probar(
    candidatas: Sequence[tuple[str, str]],
    dominio: str,
    navegador: Navegador,
    consulta: str,
    portadas: _Portadas,
    validador: Validador,
    probadas: _Probadas,
    *,
    antes_de_chromium: Callable[[], Awaitable[list[tuple[str, str]]]] | None = None,
) -> Busqueda | None:
    """La primera candidata que da un listado: primero todas por HTTP (barato) y, solo si
    ninguna lo da, con Chromium.

    `antes_de_chromium` da más candidatas cuando se acaban las de HTTP (B7: las plantillas que
    la IA ha propuesto mientras tanto). Las que no se han probado ya se prueban por HTTP antes de
    la ronda de Chromium, y en esa ronda entran como las demás.

    Si por HTTP una candidata redirige a otra ruta de búsqueda de la tienda y por el camino
    pierde la consulta (`ruta_tras_redirigir`), esa ruta con los `PARAMETROS_ENLACE` se añade al
    final de la lista (`ORIGEN_REDIRECCION`, causa R3-G de la campaña multi-sector): se prueba
    detrás de las demás, primero por HTTP y luego con Chromium. Como mucho de
    `MAX_RUTAS_REDIRIGIDAS` rutas en todo el descubrimiento.

    Con Chromium no se repiten las que por HTTP respondieron con un estado de
    `ESTADOS_DEFINITIVOS`: el estado no depende del cliente, y `parece_listado` exige un 2xx del
    documento, así que aunque su JavaScript pintara resultados encima de la página de error no se
    aceptaría. 404 y 410: la ruta no existe; 400 y 414: el servidor rechaza la propia URL; 501: no
    admite el GET; y los `RECHAZOS`. Sí se repiten un 2xx que no era un listado (puede pintarlo el
    JavaScript), un 403 grande (puede ser el WAF rechazando la huella del cliente HTTP) y los 5xx
    (pueden ser pasajeros). Los bloqueos (`Bloqueado`) ni llegan aquí: el navegador ya ha probado
    una vez con Chromium y, si sigue bloqueado, los lanza.

    Tampoco se repite lo que ya fue por Chromium: en una tienda rescatada (ADR-023), `js=False`
    va por Chromium desde la página rescatada (`Pagina.por_chromium`, o la portada) en adelante,
    también las que fallan, y repetirlas sería otra descarga igual contra la tienda.

    Causa R3-F2 de la campaña multi-sector: una candidata que por HTTP redirige a una URL que ya
    no lleva la consulta (`perdio_la_consulta`: el catálogo general, una ficha) no se acepta nunca
    (aunque liste productos que la nombren, no son sus resultados) ni se repite con Chromium (la
    redirección del servidor lo llevaría al mismo sitio). Y de las candidatas que por HTTP acaban
    en la misma URL final (`_clave_final`) solo se evalúa la primera, y solo esa puede ir por
    Chromium. Lo ya visto se guarda en `probadas`, que comparten la ronda normal y la de la IA.

    A3 del plan de velocidad: tampoco se repite con Chromium una candidata que por HTTP dio un 2xx
    tras redirigir a la portada o a un prefijo de idioma sin la consulta
    (`_redirigio_sin_la_consulta`); por HTTP se evalúa igual, comparada con la portada.
    """
    sin_repetir: set[str] = set()
    portada = portadas.http
    por_chromium = portada is not None and portada.por_chromium
    lista = list(candidatas)
    probadas.plantillas.update(p for p, _ in lista)
    for js in (False, True):
        indice = 0
        # La lista puede crecer con las derivadas y, al final de la ronda HTTP, con las de
        # `antes_de_chromium`.
        while indice < len(lista) or (not js and antes_de_chromium is not None):
            if indice >= len(lista):
                assert antes_de_chromium is not None
                extra = await antes_de_chromium()
                antes_de_chromium = None
                nuevas_extra = [c for c in _sin_repetir(extra) if c[0] not in probadas.plantillas]
                probadas.plantillas.update(p for p, _ in nuevas_extra)
                lista.extend(nuevas_extra)
                continue
            plantilla, origen = lista[indice]
            indice += 1
            url = url_de_busqueda(plantilla, consulta)
            clave = probadas.finales.get(url)
            if js and (url in sin_repetir or clave in probadas.sin_chromium):
                continue
            pagina = await abrir(navegador, url, js=js, validador=validador)
            if js:
                if clave is not None:
                    probadas.sin_chromium.add(clave)
            else:
                # La marca de la tienda no se quita: lo que viene detrás también va por Chromium.
                por_chromium = por_chromium or (pagina is not None and pagina.por_chromium)
                if por_chromium:
                    sin_repetir.add(url)
            if pagina is None:
                continue
            perdida = False
            if not js:
                clave = probadas.finales[url] = _clave_final(pagina)
                if clave in probadas.evaluadas:
                    continue  # La misma página que otra candidata: ya se ha visto que no vale.
                probadas.evaluadas.add(clave)
                # Con lo que trae Chromium no: el JavaScript puede cambiar la URL sin redirigir.
                perdida = not pagina.por_chromium and perdio_la_consulta(pagina, consulta)
                if not pagina.por_chromium and _redirigio_sin_la_consulta(pagina, consulta):
                    sin_repetir.add(url)  # A3: la redirección del servidor no cambia con Chromium.
            # Primero lo que no cuesta descargas; la portada renderizada solo si hace falta.
            if not perdida and parece_listado(
                pagina, dominio, consulta, portada=None, estricto=True
            ):
                referencia = await portadas.para(pagina, js=js)
                if referencia is None or no_es_la_portada(pagina, referencia, dominio, consulta):
                    return Busqueda(plantilla, pagina, js, origen)
            if perdida or pagina.estado in ESTADOS_DEFINITIVOS:
                sin_repetir.add(url)
            if not js and url in sin_repetir and clave is not None:
                probadas.sin_chromium.add(clave)
            if not js and probadas.derivadas < MAX_RUTAS_REDIRIGIDAS:
                ruta = ruta_tras_redirigir(pagina, dominio, consulta, portada)
                if ruta is not None:
                    probadas.derivadas += 1
                    nuevas = [
                        p for p in _plantillas_de_enlace(ruta, "") if _es_de_la_tienda(p, dominio)
                    ]
                    nuevas = [p for p in nuevas if p not in probadas.plantillas]
                    probadas.plantillas.update(nuevas)
                    lista.extend((p, ORIGEN_REDIRECCION) for p in nuevas)
            if not js and not perdida and probadas.con_buscador is None:
                nuevas = _del_buscador(pagina, dominio, probadas.plantillas)
                if nuevas:
                    probadas.con_buscador = pagina
                    probadas.plantillas.update(nuevas)
                    lista.extend((p, ORIGEN_FORMULARIO) for p in nuevas)
    return None


def _del_buscador(pagina: Pagina, dominio: str, probadas: set[str]) -> list[str]:
    """Las plantillas nuevas de los formularios de una página de la tienda que no es un listado.

    Encontrado por Julio al probar la app con amazon.es: la portada llegó a medias (solo el
    `<head>`, tras el desafío anti-bot) y la página completa, con el buscador, la devolvió después
    una de las rutas habituales; solo se miraba si era un listado. Ahora sus formularios dan
    candidatas, detrás de las demás. Solo de una página por descubrimiento
    (`_Probadas.con_buscador`): las demás páginas de una tienda suelen traer el mismo buscador, y
    no puede multiplicar las descargas.
    """
    if not _de_la_tienda_y_bien(pagina, dominio):
        return []
    return [p for p in plantillas_de_formularios(pagina, dominio) if p not in probadas]


def ruta_tras_redirigir(
    pagina: Pagina, dominio: str, consulta: str, portada: Pagina | None
) -> str | None:
    """Origen y ruta final de una búsqueda que redirigió a otra ruta de búsqueda sin la consulta.

    El Corte Inglés manda `/search?q=filtro` a `/search-nwx/`, una página vacía: la búsqueda
    está en esa ruta, pero el parámetro se ha perdido (causa R3-G). Vale si la página responde
    2xx y sigue en la tienda, su ruta final no es la pedida ni la de la portada, es una ruta de
    búsqueda (`_es_ruta_de_busqueda`; así no cuentan las rutas inexistentes que mandan a la
    portada, a la ayuda o al login) y la consulta ya no sale en la URL final: ni en su parte de
    consulta ni en los tramos de la ruta (`nombra` como inicio de palabra). Si no, `None`.
    """
    if not _de_la_tienda_y_bien(pagina, dominio):
        return None
    final = urlsplit(_url(pagina))
    ruta = final.path.rstrip("/")
    if not ruta or ruta == urlsplit(pagina.url).path.rstrip("/"):
        return None
    if portada is not None and ruta == urlsplit(_url(portada)).path.rstrip("/"):
        return None
    if not _es_ruta_de_busqueda(ruta):
        return None
    palabras = palabras_de(consulta) or normalizar(consulta).split()
    textos = (unquote_plus(final.query), _slug(_url(pagina)))
    if nombra(textos, palabras, en_palabra=True):
        return None
    return urlunsplit((final.scheme, final.netloc, final.path, "", ""))


def _es_ruta_de_busqueda(ruta: str) -> bool:
    """Algún tramo de `ruta` empieza por una palabra de `RUTAS_DE_BUSQUEDA`.

    Se compara el tramo entero, sin trocearlo por guiones: `/search-nwx/`, `/es/busqueda`,
    `/search.html` o `/catalogsearch/result` valen; `/es/content/344-busquedas-interesantes` (un
    enlace CMS de Kenay Home, causa R4-E), `/opensearch.xml` o `/blog/yogasearcher` no. A cambio
    ya no cuentan las rutas con la palabra al final (`/advanced-search`): el formulario y los
    demás orígenes siguen cubriéndolas.
    """
    return any(tramo.startswith(RUTAS_DE_BUSQUEDA) for tramo in ruta.lower().split("/"))


def consulta_en_url(url: str, consulta: str) -> bool:
    """La consulta sale en `url`: en su parte de consulta (`nombra`, como inicio de palabra) o
    como un tramo entero de la ruta, sin separadores (`/buscar/filtro-aceite`). No se mira el
    fragmento, que no llega al servidor. Lo usan `perdio_la_consulta` y el orquestador.
    """
    partes = urlsplit(url)
    palabras = palabras_de(consulta) or normalizar(consulta).split()
    if nombra((unquote_plus(partes.query),), palabras, en_palabra=True):
        return True
    compacta = _sin_separadores(consulta)
    tramos = (t for t in partes.path.split("/") if t)
    return bool(compacta) and any(_sin_separadores(unquote(t)) == compacta for t in tramos)


def solo_idioma(ruta: str) -> bool:
    """La ruta es la de la portada o solo un prefijo de idioma (`/`, `/es/`, `/es-es`)."""
    return all(_IDIOMA.fullmatch(t.lower()) for t in ruta.split("/") if t)


def perdio_la_consulta(pagina: Pagina, consulta: str) -> bool:
    """La URL pedida llevaba la consulta y la tienda redirigió a otra que ya no la lleva.

    desguacesdli manda `/recambios?s=filtro`, `/buscar?q=filtro` y las demás rutas habituales a
    su catálogo general `/recambios/`, y `/buscar/filtro` a una ficha (causa R3-F2): esas páginas
    no son los resultados de la consulta. `consulta_en_url` decide si la lleva cada una. No cuenta
    una plantilla que solo la lleva en el fragmento (una SPA: el servidor no lo ve) ni la que
    redirige a la portada o a un prefijo de idioma, que se queda como estaba (la compara ya
    `no_es_la_portada`).
    """
    final = _url(pagina)
    if not consulta_en_url(pagina.url, consulta) or consulta_en_url(final, consulta):
        return False
    return not solo_idioma(urlsplit(final).path)


def _redirigio_sin_la_consulta(pagina: Pagina, consulta: str) -> bool:
    """Respondió 2xx tras redirigir a una URL que ya no lleva la consulta (A3 del plan de
    velocidad): `perdio_la_consulta` sin su excepción de la portada y los prefijos de idioma.

    No se repite con Chromium: la redirección es del servidor y Chromium iría al mismo sitio. De
    las 121 descargas dobles de la campaña, las seis que ganaron por Chromium tenían por HTTP un
    200 con la consulta todavía en la URL final. Una plantilla con la consulta solo en el
    fragmento (una SPA) no cuenta: `consulta_en_url` no mira el fragmento de la URL pedida.
    """
    if not correcta(pagina):
        return False
    return consulta_en_url(pagina.url, consulta) and not consulta_en_url(_url(pagina), consulta)


def _sin_separadores(texto: str) -> str:
    return _NO_ALFANUMERICOS.sub("", normalizar(texto))


def _clave_final(pagina: Pagina) -> str:
    """La URL final de `pagina` para saber si dos candidatas acaban en la misma página.

    Sin la barra final de la ruta y con el fragmento de la URL pedida si la final no trae otro:
    el navegador lo conserva al redirigir y el JavaScript de una SPA lo lee, así que dos
    plantillas que solo cambian en el fragmento no son la misma página para Chromium.
    """
    final, pedida = urlsplit(_url(pagina)), urlsplit(pagina.url)
    ruta = final.path.rstrip("/")
    fragmento = final.fragment or pedida.fragment
    return urlunsplit((final.scheme.lower(), final.netloc.lower(), ruta, final.query, fragmento))


def _sin_repetir(candidatas: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Sin plantillas repetidas, conservando la primera aparición (y su origen)."""
    vistas: dict[str, str] = {}
    for plantilla, origen in candidatas:
        vistas.setdefault(plantilla, origen)
    return list(vistas.items())


def _url(pagina: Pagina) -> str:
    return pagina.url_final or pagina.url


def _base(portada: Pagina | None, host: str) -> str:
    """Origen donde se prueban las rutas: el de la portada tras redirigir (`www.` o no)."""
    if portada is not None and (origen := origen_de(_url(portada))) is not None:
        return origen
    return f"https://{host}"


def correcta(pagina: Pagina) -> bool:
    return 200 <= pagina.estado < 300


def _elementos(raiz: Nodo, etiqueta: str) -> Iterable[Nodo]:
    return (n for n in raiz.descendientes() if n.etiqueta == etiqueta)


# --- 1. OpenSearch ------------------------------------------------------------------------------


def enlace_opensearch(pagina: Pagina, dominio: str) -> str | None:
    """URL del descriptor OpenSearch que declara `pagina`, si es de la tienda."""
    base = _url(pagina)
    for enlace in _elementos(analizar(pagina.html), "link"):
        relaciones = (enlace.atributo("rel") or "").lower().split()
        tipo = (enlace.atributo("type") or "").strip().lower()
        href = enlace.atributo("href")
        if "search" in relaciones and tipo == TIPO_OPENSEARCH and href:
            return url_de_la_tienda(href[:MAX_HREF], base, dominio)
    return None


def plantilla_opensearch(descriptor: Pagina, dominio: str) -> str | None:
    """Plantilla de búsqueda HTML del descriptor OpenSearch, o `None`.

    Solo `<Url type="text/html">` por GET (las sugerencias en JSON no son la página de
    resultados) con `{searchTerms}` una vez. Los parámetros opcionales (`{startPage?}`) se
    quitan; uno obligatorio que no sabemos rellenar (`{language}`) invalida la plantilla.
    """
    base = _url(descriptor)
    urls = (n for n in _elementos(analizar(descriptor.html), "url"))
    for numero, nodo in enumerate(urls):
        if numero >= MAX_URLS_OPENSEARCH:
            break
        tipo = (nodo.atributo("type") or "").strip().lower()
        metodo = (nodo.atributo("method") or "get").strip().lower()
        plantilla = nodo.atributo("template") or ""
        if tipo != "text/html" or metodo != "get" or len(plantilla) > MAX_PLANTILLA:
            continue
        convertida = _desde_opensearch(plantilla.strip(), base)
        if convertida is not None and _es_de_la_tienda(convertida, dominio):
            return convertida
    return None


def _desde_opensearch(plantilla: str, base: str) -> str | None:
    # Como con la IA: espacios, comillas o una barra invertida harían que `urllib` y Chromium
    # no leyeran la misma URL (`https://otra.es\@tienda.es` es otra.es para Chromium).
    if plantilla.count(TERMINOS) != 1 or any(c in _PROHIBIDOS for c in plantilla):
        return None
    if not plantilla.isascii() or not plantilla.isprintable():
        return None
    try:
        esquema, host, ruta, consulta, _ = urlsplit(urljoin(base, plantilla))
    except ValueError:
        return None
    ruta = ruta.replace(TERMINOS, MARCADOR)
    if not _solo_el_marcador(ruta):
        return None
    pares: list[str] = []
    for clave, valor in parse_qsl(consulta, keep_blank_values=True):
        if valor == TERMINOS:
            pares.append(f"{quote_plus(clave)}={MARCADOR}")
        elif _OPCIONAL.fullmatch(valor):
            continue
        elif "{" in valor or "}" in valor or "{" in clave or "}" in clave:
            return None  # Un parámetro obligatorio que no sabemos rellenar.
        else:
            pares.append(urlencode([(clave, valor)]))
    resultado = urlunsplit((esquema, host, ruta, "&".join(pares), ""))
    return resultado if resultado.count(MARCADOR) == 1 else None


# --- 2. Formularios -----------------------------------------------------------------------------


def plantillas_de_formularios(pagina: Pagina, dominio: str, *, por_rol: bool = True) -> list[str]:
    """Plantillas de búsqueda (con `{consulta}`) de los formularios de búsqueda de `pagina`.

    Solo formularios GET cuyo `action` es del propio `dominio` y que tienen un campo de búsqueda
    (`type=search`, un nombre típico o, si no, por su rol: `_campo_de_busqueda`). Los campos
    ocultos se conservan en el orden del formulario, como los enviaría el navegador (PrestaShop
    necesita `controller=search`). Como mucho `MAX_FORMULARIOS`, los primeros: una portada no
    puede multiplicar las descargas. Con `por_rol=False`, sin el criterio por rol (`_solo_por_rol`).
    """
    raiz = analizar(pagina.html)
    base = _url(pagina)
    plantillas: list[str] = []
    for formulario in _elementos(raiz, "form"):
        metodo = (formulario.atributo("method") or "get").strip().lower()
        if metodo != "get":
            continue
        accion = url_de_la_tienda(formulario.atributo("action") or base, base, dominio)
        if accion is None:
            continue
        plantilla = _plantilla(formulario, accion, por_rol=por_rol)
        if plantilla is not None and plantilla not in plantillas:
            plantillas.append(plantilla)
            if len(plantillas) >= MAX_FORMULARIOS:
                break
    return plantillas


def _plantilla(formulario: Nodo, accion: str, *, por_rol: bool = True) -> str | None:
    entradas = list(_elementos(formulario, "input"))
    if any(_tipo(e) == "password" for e in entradas):
        return None  # Un formulario con contraseña es un login: nunca es la búsqueda.
    campo = _campo_de_busqueda(formulario, entradas, por_rol=por_rol)
    if campo is None:
        return None
    pares: list[str] = []
    ocultos = 0
    for entrada in entradas:
        nombre = _nombre(entrada)
        if not nombre:
            continue
        if _tipo(entrada) == "hidden":
            ocultos += 1
            valor = entrada.atributo("value") or ""
            if ocultos > MAX_OCULTOS or len(valor) > MAX_VALOR_OCULTO:
                return None
            pares.append(urlencode([(nombre, valor)]))
        elif entrada is campo:
            pares.append(f"{quote_plus(nombre)}={MARCADOR}")
    # Al enviar un formulario GET, su consulta sustituye a la que tuviera `action`.
    esquema, host, ruta, _, _ = urlsplit(accion)
    return urlunsplit((esquema, host, ruta, "", "")) + "?" + "&".join(pares)


def _tipo(entrada: Nodo) -> str:
    return (entrada.atributo("type") or "text").strip().lower()


def _nombre(entrada: Nodo) -> str:
    return (entrada.atributo("name") or "").strip()


def _de_busqueda(nombre: str, tipo: str) -> bool:
    """El campo de búsqueda: `type=search` o un texto con un nombre típico."""
    return tipo == "search" or (tipo == "text" and nombre.lower() in NOMBRES_BUSQUEDA)


def _roles(nodo: Nodo) -> list[str]:
    return (nodo.atributo("role") or "").lower().split()


def _campo_de_busqueda(
    formulario: Nodo, entradas: Sequence[Nodo], *, por_rol: bool = True
) -> Nodo | None:
    """El campo de búsqueda del formulario (con `name`), o `None`.

    Primero el primero que cumple `_de_busqueda`. Si ninguno, el primer campo de texto con
    `role="searchbox"` o, en un `<form role="search">`, el primer campo de texto: es como
    marca la accesibilidad (ARIA) el buscador. Amazon usa `name="field-keywords"`, un nombre que
    no es típico, en un `type="text"` (encontrado por Julio al probar la app con amazon.es). Un
    campo oculto, de correo o un botón nunca lo son, ni un formulario POST o con contraseña (eso
    lo miran quienes llaman). Con `por_rol=False`, solo `_de_busqueda`.
    """
    con_nombre = [e for e in entradas if _nombre(e)]
    for entrada in con_nombre:
        if _de_busqueda(_nombre(entrada), _tipo(entrada)):
            return entrada
    if not por_rol:
        return None
    de_busqueda = "search" in _roles(formulario)
    for entrada in con_nombre:
        if _tipo(entrada) == "text" and (de_busqueda or "searchbox" in _roles(entrada)):
            return entrada
    return None


def plantillas_sin_accion(pagina: Pagina, dominio: str) -> list[str]:
    """Las `RUTAS_SIN_ACCION` con el nombre del campo de un formulario de búsqueda sin `action`.

    Un formulario GET sin `action` (o con `action=""` o `"#"`) se envía a la propia página, y
    eso es lo que prueba `plantillas_de_formularios`. Pero en muchas tiendas ese formulario no lo
    envía el navegador: lo intercepta su JavaScript y navega a la página de resultados de verdad
    (PcComponentes: `name="query"` en la portada, resultados en `/buscar/?query=`). Aquí se
    prueba ese mismo nombre en las rutas de búsqueda habituales del origen de la página.

    Como mucho `MAX_RUTAS_SIN_ACCION` plantillas, del primer formulario que tenga un campo de
    búsqueda con nombre (los mismos que acepta `plantillas_de_formularios`, y nunca uno con
    contraseña). Un campo sin `name` (Doofinder, Empathy) no da ninguna: no hay parámetro.
    """
    base = _url(pagina)
    origen = origen_de(base)
    if origen is None or url_de_la_tienda(base, base, dominio) is None:
        return []
    for numero, formulario in enumerate(_elementos(analizar(pagina.html), "form")):
        if numero >= MAX_FORMULARIOS_RESUMEN:
            break
        metodo = (formulario.atributo("method") or "get").strip().lower()
        if metodo != "get" or (formulario.atributo("action") or "").strip() not in ("", "#"):
            continue
        nombre = _nombre_de_busqueda(formulario)
        if nombre is not None:
            plantillas = [f"{origen}{r}?{quote_plus(nombre)}={MARCADOR}" for r in RUTAS_SIN_ACCION]
            return plantillas[:MAX_RUTAS_SIN_ACCION]
    return []


def _nombre_de_busqueda(formulario: Nodo) -> str | None:
    """El nombre del campo de búsqueda del formulario, o `None` (también si pide contraseña)."""
    entradas = list(_elementos(formulario, "input"))
    if any(_tipo(e) == "password" for e in entradas):
        return None
    cortos = [e for e in entradas if len(_nombre(e)) <= MAX_VALOR_OCULTO]
    campo = _campo_de_busqueda(formulario, cortos)
    return None if campo is None else _nombre(campo)


# --- 3. Enlaces de búsqueda ---------------------------------------------------------------------


def plantillas_de_enlaces(pagina: Pagina, dominio: str) -> list[str]:
    """Plantillas de los enlaces de búsqueda de `pagina`.

    Como mucho `MAX_ENLACES` enlaces y `MAX_PLANTILLAS_ENLACES` plantillas en total.

    Un enlace que ya lleva un parámetro de `PARAMETROS_ENLACE` («búsquedas populares»,
    `/buscar?q=frenos&cat=3`) da una sola plantilla con ese parámetro; uno sin él (`/es/search`),
    una por cada nombre de `PARAMETROS_ENLACE`, en ese orden.
    """
    base = _url(pagina)
    propia = url_de_la_tienda(base, base, dominio)
    plantillas: list[str] = []
    destinos: set[str] = set()
    for revisados, enlace in enumerate(_elementos(analizar(pagina.html), "a")):
        if revisados >= MAX_ENLACES_REVISADOS or len(destinos) >= MAX_ENLACES:
            break
        href = (enlace.atributo("href") or "")[:MAX_HREF]
        # Filtro barato antes de normalizar la URL: casi ningún enlace es de búsqueda.
        if not any(ruta in href.lower() for ruta in RUTAS_DE_BUSQUEDA):
            continue
        url = url_de_la_tienda(href, base, dominio)
        if url is None or url == propia:
            continue
        esquema, host, ruta, consulta, _ = urlsplit(url)
        if not _es_ruta_de_busqueda(ruta):
            continue  # La palabra estaba en la consulta, en el fragmento o dentro de un tramo.
        destino = urlunsplit((esquema, host, ruta, "", ""))
        if destino in destinos:
            continue
        destinos.add(destino)
        plantillas.extend(_plantillas_de_enlace(destino, consulta))
    return list(dict.fromkeys(plantillas))[:MAX_PLANTILLAS_ENLACES]


def _plantillas_de_enlace(destino: str, consulta: str) -> list[str]:
    pares = parse_qsl(consulta, keep_blank_values=True)
    nombre = next((k for k, _ in pares if k.lower() in PARAMETROS_ENLACE), None)
    if nombre is None:
        return [f"{destino}?{quote_plus(n)}={MARCADOR}" for n in PARAMETROS_ENLACE]
    # El valor del parámetro de búsqueda se cambia por el marcador; si venía repetido, se queda
    # solo el primero. Los demás parámetros (categoría, orden...) se conservan.
    partes: list[str] = []
    puesto = False
    for clave, valor in pares:
        if clave != nombre:
            partes.append(urlencode([(clave, valor)]))
        elif not puesto:
            partes.append(f"{quote_plus(clave)}={MARCADOR}")
            puesto = True
    return [f"{destino}?{'&'.join(partes)}"]


# --- 6. Propuesta de la IA ----------------------------------------------------------------------


async def plantillas_de_la_ia(portada: Pagina, dominio: str, sesion: SesionIA) -> list[str]:
    """Hasta `MAX_PLANTILLAS_IA` plantillas propuestas por la IA, ya filtradas.

    Las instrucciones son nuestras; la portada va en los datos, resumida. Los errores de la IA
    se propagan. Si la portada no tiene ni formularios ni enlaces a otras páginas de la tienda
    (una página «Próximamente», un dominio aparcado), no se pregunta: la IA no tendría de dónde
    deducir nada y solo podría inventar, pagando la llamada.
    """
    resumen, pistas = _resumen(portada, dominio)
    if not pistas:
        return []
    respuesta = await sesion.pedir(
        PeticionIA(
            componente=COMPONENTE,
            instrucciones=INSTRUCCIONES_PLANTILLAS,
            datos=resumen,
            esquema=ESQUEMA_PLANTILLAS,
            max_tokens=max_tokens_para(ESQUEMA_PLANTILLAS),
        )
    )
    return leer_plantillas_ia(respuesta.datos, _url(portada), dominio)


def plantilla_de_entrada(entrada: str, dominio: str) -> str | None:
    """La URL de entrada del usuario como plantilla de búsqueda (A1), o `None` si no vale.

    Absoluta, ASCII imprimible sin espacios ni caracteres raros, como mucho `MAX_PLANTILLA`
    caracteres, con `{consulta}` una sola vez y fuera del esquema, del host y del fragmento, sin
    otras llaves y del dominio de la tienda. Normalmente ya llega así de
    `perfiles.entrada.normalizar_entrada`; aquí se comprueba otra vez porque es un dato guardado.
    """
    texto = entrada.strip()
    if not texto or len(texto) > MAX_PLANTILLA or any(c in _PROHIBIDOS for c in texto):
        return None
    if not texto.isascii() or not texto.isprintable() or texto.count(MARCADOR) != 1:
        return None
    if "#" in texto or not _solo_el_marcador(texto):
        return None
    return texto if _es_de_la_tienda(texto, dominio) else None


def leer_plantillas_ia(datos: Mapping[str, Any], base: str, dominio: str) -> list[str]:
    """Las plantillas de la respuesta de la IA que se pueden probar (como mucho tres).

    Cada una: texto sin espacios ni caracteres raros, con `{consulta}` una sola vez y fuera del
    fragmento, resuelta contra la portada y del dominio de la tienda. Lo demás se descarta.
    """
    brutas = datos.get("plantillas")
    if not isinstance(brutas, list):
        return []
    plantillas: list[str] = []
    for bruta in brutas[:MAX_PLANTILLAS_IA]:
        if not isinstance(bruta, str):
            continue
        texto = bruta.strip()
        if not texto or len(texto) > MAX_PLANTILLA or any(c in _PROHIBIDOS for c in texto):
            continue
        if not texto.isascii() or not texto.isprintable() or texto.count(MARCADOR) != 1:
            continue
        try:
            absoluta = urljoin(base, texto)
        except ValueError:
            continue
        if "#" in absoluta or not _solo_el_marcador(absoluta):
            continue
        if _es_de_la_tienda(absoluta, dominio) and absoluta not in plantillas:
            plantillas.append(absoluta)
    return plantillas


def resumen_portada(portada: Pagina, dominio: str) -> str:
    """Formularios y enlaces de la tienda en la portada, en texto plano y recortado.

    Es lo que ve la IA para proponer plantillas: menos texto que el HTML entero (más barato y
    con menos superficie para instrucciones inyectadas) y solo enlaces de la propia tienda.
    """
    return _resumen(portada, dominio)[0]


def _resumen(portada: Pagina, dominio: str) -> tuple[str, bool]:
    """El resumen y si trae alguna pista: un formulario o un enlace a otra página propia."""
    raiz = analizar(portada.html)
    base = _url(portada)
    propia = url_de_la_tienda(base, base, dominio)
    pistas = False
    lineas = [f"PORTADA: {base}", "FORMULARIOS:"]
    for numero, formulario in enumerate(_elementos(raiz, "form")):
        if numero >= MAX_FORMULARIOS_RESUMEN:
            break
        metodo = (formulario.atributo("method") or "get").strip().lower()[:10]
        accion = url_de_la_tienda(formulario.atributo("action") or base, base, dominio)
        pistas = pistas or accion is not None
        campos = [c for c in map(_campo, _elementos(formulario, "input")) if c is not None]
        rol = _corto(formulario.atributo("role") or "", 20)
        destino = (accion or "(otra web)") + (f" [role={rol}]" if rol else "")
        lineas.append(f"- {metodo.upper()} {destino}: {', '.join(campos[:MAX_CAMPOS_RESUMEN])}")
    lineas.append("ENLACES:")
    vistos: set[str] = set()
    for revisados, enlace in enumerate(_elementos(raiz, "a")):
        if revisados >= MAX_ENLACES_REVISADOS or len(vistos) >= MAX_ENLACES_RESUMEN:
            break
        href = (enlace.atributo("href") or "")[:MAX_HREF]
        url = url_de_la_tienda(href, base, dominio) if href else None
        if url is None or url in vistos or len(url) > MAX_PLANTILLA:
            continue
        vistos.add(url)
        pistas = pistas or url != propia
        lineas.append(f"- {url} · {_corto(enlace.texto(), MAX_TEXTO_ENLACE)}")
    return "\n".join(lineas), pistas


def _campo(entrada: Nodo) -> str | None:
    """Un campo para el resumen: nombre, tipo y, si los tiene, id, placeholder y aria-label.

    Muchos buscadores hechos con JavaScript (Doofinder, Empathy) no le ponen `name` al campo:
    sin estos atributos la IA no sabría cuál es la caja de búsqueda. Son datos no confiables
    recortados a `MAX_ATRIBUTO_RESUMEN` caracteres, como los textos de los enlaces.
    """
    nombre = _corto(entrada.atributo("name") or "", MAX_ATRIBUTO_RESUMEN)
    tipo = _corto(entrada.atributo("type") or "text", 20)
    extra = [
        f"{atributo}={valor}"
        for atributo in ("id", "placeholder", "aria-label")
        if (valor := _corto(entrada.atributo(atributo) or "", MAX_ATRIBUTO_RESUMEN))
    ]
    if not nombre and not extra:
        return None
    return f"{nombre or '(sin nombre)'} ({', '.join([tipo, *extra])})"


def _corto(texto: str, maximo: int) -> str:
    texto = _ESPACIOS.sub(" ", texto).strip()
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


def _solo_el_marcador(texto: str) -> bool:
    """Sin más llaves que las de `{consulta}` (una plantilla con otras variables no se rellena)."""
    resto = texto.replace(MARCADOR, "", 1)
    return "{" not in resto and "}" not in resto


def _es_de_la_tienda(plantilla: str, dominio: str) -> bool:
    if not consulta_fuera_del_host(plantilla):
        return False  # Ver `validador.consulta_fuera_del_host`.
    ejemplo = url_de_busqueda(plantilla, "prueba")
    return url_de_la_tienda(ejemplo, ejemplo, dominio) is not None


# --- Heurística del listado ---------------------------------------------------------------------


def parece_listado(
    pagina: Pagina,
    dominio: str,
    consulta: str,
    *,
    portada: Pagina | None,
    estricto: bool = False,
) -> bool:
    """Heurística: ¿es `pagina` una página de resultados de la tienda para `consulta`?

    Responde bien, sin haber redirigido fuera de la tienda, menciona alguna palabra de la
    consulta y tiene al menos `MIN_PRODUCTOS` enlaces a la tienda junto a un precio. Con
    `portada`, además, no es la portada (`no_es_la_portada`).

    Sin `estricto`, la palabra se busca en todo el texto de la página (lo que usan el orquestador
    y el configurador). Con `estricto` (el descubrimiento de la búsqueda), solo en el `<title>` y
    en los resultados (el texto de cada «tarjeta» con precio y el slug de su enlace), como inicio
    de palabra para admitir plurales, y las referencias también sin separadores: en toda la
    página casaba con lo que no depende de la consulta, como la etiqueta «Filtros» de las facetas
    o un menú de categorías con «Faros» (causa C2 de la campaña multi-sector).
    """
    if not correcta(pagina) or not de_la_tienda(pagina, dominio):
        return False
    raiz = analizar(pagina.html)
    palabras = palabras_de(consulta)
    productos = _enlaces_con_precio(raiz, pagina, dominio)
    if palabras:
        if estricto:
            titulos = (n.texto() for n in _elementos(raiz, "title"))
            slugs = (_slug(url) for url in productos)
            textos: Iterable[str] = (*titulos, *productos.values(), *slugs)
        else:
            textos = (raiz.texto(),)
        if not nombra(textos, palabras, en_palabra=estricto):
            return False
    if len(productos) < MIN_PRODUCTOS:
        return False
    return portada is None or no_es_la_portada(pagina, portada, dominio, consulta)


def no_es_la_portada(pagina: Pagina, portada: Pagina, dominio: str, consulta: str) -> bool:
    """`pagina` no es la `portada` con algún enlace cambiado.

    Muchas tiendas devuelven la portada, con sus destacados, cuando no entienden la ruta o la
    búsqueda va por JavaScript. Antes bastaba con que sus enlaces con precio no fueran
    exactamente los de la portada, y un carrusel que rota o un subconjunto ya lo cumplían
    (Ecooparts: 3 enlaces nuevos de 276). Ahora, de los enlaces a la tienda (con o sin precio),
    los que no están en la portada tienen que ser al menos `MIN_PRODUCTOS` y la
    `MIN_PROPORCION_NUEVOS` del total. No cuentan como nuevos los que llevan la consulta en su
    parte de consulta (paginación y filtros de la misma búsqueda, `?q=filtro&page=2`).
    `portada` tiene que estar renderizada igual que `pagina` (ver `_Portadas`).
    """
    raiz = analizar(pagina.html)
    raiz_portada = analizar(portada.html)
    productos = _enlaces_con_precio(raiz, pagina, dominio)
    if productos.keys() == _enlaces_con_precio(raiz_portada, portada, dominio).keys():
        return False
    enlaces = _enlaces_propios(raiz, pagina, dominio)
    palabras = palabras_de(consulta)
    nuevos = [
        url
        for url in enlaces - _enlaces_propios(raiz_portada, portada, dominio)
        if not nombra((unquote_plus(urlsplit(url).query),), palabras, en_palabra=True)
    ]
    return len(nuevos) >= MIN_PRODUCTOS and len(nuevos) >= MIN_PROPORCION_NUEVOS * len(enlaces)


def palabras_de(consulta: str) -> list[str]:
    """Las palabras de la consulta que se buscan en la página (de `MIN_PALABRA` letras o más)."""
    return [p for p in normalizar(consulta).split() if len(p) >= MIN_PALABRA]


def nombra(textos: Iterable[str], palabras: Sequence[str], *, en_palabra: bool) -> bool:
    """Algún texto contiene alguna de las `palabras` (ya normalizadas).

    Con `en_palabra`, tiene que empezar una palabra («filtro» casa con «filtros», no con
    «antifiltro») y las que llevan cifras (referencias) se comparan además sin separadores
    («1K0-121-011-B» casa con «1K0 121 011 B»). Sin `en_palabra`, basta con que aparezca.
    """
    patrones = [re.compile(r"(?<![a-z0-9])" + re.escape(p)) for p in palabras] if en_palabra else []
    compactas = [_compacto(p) for p in palabras if en_palabra and any(c.isdigit() for c in p)]
    for texto in textos:
        normal = normalizar(texto)
        if not en_palabra:
            if any(p in normal for p in palabras):
                return True
            continue
        if any(patron.search(normal) for patron in patrones):
            return True
        if compactas and any(c and c in _compacto(normal) for c in compactas):
            return True
    return False


def _compacto(texto: str) -> str:
    return "".join(c for c in texto if c.isalnum())


def _slug(url: str) -> str:
    """La ruta de `url` con los separadores habituales de un slug cambiados por espacios."""
    ruta = unquote(urlsplit(url).path)
    for separador in "-_/.+":
        ruta = ruta.replace(separador, " ")
    return ruta


def _enlaces_propios(raiz: Nodo, pagina: Pagina, dominio: str) -> set[str]:
    """Los enlaces de `pagina` a otras páginas de la tienda (como mucho `MAX_ENLACES_REVISADOS`)."""
    base = _url(pagina)
    propia = url_de_la_tienda(base, base, dominio)
    enlaces: set[str] = set()
    for revisados, enlace in enumerate(_elementos(raiz, "a")):
        if revisados >= MAX_ENLACES_REVISADOS:
            break
        href = (enlace.atributo("href") or "")[:MAX_HREF]
        url = url_de_la_tienda(href, base, dominio) if href else None
        if url is not None and url != propia:
            enlaces.add(url)
    return enlaces


def _enlaces_con_precio(raiz: Nodo, pagina: Pagina, dominio: str) -> dict[str, str]:
    """Enlaces a la tienda con un precio en su «tarjeta» → texto de esa tarjeta.

    No cuentan los que están dentro de algo oculto (`dom.oculto`): `resumen_para_ia` no lo
    enseña, y una página que se acepta como listado tiene que poder verla la IA (causa R2-C9).
    Tampoco los enlaces de acción (`_es_accion`: el carrito, favoritos, comparar), que además
    no marcan el borde de la tarjeta (causa R3-F1).
    """
    base = _url(pagina)
    propia = url_de_la_tienda(base, base, dominio)
    enlaces: dict[str, str] = {}
    for enlace in _elementos(raiz, "a"):
        href = enlace.atributo("href")
        url = url_de_la_tienda(href, base, dominio) if href else None
        if url is None or url == propia or url in enlaces or oculto(enlace):
            continue
        if _es_accion(enlace, base):
            continue
        # El precio tiene que estar en la «tarjeta» del producto: se sube por los ancestros
        # mientras no se llegue a un bloque grande ni a uno con enlaces a otras páginas, y como
        # mucho `NIVELES_CONTENEDOR` contenedores.
        contenedor: Nodo | None = enlace
        for _ in range(NIVELES_CONTENEDOR):
            if contenedor is None or contenedor.padre is None:
                break
            texto = contenedor.texto()
            if len(texto) > MAX_TEXTO_CONTENEDOR or _enlaza_a_otras(contenedor, url, base, dominio):
                break
            if next(buscar_precios(texto), None) is not None:
                enlaces[url] = texto
                break
            contenedor = contenedor.padre
    return enlaces


def _enlaza_a_otras(contenedor: Nodo, url: str, base: str, dominio: str) -> bool:
    """`contenedor` tiene un enlace a otra página de la tienda que no es `url` ni una acción."""
    for enlace in _elementos(contenedor, "a"):
        href = enlace.atributo("href")
        otra = url_de_la_tienda(href, base, dominio) if href else None
        if otra is not None and otra != url and not _es_accion(enlace, base):
            return True
    return False


def _es_accion(enlace: Nodo, base: str) -> bool:
    """El enlace es una acción sobre la página `base`, no otra página: un botón de la tarjeta.

    Causa R3-F1 de la campaña multi-sector: en WooCommerce el precio va junto al botón del
    carrito, `<a href="?add-to-cart=272815" rel="nofollow">`, que resuelve a la propia página con
    otros parámetros; la tarjeta parecía enlazar a otra página y el producto se quedaba sin
    precio, mientras el carrito salía como producto. Es una acción si el `href` solo cambia la
    consulta de la página (empieza por `?`) o si es `rel="nofollow"`, sin texto visible y con la
    misma ruta que la página (un icono de carrito, favoritos o comparar). Compartir la ruta no
    basta: en una tienda que enruta por la consulta (`/?product=…`) los productos la comparten.
    """
    href = (enlace.atributo("href") or "").strip()
    if href.startswith("?"):
        return True
    rel = (enlace.atributo("rel") or "").lower().split()
    if "nofollow" not in rel or enlace.texto().strip():
        return False
    try:
        return urlsplit(urljoin(base, href)).path == urlsplit(base).path
    except ValueError:
        return False
