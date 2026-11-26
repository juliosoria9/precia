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
- Lo que lee un selector del perfil tiene que tener además la forma de su campo (`con_forma`,
  R3-E): la referencia, la de una referencia del emparejado, y la marca, ser corta. Un selector
  posicional o demasiado amplio lee otra cosa anclada en la página («100% algodón» como
  referencia, el bloque del fabricante como marca): se descarta, se anota en `sin_forma` y el
  campo queda para la IA. Lo que declara la tienda (JSON-LD, metadatos) no se comprueba,
  salvo la marca.
- La marca del JSON-LD o de los metadatos tiene que verse en el texto de la página (R5-H): un
  marketplace pone ahí al vendedor («PLANETA HUERTO B2C» en una ficha de Carrefour cuyo título
  dice «Greenatural»). Si no se ve, se avisa, se guarda como reserva y la cascada sigue
  (metadatos, perfil, IA) por si hay una marca que sí se ve; de la IA solo vale una anclada. Si
  nadie da una, se recupera la reserva con `CONFIANZA_NO_VISIBLE` (un logo también es una marca
  que no está en el texto).
- Una referencia OEM que es el id interno de la tienda se descarta venga de donde venga
  (`id_interno`, R4-H): un número corto que ocupa en la URL de la ficha el sitio de un id
  («RefID: 402519» en «…-402519/», el mpn 101507 de «101507_silla….html»). El campo pasa a
  la vía siguiente; si lo leyó el perfil, se anota además en `sin_forma`.
- Un valor de la IA que no está en la página se conserva con `anclado=False` (la web lo muestra
  tachado), con confianza 0, y su precio nunca es el precio de la ficha.

