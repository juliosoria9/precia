"""Extracción de los campos de una ficha de producto (memoria §4 y §5).

Cascada por campo: JSON-LD de la página → metadatos (microdatos y OpenGraph) → selector del
perfil → extractor de respaldo con IA (solo si lo hay y faltan campos). Todo valor se ancla
contra el texto visible de la página:

- Lo que sale del JSON-LD, de los metadatos o de un atributo HTML lo lee nuestro código del propio
  marcado de la página, así que está anclado por construcción. El precio del JSON-LD vale si el
  mismo importe está en la página o en el propio JSON, y el de los metadatos sale de un atributo
  o del texto de la página; si no se ve en el texto, se avisa. Los metadatos llevan algo menos de
  confianza que el JSON-LD: OpenGraph se escribe para las redes sociales y algún tema lo deja
  desactualizado (precio sin rebaja, imagen genérica).
- El texto que lee un selector del perfil debe aparecer en el texto visible (un elemento oculto
  no cuenta) y su precio, estar escrito como precio en la página. Si no, se descarta y el campo
  pasa a la vía siguiente.
- Un valor de la IA que no está en la página se conserva con `anclado=False` (la web lo muestra
  tachado), con confianza 0, y su precio nunca es el precio de la ficha.
"""

import re
import unicodedata
from collections.abc import Iterator, Sequence
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urldefrag, urljoin, urlsplit

from precia.extraccion.anclaje import anclado
from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.jsonld import bloques_jsonld, productos_de_arbol
from precia.extraccion.metadatos import producto_metadatos
from precia.extraccion.precios import MAX_IMPORTE, buscar_precios, leer_precio
from precia.extraccion.selectores import dividir_selector, extraer_valor, seleccionar
from precia.extraccion.tipos import (
    ExtractorIA,
    FichaExtraida,
    Precio,
    ProductoJSONLD,
    SelectorNoValido,
)
from precia.ia.tipos import ErrorIA
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import CLAVES_CAMPOS, OrigenDato, ValorCampo
from precia.perfiles.tipos import Perfil

CONFIANZA = {
    OrigenDato.JSONLD: 1.0,
    OrigenDato.METADATOS: 0.95,
    OrigenDato.PERFIL: 0.9,
    OrigenDato.IA: 0.7,
}
CONFIANZA_NO_ANCLADO = 0.0
MAX_TEXTO_IA = 60_000
"""Texto de la página que se pasa al extractor de IA (el paquete `ia` recorta aún más)."""
MAX_VALOR = 5_000
MAX_TITULO = 300
"""El título es texto de la web: lo usan el emparejado y la web, así que no crece sin límite."""
MAX_TITULO_BRUTO = 2_000
"""Lo que se mira de cada candidato a título antes de limpiarlo y recortarlo a `MAX_TITULO`."""
MAX_H1 = 20
"""`<h1>` que se miran como candidatos a título; una ficha real tiene uno o dos."""
MIN_NOMBRE_SITIO = 3
"""Un nombre de sitio de una o dos letras («tv», «es») coincidiría con cualquier cosa."""
MAX_PALABRA = 100
_ESPACIOS = re.compile(r"\s+")
_IMPORTE_MAQUINA = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_SEPARADOR_TITULO = re.compile(" (?:[|\\-\u2013\u2014\u00b7\u00bb]|::) ")
"""Barra, guion, semirraya, raya, punto medio, «»» o «::», con un espacio a cada lado.

El texto llega con los espacios ya normalizados (uno solo), así que basta un espacio literal y la
búsqueda es lineal."""
_CABECERAS = frozenset({"header", "footer"})
_SECCIONES = frozenset({"article", "main", "section", "aside"})


