"""🤖 Configurador: crea el perfil de una tienda nueva con ayuda de la IA (memoria §4 y §5).

0. Si la portada es de WooCommerce o Shopify y su API pública de solo lectura devuelve productos
   con precio para la consulta de prueba, el perfil es `api_woocommerce` o `api_shopify`: la
   búsqueda va a la API (`fuentes.apis`) y no hace falta ningún selector, así que **no se llama
   a la IA** (coste 0). Si la API no responde, no existe o no da productos, se sigue como siempre.
1. Descubre la URL de búsqueda (`perfiles.descubrimiento`): OpenSearch, formularios y enlaces de
   búsqueda de la portada, rutas de la plataforma, patrones habituales y, como último recurso,
   🤖 las plantillas que proponga la IA (validadas como cualquier otra).
2. En la primera página de resultados que parece un listado, pide a la IA los selectores del
   listado a partir de `resumen_para_ia` (el HTML simplificado, como datos no confiables).
3. Abre la primera ficha y pide los selectores de los campos. Si la ficha trae JSON-LD, solo los
   campos que este no da (y siempre el precio: decide la validación y no todas las fichas de una
   tienda traen JSON-LD).
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
  se valida otra vez. El catálogo solo se guarda si el perfil vale.
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
navegador (bloqueo, robots...) y de IA (presupuesto, salida no válida tras reintentar) se
propagan tal cual para que quien llama decida; si no se consigue un perfil válido se lanza
`ConfiguracionFallida`.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.listado import extraer_listado
from precia.extraccion.texto import resumen_para_ia
from precia.extraccion.tipos import ResultadoListado
from precia.fuentes.apis import plantilla_api
from precia.fuentes.plataformas import Plataforma, detectar_plataforma, origen_de
from precia.ia.tipos import PeticionIA
from precia.navegador.tipos import ErrorDescarga, Pagina, ProhibidoPorRobots, URLNoPermitida
from precia.navegador.url import Validador, validar_url
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS, Campo
from precia.perfiles.catalogo import (
    MIN_CON_PALABRAS,
    GuardarCatalogo,
    NavegadorSitemaps,
    descubrir_catalogo,
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
    problemas_sin_resultados,
    resumir,
    url_de_listado,
    validar,
)

COMPONENTE = "configurador"
AUTOR = "🤖 configurador"
MAX_SELECTOR = 300
MAX_PROBLEMAS_IA = 20
MAX_MOTIVO = 3

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
    "con @href;\n"
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
        motivos.append(str(error))
    if catalogo is not None and isinstance(navegador, NavegadorSitemaps):
        try:
            base = (portada.url_final or portada.url) if portada is not None else None
            return await configuracion.por_sitemap(
                origen_de(base or f"https://{host}/") or f"https://{host}",
                detectar_plataforma(portada) if portada is not None else None,
                navegador,
                catalogo,
            )
        except ConfiguracionFallida as error:
            motivos.append(str(error))
    if entrada is not None and MARCADOR not in entrada:
        try:
            return await configuracion.por_listado(entrada)
        except ConfiguracionFallida as error:
            motivos.append(str(error))
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

    Un fallo al descargar la API (no existe, robots, URL no permitida) solo quiere decir que no
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
    except (ErrorDescarga, ProhibidoPorRobots, URLNoPermitida):
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
    _fichas: dict[str, Pagina | None] = field(default_factory=dict)

    @property
    def pagina(self) -> Busqueda:
        assert self.busqueda is not None, "Primero hay que tener la búsqueda o el listado."
        return self.busqueda

    async def ejecutar(self) -> Perfil:
        anterior: _Propuesta | None = None
        for _ in range(2):
            propuesta = await self.proponer(anterior)
            if propuesta.perfil is not None:
                informe = await validar(
                    propuesta.perfil, self.navegador, self.consulta, validador=self.validador
                )
                if informe.valido:
                    return replace(propuesta.perfil, nota=self.nota(informe))
                propuesta.problemas.extend(informe.problemas)
            anterior = propuesta
        assert anterior is not None
        motivo = "; ".join(anterior.problemas[:MAX_MOTIVO]) or "sin detalles"
        con = " con el listado de la URL de entrada" if self.tipo == TIPO_LISTADO else ""
        raise ConfiguracionFallida(
            f"La IA no ha conseguido un perfil válido para {self.dominio}{con}: {motivo}"
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
        sel_campos = await self.pedir_campos(ficha, anterior, propuesta)
        propuesta.perfil = self.perfil(listado, sel_campos)
        return propuesta

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
                max_tokens=512,
            )
        )
        propuesta.propuesto.update(respuesta.datos)
        return leer_listado(respuesta.datos, propuesta.problemas)

    async def primera_ficha(self, resultados: Sequence[ResultadoListado]) -> Pagina | None:
        return await self.abrir_ficha([r.url for r in resultados], js=self.pagina.necesita_js)

    async def abrir_ficha(self, urls: Sequence[str], *, js: bool) -> Pagina | None:
        """La primera de las `MAX_FICHAS` primeras `urls` que se abre bien y sigue en la tienda."""
        for url in urls[:MAX_FICHAS]:
            if url not in self._fichas:
                pagina = await abrir(self.navegador, url, js=js, validador=self.validador)
                # Una ficha que redirige fuera de la tienda no se le enseña a la IA.
                buena = pagina is not None and correcta(pagina)
                buena = buena and pagina is not None and de_la_tienda(pagina, self.dominio)
                self._fichas[url] = pagina if buena else None
            if (pagina := self._fichas[url]) is not None:
                return pagina
        return None

    # --- B. Catálogo por sitemap ------------------------------------------------------------

    async def por_sitemap(
        self,
        base: str,
        plataforma: Plataforma | None,
        navegador: NavegadorSitemaps,
        destino: GuardarCatalogo,
    ) -> Perfil:
        """Perfil `sitemap` validado, con su catálogo ya guardado en `destino` (ver el módulo)."""
        leido = await descubrir_catalogo(
            base, self.dominio, navegador, plataforma, validador=self.validador
        )
        if leido is None:
            raise ConfiguracionFallida(
                f"{self.dominio} no publica un sitemap con URL de la tienda (ni en su robots.txt "
                "ni en las rutas habituales)."
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
        con_ia = not informe.valido
        if con_ia:
            # El JSON-LD y los metadatos no bastan: 1 llamada a la IA para los selectores.
            ficha = await self.abrir_ficha(fichas, js=False)
            if ficha is None:
                raise ConfiguracionFallida(_no_vale_el_sitemap(self.dominio, informe))
            propuesta = _Propuesta()
            sel_campos = await pedir_campos(self.sesion, ficha, self.claves, None, propuesta)
            perfil = replace(perfil, sel_campos=sel_campos)
            informe = await self.validar_sitemap(perfil, fichas)
            if not informe.valido:
                raise ConfiguracionFallida(_no_vale_el_sitemap(self.dominio, informe))
        info = destino.guardar_catalogo(
            self.dominio, leido.sitemap, leido.entradas, parcial=leido.parcial
        )
        tamano = f"{info.total} URL" + (", parcial" if info.parcial else "")
        como = "🤖 selectores de la ficha" if con_ia else "sin IA"
        return replace(
            perfil, nota=_nota(f"Catálogo por sitemap ({tamano}), {como}. ", resumir(informe))
        )

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


def _no_vale_el_sitemap(dominio: str, informe: InformeValidacion) -> str:
    motivo = "; ".join(informe.problemas[:MAX_MOTIVO]) or "sin detalles"
    return f"El catálogo por sitemap de {dominio} no da fichas con precio: {motivo}"


async def pedir_campos(
    sesion: SesionIA,
    ficha: Pagina,
    claves: Sequence[str],
    anterior: _Propuesta | None,
    propuesta: _Propuesta,
) -> dict[str, str]:
    """🤖 Selectores de los campos de `ficha` (paso 3; también en B)."""
    # Lo que ya da el JSON-LD de la ficha no se pide, salvo el precio (ver el módulo).
    del_jsonld = await extraer_ficha(ficha, claves)
    pedidos = tuple(c for c in claves if c == "precio" or c not in del_jsonld.campos)
    lista = "\n".join(f"- {c}: {DESCRIPCIONES[c]}" for c in pedidos)
    respuesta = await sesion.pedir(
        PeticionIA(
            componente=COMPONENTE,
            instrucciones=_instrucciones(INSTRUCCIONES_FICHA + lista + "\n", anterior),
            datos=datos_para_ia(ficha, anterior_de(anterior), listado=False),
            esquema=esquema_campos(pedidos),
            max_tokens=512,
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
