"""🤖 Configurador: crea el perfil de una tienda nueva con ayuda de la IA (memoria §4 y §5).

1. Descubre la URL de búsqueda: el formulario de búsqueda de la portada (GET, al mismo dominio)
   o, si no lo hay o no funciona, los patrones habituales de las plataformas de tienda.
2. En la primera página de resultados que parece un listado, pide a la IA los selectores del
   listado a partir de `resumen_para_ia` (el HTML simplificado, como datos no confiables).
3. Abre la primera ficha y pide los selectores de los campos. Si la ficha trae JSON-LD, solo los
   campos que este no da (y siempre el precio: decide la validación y no todas las fichas de una
   tienda traen JSON-LD).
4. `necesita_js` si la búsqueda solo trae resultados al renderizarla con Chromium.
5. Valida el perfil y, si no pasa, reintenta una única vez enviando a la IA los problemas.

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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus, urlencode, urlsplit, urlunsplit

from precia.extraccion.anclaje import normalizar
from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.listado import extraer_listado, url_de_la_tienda
from precia.extraccion.precios import buscar_precios
from precia.extraccion.texto import resumen_para_ia
from precia.extraccion.tipos import ResultadoListado
from precia.ia.tipos import PeticionIA
from precia.navegador.tipos import ErrorDescarga, Pagina, ProhibidoPorRobots, URLNoPermitida
from precia.navegador.url import Validador, validar_url
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS, Campo
from precia.perfiles.protocolos import Navegador, SesionIA
from precia.perfiles.tipos import ConfiguracionFallida, Perfil
from precia.perfiles.validador import (
    MARCADOR,
    MAX_FICHAS,
    de_la_tienda,
    problema_selector,
    problema_sin_resultados,
    resumir,
    url_de_busqueda,
    validar,
)

COMPONENTE = "configurador"
AUTOR = "🤖 configurador"
PATRONES = (
    "/busqueda?controller=search&s={consulta}",  # PrestaShop
    "/buscar?q={consulta}",
    "/search?q={consulta}",
    "/?s={consulta}&post_type=product",  # WooCommerce
    "/catalogsearch/result/?q={consulta}",  # Magento
    "/buscar/{consulta}",
)
"""Rutas de búsqueda habituales, en el orden en que se prueban si no hay formulario."""
NOMBRES_BUSQUEDA = frozenset({"q", "s", "search", "query", "buscar", "text"})
MAX_FORMULARIOS = 3
"""Plantillas de formulario que se prueban como mucho: cada una cuesta dos descargas."""
MAX_OCULTOS = 5
MAX_VALOR_OCULTO = 100
MAX_SELECTOR = 300
MIN_PRODUCTOS = 2
"""Enlaces a productos con precio para que una página parezca un listado."""
MAX_TEXTO_CONTENEDOR = 600
NIVELES_CONTENEDOR = 4
MIN_PALABRA = 3
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
    "clases e id estables a las posiciones."
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
) -> Perfil:
    """Perfil validado para `dominio` (versión 1, pendiente de revisión). Ver el módulo."""
    claves = _claves(campos)
    host = _host(dominio)
    if host is None:
        raise ConfiguracionFallida(f"El dominio «{dominio}» no es válido.")
    normalizado = host.removeprefix("www.")
    busqueda = await _descubrir(host, normalizado, navegador, consulta_prueba, validador)
    configuracion = _Configuracion(
        dominio=normalizado,
        navegador=navegador,
        sesion=sesion_ia,
        consulta=consulta_prueba,
        claves=claves,
        validador=validador,
        busqueda=busqueda,
        creado=reloj(),
    )
    return await configuracion.ejecutar()


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


# --- 1. Descubrimiento de la búsqueda ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Busqueda:
    plantilla: str
    pagina: Pagina
    necesita_js: bool


async def _descubrir(
    dominio: str, normalizado: str, navegador: Navegador, consulta: str, validador: Validador
) -> _Busqueda:
    portada = await _abrir(navegador, f"https://{dominio}/", js=False, validador=validador)
    final = (portada.url_final or portada.url) if portada is not None else ""
    if portada is not None and _correcta(portada) and url_de_la_tienda(final, final, normalizado):
        plantillas = plantillas_de_formularios(portada, normalizado)
        # Los patrones, en el host al que redirige la portada (`www.` o no).
        partes = urlsplit(final)
        base = f"{partes.scheme}://{partes.netloc}"
    else:
        portada, plantillas, base = None, [], f"https://{dominio}"
    candidatas = list(dict.fromkeys([*plantillas, *(base + patron for patron in PATRONES)]))

    # Primero todas por HTTP (barato) y, solo si ninguna da resultados, con Chromium.
    for js in (False, True):
        for plantilla in candidatas:
            pagina = await _abrir(
                navegador, url_de_busqueda(plantilla, consulta), js=js, validador=validador
            )
            if pagina is not None and parece_listado(
                pagina, normalizado, consulta, portada=portada
            ):
                return _Busqueda(plantilla, pagina, js)
    raise ConfiguracionFallida(
        f"No se encuentra la página de búsqueda de {normalizado}: ni la portada tiene un "
        "formulario de búsqueda que funcione ni responde ninguna de las rutas habituales."
    )


async def _abrir(
    navegador: Navegador, url: str, *, js: bool, validador: Validador
) -> Pagina | None:
    """La página, o `None` si no se puede abrir. Los bloqueos sí se propagan: hay que parar."""
    try:
        return await navegador.obtener(await validador(url), js=js)
    except (ErrorDescarga, ProhibidoPorRobots, URLNoPermitida):
        return None


def plantillas_de_formularios(pagina: Pagina, dominio: str) -> list[str]:
    """Plantillas de búsqueda (con `{consulta}`) de los formularios de búsqueda de `pagina`.

    Solo formularios GET cuyo `action` es del propio `dominio` y que tienen un campo de búsqueda
    (`type=search` o un nombre típico). Los campos ocultos se conservan en el orden del
    formulario, como los enviaría el navegador (PrestaShop necesita `controller=search`). Como
    mucho `MAX_FORMULARIOS`, los primeros: una portada no puede multiplicar las descargas.
    """
    raiz = analizar(pagina.html)
    base = pagina.url_final or pagina.url
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


def parece_listado(pagina: Pagina, dominio: str, consulta: str, *, portada: Pagina | None) -> bool:
    """Heurística: ¿es `pagina` una página de resultados de la tienda para `consulta`?

    Responde bien, sin haber redirigido fuera de la tienda, menciona alguna palabra de la
    consulta y tiene al menos `MIN_PRODUCTOS` enlaces a la tienda junto a un precio, que no son
    los mismos que los de la portada (muchas tiendas devuelven la portada, con sus destacados,
    cuando no entienden la ruta).
    """
    if not _correcta(pagina) or not de_la_tienda(pagina, dominio):
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
    base = pagina.url_final or pagina.url
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


def _elementos(raiz: Nodo, etiqueta: str) -> Iterable[Nodo]:
    return (n for n in raiz.descendientes() if n.etiqueta == etiqueta)


def _correcta(pagina: Pagina) -> bool:
    return 200 <= pagina.estado < 300


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
    busqueda: _Busqueda
    creado: datetime
    _fichas: dict[str, Pagina | None] = field(default_factory=dict)

    async def ejecutar(self) -> Perfil:
        anterior: _Propuesta | None = None
        for _ in range(2):
            propuesta = await self.proponer(anterior)
            if propuesta.perfil is not None:
                informe = await validar(
                    propuesta.perfil, self.navegador, self.consulta, validador=self.validador
                )
                if informe.valido:
                    return replace(propuesta.perfil, nota=resumir(informe))
                propuesta.problemas.extend(informe.problemas)
            anterior = propuesta
        assert anterior is not None
        motivo = "; ".join(anterior.problemas[:MAX_MOTIVO]) or "sin detalles"
        raise ConfiguracionFallida(
            f"La IA no ha conseguido un perfil válido para {self.dominio}: {motivo}"
        )

    async def proponer(self, anterior: _Propuesta | None) -> _Propuesta:
        propuesta = _Propuesta()
        listado = await self.pedir_listado(anterior, propuesta)
        if listado is None:
            return propuesta
        perfil = self.perfil(listado, {})
        resultados = extraer_listado(self.busqueda.pagina, perfil)
        if not resultados:
            propuesta.problemas.append(
                problema_sin_resultados(self.busqueda.pagina, perfil, self.consulta)
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
            url_busqueda=self.busqueda.plantilla,
            sel_resultado=listado["sel_resultado"] or "",
            sel_titulo=listado["sel_titulo"] or "",
            sel_enlace=listado["sel_enlace"] or "",
            sel_precio_listado=listado["sel_precio_listado"],
            sel_campos=sel_campos,
            necesita_js=self.busqueda.necesita_js,
            autor=AUTOR,
            revisado=False,
            creado=self.creado,
        )

    async def pedir_listado(
        self, anterior: _Propuesta | None, propuesta: _Propuesta
    ) -> dict[str, str | None] | None:
        respuesta = await self.sesion.pedir(
            PeticionIA(
                componente=COMPONENTE,
                instrucciones=_instrucciones(INSTRUCCIONES_LISTADO, anterior),
                datos=datos_para_ia(self.busqueda.pagina, anterior_de(anterior)),
                esquema=ESQUEMA_LISTADO,
                max_tokens=512,
            )
        )
        propuesta.propuesto.update(respuesta.datos)
        return leer_listado(respuesta.datos, propuesta.problemas)

    async def primera_ficha(self, resultados: Sequence[ResultadoListado]) -> Pagina | None:
        for resultado in resultados[:MAX_FICHAS]:
            if resultado.url not in self._fichas:
                pagina = await _abrir(
                    self.navegador,
                    resultado.url,
                    js=self.busqueda.necesita_js,
                    validador=self.validador,
                )
                # Una ficha que redirige fuera de la tienda no se le enseña a la IA.
                buena = pagina is not None and _correcta(pagina)
                buena = buena and pagina is not None and de_la_tienda(pagina, self.dominio)
                self._fichas[resultado.url] = pagina if buena else None
            if (pagina := self._fichas[resultado.url]) is not None:
                return pagina
        return None

    async def pedir_campos(
        self, ficha: Pagina, anterior: _Propuesta | None, propuesta: _Propuesta
    ) -> dict[str, str]:
        # Lo que ya da el JSON-LD de la ficha no se pide, salvo el precio (ver el módulo).
        del_jsonld = await extraer_ficha(ficha, self.claves)
        pedidos = tuple(c for c in self.claves if c == "precio" or c not in del_jsonld.campos)
        lista = "\n".join(f"- {c}: {DESCRIPCIONES[c]}" for c in pedidos)
        respuesta = await self.sesion.pedir(
            PeticionIA(
                componente=COMPONENTE,
                instrucciones=_instrucciones(INSTRUCCIONES_FICHA + lista + "\n", anterior),
                datos=datos_para_ia(ficha, anterior_de(anterior)),
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


def datos_para_ia(pagina: Pagina, anterior: Anterior | None = None) -> str:
    """Datos de la petición: el intento anterior (si lo hay) y el resumen de la página.

    El intento anterior va delante para que, si los datos se recortan, se pierda el final de la
    página y no los problemas.
    """
    partes: list[str] = []
    if anterior is not None:
        if anterior.selectores:
            selectores = json.dumps(anterior.selectores, ensure_ascii=False, indent=1, default=str)
            partes.append(f"SELECTORES DEL INTENTO ANTERIOR:\n{selectores}")
        if anterior.problemas:
            lista = "\n".join(f"- {p}" for p in anterior.problemas[:MAX_PROBLEMAS_IA])
            partes.append(f"PROBLEMAS DETECTADOS:\n{lista}")
    partes.append(f"PÁGINA ({pagina.url_final or pagina.url}):\n{resumen_para_ia(pagina.html)}")
    return "\n\n".join(partes)