async def extraer_ficha(
    pagina: Pagina,
    campos: Sequence[str],
    *,
    perfil: Perfil | None = None,
    extractor_ia: ExtractorIA | None = None,
) -> FichaExtraida:
    """Campos pedidos de la ficha en `pagina`. Nunca lanza por datos de la web ni por la IA.

    El precio de la ficha se busca siempre (es el de la oferta), aunque no esté en `campos`;
    la IA solo se usa para los campos pedidos.
    """
    raiz = analizar(pagina.html)
    extraccion = _Extraccion(pagina, raiz, perfil)
    pedidos = extraccion.campos_validos(campos)

    for campo in dict.fromkeys(("precio", *pedidos)):
        if not (extraccion.desde_jsonld(campo) or extraccion.desde_metadatos(campo)):
            extraccion.desde_perfil(campo)

    faltan = tuple(c for c in pedidos if c not in extraccion.valores)
    if extractor_ia is not None and faltan:
        await extraccion.desde_ia(extractor_ia, faltan)

    return FichaExtraida(
        url=pagina.url_final or pagina.url,
        titulo=extraccion.titulo(),
        precio=extraccion.precio,
        campos={c: extraccion.valores[c] for c in pedidos if c in extraccion.valores},
        avisos=tuple(extraccion.avisos),
    )


