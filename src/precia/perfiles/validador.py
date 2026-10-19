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
selector de campo lee el dato correcto: por eso el perfil de la IA nace sin revisar.

Los `problemas` del informe explican qué falla con detalle suficiente para que el
configurador se los devuelva a la IA en su reintento; pueden contener texto de la tienda, así
que solo viajan en los `datos` de una petición, nunca en las instrucciones.

Los bloqueos (`Bloqueado`) y las prohibiciones de robots.txt se propagan: no son un fallo del
perfil y quien llama tiene que pararse y registrarlos. También se propaga un error al descargar
la búsqueda (la tienda no responde); el de una ficha cuenta como ficha fallida.
"""

from collections.abc import Iterable, Mapping
from urllib.parse import quote, quote_plus

from precia.extraccion.dom import analizar
from precia.extraccion.ficha import extraer_ficha, formatear_precio
from precia.extraccion.listado import dominio_normalizado, extraer_listado, url_de_la_tienda
from precia.extraccion.precios import leer_precio
from precia.extraccion.selectores import extraer_valor, seleccionar
from precia.extraccion.tipos import FichaExtraida, Precio, SelectorNoValido
from precia.navegador.tipos import ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import Validador, validar_url
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS, OrigenDato
from precia.perfiles.protocolos import Navegador
from precia.perfiles.tipos import InformeValidacion, Perfil

MAX_FICHAS = 3
MIN_FICHAS_OK = 2
"""Fichas con precio anclado necesarias (o todas, si se abren menos)."""
MARCADOR = "{consulta}"
MAX_PROBLEMAS = 20
MAX_PROBLEMA = 300
MAX_RESUMEN = 2_000
"""Lo que admite el almacén en la nota de un perfil."""
MAX_CITA = 80
CLAVES = tuple(c.clave for c in CAMPOS)
SELECTORES_LISTADO = ("sel_resultado", "sel_titulo", "sel_enlace", "sel_precio_listado")
_RAIZ_VACIA = analizar("")


async def validar(
    perfil: Perfil,
    navegador: Navegador,
    consulta_prueba: str,
    *,
    validador: Validador = validar_url,
) -> InformeValidacion:
    """Prueba `perfil` en su tienda buscando `consulta_prueba`. Ver el docstring del módulo."""
    problemas = _Problemas()
    problemas.extend(problemas_de_sintaxis(perfil))
    problemas.extend(problemas_de_url(perfil))
    if problemas:
        return problemas.informe(valido=False)

    try:
        url = await validador(url_de_busqueda(perfil.url_busqueda, consulta_prueba))
    except URLNoPermitida as error:
        problemas.add(f"url_busqueda no está permitida: {error}")
        return problemas.informe(valido=False)
    pagina = await navegador.obtener(url, js=perfil.necesita_js)
    if not _correcta(pagina):
        problemas.add(f"La búsqueda de «{consulta_prueba}» responde con el estado {pagina.estado}.")
        return problemas.informe(valido=False)
    if not de_la_tienda(pagina, perfil.dominio):
        problemas.add(f"La búsqueda redirige a otro dominio ({pagina.url_final}).")
        return problemas.informe(valido=False)

    resultados = extraer_listado(pagina, perfil)
    if not resultados:
        problemas.add(problema_sin_resultados(pagina, perfil, consulta_prueba))
        return problemas.informe(valido=False)

    fichas = resultados[:MAX_FICHAS]
    con_precio = 0
    cuenta: dict[str, int] = {}
    # Los avisos de la extracción van al final: si hay muchos, no deben tapar los problemas.
    avisos: list[str] = []
    for numero, resultado in enumerate(fichas, 1):
        try:
            url_ficha = await validador(resultado.url)
            pagina_ficha = await navegador.obtener(url_ficha, js=perfil.necesita_js)
        except (ErrorDescarga, URLNoPermitida) as error:
            problemas.add(f"Ficha {numero} ({resultado.url}): no se pudo abrir: {error}")
            continue
        if not _correcta(pagina_ficha):
            problemas.add(
                f"Ficha {numero} ({resultado.url}): responde con el estado {pagina_ficha.estado}."
            )
            continue
        if not de_la_tienda(pagina_ficha, perfil.dominio):
            problemas.add(
                f"Ficha {numero} ({resultado.url}): redirige a otro dominio "
                f"({pagina_ficha.url_final})."
            )
            continue
        ficha = await extraer_ficha(pagina_ficha, CLAVES, perfil=perfil)
        for campo in ficha.campos:
            cuenta[campo] = cuenta.get(campo, 0) + 1
        if ficha.precio is None:
            problemas.add(f"Ficha {numero} ({resultado.url}): no se encuentra un precio anclado.")
        elif resultado.precio is not None and not _mismo_precio(ficha.precio, resultado.precio):
            problemas.add(
                f"Ficha {numero} ({resultado.url}): el precio de la ficha "
                f"({formatear_precio(ficha.precio)}) no coincide con el del listado "
                f"({formatear_precio(resultado.precio)}); el selector puede estar leyendo el "
                "precio de otro producto o de otra parte de la página."
            )
        elif (otro := _otro_precio_del_selector(pagina_ficha, perfil, ficha)) is not None:
            problemas.add(
                f"Ficha {numero} ({resultado.url}): sel_campos[precio] lee "
                f"{formatear_precio(otro)}, pero el precio del producto (JSON-LD) es "
                f"{formatear_precio(ficha.precio)}."
            )
        else:
            con_precio += 1
        avisos.extend(f"Ficha {numero}: {aviso}" for aviso in ficha.avisos)

    for clave in perfil.sel_campos:
        if not cuenta.get(clave):
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
        resultados=len(resultados),
        fichas_ok=con_precio,
        fichas_total=len(fichas),
        campos={c: cuenta[c] for c in CLAVES if cuenta.get(c)},
    )


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
    if not isinstance(perfil.sel_campos, Mapping):
        return [*problemas, "sel_campos tiene que ser un objeto campo → selector."]
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
    dominio = dominio_normalizado(perfil.dominio)
    ejemplo = url_de_busqueda(plantilla, "prueba")
    if dominio is None or url_de_la_tienda(ejemplo, ejemplo, dominio) is None:
        return [
            f"url_busqueda no es una URL http(s) del dominio {perfil.dominio} "
            "(en el puerto por defecto y sin usuario)."
        ]
    return []


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
    ) -> InformeValidacion:
        return InformeValidacion(
            valido=valido,
            resultados=resultados,
            fichas_ok=fichas_ok,
            fichas_total=fichas_total,
            campos_encontrados=campos or {},
            problemas=tuple(self._lista),
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
        texto = extraer_valor(analizar(pagina.html), selector)
    except SelectorNoValido:
        return None
    leido = leer_precio(texto) if texto else None
    return leido if leido is not None and not _mismo_precio(leido, ficha.precio) else None


def problema_sin_resultados(pagina: Pagina, perfil: Perfil, consulta: str) -> str:
    """Por qué el listado sale vacío: `sel_resultado` no encaja o lo que encaja no sirve."""
    elementos = len(seleccionar(analizar(pagina.html), perfil.sel_resultado))
    if not elementos:
        return f"sel_resultado no encuentra ningún elemento en la búsqueda de «{consulta}»."
    plural = "elemento" if elementos == 1 else "elementos"
    return (
        f"sel_resultado encuentra {elementos} {plural} en la búsqueda de «{consulta}», pero "
        f"ninguno tiene título (sel_titulo) y enlace a {perfil.dominio} (sel_enlace)."
    )


def _cita(texto: str) -> str:
    return repr(texto if len(texto) <= MAX_CITA else texto[: MAX_CITA - 1] + "…")
