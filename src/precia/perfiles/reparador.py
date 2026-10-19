"""🤖 Reparador: propone una versión nueva de un perfil que ha dejado de funcionar (memoria §5).

Cuando el listado sale vacío con el perfil vigente (lo normal es que la tienda haya cambiado su
HTML), se pide a la IA un perfil corregido a partir de los selectores viejos y del resumen de la
página de resultados actual. La propuesta se trata como la del configurador: selectores del
subconjunto admitido, claves de campo cerradas en el esquema y validación completa antes de
devolverla. La IA solo toca selectores: la URL de búsqueda, el dominio y `necesita_js` se
conservan del perfil anterior.

No hay reintento: si la propuesta no valida se lanza `ReparacionFallida` y sigue vigente el
perfil anterior. La versión nueva nace con `revisado=False` (pendiente de revisión humana).
Los errores de navegador y de IA se propagan, como en el configurador.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from precia.extraccion.listado import extraer_listado
from precia.extraccion.texto import resumen_para_ia
from precia.ia.tipos import PeticionIA
from precia.navegador.tipos import Pagina
from precia.navegador.url import Validador, validar_url
from precia.perfiles.configurador import (
    DESCRIPCIONES,
    ESQUEMA_LISTADO,
    SUBCONJUNTO_CSS,
    esquema_campos,
    leer_campos,
    leer_listado,
    selectores_de,
)
from precia.perfiles.protocolos import Navegador, SesionIA
from precia.perfiles.tipos import Perfil, ReparacionFallida
from precia.perfiles.validador import CLAVES, problema_sin_resultados, resumir, validar

COMPONENTE = "reparador"
AUTOR = "🤖 reparador"
MAX_MOTIVO = 3

ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {**ESQUEMA_LISTADO["properties"], "sel_campos": esquema_campos(CLAVES)},
    "required": [*ESQUEMA_LISTADO["required"], "sel_campos"],
    "additionalProperties": False,
}
INSTRUCCIONES = (
    "Eres el reparador de perfiles de PrecIA. Un perfil indica, con selectores CSS, cómo leer "
    "los resultados de búsqueda y las fichas de producto de una tienda online de recambios. El "
    "perfil de esta tienda ha dejado de encontrar resultados, seguramente porque la tienda ha "
    "cambiado su HTML. En los datos tienes los selectores del perfil anterior y el HTML "
    "simplificado de la página de resultados actual. Devuelve el perfil corregido:\n"
    "- sel_resultado: cada resultado (producto) de la búsqueda, sin @atributo;\n"
    "- sel_titulo: dentro de cada resultado, el nombre del producto;\n"
    "- sel_enlace: dentro de cada resultado, el enlace a la ficha, normalmente con @href;\n"
    "- sel_precio_listado: dentro de cada resultado, el precio, o null si no se muestra;\n"
    "- sel_campos: para cada campo de la ficha de producto, un selector aplicado a toda la "
    "ficha, o null. La página que recibes es un listado y no una ficha: conserva los "
    "selectores de sel_campos del perfil anterior salvo que tengas un motivo claro para "
    "cambiarlos. Campos:\n"
    + "\n".join(f"  - {c}: {DESCRIPCIONES[c]}" for c in CLAVES)
    + "\nSelecciona solo los resultados de la propia tienda: ni anuncios ni productos "
    "patrocinados de otras webs, ni menús, ni recomendaciones.\n\n" + SUBCONJUNTO_CSS
)


def _ahora() -> datetime:
    return datetime.now(UTC)


async def reparar(
    perfil: Perfil,
    pagina: Pagina,
    navegador: Navegador,
    sesion_ia: SesionIA,
    consulta: str,
    *,
    validador: Validador = validar_url,
    reloj: Callable[[], datetime] = _ahora,
) -> Perfil:
    """Versión `perfil.version + 1`, validada, del perfil roto. Si no, `ReparacionFallida`.

    `pagina` es la página de resultados actual de `consulta`, la que salió vacía.
    """
    respuesta = await sesion_ia.pedir(
        PeticionIA(
            componente=COMPONENTE,
            instrucciones=INSTRUCCIONES,
            datos=_datos(perfil, pagina),
            esquema=ESQUEMA,
            max_tokens=768,
        )
    )
    problemas: list[str] = []
    listado = leer_listado(respuesta.datos, problemas)
    campos = respuesta.datos.get("sel_campos")
    sel_campos = leer_campos(campos if isinstance(campos, Mapping) else {}, CLAVES, problemas)
    if listado is None:
        raise ReparacionFallida(_motivo(perfil, "propone selectores no válidos", problemas))

    nuevo = Perfil(
        dominio=perfil.dominio,
        version=perfil.version + 1,
        url_busqueda=perfil.url_busqueda,
        sel_resultado=listado["sel_resultado"] or "",
        sel_titulo=listado["sel_titulo"] or "",
        sel_enlace=listado["sel_enlace"] or "",
        sel_precio_listado=listado["sel_precio_listado"],
        sel_campos=sel_campos,
        necesita_js=perfil.necesita_js,
        autor=AUTOR,
        revisado=False,
        creado=reloj(),
    )
    # Antes de pedir nada a la tienda: la propuesta tiene que leer la página que se le dio.
    if not extraer_listado(pagina, nuevo):
        problema = problema_sin_resultados(pagina, nuevo, consulta)
        raise ReparacionFallida(
            _motivo(perfil, "propone un perfil que no lee la página", [problema])
        )
    informe = await validar(nuevo, navegador, consulta, validador=validador)
    if not informe.valido:
        raise ReparacionFallida(
            f"El perfil propuesto para {perfil.dominio} no valida. {resumir(informe)}"
        )
    return replace(nuevo, nota=resumir(informe))


def _datos(perfil: Perfil, pagina: Pagina) -> str:
    # El perfil anterior también va en los datos: pudo generarlo la IA a partir de otra página.
    anterior = json.dumps(selectores_de(perfil), ensure_ascii=False, indent=1)
    url = pagina.url_final or pagina.url
    return (
        f"PERFIL ANTERIOR (ya no encuentra resultados):\n{anterior}\n\n"
        f"PÁGINA DE RESULTADOS ACTUAL ({url}):\n{resumen_para_ia(pagina.html)}"
    )


def _motivo(perfil: Perfil, que: str, problemas: list[str]) -> str:
    detalle = "; ".join(problemas[:MAX_MOTIVO]) or "sin detalles"
    return f"La IA {que} para {perfil.dominio}: {detalle}"