class _Extraccion:
    """Estado de la cascada de una ficha: valores obtenidos, precio y avisos."""

    def __init__(self, pagina: Pagina, raiz: Nodo, perfil: Perfil | None) -> None:
        self.url = pagina.url_final or pagina.url
        self.raiz = raiz
        self.perfil = perfil
        self.texto = raiz.texto()
        self.bloques = bloques_jsonld(raiz)
        self.producto = _principal(productos_de_arbol(raiz), self.url)
        self.metadatos = producto_metadatos(raiz, url=self.url)
        self.valores: dict[str, ValorCampo] = {}
        self.precio: Precio | None = None
        self.avisos: list[str] = []
        self._importes: frozenset[Decimal] | None = None

    def precio_en_pagina(self, importe: Decimal) -> bool:
        """Como `anclaje.precio_anclado`, pero recorriendo el texto de la página una sola vez."""
        if self._importes is None:
            self._importes = frozenset(p.importe for p in buscar_precios(self.texto))
        return importe in self._importes

    def campos_validos(self, campos: Sequence[str]) -> tuple[str, ...]:
        validos: list[str] = []
        for campo in campos:
            if campo not in CLAVES_CAMPOS:
                self.avisos.append(f"Campo desconocido «{campo}»: se ignora.")
            elif campo not in validos:
                validos.append(campo)
        return tuple(validos)

    def titulo(self) -> str:
        """Nombre del producto: el primer candidato que no sea el nombre del sitio, limpio.

        Orden: el producto estructurado (JSON-LD y luego microdatos u OpenGraph de producto),
        que es el nombre que la tienda declara para *este* producto; el selector del perfil; y
        solo al final el `<h1>`, el `og:title` suelto y el `<title>`, que en muchos temas son
        los del sitio (webscraper.io pone «Test Sites» en el `<h1>` de todas sus fichas). De
        cada candidato se quita el nombre del sitio de los extremos («Faro | Tienda» → «Faro»)
        y uno que solo sea ese nombre se salta. Si no queda ninguno, `""`: mejor sin título que
        con el del sitio.
        """
        sitio = _nombres_del_sitio(self.raiz, self.url)
        for candidato in self._candidatos_titulo():
            if candidato and (limpio := _sin_nombre_del_sitio(candidato, sitio)):
                return limpio
        return ""

    def _candidatos_titulo(self) -> Iterator[str | None]:
        """Los candidatos a título, del más fiable al menos; se generan solo hasta el bueno."""
        yield self.producto.nombre if self.producto else None
        yield self.metadatos.nombre if self.metadatos else None
        if self.perfil is not None and "titulo" in self.perfil.sel_campos:
            try:
                yield extraer_valor(self.raiz, self.perfil.sel_campos["titulo"])
            except SelectorNoValido as error:
                self.avisos.append(f"Selector del perfil para «titulo» no válido: {error}")
        yield from _encabezados(self.raiz)
        yield _meta(self.raiz, "og:title")
        yield _primer_texto(self.raiz, "title")

    def anotar(self, campo: str, valor: str, origen: OrigenDato, es_anclado: bool) -> None:
        confianza = CONFIANZA[origen] if es_anclado else CONFIANZA_NO_ANCLADO
        self.valores[campo] = ValorCampo(valor, origen, es_anclado, confianza)

    # --- 1. JSON-LD -------------------------------------------------------------------------

    def desde_jsonld(self, campo: str) -> bool:
        return self._desde_estructurado(self.producto, campo, OrigenDato.JSONLD, "del JSON-LD")

    def _desde_estructurado(
        self, producto: ProductoJSONLD | None, campo: str, origen: OrigenDato, de: str
    ) -> bool:
        """Campo de un producto estructurado (JSON-LD o metadatos), anclado por construcción."""
        if producto is None:
            return False
        if campo == "precio":
            precio = producto.precio
            if precio is None:
                return False
            if origen is OrigenDato.JSONLD and not self._precio_jsonld_anclado(precio):
                return False
            if not self.precio_en_pagina(precio.importe):
                self.avisos.append(
                    f"El precio {de} ({formatear_precio(precio)}) no se ve en el texto de "
                    "la página."
                )
            self.precio = precio
            self.anotar(campo, formatear_precio(precio), origen, True)
            return True
        valor = _campo_jsonld(producto, campo)
        if valor is None:
            return False
        self.anotar(campo, valor, origen, True)
        return True

    def _precio_jsonld_anclado(self, precio: Precio) -> bool:
        return self.precio_en_pagina(precio.importe) or any(
            precio.texto in bloque for bloque in self.bloques
        )

    # --- 2. Metadatos (microdatos y OpenGraph) ----------------------------------------------

    def desde_metadatos(self, campo: str) -> bool:
        return self._desde_estructurado(
            self.metadatos, campo, OrigenDato.METADATOS, "de los metadatos"
        )

    # --- 3. Perfil --------------------------------------------------------------------------

    def desde_perfil(self, campo: str) -> bool:
        if self.perfil is None or campo not in self.perfil.sel_campos:
            return False
        selector = self.perfil.sel_campos[campo]
        try:
            if campo == "fotos":
                return self._fotos_perfil(selector)
            if campo == "descripcion":
                valor = self._descripcion_perfil(selector)
            else:
                valor = extraer_valor(self.raiz, selector)
        except SelectorNoValido as error:
            self.avisos.append(f"Selector del perfil para «{campo}» no válido: {error}")
            return False
        if valor is None:
            return False
        valor = _recortar(valor, MAX_VALOR)
        _, atributo = dividir_selector(selector)
        if campo == "precio":
            precio = leer_precio(valor)
            if precio is None and atributo is not None:
                precio = _precio_de_maquina(valor)
            if precio is None or not self.precio_en_pagina(precio.importe):
                self.avisos.append(
                    f"El precio que lee el perfil («{_recortar(valor, 60)}») no aparece como "
                    "precio en la página: se descarta."
                )
                return False
            self.precio = precio
            self.anotar(campo, formatear_precio(precio), OrigenDato.PERFIL, True)
            return True
        # Un atributo está en el marcado por construcción; un texto, solo si se ve.
        if atributo is None and not anclado(valor, self.texto):
            self.avisos.append(f"El valor del perfil para «{campo}» no está visible: se descarta.")
            return False
        self.anotar(campo, valor, OrigenDato.PERFIL, True)
        return True

    def _descripcion_perfil(self, selector: str) -> str | None:
        css, atributo = dividir_selector(selector)
        if atributo is not None:
            return extraer_valor(self.raiz, selector)
        partes = [t for nodo in seleccionar(self.raiz, css) if (t := nodo.texto())]
        return " ".join(partes) or None

    def _fotos_perfil(self, selector: str) -> bool:
        css, atributo = dividir_selector(selector)
        nodos = [self.raiz] if atributo is not None and not css else seleccionar(self.raiz, css)
        if atributo is not None:
            nodos = [n for n in nodos if (n.atributo(atributo) or "").strip()]
        if not nodos:
            return False
        self.anotar("fotos", _fotos(len(nodos)), OrigenDato.PERFIL, True)
        return True

    # --- 4. IA ------------------------------------------------------------------------------

    async def desde_ia(self, extractor: ExtractorIA, faltan: tuple[str, ...]) -> None:
        try:
            respuesta = await extractor.extraer(self.texto[:MAX_TEXTO_IA], faltan)
        except ErrorIA as error:
            self.avisos.append(f"Extractor de IA no disponible: {error}")
            return
        if not isinstance(respuesta, dict):
            self.avisos.append("El extractor de IA devolvió una respuesta no válida.")
            return
        for campo in faltan:
            bruto = respuesta.get(campo)
            if not isinstance(bruto, str):
                continue
            valor = _recortar(bruto, MAX_VALOR)
            if not valor:
                continue
            if campo == "precio":
                self._precio_ia(valor)
            else:
                es_anclado = anclado(valor, self.texto)
                if not es_anclado:
                    self.avisos.append(f"El valor de la IA para «{campo}» no aparece en la página.")
                self.anotar(campo, valor, OrigenDato.IA, es_anclado)

    def _precio_ia(self, valor: str) -> None:
        precio = leer_precio(valor)
        if precio is None:
            self.avisos.append("La IA devolvió un precio que no se puede leer.")
            return
        es_anclado = self.precio_en_pagina(precio.importe)
        texto = formatear_precio(precio)
        if es_anclado:
            self.precio = precio
        else:
            self.avisos.append(
                f"El precio de la IA ({texto}) no aparece en la página: se descarta."
            )
        self.anotar("precio", texto, OrigenDato.IA, es_anclado)


