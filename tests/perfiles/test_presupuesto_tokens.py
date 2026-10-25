"""El tope de tokens de salida cabe en el esquema de cada petición de perfiles (R2-TOK).

Causa R2-TOK de la campaña multi-sector (ronda 2): el configurador pedía el listado y la ficha con
`max_tokens=512` y el reparador con 768, aunque sus esquemas admiten 4, 6 y 10 selectores de hasta
`MAX_SELECTOR` caracteres. En tiendas con clases generadas largas (MediaMarkt: `mms-ui-…`,
`sc-…`) la respuesta se cortaba (`stop_reason=max_tokens`) y el servicio repetía la misma
petición, que se volvía a cortar: en juguetilandia el reparador pagó 2 x 16 579 fichas de entrada
en balde y en MediaMarkt el configurador 1 x 21 397. La página de resultados es el recorte real de
MediaMarkt (`tests/fixtures/reales/mediamarkt_busqueda_filtro_tarjetas.html`). Sin red.
"""

import asyncio
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from precia.ia.salida import Consumo, SalidaCortada, validar
from precia.ia.tipos import CARACTERES_POR_TOKEN, PeticionIA, max_tokens_para
from precia.perfiles import reparador
from precia.perfiles.configurador import COMPONENTE, ESQUEMA_LISTADO, configurar
from precia.perfiles.descubrimiento import ESQUEMA_PLANTILLAS
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    LISTADO_OK,
    PORTADA,
    TIENDA,
    NavegadorFalso,
    pagina,
    paginas_sitio,
    perfil,
    sesion,
)

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
BUSQUEDA_MM = "https://www.mediamarkt.es/es/search.html?query=filtro"
SIN_BUSCADOR = (
    "<html><head><title>Recambios</title></head><body><header>"
    "<a href='/catalogo'>Catálogo</a></header></body></html>"
)


def _busqueda_mediamarkt() -> str:
    return (REALES / "mediamarkt_busqueda_filtro_tarjetas.html").read_bytes().decode("utf-8")


def _clases(html: str) -> list[str]:
    """Las listas de clases de la página, como selector compuesto (`.a.b.c`), de más a menos."""
    listas = {"." + ".".join(c.split()) for c in re.findall(r'class="([^"]+)"', html)}
    return sorted(listas, key=len, reverse=True)


def _realista(html: str) -> dict[str, Any]:
    """Una respuesta válida del reparador que copia las listas de clases enteras de la página.

    Cada selector cabe en `MAX_SELECTOR` (cumple el esquema), pero entre los diez pasan de lo que
    cabía en 768 fichas de salida.
    """
    clases = [c for c in _clases(html) if len(c) <= 280]
    tarjeta, enlace, *otras = clases
    return {
        "sel_resultado": f"article{tarjeta}",
        "sel_titulo": f"a{enlace} h3",
        "sel_enlace": f"a{enlace} @href",
        "sel_precio_listado": f"div{otras[0]} span",
        "sel_campos": {
            "precio": f"div{otras[1]} span",
            "referencia_oem": f"div{otras[2]} p",
            "marca": f"div{otras[3]} p",
            "estado": f"div{otras[4]} span",
            "descripcion": f"div{otras[5]} p",
            "fotos": f"div{otras[6]} img @src",
        },
    }


def _cortada() -> SalidaCortada:
    return SalidaCortada(
        "Salida incompleta: se ha alcanzado el máximo de tokens", Consumo(16_579, 768, Decimal(0))
    )


def _cumple(peticiones: list[PeticionIA]) -> None:
    assert peticiones
    for peticion in peticiones:
        assert peticion.max_tokens >= max_tokens_para(peticion.esquema), peticion.componente


def test_el_configurador_pide_el_listado_y_la_ficha_con_tokens_para_su_esquema() -> None:
    sesion_ia, proveedor = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK]})
    navegador = NavegadorFalso(paginas_sitio(), redirecciones={f"https://{DOMINIO}/": PORTADA})
    asyncio.run(
        configurar(DOMINIO, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
    )
    assert [p.esquema == ESQUEMA_LISTADO for p in proveedor.peticiones] == [True, False]
    _cumple(proveedor.peticiones)


def test_la_ia_del_descubrimiento_pide_con_tokens_para_su_esquema() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=f"{TIENDA}/catalogo?busca=faro+golf")
    sitio[PORTADA] = SIN_BUSCADOR
    propuesta = {"plantillas": ["/catalogo?busca={consulta}"]}
    sesion_ia, proveedor = sesion({COMPONENTE: [propuesta, LISTADO_OK, CAMPOS_OK]})
    asyncio.run(
        configurar(
            f"www.{DOMINIO}",
            NavegadorFalso(sitio),
            sesion_ia,
            CONSULTA,
            validador=validar_con_dns_publico,
        )
    )
    assert proveedor.peticiones[0].esquema == ESQUEMA_PLANTILLAS
    _cumple(proveedor.peticiones)


def test_el_reparador_con_la_busqueda_real_de_mediamarkt_no_repite_igual_la_salida_cortada() -> (
    None
):
    html = _busqueda_mediamarkt()
    roto = perfil(
        dominio="www.mediamarkt.es", url_busqueda=BUSQUEDA_MM.replace("filtro", "{consulta}")
    )
    sesion_ia, proveedor = sesion({reparador.COMPONENTE: [_cortada(), _cortada()]})
    with pytest.raises(SalidaCortada):
        asyncio.run(
            reparador.reparar(
                roto,
                pagina(BUSQUEDA_MM, html),
                NavegadorFalso({}),
                sesion_ia,
                "filtro",
                validador=validar_con_dns_publico,
            )
        )
    primera, segunda = proveedor.peticiones
    # La IA ve las listas de clases largas de la página...
    assert "mms-ui-dlUvRw" in primera.datos
    # ...y el tope cabe en su esquema y en una respuesta que las copia enteras.
    _cumple([primera])
    realista = _realista(html)
    validar(realista, reparador.ESQUEMA)
    caracteres = len(json.dumps(realista, ensure_ascii=False))
    assert caracteres / CARACTERES_POR_TOKEN <= primera.max_tokens
    # Cortada: se repite una vez con el doble de tokens, no igual.
    assert segunda.max_tokens == 2 * primera.max_tokens
    assert (segunda.instrucciones, segunda.datos) == (primera.instrucciones, primera.datos)