Con `referencias` (las OEM de la consulta), se apunta además cuáles cita la página, como palabra
completa, en su texto visible o en una pestaña oculta (B5: la pestaña «Referencias OEM» de
Oscaro). Ese texto con pestañas solo decide la cita; los valores y precios se anclan como arriba.
"""

import re
import unicodedata
from collections.abc import Iterator, Sequence
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from precia.emparejado.referencias import (
    es_id_numerico,
    es_referencia,
    normalizar_referencia,
    referencias_en,
)
from precia.extraccion.anclaje import anclado
from precia.extraccion.dom import Nodo, analizar
from precia.extraccion.estados import estado_canonico
from precia.extraccion.imagenes import anclada_en_html, candidatas_de_imagen, foto_valida
from precia.extraccion.jsonld import bloques_jsonld, productos_de_arbol
from precia.extraccion.metadatos import producto_metadatos
from precia.extraccion.precios import (
    MAX_IMPORTE,
    buscar_precios,
    leer_precio,
    precio_principal_del_sufijo,
    texto_de_precio,
)
from precia.extraccion.selectores import (
    dividir_selector,
    extraer_valor,
    extraer_valores,
    seleccionar,
)
from precia.extraccion.tipos import (
    ExtractorIA,
    FichaExtraida,
    Precio,
    ProductoJSONLD,
    SelectorNoValido,
)
from precia.ia.tipos import ErrorIA
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS, OrigenDato, ValorCampo
from precia.perfiles.tipos import Perfil

CONFIANZA = {
    OrigenDato.JSONLD: 1.0,
    OrigenDato.METADATOS: 0.95,
    OrigenDato.PERFIL: 0.9,
    OrigenDato.IA: 0.7,
}
CONFIANZA_NO_ANCLADO = 0.0
CONFIANZA_NO_VISIBLE = 0.8
"""Marca declarada (JSON-LD, metadatos) que no se ve en el texto y que nadie ha mejorado (R5-H):
por debajo del perfil, que sí la ve, y por encima de la IA."""
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
MAX_CITAS_MIRADAS = 50
"""Apariciones de cada referencia que se comprueban como mucho (`_cita`): una página hostil puede
repetirla miles de veces."""
DETRAS_DE_LA_CITA = 6
"""Lo que se mira detrás de una aparición: un espacio y cinco caracteres bastan para ver si sigue
un sufijo de revisión («… 151 E») o otro grupo de la referencia."""
MIN_IDENTIFICADOR = 6
"""Lo mínimo que mide un tramo de ruta, sku o gtin para identificar la variante de la página
(`_principal`): «p1», «12» o «index» aparecen en cualquier ruta."""
MAX_MARCADOS = 50
"""Elementos marcados como seleccionados que se miran como mucho (`_seleccionada`): una ficha real
tiene unos pocos y una página hostil podría marcar miles."""
MAX_ATRIBUTO_SELECCION = 300
MAX_MARCA = 60
MAX_PALABRAS_MARCA = 6
"""Una marca es un nombre corto («Hill's Science Plan», «C.F. Martin & Co.»): un texto más largo
es un párrafo o un bloque entero de la ficha (R3-E)."""
MAX_FOTOS_PERFIL = 10
"""Elementos del selector de fotos del perfil que se miran como candidatos a foto principal."""
AVISO_FOTO_NO_ANCLADA = "La foto no aparece en la página: se descarta."
MAX_SIN_FORMA = 80
"""Lo que se guarda de un valor sin forma para citarlo en los avisos y en el validador."""
"""Lo que se mira de cada atributo de un elemento seleccionado; un sku o un gtin caben de sobra."""
_MARCAS_SELECCION = frozenset({"selected", "checked"})
"""Trozos de clase («is-selected», «swatch--selected», «checked») que marcan la opción elegida.
«active» y «current» no: los usan las pestañas, los carruseles y los menús."""
_ARIA_SELECCION = ("aria-selected", "aria-checked", "aria-pressed", "aria-current")
_TROZOS_CLASE = re.compile(r"[-_]+")
_ETIQUETA_FILA = re.compile(r"\s*([^\W\d_][^\W\d_]*\.?(?:[ ./'-]+[^\W\d_]+\.?){0,3})\s*:\s*\S")
"""Una fila «Etiqueta: valor» (R5-A): de 1 a 4 palabras sin cifras delante de los dos puntos,
como «RefID: 402519», «Referencia pieza: A1668207259» o «Nº Puertas: 5»."""
MAX_ETIQUETA = 30
ETIQUETAS_CAMPO: dict[str, tuple[str, ...]] = {
    "referencia_oem": (
        "referencia",
        "ref",
        "oem",
        "oe",
        "mpn",
        "reference",
        "part number",
        "numero de pieza",
        "codigo de pieza",
        "no de pieza",
    ),
    "marca": ("marca", "fabricante", "brand", "marque", "manufacturer", "hersteller"),
    "estado": ("estado", "condicion", "condition", "etat", "zustand"),
}
"""Etiquetas de fila que nombran cada campo (R5-A), sin tildes y en minúsculas. Se comparan por
palabras enteras: «RefID» no es «ref» y «Referencia pieza» sí es «referencia». Un campo que no
está aquí se nombra con las palabras de su etiqueta en `CAMPOS`."""
_TOKEN_REFERENCIA = re.compile(r"[0-9A-Za-z]+(?:[-./_][0-9A-Za-z]+)*")
"""Un código suelto con sus separadores internos, como los trocea `emparejado.referencias`."""
_SEPARADOR_CITA = r"[\s\-./_]?"
_EXTENSION = re.compile(r"\.[a-z]{2,5}$")
_SEPARADORES_SLUG = re.compile(r"[-_.]+")
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
    referencias: Sequence[str] = (),
) -> FichaExtraida:
    """Campos pedidos de la ficha en `pagina`. Nunca lanza por datos de la web ni por la IA.

    El precio de la ficha se busca siempre (es el de la oferta), aunque no esté en `campos`;
    la IA solo se usa para los campos pedidos. `referencias` llegan normalizadas, como las da
    `emparejado.referencias.referencias_en`.
    """
    raiz = analizar(pagina.html)
    extraccion = _Extraccion(pagina, raiz, perfil)
    pedidos = extraccion.campos_validos(campos)

    if extraccion.listado:
        # Ni el perfil ni la IA: cualquier precio de la página es el de una de sus tarjetas.
        extraccion.avisos.append(
            f"La página es un listado (ItemList con {extraccion.listado} productos), no una "
            "ficha: no se toma ningún precio ni campo."
        )
        pedidos = ()
    else:
        for campo in dict.fromkeys(("precio", *pedidos)):
            if not (extraccion.desde_jsonld(campo) or extraccion.desde_metadatos(campo)):
                extraccion.desde_perfil(campo)
        extraccion.sin_repetidos()

    faltan = tuple(c for c in pedidos if c not in extraccion.valores)
    if extractor_ia is not None and faltan:
        await extraccion.desde_ia(extractor_ia, faltan)
    extraccion.con_reservas()
    foto = extraccion.foto()

    return FichaExtraida(
        url=pagina.url_final or pagina.url,
        titulo=extraccion.titulo(),
        precio=extraccion.precio,
        campos={c: extraccion.valores[c] for c in pedidos if c in extraccion.valores},
        avisos=tuple(extraccion.avisos),
        referencias_citadas=_citadas(raiz, referencias),
        obtenida=pagina.obtenida,
        listado=extraccion.listado,
        repetidos=extraccion.repetidos,
        sin_forma=extraccion.sin_forma,
        foto=foto,
    )


def _citadas(raiz: Nodo, referencias: Sequence[str]) -> frozenset[str]:
    """Las `referencias` que la página cita, en su texto visible o en una pestaña oculta."""
    if not referencias:
        return frozenset()
    texto = raiz.texto(pestanas=True)
    return frozenset(r for r in referencias if _cita(r, texto))


def _cita(referencia: str, texto: str) -> bool:
    """Si `texto` cita `referencia` con la normalización del emparejado (`referencias_en`).

    «1K0-698-151» y «1K0.698.151» citan 1K0698151; «1K0 698 151 E» y «1K0698151E», no: son su
    revisión E, como en `referencias_en`. Se buscan sus apariciones de borde a borde, con un
    separador como mucho entre carácter y carácter, y cada una se confirma con `referencias_en`
    sobre ella y lo que la sigue: todo el texto no puede ir a `referencias_en` (solo mira el
    principio) y la comparación compacta del anclaje no ve los guiones ni el sufijo.
    """
    patron = re.compile(
        r"(?<![0-9A-Za-z])"
        + _SEPARADOR_CITA.join(re.escape(c) for c in referencia)
        + r"(?![0-9A-Za-z])",
        re.IGNORECASE,
    )
    for vez, aparicion in enumerate(patron.finditer(texto)):
        if vez == MAX_CITAS_MIRADAS:
            break
        trozo = texto[aparicion.start() : aparicion.end() + DETRAS_DE_LA_CITA]
        if referencia in referencias_en(trozo):
            return True
    return False


class _Extraccion:
    """Estado de la cascada de una ficha: valores obtenidos, precio y avisos."""

    def __init__(self, pagina: Pagina, raiz: Nodo, perfil: Perfil | None) -> None:
        self.url = pagina.url_final or pagina.url
        self.html = pagina.html
        self.raiz = raiz
        self.perfil = perfil
        self.texto = raiz.texto()
        self.bloques = bloques_jsonld(raiz)
        productos = productos_de_arbol(raiz)
        self.producto = _principal(productos, self.url, raiz)
        self.metadatos = producto_metadatos(raiz, url=self.url)
        # Un listado (R2-C10) es lo que queda sin producto principal: sin microdatos ni OpenGraph
        # de producto, y sin `_principal` porque solo hay elementos de una lista.
        listado = self.producto is None and self.metadatos is None and _lista(productos, self.url)
        self.listado = len(productos) if listado else 0
        self.valores: dict[str, ValorCampo] = {}
        self.precio: Precio | None = None
        self.avisos: list[str] = []
        self.repetidos: dict[str, str] = {}
        self.sin_forma: dict[str, str] = {}
        self.reservas: dict[str, ValorCampo] = {}
        self.ids_url = _ids_de_la_ruta(self.url)
        self._importes: frozenset[Decimal] | None = None
        self._vigentes: frozenset[Decimal] | None = None

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
        if campo == "estado" and es_anclado:
            # Ya anclado con las palabras de la página: se guarda con el vocabulario común.
            valor = estado_canonico(valor) or valor
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
        if valor is None or self.id_interno(campo, valor, de):
            return False
        if campo == "marca" and not anclado(valor, self.texto):
            self.avisos.append(
                f"La marca {de} («{_recortar(valor, MAX_SIN_FORMA)}») no se ve en el texto de la "
                "página: se busca una que se vea."
            )
            self.reservas.setdefault(campo, ValorCampo(valor, origen, True, CONFIANZA_NO_VISIBLE))
            return False
        self.anotar(campo, valor, origen, True)
        return True

    def con_reservas(self) -> None:
        """Recupera la marca declarada que no se ve si nadie ha dado una que se vea (R5-H)."""
        for campo, valor in self.reservas.items():
            self.valores.setdefault(campo, valor)

    def id_interno(self, campo: str, valor: str, de: str) -> bool:
        """Si `valor` es el id interno de la tienda y no una referencia (R4-H); entonces avisa.

        Solo para `referencia_oem`: todas sus referencias candidatas (`referencias_en`, o el
        valor normalizado si no da ninguna) son números de menos de
        `MIN_DIGITOS_REFERENCIA` cifras que ocupan el sitio de un id en la URL de la ficha
        (`_ids_de_la_ruta`). «RefID: 402519» en «…-402519/» lo es; una OEM de 8 o más
        cifras, una alfanumérica o un número en medio del slug («febi-21876-brazo»), no. El
        productID del JSON-LD no basta: hay tiendas de recambios que ponen ahí la OEM.
        """
        if campo != "referencia_oem" or not self.ids_url:
            return False
        candidatas = referencias_en(valor) or {normalizar_referencia(valor)}
        if not all(es_id_numerico(c) and c in self.ids_url for c in candidatas):
            return False
        self.avisos.append(
            f"El valor {de} para «{campo}» («{_recortar(valor, MAX_SIN_FORMA)}») es el id de "
            "la ficha en su URL, no una referencia: se descarta."
        )
        return True

    def _precio_jsonld_anclado(self, precio: Precio) -> bool:
        """El importe del JSON-LD se ve fuera de lo tachado, o no se ve en ninguna parte.

        Si solo sale dentro de algo tachado (`dom.tachado`), es el precio anterior de una
        oferta (R3-A1, Brico Dépôt: `<s>316,54 €</s>` con el JSON-LD a 316.54 y el vigente a
        228,96 €): no vale y la cascada sigue con los metadatos y el perfil. Mejor sin precio
        que con uno que no se paga. Si no sale en ninguna parte del texto (precio pintado por
        JS), se acepta como siempre, con el aviso de que no se ve.
        """
        if self.precio_en_pagina(precio.importe):
            if precio.importe in self._importes_sin_tachados():
                return True
            self.avisos.append(
                f"El precio del JSON-LD ({formatear_precio(precio)}) solo aparece tachado en "
                "la página: se descarta."
            )
            return False
        return any(precio.texto in bloque for bloque in self.bloques)

    def _importes_sin_tachados(self) -> frozenset[Decimal]:
        if self._vigentes is None:
            texto = self.raiz.texto(sin_tachados=True)
            self._vigentes = frozenset(p.importe for p in buscar_precios(texto))
        return self._vigentes

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
            elif campo == "precio":
                valor = texto_de_precio(self.raiz, selector)
            else:
                valores = extraer_valores(self.raiz, selector)
                valor = fila_del_campo(campo, valores)
                if valor is None and valores:
                    cita = _recortar(valores[0], MAX_SIN_FORMA)
                    self.sin_forma[campo] = cita
                    self.avisos.append(
                        f"El selector del perfil para «{campo}» es ambiguo: casa {len(valores)} "
                        f"filas con etiquetas distintas («{cita}»…) y ninguna nombra el campo: "
                        "se descarta."
                    )
                    return False
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
        if not con_forma(campo, valor):
            cita = _recortar(valor, MAX_SIN_FORMA)
            self.sin_forma[campo] = cita
            self.avisos.append(
                f"El valor del perfil para «{campo}» («{cita}») no tiene forma de {campo}: se "
                "descarta."
            )
            return False
        if self.id_interno(campo, valor, "del perfil"):
            self.sin_forma[campo] = _recortar(valor, MAX_SIN_FORMA)
            return False
        self.anotar(campo, valor, OrigenDato.PERFIL, True)
        return True

    def sin_repetidos(self) -> None:
        """Descarta el valor del perfil que repite el de otro campo del perfil (R2-CAMPOS).

        Dos selectores que leen el mismo texto (la marca y la referencia leyendo el mismo «P/N:
        …») no pueden acertar los dos: se queda el campo de más prioridad, en el orden de
        `CAMPOS`, y el otro se anota en `repetidos` y queda para la IA, si la hay. Solo se
        comparan valores del perfil entre sí; espacios y mayúsculas no cuentan.
        """
        vistos: dict[str, str] = {}
        for campo in (c.clave for c in CAMPOS):
            valor = self.valores.get(campo)
            if valor is None or valor.origen is not OrigenDato.PERFIL:
                continue
            clave = " ".join(valor.valor.split()).casefold()
            otro = vistos.setdefault(clave, campo)
            if otro != campo:
                del self.valores[campo]
                self.repetidos[campo] = otro
                self.avisos.append(
                    f"El valor del perfil para «{campo}» es el mismo que el de «{otro}»: se "
                    "descarta."
                )

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

    # --- Foto principal ---------------------------------------------------------------------

    def foto(self) -> str | None:
        """URL de la foto principal, o `None`. Se llama después de la cascada (mira el precio).

        Solo del marcado de la página, nunca de la IA ni del texto visible, y por este orden: el
        JSON-LD del producto principal, sus metadatos (microdatos u OpenGraph de producto), un
        `og:image` suelto si la ficha tiene precio (si no, puede ser una portada) y el selector
        `fotos` del perfil. Cada candidata pasa `imagenes.foto_valida` y su valor crudo tiene
        que aparecer en el HTML (`anclada_en_html`). Un listado no tiene foto.
        """
        if self.listado:
            return None
        avisado = False
        for crudo in self._candidatas_foto():
            foto = foto_valida(crudo, base=self.url)
            if foto is None:
                continue
            if anclada_en_html(crudo.strip(), self.html):
                return foto
            if not avisado:
                self.avisos.append(AVISO_FOTO_NO_ANCLADA)
                avisado = True
        return None

    def _candidatas_foto(self) -> Iterator[str]:
        if self.producto is not None:
            yield from self.producto.imagenes
        if self.metadatos is not None:
            yield from self.metadatos.imagenes
        if self.precio is not None and (og := _meta(self.raiz, "og:image")) is not None:
            yield og
        if self.perfil is not None and "fotos" in self.perfil.sel_campos:
            yield from self._fotos_del_perfil(self.perfil.sel_campos["fotos"])

    def _fotos_del_perfil(self, selector: str) -> Iterator[str]:
        """Valores crudos del selector `fotos`: su atributo o, sin atributo, sus `<img>`."""
        try:
            css, atributo = dividir_selector(selector)
            nodos = [self.raiz] if atributo is not None and not css else seleccionar(self.raiz, css)
        except SelectorNoValido:
            return  # Ya se avisó al leer el campo, si se pidió.
        for nodo in nodos[:MAX_FOTOS_PERFIL]:
            if atributo is None:
                yield from candidatas_de_imagen(nodo)
            elif valor := nodo.atributo(atributo):
                yield valor

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
            elif not self.id_interno(campo, valor, "de la IA"):
                es_anclado = anclado(valor, self.texto)
                if not es_anclado and campo in self.reservas:
                    self.avisos.append(
                        f"El valor de la IA para «{campo}» no aparece en la página: se queda el "
                        "declarado por la tienda."
                    )
                    continue
                if not es_anclado:
                    self.avisos.append(f"El valor de la IA para «{campo}» no aparece en la página.")
                self.anotar(campo, valor, OrigenDato.IA, es_anclado)

    def _precio_ia(self, valor: str) -> None:
        precio = leer_precio(valor)
        if precio is None:
            self.avisos.append("La IA devolvió un precio que no se puede leer.")
            return
        es_anclado = self.precio_en_pagina(precio.importe)
        if es_anclado and (
            principal := precio_principal_del_sufijo(self.raiz, precio.importe, self.texto)
        ):
            self.avisos.append(
                f"El precio de la IA ({formatear_precio(precio)}) es el del sufijo de otro precio "
                f"(suele ser sin IVA): se usa el principal ({formatear_precio(principal)})."
            )
            precio = principal
        texto = formatear_precio(precio)
        if es_anclado:
            self.precio = precio
        else:
            self.avisos.append(
                f"El precio de la IA ({texto}) no aparece en la página: se descarta."
            )
        self.anotar("precio", texto, OrigenDato.IA, es_anclado)


def con_forma(campo: str, valor: str) -> bool:
    """Si `valor` tiene la forma de `campo` (R3-E); los campos sin forma fija siempre la tienen.

    - `referencia_oem`: alguna referencia candidata del emparejado (`referencias_en`), así que
      vale cualquier forma que el emparejado entienda («8200 660 034», «P/N: SP634452», varias
      separadas por comas), o algún código suelto con forma de referencia (`es_referencia`):
      `referencias_en` toma «038253014G» por una medida en gramos. No valen «100% algodón» ni
      «Talla: M».
    - `marca`: como mucho `MAX_MARCA` caracteres y `MAX_PALABRAS_MARCA` palabras.
    """
    if campo == "referencia_oem":
        return bool(referencias_en(valor)) or any(
            es_referencia(normalizar_referencia(codigo.group()))
            for codigo in _TOKEN_REFERENCIA.finditer(valor)
        )
    if campo == "marca":
        return len(valor) <= MAX_MARCA and len(valor.split()) <= MAX_PALABRAS_MARCA
    return True


def etiqueta_de_fila(valor: str) -> str | None:
    """«Referencia pieza: A1668207259» → «referencia pieza»; `None` si no es una fila con etiqueta.

    La etiqueta sale sin tildes, en minúsculas y con las palabras separadas por un espacio.
    """
    encaje = _ETIQUETA_FILA.match(valor)
    if encaje is None or len(encaje.group(1)) > MAX_ETIQUETA:
        return None
    return " ".join(_palabras_sin_tildes(encaje.group(1)))


def nombra_campo(campo: str, etiqueta: str) -> bool:
    """Si la etiqueta de una fila (`etiqueta_de_fila`) nombra `campo`, por palabras enteras."""
    nombres = ETIQUETAS_CAMPO.get(campo) or tuple(
        _palabras_sin_tildes(c.etiqueta)[0] for c in CAMPOS if c.clave == campo
    )
    rodeada = f" {etiqueta} "
    return any(f" {nombre} " in rodeada for nombre in nombres)


def fila_del_campo(campo: str, valores: Sequence[str]) -> str | None:
    """El valor que toca leer de lo que casa un selector de texto del perfil (R5-A).

    Si casa varias filas «Etiqueta: valor» con etiquetas distintas (la descripción corta de un
    desguace: «RefID: …», «Referencia pieza: …», «Estado: …»), el selector es ambiguo: vale la
    primera fila cuya etiqueta nombra el campo (`nombra_campo`) y, si ninguna, `None`. Si no,
    como siempre, la primera coincidencia (`None` si no hay ninguna).
    """
    etiquetas = [etiqueta_de_fila(valor) for valor in valores]
    if len({e for e in etiquetas if e is not None}) < 2:
        return valores[0] if valores else None
    return next(
        (v for v, e in zip(valores, etiquetas, strict=True) if e and nombra_campo(campo, e)),
        None,
    )


def _palabras_sin_tildes(texto: str) -> list[str]:
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", sin_tildes.casefold())


def formatear_precio(precio: Precio) -> str:
    """`Precio(Decimal("1234.5"))` → `"1.234,50 €"`; otras monedas con su código."""
    cuantizado = precio.importe.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    entero, _, decimales = f"{cuantizado:,.2f}".partition(".")
    cifra = f"{entero.replace(',', '.')},{decimales}"
    return f"{cifra} €" if precio.moneda == "EUR" else f"{cifra} {precio.moneda}"


def _principal(
    productos: list[ProductoJSONLD], url: str, raiz: Nodo | None = None
) -> ProductoJSONLD | None:
    """El producto de la ficha: el de la URL de la página o, si no, el primero con precio.

    Si varios casan con la URL, gana el primero con precio: en las librerías el `Book` exterior no
    tiene oferta y la lleva el `Product` de `workExample`, con el mismo `@id`. Si ninguno casa
    exacto, se busca la variante de la página (`_variante`) antes de caer en el primero con precio,
    y en ese último paso los productos sueltos van antes que los de una lista (relacionados). Si
    solo hay productos de una lista y de varias URL (`_lista`), ninguno es el de la página: `None`.
    """
    propia = _sin_fragmento(url)
    suyos: list[ProductoJSONLD] = []
    for producto in productos:
        suya = _url_de(producto, url)
        if suya is not None and _sin_fragmento(suya) == propia:
            suyos.append(producto)
    if not suyos and (variante := _variante(productos, url, raiz)) is not None:
        return variante
    if not suyos and _lista(productos, url):
        return None
    candidatos = suyos or [p for p in productos if not p.en_lista] or productos
    con_precio = (p for p in candidatos if p.precio is not None)
    return next(con_precio, candidatos[0] if candidatos else None)


def _lista(productos: list[ProductoJSONLD], url: str) -> bool:
    """Si los productos son solo los elementos de una lista de varias URL, ninguna la de la página.

    Es lo que publican las landings de marca o SEO y las categorías (R2-C10): un `ItemList` de
    tarjetas. Con un solo producto, o con todos en la misma URL, no se sabe y se deja como antes.
    """
    if not productos or any(not p.en_lista for p in productos):
        return False
    urls = {_sin_fragmento(suya) for p in productos if (suya := _url_de(p, url)) is not None}
    return len(urls) >= 2 and _sin_fragmento(url) not in urls


def _sin_fragmento(url: str) -> str:
    return urldefrag(url).url.rstrip("/")


def _url_de(producto: ProductoJSONLD, url: str) -> str | None:
    """La url del producto resuelta contra la de la página, o `None` si no tiene o no es válida."""
    if not producto.url:
        return None
    try:
        return urljoin(url, producto.url)
    except ValueError:
        return None


def _variante(
    productos: list[ProductoJSONLD], url: str, raiz: Nodo | None = None
) -> ProductoJSONLD | None:
    """La variante de la página cuando su URL no es exactamente la de ningún producto (C7).

    Las tiendas con variantes declaran un `Product` por color o talla con su propia url, y la
    página suele llegar con otro prefijo o slug (footlocker: `/es/product/~/<sku>.html` frente a
    `/product/<slug>/<sku>.html`). Se casa, por este orden: (1) el último tramo de la ruta, si
    lleva alguna cifra y mide al menos `MIN_IDENTIFICADOR` (un tramo genérico como `product.php`
    o `index.html` lo comparten todas las variantes cuando la variante va en la query); (2) la
    variante marcada como seleccionada en la página (`_seleccionada`, R2-C11); (3) el sku o el
    gtin escrito en la ruta de la página como palabra completa. La (2) va antes que la (3) porque
    la URL puede llevar el id del maestro y no el de la variante (Salesforce Commerce Cloud:
    `<id>_M.html`, que a veces es el sku de otra variante). El mpn no cuenta: en recambios la
    referencia OEM va en el slug de varios productos relacionados. En cada paso tiene que casar
    un único producto; si no, `None` y nada cambia.
    """
    tramo = _ultimo_tramo(url)
    if len(tramo) >= MIN_IDENTIFICADOR and any(c.isdigit() for c in tramo):
        por_tramo = [
            p for p in productos if (suya := _url_de(p, url)) and _ultimo_tramo(suya) == tramo
        ]
        if len(por_tramo) == 1:
            return por_tramo[0]
    if raiz is not None and len(productos) >= 2:
        seleccionada = _seleccionada(productos, raiz)
        if seleccionada is not None:
            return seleccionada
    ruta = urlsplit(url).path.lower()
    por_identificador = [
        p
        for p in productos
        if any(_en_ruta(ident, ruta) for ident in (p.sku, p.gtin) if ident is not None)
    ]
    return por_identificador[0] if len(por_identificador) == 1 else None


def _seleccionada(productos: list[ProductoJSONLD], raiz: Nodo) -> ProductoJSONLD | None:
    """La variante que la página marca como elegida: botón, muestra, radio u opción seleccionados.

    Un elemento cuenta como marcado (`_marcado`) si alguno de sus atributos `data-*`, `value` o
    `id` lleva el sku o el gtin de una sola variante como identificador completo (`_identifica`):
    `id="variant-HIL604334"` o `data-variantid="HIL604334"` señalan esa variante, pero no la del
    sku «HIL604334» dentro de «HIL604334_B2» (el pack de dos). Se miran como mucho `MAX_MARCADOS`
    elementos marcados; si los que señalan una variante no coinciden, `None`.
    """
    identificadores = [
        (i, ident)
        for i, producto in enumerate(productos)
        for bruto in (producto.sku, producto.gtin)
        if bruto is not None and len(ident := bruto.strip().lower()) >= MIN_IDENTIFICADOR
    ]
    if not identificadores:
        return None
    patrones = [
        (i, re.compile(r"(?<![0-9a-z_])" + re.escape(ident) + r"(?![0-9a-z_])"))
        for i, ident in identificadores
    ]
    elegidas: set[int] = set()
    marcados = 0
    for nodo in raiz.descendientes():
        if not _marcado(nodo):
            continue
        marcados += 1
        if marcados > MAX_MARCADOS:
            break
        valores = [
            valor[:MAX_ATRIBUTO_SELECCION].lower()
            for nombre, valor in nodo.atributos.items()
            if nombre.startswith("data-") or nombre in ("value", "id")
        ]
        senaladas = {i for i, patron in patrones if any(patron.search(v) for v in valores)}
        if len(senaladas) == 1:
            elegidas |= senaladas
    return productos[elegidas.pop()] if len(elegidas) == 1 else None


def _marcado(nodo: Nodo) -> bool:
    """Si `nodo` está marcado como seleccionado (ver `_MARCAS_SELECCION` y `_ARIA_SELECCION`)."""
    if any((nodo.atributo(aria) or "").strip().lower() == "true" for aria in _ARIA_SELECCION):
        return True
    if nodo.etiqueta == "input" and "checked" in nodo.atributos:
        return True
    if nodo.etiqueta == "option" and "selected" in nodo.atributos:
        return True
    return any(
        trozo.lower() in _MARCAS_SELECCION
        for clase in nodo.clases
        for trozo in _TROZOS_CLASE.split(clase)
    )


def _ultimo_tramo(url: str) -> str:
    """`https://t.es/a/b/123456.html?x=1` → `"123456.html"` (en minúsculas)."""
    return urlsplit(url).path.rstrip("/").rpartition("/")[2].lower()


def _ids_de_la_ruta(url: str) -> frozenset[str]:
    """Los números de la ruta de `url` en el sitio de un id (R4-H), como en `fuentes.sitemap`.

    Un tramo que solo es un número (`/p/21876`), o el número del principio o del final del
    último tramo, sin su extensión: `101507_silla-paseo.html` (PrestaShop),
    `faro-izquierdo-402519/`. Un número en medio del slug no cuenta: suele ser el modelo o la
    propia referencia («febi-21876-brazo»). Solo los de menos de `MIN_DIGITOS_REFERENCIA`
    cifras (`es_id_numerico`).
    """
    try:
        ruta = unquote(urlsplit(url).path).lower()
    except ValueError:
        return frozenset()
    tramos = [_EXTENSION.sub("", t) for t in ruta.split("/") if t]
    ids = {t for t in tramos if es_id_numerico(t)}
    if tramos and (palabras := [p for p in _SEPARADORES_SLUG.split(tramos[-1]) if p]):
        ids.update(p for p in (palabras[0], palabras[-1]) if es_id_numerico(p))
    return frozenset(ids)


def _en_ruta(identificador: str, ruta: str) -> bool:
    """Si `identificador` está en `ruta` (ya en minúsculas) sin alfanuméricos pegados."""
    ident = identificador.strip().lower()
    if len(ident) < MIN_IDENTIFICADOR:
        return False
    patron = r"(?<![0-9a-z])" + re.escape(ident) + r"(?![0-9a-z])"
    return re.search(patron, ruta) is not None


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
