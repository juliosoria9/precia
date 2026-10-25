"""Validador automático de perfiles (memoria §4): un perfil solo vale si funciona en la tienda.

Se usa con los perfiles que propone la IA (configurador y reparador) antes de darlos por buenos,
y con cualquier otro cuando haga falta comprobarlo. Pasos:

1. Sintaxis: cada selector tiene que pertenecer al subconjunto de CSS de
   `extraccion.selectores` y cada clave de `sel_campos`, a `nucleo.modelos.CAMPOS`; la URL de
   búsqueda, llevar `{consulta}` y ser `http(s)` del dominio de la tienda. Si algo falla no se
   descarga nada: un perfil mal formado no debe costar peticiones a la tienda.
2. Búsqueda real con la consulta de prueba y extracción del listado.
3. Hasta `MAX_FICHAS` fichas: se cuentan los campos obtenidos y las que tienen precio anclado.

Es válido si hay al menos un resultado y tienen precio anclado 2 de 3 fichas (o todas, si hay
menos de 3). Que el precio esté anclado no basta para saber que es el del producto: un selector
puede leer un precio visible de otra parte de la página (productos relacionados, «envío gratis
desde 49 €») y quedar anclado igual. Por eso una ficha solo cuenta si, además:

- sigue siendo de la tienda tras las redirecciones (un enlace propio que redirige a un afiliado
  o a otra tienda no vale, aunque allí haya un precio), y lo mismo la página de búsqueda;
- su precio coincide con el que da el listado para ese resultado, cuando el perfil lee precios
  en el listado. Es la única referencia independiente que tenemos del precio de cada producto;
- si el precio sale del JSON-LD, `sel_campos[precio]` no lee en la ficha un precio distinto. Al
  validar ese selector no se usa (manda el JSON-LD), pero sí en las fichas que no lo traen.

Aun así, el validador no puede saber si los resultados son los productos buscados o si un
selector de campo lee el dato correcto: por eso el perfil de la IA nace sin revisar. Sí ve un
caso seguro de error (R2-CAMPOS): dos selectores de campo que leen el mismo texto, como la marca
y la referencia leyendo el mismo «P/N: …». `extraer_ficha` ya descarta el de menos prioridad en
cada ficha; si en ninguna lee un valor propio, el campo va a `campos_repetidos` con un problema
que lo explica (sin restar validez) y quien guarda el perfil lo quita con `sin_campos_repetidos`.
Igual con un selector que lee un valor sin la forma de su campo (R3-E: la referencia leyendo
«100% algodón», la marca leyendo el bloque entero del fabricante): `extraer_ficha` lo descarta
(`con_forma`) y, si en ninguna ficha lee un valor con forma, el campo va a `campos_sin_forma`.

Los `problemas` del informe explican qué falla con detalle suficiente para que el
configurador se los devuelva a la IA en su reintento; pueden contener texto de la tienda, así
que solo viajan en los `datos` de una petición, nunca en las instrucciones.

Un perfil `listado` (nota 16 §A2) se valida como uno `html`, pero sobre la página 1 de su URL
(`url_de_listado`), que no lleva `{consulta}`: hacen falta al menos `MIN_RESULTADOS_LISTADO`
resultados de la tienda y 2 de 3 fichas con precio anclado. No se exige que los resultados nombren
la consulta: un listado es una categoría, no una búsqueda.

Un perfil `sitemap` (nota 16 §B) no tiene listado que leer: se abren las fichas del catálogo que
se le pasan en `fichas` (las elige el configurador, o el orquestador del catálogo local) y vale
si 2 de 3 tienen precio anclado. Sin `sel_campos` solo cuentan el JSON-LD y los metadatos (sin IA).
El sitemap no se descarga aquí: pesa hasta 20 MiB y es cosa de `perfiles.catalogo`.

Un perfil `api_*` (nota 15 §D) no tiene selectores: se comprueban su tipo y su URL, se descarga
la búsqueda de la API (sin JavaScript) y vale si `fuentes.apis.ofertas_api` saca de la respuesta
al menos un producto con precio. No se abre ninguna ficha: los datos vienen de la propia tienda.
En Shopify la moneda sale de la portada (`Shopify.currency`); si no se sabe, el perfil no vale,
porque leer libras como euros daría precios falsos con confianza 1.

Los bloqueos (`Bloqueado`) se propagan: no son un fallo del perfil y quien llama tiene que
pararse y registrarlos. También se propaga un error al descargar la búsqueda (la tienda no
responde); el de una ficha cuenta como ficha fallida.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from itertools import islice
from urllib.parse import quote, quote_plus, urlsplit

from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.ficha import extraer_ficha, formatear_precio
from precia.extraccion.listado import (
    MAX_URL,
    dominio_normalizado,
    enlace_del_resultado,
    envoltura,
    extraer_listado,
    url_de_la_tienda,
)
from precia.extraccion.precios import leer_precio, texto_de_precio
from precia.extraccion.selectores import dividir_selector, extraer_valor, seleccionar
from precia.extraccion.tipos import FichaExtraida, Precio, SelectorNoValido
from precia.fuentes.apis import MAX_FICHAS as MAX_FICHAS_API
from precia.fuentes.apis import ofertas_api
from precia.fuentes.plataformas import Plataforma, moneda_shopify, origen_de
from precia.fuentes.sitemap import url_de_sitemap
from precia.navegador.tipos import ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import Validador, validar_url
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS, OrigenDato
from precia.perfiles.protocolos import Navegador
from precia.perfiles.tipos import (
    TIPO_API_SHOPIFY,
    TIPO_API_WOOCOMMERCE,
    TIPO_HTML,
    TIPO_LISTADO,
    TIPO_SITEMAP,
    InformeValidacion,
    Perfil,
)

MAX_FICHAS = 3
MIN_FICHAS_OK = 2
"""Fichas con precio anclado necesarias (o todas, si se abren menos)."""
MARCADOR = "{consulta}"
MARCADOR_PAGINA = "{pagina}"
"""Número de página en la URL de un perfil `listado` (opcional, como mucho una vez)."""
MIN_RESULTADOS_LISTADO = 3
"""Resultados de la tienda que tiene que dar la página 1 de un listado para que valga."""
MAX_URL_ENTRADA = 500
"""Caracteres de la URL de un listado (la URL de entrada del usuario; como el almacén)."""
MAX_FICHAS_REVISADAS = 100
"""Fichas de catálogo que se miran como mucho para elegir las que se abren (perfil `sitemap`)."""
MAX_PROBLEMAS = 20
MAX_PROBLEMA = 300
MAX_RESUMEN = 2_000
"""Lo que admite el almacén en la nota de un perfil."""
MAX_CITA = 80
MAX_RESULTADOS_REVISADOS = 50
"""Resultados en los que se busca un selector al explicar por qué no lee nada."""
CLAVES = tuple(c.clave for c in CAMPOS)
SELECTORES_LISTADO = ("sel_resultado", "sel_titulo", "sel_enlace", "sel_precio_listado")
_FORMA = {"referencia_oem": "referencia"}
"""Cómo se nombra la forma de un campo en el problema de un selector sin forma (R3-E)."""
_RAIZ_VACIA = analizar("")
PLATAFORMA_DE_TIPO: dict[str, Plataforma] = {
    TIPO_API_WOOCOMMERCE: Plataforma.WOOCOMMERCE,
    TIPO_API_SHOPIFY: Plataforma.SHOPIFY,
}
TIPO_DE_PLATAFORMA = {plataforma: tipo for tipo, plataforma in PLATAFORMA_DE_TIPO.items()}
NOMBRE_PLATAFORMA = {Plataforma.WOOCOMMERCE: "WooCommerce", Plataforma.SHOPIFY: "Shopify"}
# Caracteres que no pueden ir en una URL de listado o de sitemap: cada analizador de URL los
# entiende a su manera (`https://tienda.es\@otra.es` es otra.es para Chromium).
_PROHIBIDOS = frozenset(" \t\r\n\"'<>\\`|^")


async def validar(
    perfil: Perfil,
    navegador: Navegador,
    consulta_prueba: str,
    *,
    validador: Validador = validar_url,
    fichas: Sequence[str] = (),
) -> InformeValidacion:
    """Prueba `perfil` en su tienda buscando `consulta_prueba`. Ver el docstring del módulo.

    `fichas` son las URL del catálogo que se abren con un perfil `sitemap` (los demás tipos no
    lo usan): datos de la web, así que solo se abren las de la tienda, como mucho `MAX_FICHAS`.
    """
    if perfil.tipo == TIPO_SITEMAP:
        return await _validar_sitemap(perfil, navegador, fichas, validador)
    if perfil.tipo not in (TIPO_HTML, TIPO_LISTADO):
        return await _validar_api(perfil, navegador, consulta_prueba, validador)
    listado = perfil.tipo == TIPO_LISTADO
    problemas = _Problemas()
    problemas.extend(problemas_de_sintaxis(perfil))
    if listado:
        problemas.extend(problemas_de_url_listado(perfil.url_busqueda, perfil.dominio))
    else:
        problemas.extend(problemas_de_url(perfil))
    if problemas:
        return problemas.informe(valido=False)

    if listado:
        destino = url_de_listado(perfil.url_busqueda, 1)
        que = "El listado (página 1)"
    else:
        destino = url_de_busqueda(perfil.url_busqueda, consulta_prueba)
        que = f"La búsqueda de «{consulta_prueba}»"
    try:
        url = await validador(destino)
    except URLNoPermitida as error:
        problemas.add(f"url_busqueda no está permitida: {error}")
        return problemas.informe(valido=False)
    pagina = await navegador.obtener(url, js=perfil.necesita_js)
    if not _correcta(pagina):
        problemas.add(f"{que} responde con el estado {pagina.estado}.")
        return problemas.informe(valido=False)
    if not de_la_tienda(pagina, perfil.dominio):
        problemas.add(f"{que} redirige a otro dominio ({pagina.url_final}).")
        return problemas.informe(valido=False)

    resultados = extraer_listado(pagina, perfil)
    if not resultados:
        problemas.extend(problemas_sin_resultados(pagina, perfil, consulta_prueba))
        return problemas.informe(valido=False)
    if listado and len(resultados) < MIN_RESULTADOS_LISTADO:
        # Una página con uno o dos enlaces de producto (destacados, un menú) no es el listado.
        problemas.add(
            f"El listado solo da {len(resultados)} resultados de la tienda (hacen falta al "
            f"menos {MIN_RESULTADOS_LISTADO})."
        )
        return problemas.informe(valido=False, resultados=len(resultados))
    if perfil.sel_precio_listado and all(r.precio is None for r in resultados):
        # El perfil puede valer sin precios en el listado, pero el selector no lee ninguno.
        problemas.extend(selectores_ausentes(analizar(pagina.html), perfil))

    return await _comprobar_fichas(
        perfil,
        navegador,
        validador,
        [(r.url, r.precio) for r in resultados[:MAX_FICHAS]],
        problemas,
        resultados=len(resultados),
    )


async def _validar_sitemap(
    perfil: Perfil, navegador: Navegador, fichas: Sequence[str], validador: Validador
) -> InformeValidacion:
    """Perfil `sitemap`: sin listado; se abren hasta `MAX_FICHAS` fichas de la tienda."""
    problemas = _Problemas()
    problemas.extend(problemas_de_url_sitemap(perfil.url_busqueda, perfil.dominio))
    for nombre in ("sel_resultado", "sel_titulo", "sel_enlace"):
        if getattr(perfil, nombre) != "":
            problemas.add(f"Un perfil sitemap no lee ningún listado: {nombre} tiene que ir vacío.")
    if perfil.sel_precio_listado is not None:
        problemas.add("Un perfil sitemap no lee ningún listado: sel_precio_listado va a null.")
    problemas.extend(problemas_de_campos(perfil))
    if problemas:
        return problemas.informe(valido=False)
    dominio = dominio_normalizado(perfil.dominio)
    propias: dict[str, None] = {}
    for ficha in islice(fichas, MAX_FICHAS_REVISADAS):
        if dominio is not None and isinstance(ficha, str):
            url = url_de_la_tienda(ficha, ficha, dominio)
            if url is not None:
                propias[url] = None
    if not propias:
        problemas.add("No hay fichas del catálogo de la tienda con las que probar el perfil.")
        return problemas.informe(valido=False)
    return await _comprobar_fichas(
        perfil,
        navegador,
        validador,
        [(url, None) for url in list(propias)[:MAX_FICHAS]],
        problemas,
        resultados=len(propias),
    )


async def _comprobar_fichas(
    perfil: Perfil,
    navegador: Navegador,
    validador: Validador,
    fichas: Sequence[tuple[str, Precio | None]],
    problemas: "_Problemas",
    *,
    resultados: int,
) -> InformeValidacion:
    """Abre las `fichas` (URL y precio del listado, si lo hay) y cuenta las de precio anclado."""
    con_precio = 0
    cuenta: dict[str, int] = {}
    # R2-CAMPOS: campo descartado por repetido → (campo que se quedó el valor, valor), de la
    # primera ficha; en cuántas fichas se descartó; y los que el perfil leyó con valor propio.
    repetidos: dict[str, tuple[str, str]] = {}
    veces: dict[str, int] = {}
    propios: set[str] = set()
    # R3-E: campo descartado por no tener la forma de su campo → valor leído en la primera ficha,
    # y en cuántas fichas se descartó.
    sin_forma: dict[str, str] = {}
    veces_sin_forma: dict[str, int] = {}
    # Los avisos de la extracción van al final: si hay muchos, no deben tapar los problemas.
    avisos: list[str] = []
    for numero, (url_resultado, precio_listado) in enumerate(fichas, 1):
        try:
            url_ficha = await validador(url_resultado)
            pagina_ficha = await navegador.obtener(url_ficha, js=perfil.necesita_js)
        except (ErrorDescarga, URLNoPermitida) as error:
            problemas.add(f"Ficha {numero} ({url_resultado}): no se pudo abrir: {error}")
            continue
        if not _correcta(pagina_ficha):
            problemas.add(
                f"Ficha {numero} ({url_resultado}): responde con el estado {pagina_ficha.estado}."
            )
            continue
        if not de_la_tienda(pagina_ficha, perfil.dominio):
            problemas.add(
                f"Ficha {numero} ({url_resultado}): redirige a otro dominio "
                f"({pagina_ficha.url_final})."
            )
            continue
        ficha = await extraer_ficha(pagina_ficha, CLAVES, perfil=perfil)
        if ficha.listado:
            # R2-C10: una landing de marca o SEO no es una ficha, aunque traiga precios.
            problemas.add(
                f"Ficha {numero} ({url_resultado}): es un listado (ItemList con "
                f"{ficha.listado} productos), no una ficha."
            )
            continue
        for campo, leido in ficha.campos.items():
            cuenta[campo] = cuenta.get(campo, 0) + 1
            if leido.origen is OrigenDato.PERFIL:
                propios.add(campo)
        for campo, ganador in ficha.repetidos.items():
            veces[campo] = veces.get(campo, 0) + 1
            if ganador in ficha.campos:
                repetidos.setdefault(campo, (ganador, ficha.campos[ganador].valor))
        for campo, leido_sin_forma in ficha.sin_forma.items():
            veces_sin_forma[campo] = veces_sin_forma.get(campo, 0) + 1
            sin_forma.setdefault(campo, leido_sin_forma)
        if ficha.precio is None:
            problemas.add(f"Ficha {numero} ({url_resultado}): no se encuentra un precio anclado.")
        elif precio_listado is not None and not _mismo_precio(ficha.precio, precio_listado):
            problemas.add(
                f"Ficha {numero} ({url_resultado}): el precio de la ficha "
                f"({formatear_precio(ficha.precio)}) no coincide con el del listado "
                f"({formatear_precio(precio_listado)}); el selector puede estar leyendo el "
                "precio de otro producto o de otra parte de la página."
            )
        elif (otro := _otro_precio_del_selector(pagina_ficha, perfil, ficha)) is not None:
            problemas.add(
                f"Ficha {numero} ({url_resultado}): sel_campos[precio] lee "
                f"{formatear_precio(otro)}, pero el precio del producto (JSON-LD) es "
                f"{formatear_precio(ficha.precio)}."
            )
        else:
            con_precio += 1
        avisos.extend(f"Ficha {numero}: {aviso}" for aviso in ficha.avisos)

    quitados = tuple(
        c for c in CLAVES if c in perfil.sel_campos and c in repetidos and c not in propios
    )
    for clave in quitados:
        ganador, texto = repetidos[clave]
        donde = "en la ficha" if veces[clave] == 1 else f"en las {veces[clave]} fichas"
        problemas.add(
            f"sel_campos[{clave}] lee lo mismo que sel_campos[{ganador}] {donde} "
            f"(«{_recortar_cita(texto)}»): se quita."
        )
    quitados_sin_forma = tuple(
        c
        for c in CLAVES
        if c in perfil.sel_campos and c in sin_forma and c not in propios and c not in quitados
    )
    for clave in quitados_sin_forma:
        veces_clave = veces_sin_forma[clave]
        donde = "en la ficha" if veces_clave == 1 else f"en las {veces_clave} fichas"
        problemas.add(
            f"sel_campos[{clave}] lee «{_recortar_cita(sin_forma[clave])}» {donde}, que no tiene "
            f"forma de {_FORMA.get(clave, clave)}: se quita."
        )
    for clave in perfil.sel_campos:
        if not cuenta.get(clave) and clave not in quitados and clave not in quitados_sin_forma:
            problemas.add(
                f"sel_campos[{clave}] no encuentra nada en ninguna de las {len(fichas)} fichas."
            )
    requeridas = min(len(fichas), MIN_FICHAS_OK)
    valido = con_precio >= requeridas
    if not valido:
        problemas.add(
            f"Solo {con_precio} de {len(fichas)} fichas tienen precio anclado "
            f"(hacen falta {requeridas})."
        )
    problemas.extend(avisos)
    return problemas.informe(
        valido=valido,
        resultados=resultados,
        fichas_ok=con_precio,
        fichas_total=len(fichas),
        campos={c: cuenta[c] for c in CLAVES if cuenta.get(c)},
        repetidos=quitados,
        sin_forma=quitados_sin_forma,
    )


def sin_campos_repetidos(perfil: Perfil, informe: InformeValidacion) -> Perfil:
    """`perfil` sin las claves de `sel_campos` que el informe da por repetidas (R2-CAMPOS) o sin
    la forma de su campo (R3-E).

    Vale más que falte un campo (puede salir del JSON-LD, de los metadatos o de la IA) que
    guardar un selector que lee el valor de otro o algo que no es ese dato.
    """
    quitar = (*informe.campos_repetidos, *informe.campos_sin_forma)
    if not quitar:
        return perfil
    sel_campos = {c: s for c, s in perfil.sel_campos.items() if c not in quitar}
    return replace(perfil, sel_campos=sel_campos)


async def _validar_api(
    perfil: Perfil, navegador: Navegador, consulta: str, validador: Validador
) -> InformeValidacion:
    problemas = _Problemas()
    plataforma = PLATAFORMA_DE_TIPO.get(perfil.tipo) if isinstance(perfil.tipo, str) else None
    if plataforma is None:
        problemas.add(f"El tipo de perfil {_cita(str(perfil.tipo))} no es válido.")
        return problemas.informe(valido=False)
    problemas.extend(problemas_de_url(perfil))
    if problemas:
        return problemas.informe(valido=False)
    try:
        url = await validador(url_de_busqueda(perfil.url_busqueda, consulta))
    except URLNoPermitida as error:
        problemas.add(f"url_busqueda no está permitida: {error}")
        return problemas.informe(valido=False)
    pagina = await navegador.obtener(url, js=False)
    nombre = NOMBRE_PLATAFORMA[plataforma]
    if not _correcta(pagina):
        problemas.add(f"La API de {nombre} responde con el estado {pagina.estado}.")
        return problemas.informe(valido=False)
    if not de_la_tienda(pagina, perfil.dominio):
        problemas.add(f"La API de {nombre} redirige a otro dominio ({pagina.url_final}).")
        return problemas.informe(valido=False)
    moneda = await moneda_de_la_api(plataforma, pagina, navegador, validador)
    if moneda is None:
        problemas.add(
            f"No se sabe la moneda de la tienda {nombre}: su portada no declara Shopify.currency."
        )
        return problemas.informe(valido=False)
    fichas = ofertas_api(plataforma, pagina, perfil.dominio, CLAVES, moneda=moneda)
    con_precio = sum(f.precio is not None for f in fichas)
    if not fichas:
        problemas.add(f"La API de {nombre} no devuelve productos de la tienda para «{consulta}».")
    elif not con_precio:
        problemas.add(f"La API de {nombre} devuelve productos para «{consulta}», pero sin precio.")
    cuenta: dict[str, int] = {}
    for ficha in fichas:
        for campo in ficha.campos:
            cuenta[campo] = cuenta.get(campo, 0) + 1
    return problemas.informe(
        valido=con_precio > 0,
        resultados=len(fichas),
        fichas_ok=con_precio,
        fichas_total=len(fichas),
        campos={c: cuenta[c] for c in CLAVES if cuenta.get(c)},
    )


async def moneda_de_la_api(
    plataforma: Plataforma, pagina: Pagina, navegador: Navegador, validador: Validador
) -> str | None:
    """Moneda de los precios de la API, o `None` si no se sabe.

    WooCommerce trae la suya en cada producto (lo que se devuelve aquí no se usa). Shopify no:
    sale de `Shopify.currency` en la portada de la tienda que sirvió la respuesta. Si la
    portada no se puede abrir o no la declara, `None`. Los bloqueos se propagan.
    """
    if plataforma is not Plataforma.SHOPIFY:
        return "EUR"
    origen = origen_de(pagina.url_final or pagina.url)
    if origen is None:
        return None
    try:
        portada = await navegador.obtener(await validador(origen + "/"), js=False)
    except (ErrorDescarga, URLNoPermitida):
        return None
    if not _correcta(portada):
        return None
    return moneda_shopify(portada)


async def ofertas_de_api(
    perfil: Perfil,
    pagina: Pagina,
    navegador: Navegador,
    campos: Sequence[str],
    *,
    validador: Validador = validar_url,
    maximo: int = MAX_FICHAS_API,
) -> list[FichaExtraida]:
    """Fichas de la respuesta de la API de un perfil `api_*` (lista vacía si no es `api_*`)."""
    plataforma = PLATAFORMA_DE_TIPO.get(perfil.tipo)
    if plataforma is None:
        return []
    moneda = await moneda_de_la_api(plataforma, pagina, navegador, validador)
    if moneda is None:
        return []
    return ofertas_api(plataforma, pagina, perfil.dominio, campos, moneda=moneda, maximo=maximo)


def url_de_busqueda(plantilla: str, consulta: str) -> str:
    """`plantilla` con la consulta codificada en lugar de `{consulta}`.

    En la parte de consulta de la URL, como lo haría un formulario (`faro+golf`); en la ruta,
    escapándolo todo (`faro%20golf`, `1%2F2`), para que la consulta no pueda cambiar de ruta.
    """
    posicion = plantilla.find(MARCADOR)
    interrogacion = plantilla.find("?")
    en_consulta = 0 <= interrogacion < posicion
    codificada = quote_plus(consulta) if en_consulta else quote(consulta, safe="")
    return plantilla.replace(MARCADOR, codificada)


def problema_selector(nombre: str, selector: object, *, con_atributo: bool = True) -> str | None:
    """Por qué `selector` no se admite, o `None` si es válido.

    Sin `con_atributo` no se acepta el sufijo ` @atributo` (en `sel_resultado`, que selecciona
    elementos, no lee un valor).
    """
    if not isinstance(selector, str):
        return f"{nombre} tiene que ser texto."
    try:
        if con_atributo:
            extraer_valor(_RAIZ_VACIA, selector)
        else:
            seleccionar(_RAIZ_VACIA, selector)
    except SelectorNoValido as error:
        return f"{nombre} ({_cita(selector)}) no es un selector admitido: {error}"
    return None


def problemas_de_sintaxis(perfil: Perfil) -> list[str]:
    """Selectores fuera del subconjunto admitido y claves de `sel_campos` desconocidas."""
    problemas: list[str] = []
    for nombre in SELECTORES_LISTADO:
        selector = getattr(perfil, nombre)
        if selector is None and nombre == "sel_precio_listado":
            continue
        problema = problema_selector(nombre, selector, con_atributo=nombre != "sel_resultado")
        if problema:
            problemas.append(problema)
    return problemas + problemas_de_campos(perfil)


def problemas_de_campos(perfil: Perfil) -> list[str]:
    """Claves de `sel_campos` desconocidas y selectores de campo fuera del subconjunto."""
    if not isinstance(perfil.sel_campos, Mapping):
        return ["sel_campos tiene que ser un objeto campo → selector."]
    problemas: list[str] = []
    for clave, selector in perfil.sel_campos.items():
        if clave not in CLAVES_CAMPOS:
            problemas.append(
                f"sel_campos tiene un campo desconocido ({_cita(str(clave))}); solo se admiten: "
                f"{', '.join(CLAVES)}."
            )
        elif problema := problema_selector(f"sel_campos[{clave}]", selector):
            problemas.append(problema)
    return problemas


def problemas_de_url(perfil: Perfil) -> list[str]:
    """La plantilla de búsqueda tiene que llevar `{consulta}` y ser del dominio de la tienda."""
    plantilla = perfil.url_busqueda
    if not isinstance(plantilla, str) or MARCADOR not in plantilla:
        return ["url_busqueda tiene que contener {consulta}."]
    if 0 <= plantilla.find("#") < plantilla.find(MARCADOR):
        # El fragmento no se envía: la tienda recibiría siempre la misma búsqueda.
        return ["url_busqueda lleva {consulta} en el fragmento (#), que no llega a la tienda."]
    if not consulta_fuera_del_host(plantilla):
        return ["url_busqueda lleva {consulta} en el host: cada búsqueda iría a otra web."]
    dominio = dominio_normalizado(perfil.dominio)
    ejemplo = url_de_busqueda(plantilla, "prueba")
    if dominio is None or url_de_la_tienda(ejemplo, ejemplo, dominio) is None:
        return [
            f"url_busqueda no es una URL http(s) del dominio {perfil.dominio} "
            "(en el puerto por defecto y sin usuario)."
        ]
    return []


def url_de_listado(plantilla: str, pagina: int) -> str:
    """La URL de la página `pagina` de un listado: `plantilla` con el número en `{pagina}`.

    Sin `{pagina}` el listado tiene una sola página y se devuelve `plantilla` tal cual.
    """
    return plantilla.replace(MARCADOR_PAGINA, str(pagina), 1)


def problemas_de_url_listado(plantilla: object, dominio: str) -> list[str]:
    """Por qué `plantilla` no vale como URL de un perfil `listado` (lista vacía si vale).

    Las mismas reglas que la URL de entrada del usuario (nota 16 §A): `http(s)` del dominio de
    la tienda (con o sin `www.`, sin usuario ni puerto raro), sin fragmento, ASCII imprimible sin
    espacios, comillas, `<`, `>` ni barra invertida, como mucho `MAX_URL_ENTRADA` caracteres,
    sin `{consulta}`, con `{pagina}` como mucho una vez (fuera del esquema y del host) y sin
    ninguna otra llave.
    """
    if not isinstance(plantilla, str):
        return ["url_busqueda tiene que ser texto."]
    if len(plantilla) > MAX_URL_ENTRADA:
        return [f"La URL del listado es demasiado larga (más de {MAX_URL_ENTRADA} caracteres)."]
    if problema := _caracteres_no_admitidos("La URL del listado", plantilla):
        return [problema]
    if MARCADOR in plantilla:
        return ["La URL de un listado no lleva {consulta}: eso es una plantilla de búsqueda."]
    if plantilla.count(MARCADOR_PAGINA) > 1:
        return ["{pagina} solo puede ir una vez en la URL del listado."]
    resto = plantilla.replace(MARCADOR_PAGINA, "", 1)
    if "{" in resto or "}" in resto:
        return ["La URL del listado lleva una llave que no es {pagina}."]
    if "#" in plantilla:
        return ["La URL del listado lleva un fragmento (#), que no llega a la tienda."]
    try:
        partes = urlsplit(plantilla)
    except ValueError:
        partes = None
    if partes is not None and (
        MARCADOR_PAGINA in partes.scheme or MARCADOR_PAGINA in partes.netloc
    ):
        return ["{pagina} no puede ir en el esquema ni en el host: cada página sería otra web."]
    normalizado = dominio_normalizado(dominio)
    ejemplo = url_de_listado(plantilla, 1)
    if partes is None or normalizado is None or not _de_la_tienda_url(ejemplo, normalizado):
        return [
            f"La URL del listado no es una URL http(s) del dominio {dominio} (en el puerto por "
            "defecto y sin usuario)."
        ]
    return []


def problemas_de_url_sitemap(url: object, dominio: str) -> list[str]:
    """Por qué `url` no vale como sitemap raíz de un perfil `sitemap` (lista vacía si vale).

    El host es el de la tienda o un subdominio estricto suyo, con la misma regla con la que se
    descarga (`fuentes.sitemap.url_de_sitemap`, causa R5-B: Xtralife lo sirve en
    `static.xtralife.com`). Las fichas que lleva dentro siguen exigiendo el host de la tienda.
    """
    if not isinstance(url, str):
        return ["url_busqueda tiene que ser texto."]
    if "{" in url or "}" in url:
        return ["La URL del sitemap no es una plantilla: no lleva llaves ({consulta}, {pagina})."]
    if len(url) > MAX_URL or _caracteres_no_admitidos("La URL del sitemap", url):
        return ["La URL del sitemap es demasiado larga o lleva caracteres no admitidos."]
    normalizado = dominio_normalizado(dominio)
    if "#" in url or normalizado is None or url_de_sitemap(url, f"https://{normalizado}/") is None:
        return [f"La URL del sitemap no es una URL http(s) del dominio {dominio}."]
    return []


def _caracteres_no_admitidos(que: str, url: str) -> str | None:
    if url.isascii() and url.isprintable() and not any(c in _PROHIBIDOS for c in url):
        return None
    return (
        f"{que} lleva caracteres no admitidos (espacios, comillas, <, >, barra invertida o "
        "caracteres que no son ASCII)."
    )


def _de_la_tienda_url(url: str, dominio: str) -> bool:
    """URL absoluta `http(s)` de la tienda (una relativa no tiene host que comprobar)."""
    return url_de_la_tienda(url, url, dominio) is not None


def consulta_fuera_del_host(plantilla: str) -> bool:
    """`{consulta}` no está en el esquema ni en el host (ni en el usuario o el puerto).

    La comprobación de dominio se hace con una consulta de ejemplo («prueba»): en la tienda
    `recambios-prueba.es`, la plantilla `https://recambios-{consulta}.es/` la pasaría y luego
    cada búsqueda iría a una web distinta (`recambios-x.es`). La plantilla tiene que ser
    absoluta: una relativa no tiene host que comprobar.
    """
    try:
        partes = urlsplit(plantilla)
    except ValueError:
        return False
    return MARCADOR not in partes.scheme and MARCADOR not in partes.netloc


def resumir(informe: InformeValidacion) -> str:
    """Una o dos frases para la nota del perfil y para la persona que lo revisa."""
    estado = "Válido" if informe.valido else "No válido"
    partes = [
        f"{estado}: {informe.resultados} resultados, {informe.fichas_ok}/{informe.fichas_total} "
        "fichas con precio anclado."
    ]
    if informe.campos_encontrados:
        campos = ", ".join(
            f"{c} {n}/{informe.fichas_total}" for c, n in informe.campos_encontrados.items()
        )
        partes.append(f"Campos: {campos}.")
    if informe.problemas:
        partes.append("Problemas: " + " ".join(informe.problemas))
    texto = " ".join(partes)
    return texto if len(texto) <= MAX_RESUMEN else texto[: MAX_RESUMEN - 1] + "…"


class _Problemas:
    """Problemas sin repetidos, cada uno recortado y con un máximo en total."""

    def __init__(self) -> None:
        self._lista: list[str] = []

    def __bool__(self) -> bool:
        return bool(self._lista)

    def add(self, problema: str) -> None:
        if len(problema) > MAX_PROBLEMA:
            problema = problema[: MAX_PROBLEMA - 1] + "…"
        if problema not in self._lista and len(self._lista) < MAX_PROBLEMAS:
            self._lista.append(problema)

    def extend(self, problemas: Iterable[str]) -> None:
        for problema in problemas:
            self.add(problema)

    def informe(
        self,
        *,
        valido: bool,
        resultados: int = 0,
        fichas_ok: int = 0,
        fichas_total: int = 0,
        campos: dict[str, int] | None = None,
        repetidos: tuple[str, ...] = (),
        sin_forma: tuple[str, ...] = (),
    ) -> InformeValidacion:
        return InformeValidacion(
            valido=valido,
            resultados=resultados,
            fichas_ok=fichas_ok,
            fichas_total=fichas_total,
            campos_encontrados=campos or {},
            problemas=tuple(self._lista),
            campos_repetidos=repetidos,
            campos_sin_forma=sin_forma,
        )


def _correcta(pagina: Pagina) -> bool:
    return 200 <= pagina.estado < 300


def de_la_tienda(pagina: Pagina, dominio: str) -> bool:
    """La página sigue siendo de `dominio` después de las redirecciones."""
    normalizado = dominio_normalizado(dominio)
    final = pagina.url_final or pagina.url
    return normalizado is not None and url_de_la_tienda(final, final, normalizado) is not None


def _mismo_precio(a: Precio, b: Precio) -> bool:
    return a.importe == b.importe and a.moneda == b.moneda


def _otro_precio_del_selector(
    pagina: Pagina, perfil: Perfil, ficha: FichaExtraida
) -> Precio | None:
    """El precio que lee `sel_campos[precio]` si es distinto del de la ficha sacado del JSON-LD.

    Solo cuenta un texto que se lee claramente como precio: si el selector no encuentra nada o
    lee un formato de máquina («89.90» en un atributo), no hay nada que comparar.
    """
    valor_ficha = ficha.campos.get("precio")
    selector = perfil.sel_campos.get("precio")
    if ficha.precio is None or selector is None or valor_ficha is None:
        return None
    if valor_ficha.origen is not OrigenDato.JSONLD:
        return None
    try:
        texto = texto_de_precio(analizar(pagina.html), selector)
    except SelectorNoValido:
        return None
    leido = leer_precio(texto) if texto else None
    return leido if leido is not None and not _mismo_precio(leido, ficha.precio) else None


def problemas_sin_resultados(pagina: Pagina, perfil: Perfil, consulta: str) -> list[str]:
    """`problema_sin_resultados` y, detrás, `selectores_ausentes` (para la IA en su reintento)."""
    raiz = analizar(pagina.html)
    return [_sin_resultados(raiz, perfil, consulta), *selectores_ausentes(raiz, perfil)]


def problema_sin_resultados(pagina: Pagina, perfil: Perfil, consulta: str) -> str:
    """Por qué el listado sale vacío: `sel_resultado` no encaja o lo que encaja no sirve."""
    return _sin_resultados(analizar(pagina.html), perfil, consulta)


def selectores_ausentes(raiz: Nodo, perfil: Perfil) -> list[str]:
    """Un problema por cada selector del listado que no encuentra nada en la página `raiz`.

    Distingue si el selector no aparece en ninguna parte de la página, si aparece pero no dentro
    de los resultados o si sus elementos no tienen el atributo que se lee. Lo primero es lo que
    pasa cuando la IA, sin ver los productos, se inventa las clases típicas de la plataforma
    (`h2.woocommerce-loop-product__title` en una tienda WooCommerce cuya plantilla no las usa):
    decírselo por selector la empuja a usar clases que sí están en el HTML.
    """
    problemas: list[str] = []
    resultados = seleccionar(raiz, perfil.sel_resultado)
    if not resultados:
        problemas.append(_no_aparece("sel_resultado", perfil.sel_resultado))
    for nombre in ("sel_titulo", "sel_enlace", "sel_precio_listado"):
        selector = getattr(perfil, nombre)
        if selector and (problema := _ausente(raiz, resultados, nombre, selector)):
            problemas.append(problema)
    return problemas


def _ausente(raiz: Nodo, resultados: Sequence[Nodo], nombre: str, selector: str) -> str | None:
    if nombre != "sel_enlace":
        return _ausente_en(raiz, resultados, nombre, selector)
    # La misma regla que `extraer_listado`: el enlace puede ser el resultado o envolverlo (R2-C13).
    ids = {id(r) for r in resultados}
    revisados = resultados[:MAX_RESULTADOS_REVISADOS]
    if any(enlace_del_resultado(r, selector, ids) for r in revisados):
        return None
    problema = _ausente_en(raiz, resultados, nombre, selector)
    pista = _pista_enlace(revisados, ids)
    return f"{problema} {pista}" if problema and pista else problema


def _pista_enlace(revisados: Sequence[Nodo], ids: set[int]) -> str | None:
    """Si los resultados son enlaces `<a href>` o están dentro de uno, cómo leerlo."""
    envolturas = [envoltura(r, "a[href]", ids) for r in revisados]
    if any(e is not None and e is r for e, r in zip(envolturas, revisados, strict=True)):
        return "Cada resultado ya es un enlace <a href>: usa «@href» a secas como sel_enlace."
    if any(e is not None for e in envolturas):
        return (
            "Cada resultado está dentro de un enlace <a href>: usa ese <a> como sel_resultado y "
            "«@href» a secas como sel_enlace."
        )
    return None


def _ausente_en(raiz: Nodo, resultados: Sequence[Nodo], nombre: str, selector: str) -> str | None:
    css, atributo = dividir_selector(selector)
    revisados = resultados[:MAX_RESULTADOS_REVISADOS]
    elementos = seleccionar(raiz, css) if css else list(revisados)
    if css and not elementos:
        return _no_aparece(nombre, selector)
    # Sin elementos (`@atributo` del propio resultado y ningún resultado) no hay nada que decir:
    # el problema ya es sel_resultado.
    if elementos and atributo is not None and not any(e.atributo(atributo) for e in elementos):
        return (
            f"{nombre} ({_cita(selector)}): hay elementos, pero ninguno tiene el atributo "
            f"{atributo}."
        )
    if css and revisados and not any(seleccionar(r, css) for r in revisados):
        return (
            f"{nombre} ({_cita(selector)}) aparece en la página, pero no dentro de ningún "
            "elemento de sel_resultado: tiene que ser relativo a cada resultado."
        )
    return None


def _no_aparece(nombre: str, selector: str) -> str:
    return (
        f"{nombre} ({_cita(selector)}) no aparece en ninguna parte de la página: usa clases e id "
        "que estén en el HTML."
    )


def _sin_resultados(raiz: Nodo, perfil: Perfil, consulta: str) -> str:
    elementos = len(seleccionar(raiz, perfil.sel_resultado))
    if not elementos:
        return f"sel_resultado no encuentra ningún elemento en la búsqueda de «{consulta}»."
    plural = "elemento" if elementos == 1 else "elementos"
    return (
        f"sel_resultado encuentra {elementos} {plural} en la búsqueda de «{consulta}», pero "
        f"ninguno tiene título (sel_titulo) y enlace a {perfil.dominio} (sel_enlace)."
    )


def _cita(texto: str) -> str:
    return repr(_recortar_cita(texto))


def _recortar_cita(texto: str) -> str:
    return texto if len(texto) <= MAX_CITA else texto[: MAX_CITA - 1] + "…"
