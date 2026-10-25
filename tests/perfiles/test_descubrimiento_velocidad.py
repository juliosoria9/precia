"""Velocidad al configurar una tienda nueva (plan de velocidad de S03, tanda 2).

- A3: con Chromium no se repiten las candidatas que por HTTP perdieron la consulta al redirigir.
- A4: si la portada delata la plataforma, no se prueban las rutas fijas de las otras.
- B7: si la portada no da candidatas, la IA propone plantillas mientras se prueban las demás por
  HTTP, y las suyas se prueban antes que la ronda de Chromium.
"""

import asyncio
from typing import Any

import pytest

from precia.ia.tipos import PeticionIA, RespuestaIA
from precia.navegador.tipos import Pagina
from precia.perfiles.configurador import COMPONENTE, configurar
from precia.perfiles.descubrimiento import ESQUEMA_PLANTILLAS, PATRONES
from precia.perfiles.validador import url_de_busqueda
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    LISTADO_OK,
    PORTADA,
    TIENDA,
    NavegadorFalso,
    paginas_sitio,
    sesion,
)
from tests.perfiles.test_descubrimiento import SIN_BUSCADOR, con_cabeza, configurar_con, fallo_con

PRESTASHOP = PATRONES[0]
WOOCOMMERCE = PATRONES[3]
MAGENTO = PATRONES[4]
GENERICOS = (PATRONES[1], PATRONES[2], PATRONES[5])

SENAL_MAGENTO = '<script type="text/x-magento-init">{"*": {}}</script>'
SENAL_PRESTASHOP = '<script src="/modules/ps_searchbar/ps_searchbar.js"></script>'


def _ruta(patron: str) -> str:
    return url_de_busqueda(TIENDA + patron, CONSULTA)


# --- A4: rutas de otra plataforma ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("senal", "ajenas"),
    [
        (SENAL_MAGENTO, (PRESTASHOP, WOOCOMMERCE)),
        (SENAL_PRESTASHOP, (WOOCOMMERCE, MAGENTO)),
    ],
    ids=["magento", "prestashop"],
)
def test_plataforma_detectada_no_prueba_las_rutas_de_las_otras(
    senal: str, ajenas: tuple[str, ...]
) -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = con_cabeza(SIN_BUSCADOR, senal)
    navegador = NavegadorFalso(sitio)
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    for patron in ajenas:
        assert _ruta(patron) not in navegador.urls
    for patron in GENERICOS:
        assert _ruta(patron) in navegador.urls


def test_sin_plataforma_detectada_se_prueban_todas_las_rutas() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    navegador = NavegadorFalso(sitio)
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    for patron in PATRONES:
        assert _ruta(patron) in navegador.urls


# --- A3: Chromium solo para las candidatas que lo merecen ---------------------------------------


@pytest.mark.parametrize("destino", [PORTADA, f"{TIENDA}/es/"], ids=["portada", "idioma"])
def test_candidata_que_redirige_sin_la_consulta_no_se_repite_con_chromium(destino: str) -> None:
    # Muchas tiendas mandan una ruta de búsqueda que no conocen a la portada o a su prefijo de
    # idioma, sin la consulta. La redirección es del servidor: con Chromium iría al mismo sitio.
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    sitio[f"{TIENDA}/es/"] = SIN_BUSCADOR
    redirigida = _ruta(PATRONES[1])
    navegador = NavegadorFalso(sitio, paginas_js={}, redirecciones={redirigida: destino})
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    assert (redirigida, False) in navegador.pedidas
    assert (redirigida, True) not in navegador.pedidas


def test_candidata_con_la_consulta_en_la_url_final_si_se_repite_con_chromium() -> None:
    # Es el patrón de las seis búsquedas que ganó Chromium en la campaña: un 200 por HTTP sin
    # resultados (los pinta el JavaScript) y la consulta todavía en la URL final.
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    candidata = _ruta(PATRONES[1])
    final = f"{TIENDA}/resultados?q=faro+golf"
    sitio[final] = SIN_BUSCADOR
    navegador = NavegadorFalso(sitio, paginas_js={}, redirecciones={candidata: final})
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    assert (candidata, True) in navegador.pedidas


# --- B7: plantillas de la IA en paralelo con la ronda HTTP --------------------------------------

CATALOGO = f"{TIENDA}/catalogo?busca=faro+golf"
PROPUESTA = {"plantillas": ["/catalogo?busca={consulta}"]}


