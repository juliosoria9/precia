"""Descubrimiento de la búsqueda de una tienda: qué URL da su página de resultados (nota 15 §D).

El configurador lo usa antes de pedir selectores a la IA. Las plantillas candidatas (con
`{consulta}`, como `Perfil.url_busqueda`) se prueban en este orden, de la más fiable a la menos:

1. El descriptor **OpenSearch** que enlaza la portada (`<link rel="search"
   type="application/opensearchdescription+xml">`): es la propia tienda diciendo cómo se busca.
2. Los **formularios** de búsqueda GET de la portada.
3. Los **enlaces de búsqueda** de la portada (rutas con `search`, `buscar`, `busqueda`,
   `catalogsearch` o `recherche`). Si el enlace ya lleva un parámetro de búsqueda conocido se usa
   ese; si no, se prueba con `q`, `s`, `query`, `search`, `keyword` y `text`. Es lo que hay en
   las tiendas hechas con Next.js o Vue, que pintan el buscador con JavaScript y no tienen
   `<form>` en el HTML.
4. Las rutas de búsqueda de la **plataforma** detectada (`fuentes.plataformas`).
5. Los **patrones fijos** de siempre (`PATRONES`).
6. 🤖 Como último recurso, la **IA** propone hasta `MAX_PLANTILLAS_IA` plantillas a partir de un
   resumen de la portada (sus formularios y sus enlaces). Lo que devuelve es un dato no
   confiable más: solo se aceptan plantillas del dominio de la tienda, con `{consulta}` una vez y
   fuera del fragmento, y cada una pasa por `validar_url` y por `parece_listado` antes de usarla.

Si la portada por HTTP no trae ni formularios ni enlaces de búsqueda (ni OpenSearch), se mira la
versión renderizada con Chromium, que es donde están en una tienda que se pinta con JavaScript.
Cada candidata se prueba primero por HTTP y, solo si ninguna da un listado, con Chromium; las que
por HTTP dieron un estado de `ESTADOS_DEFINITIVOS` (un 404, por ejemplo) no se repiten.

Todo sale de páginas no confiables: hay topes de formularios, enlaces y plantillas (una portada no
puede multiplicar las descargas), solo se aceptan URL `http(s)` de la propia tienda
(`extraccion.listado.url_de_la_tienda`) y no se usa ninguna expresión regular sobre el HTML que
pueda retroceder de forma cuadrática. El descriptor OpenSearch se lee con el analizador de HTML
propio (`extraccion.dom`), que no expande entidades externas ni definidas en el documento.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

from precia.extraccion.anclaje import normalizar
from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.listado import url_de_la_tienda
from precia.extraccion.precios import buscar_precios
from precia.fuentes.plataformas import detectar_plataforma, origen_de, plantillas_html
from precia.ia.tipos import ErrorIA, PeticionIA, PresupuestoAgotado
from precia.navegador.tipos import ErrorDescarga, Pagina, ProhibidoPorRobots, URLNoPermitida
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
NOMBRES_BUSQUEDA = frozenset({"q", "s", "search", "query", "buscar", "text"})
MAX_FORMULARIOS = 3
"""Plantillas de formulario que se prueban como mucho: cada una cuesta dos descargas."""
MAX_OCULTOS = 5
MAX_VALOR_OCULTO = 100
RUTAS_DE_BUSQUEDA = ("search", "buscar", "busqueda", "catalogsearch", "recherche")
PARAMETROS_ENLACE = ("q", "s", "query", "search", "keyword", "text")
"""Nombres del parámetro de búsqueda que se prueban con un enlace que no lleva ninguno."""
MAX_ENLACES = 2
"""Enlaces de búsqueda distintos de la portada que se prueban como mucho."""
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
MAX_ENLACES_RESUMEN = 150
MAX_TEXTO_ENLACE = 80
RECHAZOS = frozenset({401, 403, 429, 451})
"""La tienda dice que no: repetirlo con un navegador real sería insistir (regla 5, sin evasión)."""
ESTADOS_DEFINITIVOS = frozenset({400, 404, 410, 414, 501}) | RECHAZOS
"""Estados por HTTP con los que una candidata no se repite con Chromium (ver `_probar`)."""
MIN_PRODUCTOS = 2
"""Enlaces a productos con precio para que una página parezca un listado."""
MAX_TEXTO_CONTENEDOR = 600
NIVELES_CONTENEDOR = 4
MIN_PALABRA = 3
TIPO_OPENSEARCH = "application/opensearchdescription+xml"
TERMINOS = "{searchTerms}"

ORIGEN_OPENSEARCH = "OpenSearch"
ORIGEN_FORMULARIO = "formulario"
ORIGEN_ENLACE = "enlace de la portada"
ORIGEN_PLATAFORMA = "plataforma"
ORIGEN_PATRON = "patrón habitual"
ORIGEN_IA = "🤖 propuesta de la IA"

# Un parámetro opcional de OpenSearch (`{startPage?}`, `{os:count?}`): se quita de la plantilla.
_OPCIONAL = re.compile(r"\{[A-Za-z0-9:_.-]{1,50}\?\}")
_ESPACIOS = re.compile(r"\s+")
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
    except (ErrorDescarga, ProhibidoPorRobots, URLNoPermitida):
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
) -> Busqueda:
    """La búsqueda de la tienda, o `ConfiguracionFallida`. Ver el docstring del módulo.

    `portada` es la de `abrir_portada` (`None` si no se pudo abrir). Sin `sesion` no se pide
    nada a la IA. `PresupuestoAgotado` se propaga; cualquier otro error de la IA solo significa
    que no ha propuesto nada.
    """
    base = _base(portada, host)
    candidatas: list[tuple[str, str]] = []
    resumida = portada
    if portada is not None:
        descriptores: set[str] = set()
        de_portada = await _de_la_portada(portada, dominio, navegador, validador, descriptores)
        if not de_portada:
            renderizada = await abrir(navegador, _url(portada), js=True, validador=validador)
            if renderizada is not None and _de_la_tienda_y_bien(renderizada, dominio):
                de_portada = await _de_la_portada(
                    renderizada, dominio, navegador, validador, descriptores
                )
                resumida = renderizada
        candidatas.extend(de_portada)
        plataforma = detectar_plataforma(portada)
        if plataforma is not None:
            candidatas.extend((p, ORIGEN_PLATAFORMA) for p in plantillas_html(plataforma, base))
    candidatas.extend((base + patron, ORIGEN_PATRON) for patron in PATRONES)

    unicas = _sin_repetir(candidatas)
    encontrada = await _probar(unicas, dominio, navegador, consulta, portada, validador)
    if encontrada is not None:
        return encontrada
    motivo = ""
    if sesion is not None and resumida is not None:
        try:
            propuestas = await plantillas_de_la_ia(resumida, dominio, sesion)
        except PresupuestoAgotado:
            raise
        except ErrorIA as error:
            propuestas = []
            motivo = f" La IA tampoco ha podido proponer una: {error}"
        conocidas = {p for p, _ in candidatas}
        nuevas = [(p, ORIGEN_IA) for p in propuestas if p not in conocidas]
        encontrada = await _probar(nuevas, dominio, navegador, consulta, portada, validador)
        if encontrada is not None:
            return encontrada
    raise ConfiguracionFallida(
        f"No se encuentra la página de búsqueda de {dominio}: ni la portada tiene un "
        "buscador que funcione ni responde ninguna de las rutas habituales." + motivo
    )


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


async def _probar(
    candidatas: Sequence[tuple[str, str]],
    dominio: str,
    navegador: Navegador,
    consulta: str,
    portada: Pagina | None,
    validador: Validador,
) -> Busqueda | None:
    """La primera candidata que da un listado: primero todas por HTTP (barato) y, solo si
    ninguna lo da, con Chromium.

    Con Chromium no se repiten las que por HTTP respondieron con un estado de
    `ESTADOS_DEFINITIVOS`: Chromium pide el mismo documento, con el mismo User-Agent, y recibiría
    el mismo estado; y `parece_listado` exige un 2xx del documento, así que aunque su JavaScript
    pintara resultados encima de la página de error no se aceptaría. 404 y 410: la ruta no existe;
    400 y 414: el servidor rechaza la propia URL; 501: no admite el GET. Sí se repiten un 2xx que
    no era un listado (puede pintarlo el JavaScript) y los 5xx (pueden ser pasajeros). Los
    bloqueos (`Bloqueado`, un 403 o un 429 de un anti-bot) ni llegan aquí: el navegador los lanza
    y se para.
    """
    definitivas: set[str] = set()
    for js in (False, True):
        for plantilla, origen in candidatas:
            url = url_de_busqueda(plantilla, consulta)
            if js and url in definitivas:
                continue
            pagina = await abrir(navegador, url, js=js, validador=validador)
            if pagina is None:
                continue
            if parece_listado(pagina, dominio, consulta, portada=portada):
                return Busqueda(plantilla, pagina, js, origen)
            if pagina.estado in ESTADOS_DEFINITIVOS:
                definitivas.add(url)
    return None


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


def plantillas_de_formularios(pagina: Pagina, dominio: str) -> list[str]:
    """Plantillas de búsqueda (con `{consulta}`) de los formularios de búsqueda de `pagina`.

    Solo formularios GET cuyo `action` es del propio `dominio` y que tienen un campo de búsqueda
    (`type=search` o un nombre típico). Los campos ocultos se conservan en el orden del
    formulario, como los enviaría el navegador (PrestaShop necesita `controller=search`). Como
    mucho `MAX_FORMULARIOS`, los primeros: una portada no puede multiplicar las descargas.
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
        plantilla = _plantilla(formulario, accion)
        if plantilla is not None and plantilla not in plantillas:
            plantillas.append(plantilla)
            if len(plantillas) >= MAX_FORMULARIOS:
                break
    return plantillas


