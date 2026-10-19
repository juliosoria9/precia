"""Contrato del paquete `perfiles`: el perfil de una tienda y su informe de validación.

Un perfil dice cómo buscar en una tienda y cómo leer sus fichas con selectores CSS. Los
selectores pertenecen a un subconjunto de CSS (ver `extraccion/selectores.py`) y pueden acabar en
` @atributo` para leer un atributo en vez del texto: `"h2.nombre a @href"`.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Perfil:
    dominio: str
    version: int
    url_busqueda: str
    """Plantilla con `{consulta}`, p. ej. `https://tienda.es/buscar?q={consulta}`."""
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
    """`manual`, `🤖 configurador` o `🤖 reparador`."""
    revisado: bool
    """Memoria §4: un perfil generado por la IA no se fija hasta que lo revisa una persona."""
    creado: datetime
    nota: str = ""


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


class ErrorPerfil(Exception):
    """Base de los errores del paquete. El mensaje se puede enseñar al usuario."""


class ConfiguracionFallida(ErrorPerfil):
    """El configurador no ha conseguido un perfil que valide en la tienda."""


class ReparacionFallida(ErrorPerfil):
    """El perfil propuesto por el reparador no valida: se conserva el anterior."""
