"""Contrato del paquete `perfiles`: el perfil de una tienda y su informe de validación.

Un perfil dice cómo buscar en una tienda y cómo leer sus fichas con selectores CSS. Los
selectores pertenecen a un subconjunto de CSS (ver `extraccion/selectores.py`) y pueden acabar en
` @atributo` para leer un atributo en vez del texto: `"h2.nombre a @href"`.

Hay estas clases de perfil (`Perfil.tipo`, nota 15 §D y nota 16):

- `html`: la búsqueda es una página HTML y se lee con los selectores.
- `api_woocommerce` y `api_shopify`: la búsqueda va a la API pública de solo lectura que la
  propia tienda usa en su buscador (`fuentes.apis`). La respuesta ya trae los productos con sus
  datos, así que el perfil no necesita selectores (pueden ir vacíos) y no se visita cada ficha.
- `listado` (nota 16 §A2): la tienda no tiene buscador y el usuario ha dado la URL de un listado
  o categoría, sin `{consulta}` y con `{pagina}` opcional. Se recorren unas pocas páginas con los
  selectores del listado y se filtran los resultados por la consulta.
- `sitemap` (nota 16 §B): la tienda no tiene buscador y se busca en el catálogo local de URL que
  sale de su `sitemap.xml` (`url_busqueda` es el sitemap raíz). No hay selectores de listado.
"""

from dataclasses import dataclass, field
from datetime import datetime

TIPO_HTML = "html"
TIPO_API_WOOCOMMERCE = "api_woocommerce"
TIPO_API_SHOPIFY = "api_shopify"
TIPO_LISTADO = "listado"
TIPO_SITEMAP = "sitemap"
TIPOS_PERFIL = (TIPO_HTML, TIPO_API_WOOCOMMERCE, TIPO_API_SHOPIFY, TIPO_LISTADO, TIPO_SITEMAP)
"""Tipos admitidos; el almacén rechaza cualquier otro (también con un CHECK en la tabla)."""


@dataclass(frozen=True, slots=True)
class Perfil:
    dominio: str
    version: int
    url_busqueda: str
    """Plantilla con `{consulta}`, p. ej. `https://tienda.es/buscar?q={consulta}`. En un perfil
    `listado` es la URL del listado (sin `{consulta}`, con `{pagina}` opcional) y en uno
    `sitemap`, la del sitemap raíz."""
    sel_resultado: str
    """Cada resultado de la página de búsqueda."""
    sel_titulo: str
    """Relativo a `sel_resultado`."""
    sel_enlace: str
    """Relativo a `sel_resultado`, normalmente con ` @href`."""
    sel_precio_listado: str | None
    sel_campos: dict[str, str]
    """En la ficha de producto: clave de campo (ver `nucleo.modelos.CAMPOS`) → selector."""
    necesita_js: bool
    autor: str
    """`manual`, `configurador IA` o `reparador IA`."""
    revisado: bool
    """Memoria §4: un perfil generado por la IA no se fija hasta que lo revisa una persona."""
    creado: datetime
    nota: str = ""
    tipo: str = TIPO_HTML
    """Uno de `TIPOS_PERFIL`. Va al final, con valor por defecto, para no romper los perfiles
    que ya se crean sin él (todos los anteriores a la nota 15 son `html`)."""

    @property
    def es_api(self) -> bool:
        """La búsqueda va a la API pública de la plataforma y no se usan los selectores."""
        return self.tipo in (TIPO_API_WOOCOMMERCE, TIPO_API_SHOPIFY)

    @property
    def es_listado(self) -> bool:
        """La búsqueda recorre un listado o categoría de la tienda (no tiene buscador)."""
        return self.tipo == TIPO_LISTADO

    @property
    def es_sitemap(self) -> bool:
        """La búsqueda va al catálogo local sacado del `sitemap.xml` (sin selectores de listado)."""
        return self.tipo == TIPO_SITEMAP


@dataclass(frozen=True, slots=True)
class InformeValidacion:
    valido: bool
    resultados: int
    """Resultados encontrados en la página de búsqueda."""
    fichas_ok: int
    fichas_total: int
    campos_encontrados: dict[str, int] = field(default_factory=dict)
    """Campo → en cuántas fichas se encontró."""
    problemas: tuple[str, ...] = ()
    campos_repetidos: tuple[str, ...] = ()
    """Claves de `sel_campos` que en todas las fichas leen lo mismo que otro campo de más
    prioridad (R2-CAMPOS). No restan validez: quien guarda el perfil las quita
    (`validador.sin_campos_repetidos`)."""
    campos_sin_forma: tuple[str, ...] = ()
    """Claves de `sel_campos` que en ninguna ficha leen un valor con la forma de su campo (R3-E:
    «100% algodón» como referencia). Tampoco restan validez y también las quita
    `validador.sin_campos_repetidos`."""


class ErrorPerfil(Exception):
    """Base de los errores del paquete. El mensaje se puede enseñar al usuario."""


class ConfiguracionFallida(ErrorPerfil):
    """El configurador no ha conseguido un perfil que valide en la tienda."""


class ReparacionFallida(ErrorPerfil):
    """El perfil propuesto por el reparador no valida: se conserva el anterior."""