def _plantilla(formulario: Nodo, accion: str) -> str | None:
    pares: list[str] = []
    ocultos = 0
    busqueda = False
    for entrada in _elementos(formulario, "input"):
        nombre = (entrada.atributo("name") or "").strip()
        tipo = (entrada.atributo("type") or "text").strip().lower()
        if not nombre:
            continue
        if tipo == "hidden":
            ocultos += 1
            valor = entrada.atributo("value") or ""
            if ocultos > MAX_OCULTOS or len(valor) > MAX_VALOR_OCULTO:
                return None
            pares.append(urlencode([(nombre, valor)]))
        elif not busqueda and (
            tipo == "search" or (tipo == "text" and nombre.lower() in NOMBRES_BUSQUEDA)
        ):
            busqueda = True
            pares.append(f"{quote_plus(nombre)}={MARCADOR}")
    if not busqueda:
        return None
    # Al enviar un formulario GET, su consulta sustituye a la que tuviera `action`.
    esquema, host, ruta, _, _ = urlsplit(accion)
    return urlunsplit((esquema, host, ruta, "", "")) + "?" + "&".join(pares)


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
        if not any(r in ruta.lower() for r in RUTAS_DE_BUSQUEDA):
            continue  # La palabra estaba en la consulta o en el fragmento, no en la ruta.
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
    """🤖 Hasta `MAX_PLANTILLAS_IA` plantillas propuestas por la IA, ya filtradas.

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
            max_tokens=512,
        )
    )
    return leer_plantillas_ia(respuesta.datos, _url(portada), dominio)


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
        campos = []
        for entrada in list(_elementos(formulario, "input"))[:MAX_CAMPOS_RESUMEN]:
            nombre = _corto(entrada.atributo("name") or "", 40)
            tipo = _corto(entrada.atributo("type") or "text", 20)
            if nombre:
                campos.append(f"{nombre} ({tipo})")
        lineas.append(f"- {metodo.upper()} {accion or '(otra web)'}: {', '.join(campos)}")
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


def parece_listado(pagina: Pagina, dominio: str, consulta: str, *, portada: Pagina | None) -> bool:
    """Heurística: ¿es `pagina` una página de resultados de la tienda para `consulta`?

    Responde bien, sin haber redirigido fuera de la tienda, menciona alguna palabra de la
    consulta y tiene al menos `MIN_PRODUCTOS` enlaces a la tienda junto a un precio, que no son
    los mismos que los de la portada (muchas tiendas devuelven la portada, con sus destacados,
    cuando no entienden la ruta).
    """
    if not correcta(pagina) or not de_la_tienda(pagina, dominio):
        return False
    raiz = analizar(pagina.html)
    texto = normalizar(raiz.texto())
    palabras = [p for p in normalizar(consulta).split() if len(p) >= MIN_PALABRA]
    if palabras and not any(p in texto for p in palabras):
        return False
    productos = _enlaces_con_precio(raiz, pagina, dominio)
    if len(productos) < MIN_PRODUCTOS:
        return False
    return portada is None or productos != _enlaces_con_precio(
        analizar(portada.html), portada, dominio
    )


def _enlaces_con_precio(raiz: Nodo, pagina: Pagina, dominio: str) -> set[str]:
    base = _url(pagina)
    propia = url_de_la_tienda(base, base, dominio)
    enlaces: set[str] = set()
    for enlace in _elementos(raiz, "a"):
        href = enlace.atributo("href")
        url = url_de_la_tienda(href, base, dominio) if href else None
        if url is None or url == propia or url in enlaces:
            continue
        # El precio tiene que estar en la «tarjeta» del producto: se sube por los ancestros
        # mientras no se llegue a un bloque grande ni a uno con enlaces a otras páginas.
        contenedor: Nodo | None = enlace
        for _ in range(NIVELES_CONTENEDOR):
            if contenedor is None or contenedor.padre is None:
                break
            texto = contenedor.texto()
            if len(texto) > MAX_TEXTO_CONTENEDOR or _enlaza_a_otras(contenedor, url, base, dominio):
                break
            if next(buscar_precios(texto), None) is not None:
                enlaces.add(url)
                break
            contenedor = contenedor.padre
    return enlaces


def _enlaza_a_otras(contenedor: Nodo, url: str, base: str, dominio: str) -> bool:
    for enlace in _elementos(contenedor, "a"):
        href = enlace.atributo("href")
        otra = url_de_la_tienda(href, base, dominio) if href else None
        if otra is not None and otra != url:
            return True
    return False