def formatear_precio(precio: Precio) -> str:
    """`Precio(Decimal("1234.5"))` → `"1.234,50 €"`; otras monedas con su código."""
    cuantizado = precio.importe.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    entero, _, decimales = f"{cuantizado:,.2f}".partition(".")
    cifra = f"{entero.replace(',', '.')},{decimales}"
    return f"{cifra} €" if precio.moneda == "EUR" else f"{cifra} {precio.moneda}"


def _principal(productos: list[ProductoJSONLD], url: str) -> ProductoJSONLD | None:
    """El producto de la ficha: el de la URL de la página o, si no, el primero con precio."""
    propia = urldefrag(url).url.rstrip("/")
    for producto in productos:
        try:
            suya = urldefrag(urljoin(url, producto.url)).url.rstrip("/") if producto.url else None
        except ValueError:
            continue
        if suya == propia:
            return producto
    con_precio = (p for p in productos if p.precio is not None)
    return next(con_precio, productos[0] if productos else None)


def _precio_de_maquina(valor: str) -> Precio | None:
    """`content="89.9"` (microdatos de PrestaShop y similares): punto decimal y sin moneda.

    `leer_precio` lo rechaza por prudencia (en un texto, «89.9» podría ser cualquier cosa), pero en
    un atributo de precio es el formato normal. El anclaje se sigue comprobando fuera.
    """
    if _IMPORTE_MAQUINA.fullmatch(valor) is None:
        return None
    importe = Decimal(valor)
    return Precio(importe, "EUR", valor) if 0 < importe < MAX_IMPORTE else None


def _campo_jsonld(producto: ProductoJSONLD, campo: str) -> str | None:
    match campo:
        case "referencia_oem":
            return producto.mpn
        case "marca":
            return producto.marca
        case "estado":
            return producto.estado
        case "descripcion":
            return producto.descripcion
        case "fotos":
            return _fotos(len(producto.imagenes)) if producto.imagenes else None
    return None


def _fotos(numero: int) -> str:
    return "1 foto" if numero == 1 else f"{numero} fotos"


def _primer_texto(raiz: Nodo, etiqueta: str) -> str | None:
    for nodo in raiz.descendientes():
        if nodo.etiqueta == etiqueta and (texto := nodo.texto()):
            return texto
    return None


def _meta(raiz: Nodo, propiedad: str, atributo: str = "property") -> str | None:
    for nodo in raiz.descendientes():
        if nodo.etiqueta == "meta" and nodo.atributo(atributo) == propiedad:
            contenido = (nodo.atributo("content") or "").strip()
            if contenido:
                return contenido
    return None


# --- Título -------------------------------------------------------------------------------------


def _encabezados(raiz: Nodo) -> Iterator[str]:
    """Textos de los `<h1>` que no están en la cabecera, el menú o el pie de la página.

    El `<h1>` de la cabecera suele ser el logotipo del sitio (`<header><h1><a href="/">…`). Se
    miran como mucho `MAX_H1` (cada uno sube por sus antepasados, hasta `dom.MAX_PROFUNDIDAD`).
    """
    vistos = 0
    for nodo in raiz.descendientes():
        if nodo.etiqueta != "h1":
            continue
        vistos += 1
        if vistos > MAX_H1:
            return
        if not _en_zona_del_sitio(nodo) and (texto := nodo.texto()):
            yield texto