class NavegadorQueEspera(NavegadorFalso):
    """Cede el turno en cada descarga, como el navegador real (red, cortesía)."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        await asyncio.sleep(0)
        return await super().obtener(url, js=js, grande=grande)


def _sin_candidatas_en_la_portada(navegador: type[NavegadorFalso] = NavegadorFalso) -> Any:
    """Portada sin buscador (solo un enlace al catálogo) y `/buscar?q=` con un 200 que no es un
    listado: sin B7 se repetiría con Chromium antes de preguntar a la IA."""
    sitio = paginas_sitio(formulario=False, busqueda=CATALOGO)
    sitio[PORTADA] = SIN_BUSCADOR.replace("</header>", "<a href='/catalogo'>Catálogo</a></header>")
    sitio[_ruta(PATRONES[1])] = SIN_BUSCADOR
    return navegador(sitio, paginas_js={})


def test_portada_sin_candidatas_prueba_lo_de_la_ia_antes_que_chromium() -> None:
    navegador = _sin_candidatas_en_la_portada()
    perfil, peticiones = configurar_con(
        navegador, [PROPUESTA, LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}"
    )
    assert perfil.url_busqueda == f"{TIENDA}/catalogo?busca={{consulta}}"
    assert not perfil.necesita_js
    assert peticiones[0].esquema == ESQUEMA_PLANTILLAS
    # Ninguna candidata ha ido por Chromium (solo la portada, que no tenía buscador).
    assert [url for url, js in navegador.pedidas if js] == [PORTADA]
    assert navegador.urls.index(CATALOGO) > navegador.urls.index(_ruta(PATRONES[-1]))


def test_la_ia_se_pide_mientras_se_prueban_las_candidatas_por_http() -> None:
    navegador = _sin_candidatas_en_la_portada(NavegadorQueEspera)
    sesion_ia, proveedor = sesion({COMPONENTE: [PROPUESTA, LISTADO_OK, CAMPOS_OK]})
    completar = proveedor.completar
    momentos: list[int] = []

    async def anotar(peticion: PeticionIA) -> RespuestaIA:
        if peticion.esquema == ESQUEMA_PLANTILLAS:
            momentos.append(len(navegador.pedidas))
        return await completar(peticion)

    proveedor.completar = anotar  # type: ignore[method-assign]
    asyncio.run(
        configurar(
            f"www.{DOMINIO}", navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico
        )
    )
    assert len(momentos) == 1
    # Portada, portada con Chromium y, como mucho, la primera candidata: no espera a las demás.
    assert momentos[0] <= 3


def test_si_gana_una_candidata_por_http_no_se_espera_a_la_ia() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=_ruta(PATRONES[1]))
    sitio[PORTADA] = SIN_BUSCADOR.replace("</header>", "<a href='/catalogo'>Catálogo</a></header>")
    navegador = NavegadorQueEspera(sitio, paginas_js={})
    sesion_ia, proveedor = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK]})
    completar = proveedor.completar

    async def ia_lenta(peticion: PeticionIA) -> RespuestaIA:
        if peticion.esquema == ESQUEMA_PLANTILLAS:
            await asyncio.sleep(3600)  # No contesta nunca: no hay que esperarla.
        return await completar(peticion)

    proveedor.completar = ia_lenta  # type: ignore[method-assign]

    async def probar() -> Any:
        return await asyncio.wait_for(
            configurar(
                f"www.{DOMINIO}", navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico
            ),
            timeout=5,
        )

    perfil = asyncio.run(probar())
    assert perfil.url_busqueda == TIENDA + PATRONES[1]


def test_con_candidatas_de_la_portada_la_ia_sigue_siendo_el_ultimo_recurso() -> None:
    # La portada tiene un formulario: su búsqueda da un 200 sin resultados por HTTP y se repite
    # con Chromium antes de preguntar a la IA, como siempre (nota 15 §D).
    sitio = paginas_sitio(busqueda=CATALOGO)
    sitio[BUSQUEDA] = SIN_BUSCADOR
    navegador = NavegadorFalso(sitio, paginas_js={})
    perfil, _ = configurar_con(
        navegador, [PROPUESTA, LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}"
    )
    assert perfil.url_busqueda == f"{TIENDA}/catalogo?busca={{consulta}}"
    assert navegador.pedidas.index((BUSQUEDA, True)) < navegador.urls.index(CATALOGO)
