"""IA · Configurador: crea el perfil de una tienda nueva con ayuda de la IA (memoria §4 y §5).

0. Si la portada es de WooCommerce o Shopify y su API pública de solo lectura devuelve productos
   con precio para la consulta de prueba, el perfil es `api_woocommerce` o `api_shopify`: la
   búsqueda va a la API (`fuentes.apis`) y no hace falta ningún selector, así que **no se llama
   a la IA** (coste 0). Si la API no responde, no existe o no da productos, se sigue como siempre.
1. Descubre la URL de búsqueda (`perfiles.descubrimiento`): OpenSearch, formularios y enlaces de
   búsqueda de la portada, rutas de la plataforma, patrones habituales y las plantillas que
   proponga la IA (validadas como cualquier otra): como último recurso o, si la portada no da
   ninguna candidata, en paralelo con la ronda HTTP y antes de Chromium (B7).
2. En la primera página de resultados que parece un listado, pide a la IA los selectores del
   listado a partir de `resumen_para_ia` (el HTML simplificado, como datos no confiables).
3. Abre la primera ficha y pide los selectores de los campos. Si la ficha trae JSON-LD, solo los
   campos que este no da (y siempre el precio: decide la validación y no todas las fichas de una
   tienda traen JSON-LD). Mientras responde la IA se abren las demás fichas que mirará la
   validación (`adelantar_fichas`, A6), que después las toma de las ya abiertas (`_ConAbiertas`),
   también las que no dan un 2xx, que la caché del navegador no guarda.
4. `necesita_js` si la búsqueda solo trae resultados al renderizarla con Chromium.
5. Valida el perfil y, si no pasa, reintenta una única vez enviando a la IA los problemas.

Tiendas sin buscador (ADR-018, nota 16). El orden completo es: API de la plataforma (0) →
**plantilla de búsqueda del usuario** (A1: la URL de entrada con `{consulta}`, que
`descubrimiento` prueba la primera) → buscador descubierto (1-5) → **catálogo por sitemap** (B) →
**listado del usuario** (A2: la URL de entrada sin `{consulta}`) → `ConfiguracionFallida` con un
mensaje que pide (o pide revisar) la URL de entrada. Si un paso no da un perfil válido se pasa al
siguiente; un bloqueo, un error de presupuesto de la IA o un error de programación se propagan.

- **B** solo se intenta si quien llama pasa `catalogo` (dónde guardarlo: el `Almacen` lo cumple)
  y el navegador sabe leer los sitemaps del robots.txt y descargar ficheros grandes
  (`perfiles.catalogo.NavegadorSitemaps`). Se lee el catálogo (`perfiles.catalogo`); si menos del
  60 % de sus URL tienen palabras en el slug, no sirve. Si sirve, se prueban 3 fichas que encajan
  con la consulta de prueba: si 2 de 3 tienen precio anclado con el JSON-LD o los metadatos, el
  perfil `sitemap` no gasta IA; si no, 1 llamada a la IA para `sel_campos` (como en el paso 3) y
  se valida otra vez. Si un sitemap no sirve, se prueba el siguiente de los declarados (causa
  R3-C: Carrefour declara primero uno de páginas informativas), como mucho
  `MAX_SITEMAPS_PROBADOS` y con una sola llamada a la IA en total. El catálogo solo se guarda si
  el perfil vale. Si ninguna ficha tiene precio y las que se abren por HTTP son el armazón de
  una SPA (`parece_armazon`, causa R5-E: Xtralife), antes de la IA se valida otra vez con
  Chromium y, desde ahí, el perfil queda con `necesita_js` y la ficha de la IA se abre igual.
- **A2**: la página 1 del listado hace de página de resultados: la IA da los selectores del
  listado y de la ficha, y el perfil `listado` se valida sin exigir que los resultados nombren la
  consulta. Si la página por HTTP no parece un listado se mira con Chromium (salvo un estado
  definitivo, como en `descubrimiento`); si tampoco, se usa igual la de HTTP si respondió bien,
  porque la URL la ha elegido el usuario.

La IA no elige URL ni ejecuta nada: solo propone selectores, que se comprueban con el lenguaje
restringido de `extraccion.selectores`, y las claves de los campos están cerradas en el propio
esquema de la salida (y se vuelven a comprobar aquí). Las URL que se visitan salen de nuestro
código o de la propia tienda (el formulario, los enlaces del listado) y pasan por el validador
anti-SSRF y por el filtro de mismo dominio.

El perfil nace con `revisado=False`: no se fija hasta que lo revisa una persona. Los errores de
navegador (bloqueo...) y de IA (presupuesto, salida no válida tras reintentar) se
propagan tal cual para que quien llama decida; si no se consigue un perfil válido se lanza
`ConfiguracionFallida`.

Si el perfil sale de un paso posterior (sitemap o listado del usuario), su nota acaba con los
motivos de los pasos descartados antes («Antes, descartado: Buscador <plantilla>: …»): la IA
pudo gastar llamadas en el buscador y quien revisa tiene que saber por qué no valió (causa
R2-OBS de la campaña multi-sector). Cada motivo descartado y lo que propuso la IA en cada
intento (selectores y problemas de la validación) van también al registro (`logging`, nivel
INFO), en una sola línea y recortados: son datos no confiables y nunca se interpretan.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from precia.extraccion.dom import analizar
from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.jsonld import productos_de_arbol
from precia.extraccion.listado import extraer_listado
from precia.extraccion.metadatos import producto_metadatos
from precia.extraccion.precios import buscar_precios
from precia.extraccion.texto import resumen_para_ia
from precia.extraccion.tipos import ResultadoListado
from precia.fuentes.apis import plantilla_api
from precia.fuentes.plataformas import Plataforma, detectar_plataforma
from precia.ia.tipos import PeticionIA, max_tokens_para
from precia.navegador.tipos import (
    ErrorDescarga,
    ErrorNavegador,
    Pagina,
    URLNoPermitida,
)
from precia.navegador.url import Validador, validar_url
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS, Campo
from precia.perfiles.catalogo import (
    MAX_SITEMAPS_PROBADOS,
    MIN_CON_PALABRAS,
    Catalogo,
    GuardarCatalogo,
    NavegadorSitemaps,
    catalogos,
    elegir_fichas,
)
from precia.perfiles.descubrimiento import (
    ESTADOS_DEFINITIVOS,
    ORIGEN_IA,
    ORIGEN_USUARIO,
    Busqueda,
    abrir,
    abrir_portada,
    correcta,
    descubrir,
    parece_listado,
)
from precia.perfiles.protocolos import Navegador, SesionIA
from precia.perfiles.tipos import (
    TIPO_HTML,
    TIPO_LISTADO,
    TIPO_SITEMAP,
    ConfiguracionFallida,
    InformeValidacion,
    Perfil,
)
from precia.perfiles.validador import (
    MARCADOR,
    MAX_FICHAS,
    MAX_RESUMEN,
    NOMBRE_PLATAFORMA,
    PLATAFORMA_DE_TIPO,
    TIPO_DE_PLATAFORMA,
    de_la_tienda,
    problema_selector,
    problemas_de_url_listado,
    problemas_de_url_sitemap,
    problemas_sin_resultados,
    resumir,
    sin_campos_repetidos,
    url_de_listado,
    validar,
)

COMPONENTE = "configurador"
AUTOR = "configurador IA"
MAX_SELECTOR = 300
MAX_PROBLEMAS_IA = 20
MAX_MOTIVO = 3
MAX_DESCARTE = 400
"""Caracteres de cada motivo descartado que van a la nota del perfil que sale."""
MIN_DESCARTES = 200
"""Sitio mínimo para los descartes en la nota, aunque haya que recortar el resumen."""
MAX_REGISTRO = 2_000
MAX_TEXTO_ARMAZON = 500
"""Caracteres de texto por debajo de los que una ficha sin producto es el armazón de una SPA
(R5-E): el de Xtralife deja solo el título (87); una página informativa real da miles."""

registro = logging.getLogger(__name__)

_ETIQUETA = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
_TEXTO: dict[str, Any] = {"type": "string", "maxLength": MAX_SELECTOR}
_NULO: dict[str, Any] = {"type": "null"}

SUBCONJUNTO_CSS = (
    "Los selectores son CSS de un subconjunto restringido; cualquier otra cosa se rechaza:\n"
    "- tipo (div), universal (*), #id, .clase (una o varias), [atributo], [atributo=valor], "
    "[atributo~=valor], [atributo^=valor], [atributo$=valor] y [atributo*=valor];\n"
    "- combinador descendiente (espacio) e hijo (>), y grupos separados por comas;\n"
    "- :nth-of-type(n) y :first-of-type.\n"
    "No se admiten otros combinadores (+, ~), otras pseudoclases (:has, :not, :nth-child, "
    ":contains...), pseudoelementos ni XPath. Para leer un atributo en lugar del texto, añade "
    "al final un espacio y @atributo, por ejemplo «a.nombre @href» o «img.foto @src». Prefiere "
    "clases e id estables a las posiciones. Usa solo clases, id y atributos que aparezcan en el "
    "HTML de los datos: no supongas los habituales de la plataforma.\n"
    "En ese HTML, «<!-- N elementos similares omitidos -->» ocupa el lugar de N hermanos con la "
    "misma estructura que los anteriores, quitados para ahorrar espacio."
)
INSTRUCCIONES_LISTADO = (
    "Eres el configurador de perfiles de PrecIA. Los datos son el HTML simplificado de la "
    "página de resultados de búsqueda de una tienda online de recambios. Devuelve los "
    "selectores CSS para leer los resultados:\n"
    "- sel_resultado: cada resultado (producto) de la búsqueda, sin @atributo;\n"
    "- sel_titulo: dentro de cada resultado, el nombre del producto;\n"
    "- sel_enlace: dentro de cada resultado, el enlace a la ficha del producto, normalmente "
    "con @href; si cada resultado es en sí mismo el enlace (un <a href> que envuelve la "
    "tarjeta), sel_resultado es ese <a> y sel_enlace es «@href» a secas, sin selector;\n"
    "- sel_precio_listado: dentro de cada resultado, el precio, o null si el listado no "
    "muestra precios.\n"
    "Selecciona solo los resultados de la propia tienda: ni anuncios ni productos patrocinados "
    "de otras webs, ni menús, ni recomendaciones.\n"
)
INSTRUCCIONES_CATEGORIA = (
    "Eres el configurador de perfiles de PrecIA. La tienda no tiene buscador. Los datos son el "
    "HTML simplificado de una página de listado o categoría de productos de la tienda (la que "
    "ha indicado el usuario): trátala como una página de resultados. Devuelve los selectores "
    "CSS para leer los productos del listado:\n"
) + INSTRUCCIONES_LISTADO.split(":\n", 1)[1]
"""Las de `INSTRUCCIONES_LISTADO` para la página de un listado del usuario (A2)."""
DESCRIPCIONES: dict[str, str] = {
    "precio": "el precio de venta actual del producto (no el precio tachado, ni los gastos de "
    "envío, ni los de productos relacionados)",
    "referencia_oem": "la referencia OEM o número de pieza del fabricante original",
    "marca": "la marca o fabricante de la pieza",
    "estado": "el estado de la pieza (nueva, usada o reacondicionada)",
    "descripcion": "la descripción del producto",
    "fotos": "las fotos del producto, con @src (las de la galería, no logotipos ni iconos)",
}
INSTRUCCIONES_FICHA = (
    "Eres el configurador de perfiles de PrecIA. Los datos son el HTML simplificado de una "
    "ficha de producto de una tienda online de recambios. Para cada campo pedido devuelve un "
    "selector CSS, aplicado a toda la página, del elemento que contiene su valor, o null si el "
    "campo no aparece en la ficha. Campos:\n"
)
INSTRUCCIONES_REINTENTO = (
    "\nEs un reintento: el perfil del intento anterior no superó la validación automática. En "
    "los datos, antes de la página, tienes los selectores anteriores y los problemas "
    "detectados; corrige lo necesario.\n"
)


def _ahora() -> datetime:
    return datetime.now(UTC)


async def configurar(
    dominio: str,
    navegador: Navegador,
    sesion_ia: SesionIA,
    consulta_prueba: str,
    *,
    campos: Sequence[Campo] = CAMPOS,
    validador: Validador = validar_url,
    reloj: Callable[[], datetime] = _ahora,
    entrada: str | None = None,
    catalogo: GuardarCatalogo | None = None,
) -> Perfil:
    """Perfil validado para `dominio` (versión 1, pendiente de revisión). Ver el módulo.

    `entrada` es la URL de entrada de la tienda (`Tienda.entrada`, ya normalizada por
    `perfiles.entrada.normalizar_entrada`): con `{consulta}` es la plantilla A1; sin ella, el
    listado A2. `catalogo` es dónde se guarda el catálogo de un perfil `sitemap` (B; el
    `Almacen`): sin él no se intenta B. El catálogo se guarda antes de devolver el perfil, que
    quien llama guarda después (`Almacen.guardar_perfil`).
    """
    claves = _claves(campos)
    host = _host(dominio)
    if host is None:
        raise ConfiguracionFallida(f"El dominio «{dominio}» no es válido.")
    normalizado = host.removeprefix("www.")
    portada = await abrir_portada(host, normalizado, navegador, validador=validador)
    if portada is not None:
        api = await _perfil_api(portada, normalizado, navegador, consulta_prueba, validador)
        if api is not None:
            return replace(api, creado=reloj())
    configuracion = _Configuracion(
        dominio=normalizado,
        navegador=navegador,
        sesion=sesion_ia,
        consulta=consulta_prueba,
        claves=claves,
        validador=validador,
        busqueda=None,
        creado=reloj(),
    )
    motivos: list[str] = []

    def descartar(motivo: str) -> None:
        motivos.append(motivo)
        registro.info("Configurador de %s: descartado. %s", normalizado, _linea(motivo))

    try:
        configuracion.busqueda = await descubrir(
            portada,
            host,
            normalizado,
            navegador,
            consulta_prueba,
            sesion=sesion_ia,
            validador=validador,
            entrada=entrada,
        )
        return await configuracion.ejecutar()
    except ConfiguracionFallida as error:
        busqueda = configuracion.busqueda
        descartar(str(error) if busqueda is None else f"Buscador {busqueda.plantilla}: {error}")
    if catalogo is not None and isinstance(navegador, NavegadorSitemaps):
        try:
            base = (portada.url_final or portada.url) if portada is not None else None
            # La URL final entera, no solo su origen: su ruta da el prefijo de idioma o país
            # (`/es/es/`) con que se filtra el catálogo (R4-C); con el origen se perdía (R5-C).
            perfil = await configuracion.por_sitemap(
                base or f"https://{host}/",
                detectar_plataforma(portada) if portada is not None else None,
                navegador,
                catalogo,
            )
            return replace(perfil, nota=con_descartes(perfil.nota, motivos))
        except ConfiguracionFallida as error:
            descartar(str(error))
    if entrada is not None and MARCADOR not in entrada:
        try:
            perfil = await configuracion.por_listado(entrada)
            return replace(perfil, nota=con_descartes(perfil.nota, motivos))
        except ConfiguracionFallida as error:
            descartar(str(error))
    raise ConfiguracionFallida(sin_perfil(normalizado, motivos, con_entrada=entrada is not None))


PISTA_ENTRADA = (
    "la de su página de búsqueda con {consulta} en el lugar del texto buscado, o la de un "
    "listado o categoría de productos (con {pagina} en el número de página, si tiene varias)."
)


def sin_perfil(dominio: str, motivos: Sequence[str], *, con_entrada: bool) -> str:
    """El mensaje de `ConfiguracionFallida` cuando nada ha servido: los motivos de cada paso y
    qué URL de entrada hace falta (o que se revise la que hay). Se enseña al usuario."""
    pista = (
        "Revisa la URL de entrada de la tienda: tiene que ser "
        if con_entrada
        else "Si la tienda no tiene buscador, indica su URL de entrada: "
    ) + PISTA_ENTRADA
    detalle = " ".join(m if m.endswith(".") else m + "." for m in motivos)
    hueco = MAX_RESUMEN - len(pista) - len(dominio) - 40
    if len(detalle) > hueco:
        detalle = detalle[: max(hueco - 1, 0)] + "…"
    return f"No se ha podido configurar {dominio}. {detalle} {pista}"


def con_descartes(nota: str, motivos: Sequence[str]) -> str:
    """`nota` con los motivos de los pasos descartados al final (R2-OBS), en `MAX_RESUMEN`.

    El principio de la nota (de dónde sale el perfil y su validación) no se toca; si no cabe
    todo, se recortan primero los descartes, que tienen al menos `MIN_DESCARTES` caracteres.
    """
    if not motivos:
        return nota
    partes = (_recortar(m.strip(), MAX_DESCARTE) for m in motivos)
    descartes = "Antes, descartado: " + " ".join(
        m if m.endswith((".", "…")) else m + "." for m in partes
    )
    hueco = max(MAX_RESUMEN - len(nota) - 1, MIN_DESCARTES)
    nota = _recortar(nota, MAX_RESUMEN - 1 - min(len(descartes), hueco))
    return f"{nota} {_recortar(descartes, hueco)}"


def _recortar(texto: str, maximo: int) -> str:
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def _linea(texto: str) -> str:
    """`texto` para el registro: una sola línea (sin caracteres de control) y recortado."""
    return _recortar(_CONTROL.sub(" ", texto), MAX_REGISTRO)


def _propuesto(propuesto: Mapping[str, Any]) -> str:
    """Lo que propuso la IA, para el registro (dato no confiable: no se interpreta)."""
    return _linea(json.dumps(propuesto, ensure_ascii=False, default=str))


def _host(dominio: str) -> str | None:
    """`dominio` como nombre de host (minúsculas, IDNA, sin punto final), o `None`.

    Con él se escribe la URL de la portada, así que solo se admiten etiquetas de letras, cifras y
    guiones: un `#`, `?`, `@` o una barra invertida en el dominio cambiaría el host al que se
    conecta («otra.es#tienda.es» visitaría otra.es).
    """
    texto = dominio.strip().lower().rstrip(".")
    try:
        host = texto.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    etiquetas = host.split(".")
    if len(etiquetas) < 2 or len(host) > 253:
        return None
    return host if all(_ETIQUETA.fullmatch(e) for e in etiquetas) else None


# --- 0. Perfil de la API pública --------------------------------------------------------------


async def _perfil_api(
    portada: Pagina, dominio: str, navegador: Navegador, consulta: str, validador: Validador
) -> Perfil | None:
    """Perfil `api_*` validado si la plataforma de la portada tiene API pública y responde.

    Un fallo al descargar la API (no existe, URL no permitida) solo quiere decir que no
    se puede usar: se sigue por el HTML. Un bloqueo sí se propaga.
    """
    plataforma = detectar_plataforma(portada)
    if plataforma is None or (tipo := TIPO_DE_PLATAFORMA.get(plataforma)) is None:
        return None
    plantilla = plantilla_api(plataforma, portada.url_final or portada.url)
    if plantilla is None:
        return None
    perfil = Perfil(
        dominio=dominio,
        version=1,
        url_busqueda=plantilla,
        sel_resultado="",
        sel_titulo="",
        sel_enlace="",
        sel_precio_listado=None,
        sel_campos={},
        necesita_js=False,
        autor=AUTOR,
        revisado=False,
        creado=_ahora(),
        tipo=tipo,
    )
    try:
        informe = await validar(perfil, navegador, consulta, validador=validador)
    except (ErrorDescarga, URLNoPermitida):
        return None
    if not informe.valido:
        return None
    nombre = NOMBRE_PLATAFORMA[PLATAFORMA_DE_TIPO[tipo]]
    return replace(perfil, nota=_nota(f"API pública de {nombre}, sin IA. ", resumir(informe)))


def _nota(prefijo: str, resumen: str) -> str:
    texto = prefijo + resumen
    return texto if len(texto) <= MAX_RESUMEN else texto[: MAX_RESUMEN - 1] + "…"


# --- 2 y 3. Selectores con IA, validación y reintento ----------------------------------------


@dataclass
class _Propuesta:
    """Un intento: lo que propuso la IA (tal cual), el perfil si se llegó a montar y los
    problemas encontrados, que se le devuelven a la IA en el reintento."""

    propuesto: dict[str, Any] = field(default_factory=dict)
    perfil: Perfil | None = None
    problemas: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ConAbiertas:
    """El navegador, pero lo que ya abrió `_Configuracion.ficha` se sirve sin pedirlo otra vez.

    La caché del navegador solo guarda los 2xx: sin esto, una ficha con un 404 (caso real:
    decathlon.es, ronda 1) se pedía al adelantar (A6) y otra vez al validar.
    """

    navegador: Navegador
    abiertas: Mapping[tuple[str, bool], Pagina]

    async def obtener(self, url: str, *, js: bool = False) -> Pagina:
        abierta = self.abiertas.get((url, js))
        if abierta is not None:
            return abierta
        return await self.navegador.obtener(url, js=js)


@dataclass
class _Configuracion:
    dominio: str
    navegador: Navegador
    sesion: SesionIA
    consulta: str
    claves: tuple[str, ...]
    validador: Validador
    busqueda: Busqueda | None
    """La página de resultados (o, en A2, la página 1 del listado) que se le enseña a la IA."""
    creado: datetime
    tipo: str = TIPO_HTML
    _fichas: dict[tuple[str, bool], Pagina | None] = field(default_factory=dict)
    """Fichas ya abiertas, por URL y por si se abrieron con Chromium (R5-E)."""
    _abiertas: dict[tuple[str, bool], Pagina] = field(default_factory=dict)
    """Lo que devolvió el navegador para esas fichas, por URL validada y `js`, sea cual sea su
    estado: la validación lo usa en lugar de pedirlo otra vez (`_ConAbiertas`)."""
    _ia_en_sitemap: bool = False
    """Ya se ha pedido a la IA la ficha de un sitemap: no se pide para otro (B)."""

    @property
    def pagina(self) -> Busqueda:
        assert self.busqueda is not None, "Primero hay que tener la búsqueda o el listado."
        return self.busqueda

    async def ejecutar(self) -> Perfil:
        anterior: _Propuesta | None = None
        for intento in (1, 2):
            propuesta = await self.proponer(anterior)
            informe: InformeValidacion | None = None
            if propuesta.perfil is not None:
                informe = await validar(
                    propuesta.perfil,
                    _ConAbiertas(self.navegador, self._abiertas),
                    self.consulta,
                    validador=self.validador,
                )
                if not informe.valido:
                    propuesta.problemas.extend(informe.problemas)
            self.registrar(f"intento {intento} de 2", propuesta, informe)
            if propuesta.perfil is not None and informe is not None and informe.valido:
                perfil = sin_campos_repetidos(propuesta.perfil, informe)
                return replace(perfil, nota=self.nota(informe))
            anterior = propuesta
        assert anterior is not None
        motivo = "; ".join(anterior.problemas[:MAX_MOTIVO]) or "sin detalles"
        con = " con el listado de la URL de entrada" if self.tipo == TIPO_LISTADO else ""
        raise ConfiguracionFallida(
            f"La IA no ha conseguido un perfil válido para {self.dominio}{con}: {motivo}"
        )

    def registrar(
        self, paso: str, propuesta: _Propuesta, informe: InformeValidacion | None
    ) -> None:
        """Al registro: lo que propuso la IA en un intento y cómo salió (R2-OBS)."""
        if informe is not None and informe.valido:
            resultado = resumir(informe)
        else:
            resultado = "problemas: " + ("; ".join(propuesta.problemas) or "sin detalles")
        registro.info(
            "Configurador de %s (%s, %s): la IA propone %s; %s",
            self.dominio,
            self.pagina.plantilla if self.busqueda is not None else "sin búsqueda",
            paso,
            _propuesto(propuesta.propuesto),
            _linea(resultado),
        )

    def nota(self, informe: InformeValidacion) -> str:
        """El resumen de la validación, con delante de dónde salió la URL (para quien revisa)."""
        nota = resumir(informe)
        if self.tipo == TIPO_LISTADO:
            return _nota("Listado de la URL de entrada (sin buscador). ", nota)
        if self.pagina.origen == ORIGEN_IA:
            # Quien revisa el perfil tiene que saber que la URL no salió de la tienda.
            return _nota("URL de búsqueda propuesta por la IA. ", nota)
        if self.pagina.origen == ORIGEN_USUARIO:
            return _nota("URL de entrada del usuario. ", nota)
        return nota

    async def proponer(self, anterior: _Propuesta | None) -> _Propuesta:
        propuesta = _Propuesta()
        listado = await self.pedir_listado(anterior, propuesta)
        if listado is None:
            return propuesta
        perfil = self.perfil(listado, {})
        resultados = extraer_listado(self.pagina.pagina, perfil)
        if not resultados:
            propuesta.problemas.extend(
                problemas_sin_resultados(self.pagina.pagina, perfil, self.consulta)
            )
            return propuesta
        ficha = await self.primera_ficha(resultados)
        if ficha is None:
            propuesta.problemas.append("No se pudo abrir ninguna ficha de los resultados.")
            return propuesta
        urls = [r.url for r in resultados[:MAX_FICHAS]]
        adelantadas = asyncio.create_task(self.adelantar_fichas(urls, js=self.pagina.necesita_js))
        try:
            sel_campos = await self.pedir_campos(ficha, anterior, propuesta)
        except BaseException:
            # Cancelar una descarga es seguro: el limitador da su dominio por ocupado hasta su
            # timeout (C1), así que no coincide con la siguiente (ADR-017).
            adelantadas.cancel()
            raise
        await adelantadas
        propuesta.perfil = self.perfil(listado, sel_campos)
        return propuesta

    async def adelantar_fichas(self, urls: Sequence[str], *, js: bool) -> None:
        """Abre las fichas que abrirá `validar` mientras responde la IA de campos (A6 del plan de
        velocidad): luego las encuentra en la caché de páginas del navegador.

        Son las mismas URL y con el mismo `js` (las `MAX_FICHAS` primeras del listado, que el
        perfil de la validación lee igual: solo cambian los selectores de la ficha), en orden y
        por la cortesía del navegador. Las ya abiertas no se piden otra vez (`ficha`), y
        `validar` las toma de `_abiertas`, también las que no dan un 2xx. Un error aquí no decide
        nada: se deja de adelantar, y `validar` vuelve a pedir la ficha y decide (un `Bloqueado`
        sale de ahí). Si la IA falla, se habrán abierto hasta dos fichas de más.
        """
        try:
            for url in urls:
                await self.ficha(url, js=js)
        except ErrorNavegador:
            return

    def perfil(self, listado: Mapping[str, str | None], sel_campos: dict[str, str]) -> Perfil:
        return Perfil(
            dominio=self.dominio,
            version=1,
            url_busqueda=self.pagina.plantilla,
            sel_resultado=listado["sel_resultado"] or "",
            sel_titulo=listado["sel_titulo"] or "",
            sel_enlace=listado["sel_enlace"] or "",
            sel_precio_listado=listado["sel_precio_listado"],
            sel_campos=sel_campos,
            necesita_js=self.pagina.necesita_js,
            autor=AUTOR,
            revisado=False,
            creado=self.creado,
            tipo=self.tipo,
        )

    async def pedir_listado(
        self, anterior: _Propuesta | None, propuesta: _Propuesta
    ) -> dict[str, str | None] | None:
        tarea = INSTRUCCIONES_CATEGORIA if self.tipo == TIPO_LISTADO else INSTRUCCIONES_LISTADO
        respuesta = await self.sesion.pedir(
            PeticionIA(
                componente=COMPONENTE,
                instrucciones=_instrucciones(tarea, anterior),
                datos=datos_para_ia(self.pagina.pagina, anterior_de(anterior)),
                esquema=ESQUEMA_LISTADO,
                max_tokens=max_tokens_para(ESQUEMA_LISTADO),
            )
        )
        propuesta.propuesto.update(respuesta.datos)
        return leer_listado(respuesta.datos, propuesta.problemas)

    async def primera_ficha(self, resultados: Sequence[ResultadoListado]) -> Pagina | None:
        return await self.abrir_ficha([r.url for r in resultados], js=self.pagina.necesita_js)

    async def abrir_ficha(
        self, urls: Sequence[str], *, js: bool, producto: bool = False
    ) -> Pagina | None:
        """La primera de las `MAX_FICHAS` primeras `urls` que se abre bien y sigue en la tienda;
        con `producto`, además tiene que parecer un producto (`parece_producto`)."""
        for url in urls[:MAX_FICHAS]:
            pagina = await self.ficha(url, js=js)
            if pagina is not None and (not producto or parece_producto(pagina)):
                return pagina
        return None

    async def ficha(self, url: str, *, js: bool) -> Pagina | None:
        """La ficha `url` si se abre bien y sigue en la tienda; cada `(url, js)` se abre una vez."""
        clave = (url, js)
        if clave not in self._fichas:
            pagina = await self._abrir(url, js=js)
            # Una ficha que redirige fuera de la tienda no se le enseña a la IA.
            buena = pagina is not None and correcta(pagina)
            buena = buena and pagina is not None and de_la_tienda(pagina, self.dominio)
            self._fichas[clave] = pagina if buena else None
        return self._fichas[clave]

    async def _abrir(self, url: str, *, js: bool) -> Pagina | None:
        """Como `descubrimiento.abrir`, y guarda lo que devuelve el navegador en `_abiertas`."""
        try:
            validada = await self.validador(url)
            pagina = await self.navegador.obtener(validada, js=js)
        except (ErrorDescarga, URLNoPermitida):
            return None
        self._abiertas[(validada, js)] = pagina
        return pagina

    async def son_armazones(self, urls: Sequence[str]) -> bool:
        """Alguna de las `MAX_FICHAS` primeras `urls` se abre bien por HTTP y todas las que se
        abren son el armazón de una SPA (`parece_armazon`): con Chromium pueden pintarse."""
        abiertas = [
            p for url in urls[:MAX_FICHAS] if (p := await self.ficha(url, js=False)) is not None
        ]
        return bool(abiertas) and all(parece_armazon(p) for p in abiertas)

    # --- B. Catálogo por sitemap ------------------------------------------------------------

    async def por_sitemap(
        self,
        base: str,
        plataforma: Plataforma | None,
        navegador: NavegadorSitemaps,
        destino: GuardarCatalogo,
    ) -> Perfil:
        """Perfil `sitemap` validado, con su catálogo ya guardado en `destino` (ver el módulo).

        `base` es la URL final de la portada, con su ruta: de ella sale el prefijo de idioma o país
        del catálogo (`Catalogo.idioma`), que se guarda con él (R5-C).

        Prueba los catálogos de `perfiles.catalogo.catalogos` en orden, como mucho
        `MAX_SITEMAPS_PROBADOS`, hasta que uno vale; si ninguno, el motivo junta los de todos.
        """
        motivos: list[tuple[str, str]] = []
        async with aclosing(
            catalogos(base, self.dominio, navegador, plataforma, validador=self.validador)
        ) as leidos:
            async for leido in leidos:
                try:
                    perfil, informe, con_ia = await self.probar_sitemap(leido)
                except ConfiguracionFallida as error:
                    motivos.append((leido.sitemap, str(error)))
                    registro.info(
                        "Configurador de %s: sitemap %s descartado. %s",
                        self.dominio,
                        leido.sitemap,
                        _linea(str(error)),
                    )
                    if len(motivos) >= MAX_SITEMAPS_PROBADOS:
                        break
                    continue
                info = destino.guardar_catalogo(
                    self.dominio,
                    leido.sitemap,
                    leido.entradas,
                    parcial=leido.parcial,
                    pendientes=leido.pendientes,
                    idioma=leido.idioma,
                )
                tamano = f"{info.total} URL" + (", parcial" if info.parcial else "")
                como = "selectores de la ficha por IA" if con_ia else "sin IA"
                if perfil.necesita_js:
                    como += ", con Chromium"
                nota = _nota(f"Catálogo por sitemap ({tamano}), {como}. ", resumir(informe))
                return replace(perfil, nota=nota)
        if not motivos:
            raise ConfiguracionFallida(
                f"{self.dominio} no publica un sitemap con URL de la tienda (ni en su robots.txt "
                "ni en las rutas habituales)."
            )
        raise ConfiguracionFallida(_ningun_sitemap(self.dominio, motivos))

    async def probar_sitemap(self, leido: Catalogo) -> tuple[Perfil, InformeValidacion, bool]:
        """El perfil `sitemap` de `leido` validado, su informe y si se usó la IA; si no vale,
        `ConfiguracionFallida`. La IA solo se pide si no se ha pedido ya para otro sitemap, si la
        URL del sitemap vale para el perfil y si alguna ficha abierta parece un producto (R5-F):
        si no, no podría arreglar nada y el sitemap siguiente se quedaría sin ella. Si las fichas
        por HTTP son armazones de una SPA, se prueba con Chromium antes que la IA (R5-E)."""
        problemas = problemas_de_url_sitemap(leido.sitemap, self.dominio)
        if problemas:
            raise ConfiguracionFallida(
                f"El sitemap {leido.sitemap} no vale para el perfil de {self.dominio}: "
                f"{problemas[0]}"
            )
        legibles = leido.con_palabras
        if legibles < MIN_CON_PALABRAS:
            raise ConfiguracionFallida(
                f"El sitemap de {self.dominio} no sirve para buscar: solo el {legibles:.0%} de "
                f"sus URL tienen palabras en la ruta (hace falta el {MIN_CON_PALABRAS:.0%})."
            )
        fichas = elegir_fichas(leido, self.consulta)
        perfil = Perfil(
            dominio=self.dominio,
            version=1,
            url_busqueda=leido.sitemap,
            sel_resultado="",
            sel_titulo="",
            sel_enlace="",
            sel_precio_listado=None,
            sel_campos={},
            necesita_js=False,
            autor=AUTOR,
            revisado=False,
            creado=self.creado,
            tipo=TIPO_SITEMAP,
        )
        informe = await self.validar_sitemap(perfil, fichas)
        if informe.valido:
            return perfil, informe, False
        if informe.fichas_ok == 0 and await self.son_armazones(fichas):
            # R5-E: por HTTP las fichas son el armazón de una SPA; se prueba con Chromium antes de
            # gastar la IA, y desde aquí todo (la ficha de la IA y su validación) va con Chromium.
            perfil = replace(perfil, necesita_js=True)
            informe = await self.validar_sitemap(perfil, fichas)
            if informe.valido:
                return perfil, informe, False
        # El JSON-LD y los metadatos no bastan: 1 llamada a la IA para los selectores, si no se
        # ha gastado ya en otro sitemap y alguna ficha se abre y parece un producto.
        ficha = (
            None
            if self._ia_en_sitemap
            else await self.abrir_ficha(fichas, js=perfil.necesita_js, producto=True)
        )
        if ficha is None:
            raise ConfiguracionFallida(_no_vale_el_sitemap(self.dominio, informe))
        self._ia_en_sitemap = True
        propuesta = _Propuesta()
        sel_campos = await pedir_campos(self.sesion, ficha, self.claves, None, propuesta)
        perfil = replace(perfil, sel_campos=sel_campos)
        informe = await self.validar_sitemap(perfil, fichas)
        registro.info(
            "Configurador de %s (sitemap %s): la IA propone %s; %s",
            self.dominio,
            leido.sitemap,
            _propuesto(propuesta.propuesto),
            _linea(resumir(informe)),
        )
        if not informe.valido:
            raise ConfiguracionFallida(_no_vale_el_sitemap(self.dominio, informe))
        return sin_campos_repetidos(perfil, informe), informe, True

    async def validar_sitemap(self, perfil: Perfil, fichas: Sequence[str]) -> InformeValidacion:
        return await validar(
            perfil, self.navegador, self.consulta, validador=self.validador, fichas=fichas
        )

    # --- A2. Listado del usuario ------------------------------------------------------------

    async def por_listado(self, entrada: str) -> Perfil:
        """Perfil `listado` validado a partir de la URL de entrada sin `{consulta}`."""
        problemas = problemas_de_url_listado(entrada, self.dominio)
        if problemas:
            raise ConfiguracionFallida(f"La URL de entrada no vale como listado: {problemas[0]}")
        url = url_de_listado(entrada, 1)
        pagina = await abrir(self.navegador, url, js=False, validador=self.validador)
        elegida, js = (pagina, False) if self.es_listado(pagina) else (None, False)
        if elegida is None and (pagina is None or pagina.estado not in ESTADOS_DEFINITIVOS):
            renderizada = await abrir(self.navegador, url, js=True, validador=self.validador)
            if self.es_listado(renderizada):
                elegida, js = renderizada, True
        bien = pagina is not None and correcta(pagina) and de_la_tienda(pagina, self.dominio)
        if elegida is None and bien:
            elegida = pagina  # La URL la ha elegido el usuario: que la IA la mire.
        if elegida is None:
            estado = f" (responde con el estado {pagina.estado})" if pagina is not None else ""
            raise ConfiguracionFallida(f"La URL de entrada ({url}) no da un listado{estado}.")
        self.busqueda = Busqueda(entrada, elegida, js, ORIGEN_USUARIO)
        self.tipo = TIPO_LISTADO
        return await self.ejecutar()

    def es_listado(self, pagina: Pagina | None) -> bool:
        """Parece un listado de la tienda (sin exigir palabras de la consulta: es una categoría)."""
        return pagina is not None and parece_listado(pagina, self.dominio, "", portada=None)

    async def pedir_campos(
        self, ficha: Pagina, anterior: _Propuesta | None, propuesta: _Propuesta
    ) -> dict[str, str]:
        return await pedir_campos(self.sesion, ficha, self.claves, anterior, propuesta)


def parece_producto(pagina: Pagina) -> bool:
    """La página parece la ficha de un producto: un importe a la vista (el mismo criterio que el
    anclaje del precio), un `Product` en JSON-LD, o microdatos u OpenGraph de producto.

    Antes de enseñarle una ficha a la IA (R5-F): un armazón SPA vacío o una página informativa
    sin precio no le dejan proponer nada útil. No se exige el precio a la vista porque un
    selector de atributo (`meta[itemprop=price] @content`) lo lee sin él.
    """
    raiz = analizar(pagina.html)
    if next(buscar_precios(raiz.texto()), None) is not None:
        return True
    url = pagina.url_final or pagina.url
    return bool(productos_de_arbol(raiz)) or producto_metadatos(raiz, url=url) is not None


def parece_armazon(pagina: Pagina) -> bool:
    """La página es el armazón de una SPA: no parece un producto (`parece_producto`), casi no
    tiene texto (menos de `MAX_TEXTO_ARMAZON` caracteres) y no viene ya de Chromium.

    Una ficha así por HTTP solo se pinta con JavaScript (R5-E): vale la pena probar el perfil
    `sitemap` con Chromium. Una página sin precio pero con texto (informativa, agotada) no lo es,
    y lo que ya vino por Chromium (`Pagina.por_chromium`, ADR-023) no gana nada repitiéndolo.
    """
    if pagina.por_chromium or parece_producto(pagina):
        return False
    return len(analizar(pagina.html).texto()) < MAX_TEXTO_ARMAZON


def _no_vale_el_sitemap(dominio: str, informe: InformeValidacion) -> str:
    motivo = "; ".join(informe.problemas[:MAX_MOTIVO]) or "sin detalles"
    return f"El catálogo por sitemap de {dominio} no da fichas con precio: {motivo}"


def _ningun_sitemap(dominio: str, motivos: Sequence[tuple[str, str]]) -> str:
    """El motivo de B cuando no vale ningún sitemap: el de cada uno, con su URL si son varios."""
    if len(motivos) == 1:
        return motivos[0][1]
    partes = (_recortar(motivo, MAX_DESCARTE) for _, motivo in motivos)
    detalle = " ".join(
        f"{sitemap}: {m if m.endswith(('.', '…')) else m + '.'}"
        for (sitemap, _), m in zip(motivos, partes, strict=True)
    )
    return f"Se han probado {len(motivos)} sitemaps de {dominio} y ninguno sirve. {detalle}"


async def pedir_campos(
    sesion: SesionIA,
    ficha: Pagina,
    claves: Sequence[str],
    anterior: _Propuesta | None,
    propuesta: _Propuesta,
) -> dict[str, str]:
    """Selectores de los campos de `ficha` (paso 3; también en B)."""
    # Lo que ya da el JSON-LD de la ficha no se pide, salvo el precio (ver el módulo).
    del_jsonld = await extraer_ficha(ficha, claves)
    pedidos = tuple(c for c in claves if c == "precio" or c not in del_jsonld.campos)
    lista = "\n".join(f"- {c}: {DESCRIPCIONES[c]}" for c in pedidos)
    esquema = esquema_campos(pedidos)
    respuesta = await sesion.pedir(
        PeticionIA(
            componente=COMPONENTE,
            instrucciones=_instrucciones(INSTRUCCIONES_FICHA + lista + "\n", anterior),
            datos=datos_para_ia(ficha, anterior_de(anterior), listado=False),
            esquema=esquema,
            max_tokens=max_tokens_para(esquema),
        )
    )
    propuesta.propuesto["sel_campos"] = respuesta.datos
    return leer_campos(respuesta.datos, pedidos, propuesta.problemas)


def _claves(campos: Sequence[Campo]) -> tuple[str, ...]:
    """Claves pedidas, siempre con el precio: sin él no hay oferta ni validación posible."""
    claves = tuple(dict.fromkeys(("precio", *(c.clave for c in campos))))
    desconocidas = [c for c in claves if c not in CLAVES_CAMPOS]
    if desconocidas:
        raise ValueError(f"Campos no admitidos: {', '.join(desconocidas)}")
    return claves


def _instrucciones(tarea: str, anterior: _Propuesta | None) -> str:
    reintento = INSTRUCCIONES_REINTENTO if anterior is not None else ""
    return tarea + reintento + "\n" + SUBCONJUNTO_CSS


# --- Piezas compartidas con el reparador ------------------------------------------------------

ESQUEMA_LISTADO: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sel_resultado": {**_TEXTO, "description": "Cada resultado de la búsqueda"},
        "sel_titulo": {**_TEXTO, "description": "Nombre del producto, dentro del resultado"},
        "sel_enlace": {**_TEXTO, "description": "Enlace a la ficha, dentro del resultado"},
        "sel_precio_listado": {
            "description": "Precio dentro del resultado, o null",
            "anyOf": [_TEXTO, _NULO],
        },
    },
    "required": ["sel_resultado", "sel_titulo", "sel_enlace", "sel_precio_listado"],
    "additionalProperties": False,
}


def esquema_campos(claves: Sequence[str]) -> dict[str, Any]:
    """Objeto con una propiedad por campo de `nucleo.modelos.CAMPOS` (texto o null) y nada más."""
    return {
        "type": "object",
        "properties": {
            c: {"description": DESCRIPCIONES[c], "anyOf": [_TEXTO, _NULO]}
            for c in claves
            if c in CLAVES_CAMPOS
        },
        "required": [c for c in claves if c in CLAVES_CAMPOS],
        "additionalProperties": False,
    }


def leer_listado(datos: Mapping[str, Any], problemas: list[str]) -> dict[str, str | None] | None:
    """Selectores del listado propuestos por la IA, o `None` (con el motivo en `problemas`)."""
    listado: dict[str, str | None] = {}
    valido = True
    for nombre in ("sel_resultado", "sel_titulo", "sel_enlace", "sel_precio_listado"):
        bruto = datos.get(nombre)
        if bruto is None and nombre == "sel_precio_listado":
            listado[nombre] = None
            continue
        selector = bruto.strip() if isinstance(bruto, str) else bruto
        problema = problema_selector(nombre, selector, con_atributo=nombre != "sel_resultado")
        if problema is None:
            listado[nombre] = selector
        elif nombre == "sel_precio_listado":
            # El precio del listado es opcional: sin él, el perfil aún puede valer.
            problemas.append(problema)
            listado[nombre] = None
        else:
            problemas.append(problema)
            valido = False
    return listado if valido else None


def leer_campos(
    datos: Mapping[str, Any], pedidos: Sequence[str], problemas: list[str]
) -> dict[str, str]:
    """Selectores válidos de los campos pedidos. Lo demás (claves inventadas, nulos, tipos
    raros, selectores fuera del subconjunto) no entra en el perfil."""
    sel_campos: dict[str, str] = {}
    for clave in pedidos:
        bruto = datos.get(clave)
        if clave not in CLAVES_CAMPOS or bruto is None:
            continue
        selector = bruto.strip() if isinstance(bruto, str) else bruto
        if selector == "":
            continue
        problema = problema_selector(f"sel_campos[{clave}]", selector)
        if problema is None and isinstance(selector, str):
            sel_campos[clave] = selector
        else:
            problemas.append(problema or f"sel_campos[{clave}] no es válido.")
    return sel_campos


def selectores_de(perfil: Perfil) -> dict[str, Any]:
    return {
        "sel_resultado": perfil.sel_resultado,
        "sel_titulo": perfil.sel_titulo,
        "sel_enlace": perfil.sel_enlace,
        "sel_precio_listado": perfil.sel_precio_listado,
        "sel_campos": dict(perfil.sel_campos),
    }


@dataclass(frozen=True, slots=True)
class Anterior:
    """Lo que se le cuenta a la IA de un intento anterior (va en los datos: no es de fiar)."""

    selectores: Mapping[str, Any]
    problemas: Sequence[str] = ()


def anterior_de(propuesta: _Propuesta | None) -> Anterior | None:
    if propuesta is None:
        return None
    return Anterior(propuesta.propuesto, propuesta.problemas)


def datos_para_ia(pagina: Pagina, anterior: Anterior | None = None, *, listado: bool = True) -> str:
    """Datos de la petición: el intento anterior (si lo hay) y el resumen de la página.

    El intento anterior va delante para que, si los datos se recortan, se pierda el final de la
    página y no los problemas. `listado` va a `resumen_para_ia`: en una ficha (`False`) no se
    reserva sitio para la serie de productos relacionados.
    """
    partes: list[str] = []
    if anterior is not None:
        if anterior.selectores:
            selectores = json.dumps(anterior.selectores, ensure_ascii=False, indent=1, default=str)
            partes.append(f"SELECTORES DEL INTENTO ANTERIOR:\n{selectores}")
        if anterior.problemas:
            lista = "\n".join(f"- {p}" for p in anterior.problemas[:MAX_PROBLEMAS_IA])
            partes.append(f"PROBLEMAS DETECTADOS:\n{lista}")
    resumen = resumen_para_ia(pagina.html, listado=listado)
    partes.append(f"PÁGINA ({pagina.url_final or pagina.url}):\n{resumen}")
    return "\n\n".join(partes)