def _en_zona_del_sitio(nodo: Nodo) -> bool:
    """Si `nodo` está en el menú, o en la cabecera o el pie *del sitio*.

    Como en HTML5, un `<header>` o `<footer>` dentro de `<article>`, `<main>`, `<section>` o
    `<aside>` es el de esa sección (muchos temas ponen ahí el nombre del producto), no el del sitio.
    """
    en_cabecera = False
    padre = nodo.padre
    while padre is not None:
        if padre.etiqueta == "nav":
            return True
        if padre.etiqueta in _CABECERAS:
            en_cabecera = True
        elif padre.etiqueta in _SECCIONES:
            return False
        padre = padre.padre
    return en_cabecera


def _nombres_del_sitio(raiz: Nodo, url: str) -> frozenset[str]:
    """Claves (ver `_clave`) del nombre del sitio: `og:site_name`, `application-name` y dominio.

    Del dominio valen el nombre entero sin `www.` («desguacespepe.es») y su etiqueta más larga
    sin el TLD («desguacespepe»), que es como suele llamarse la tienda en sus títulos.
    """
    nombres = [_meta(raiz, "og:site_name"), _meta(raiz, "application-name", "name")]
    try:
        host = (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        host = ""
    if host:
        etiquetas = host.split(".")
        nombres += [host, max(etiquetas[:-1] or etiquetas, key=len)]
    claves = (_clave(n[:MAX_TITULO_BRUTO]) for n in nombres if n)
    return frozenset(c for c in claves if len(c) >= MIN_NOMBRE_SITIO)


def _sin_nombre_del_sitio(texto: str, sitio: frozenset[str]) -> str:
    """`texto` sin el nombre del sitio al final ni al principio, recortado a `MAX_TITULO`.

    Solo se quita un extremo separado por `_SEPARADOR_TITULO` (« | », « - »…) que *es* el
    nombre del sitio: sin saberlo, en «Lámpara H7 - 55W» el «55W» es parte del nombre.
    Si el texto entero es el nombre del sitio, `""`.
    """
    limpio = _ESPACIOS.sub(" ", _sin_invisibles(texto[:MAX_TITULO_BRUTO])).strip()
    if not limpio or _clave(limpio) in sitio:
        return ""
    separadores = list(_SEPARADOR_TITULO.finditer(limpio))
    if separadores:
        ultimo = separadores[-1]
        if _clave(limpio[ultimo.end() :]) in sitio:
            limpio = limpio[: ultimo.start()]
            separadores.pop()
    if separadores:
        primero = separadores[0]
        if _clave(limpio[: primero.start()]) in sitio:
            limpio = limpio[primero.end() :]
    # «Tienda | Tienda»: lo que queda puede seguir siendo el nombre del sitio.
    return "" if _clave(limpio) in sitio else _recortar(limpio, MAX_TITULO)


def _sin_invisibles(texto: str) -> str:
    """Sin caracteres de control (pasan a espacio) ni de formato (anchura cero, bidireccionales).

    El título se muestra en la web y lo compara el emparejado: una inversión bidireccional le daría
    la vuelta en pantalla y un espacio de anchura cero partiría una palabra sin que se viera.
    """
    return "".join(
        " " if (categoria := unicodedata.category(c)) == "Cc" else "" if categoria == "Cf" else c
        for c in texto
    )


def _clave(texto: str) -> str:
    """«Desguaces D.L.I.» → «desguacesdli»: sin tildes, en minúsculas y solo letras y cifras."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto.casefold() if c.isalnum())


def _recortar(texto: str, maximo: int) -> str:
    """Espacios normalizados y como mucho `maximo` caracteres, sin partir la última palabra.

    El anclaje exige palabras completas: un valor cortado a media palabra ya no se anclaría.
    """
    limpio = _ESPACIOS.sub(" ", texto).strip()
    if len(limpio) <= maximo:
        return limpio
    cortado = limpio[:maximo]
    # Solo se quita una palabra normal; una «palabra» de miles de letras no se reduce a lo que
    # haya delante (eso cambiaría el valor y lo podría anclar).
    corte = cortado.rfind(" ")
    if limpio[maximo] != " " and corte >= maximo - MAX_PALABRA:
        cortado = cortado[:corte]
    return cortado.rstrip()
