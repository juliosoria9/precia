"""Descubrir tiendas con Google dentro de una búsqueda (H5). Sin red."""

import asyncio
from typing import Any

from precia.fuentes.google import url_busqueda
from precia.navegador.tipos import Bloqueado, Pagina
from precia.nucleo.modelos import EstadoPaso, Modo, ProgresoTienda, ResultadoBusqueda
from tests.nucleo.test_orquestador import montar, nuevo_almacen, ofertas, progreso
from tests.perfiles.sitio import CONSULTA, DOMINIO, PRODUCTOS, NavegadorFalso, paginas_sitio, perfil

NUEVA = "otra-tienda.es"
FICHA_NUEVA = f"https://www.{NUEVA}/p/faro-golf"
GOOGLE = url_busqueda(CONSULTA)
SHOPPING = url_busqueda(CONSULTA, shopping=True)
SERP = (
    f'<html><body><div id="search"><a href="{FICHA_NUEVA}"><h3>Faro Golf - Otra Tienda</h3></a>'
    f'<a href="{PRODUCTOS[0].url}"><h3>Faro en la tienda de siempre</h3></a>'
    '<a href="https://www.youtube.com/watch?v=1"><h3>Vídeo</h3></a></div></body></html>'
)
FICHA = (
    '<html><head><script type="application/ld+json">{"@type": "Product", "name": "Faro Golf",'
    ' "offers": {"@type": "Offer", "price": "45.00", "priceCurrency": "EUR"}}</script></head>'
    "<body><h1>Faro Golf</h1><p>45,00 €</p></body></html>"
)
VACIA = "<html><body></body></html>"


def navegador(**cambios: Any) -> NavegadorFalso:
    paginas = {**paginas_sitio(), GOOGLE: SERP, SHOPPING: VACIA, FICHA_NUEVA: FICHA}
    return NavegadorFalso(paginas, paginas_js={}, **cambios)


def pasos_google(resultado: ResultadoBusqueda) -> ProgresoTienda:
    assert resultado.progreso[0].tienda == "Google"
    return resultado.progreso[0]


def test_desactivado_en_ajustes_no_consulta_google() -> None:
    nav = navegador()
    m = montar(nuevo_almacen(perfil()), nav)
    r = m.buscar(modo=Modo.CLASICO, descubrir=True)
    [paso] = pasos_google(r).pasos
    assert paso.nombre == "descubrir" and paso.estado is EstadoPaso.AVISO
    assert "desactivado" in paso.detalle
    assert not any("google.com" in url for url in nav.urls)
    assert m.almacen.tienda(NUEVA) is None


def test_sin_pedirlo_no_se_consulta_google_aunque_este_activado() -> None:
    nav = navegador()
    m = montar(nuevo_almacen(perfil()), nav, google_activado=True)
    r = m.buscar(modo=Modo.CLASICO)
    assert all(p.tienda != "Google" for p in r.progreso)
    assert not any("google.com" in url for url in nav.urls)


def test_da_de_alta_la_tienda_descubierta_y_la_busca_en_la_misma_busqueda() -> None:
    nav = navegador()
    m = montar(nuevo_almacen(perfil()), nav, google_activado=True)
    r = m.buscar(modo=Modo.CLASICO, descubrir=True)

    tienda = m.almacen.tienda(NUEVA)
    assert tienda is not None and tienda.nombre == NUEVA
    google = pasos_google(r)
    assert all(p.nombre != "descubrir" for p in google.pasos)
    nuevas = next(p for p in google.pasos if p.nombre == "tiendas nuevas")
    assert nuevas.estado is EstadoPaso.OK and NUEVA in nuevas.detalle
    # La tienda nueva entra en esta búsqueda, junto a la de siempre, que sigue dando ofertas.
    assert {p.tienda for p in r.progreso} >= {"Google", DOMINIO, NUEVA}
    assert len(ofertas(r)) >= len(PRODUCTOS)
    # Google se pide con Chromium; la tienda ya conocida no se vuelve a abrir como candidata.
    assert (GOOGLE, True) in nav.pedidas and (SHOPPING, True) in nav.pedidas
    assert (PRODUCTOS[0].url, False) not in nav.pedidas[: nav.pedidas.index((FICHA_NUEVA, False))]


def test_con_tiendas_elegidas_la_descubierta_tambien_se_busca() -> None:
    m = montar(nuevo_almacen(perfil()), navegador(), google_activado=True)
    r = m.buscar(modo=Modo.CLASICO, descubrir=True, tiendas=(DOMINIO,))
    assert {p.tienda for p in r.progreso} >= {DOMINIO, NUEVA}
    # No es una tienda sin dar de alta que se ignore.
    assert not any("no están dadas de alta" in p.detalle for t in r.progreso for p in t.pasos)


def test_si_google_bloquea_la_busqueda_sigue_con_las_tiendas_de_siempre() -> None:
    nav = navegador(errores={GOOGLE: Bloqueado(GOOGLE, "La tienda pide resolver un CAPTCHA.")})
    m = montar(nuevo_almacen(perfil()), nav, google_activado=True)
    r = m.buscar(modo=Modo.CLASICO, descubrir=True)
    [paso] = pasos_google(r).pasos
    assert paso.nombre == "descubrir" and paso.estado is EstadoPaso.ERROR
    assert "no se intenta resolver" in paso.detalle
    assert m.almacen.tienda(NUEVA) is None
    assert len(ofertas(r)) == len(PRODUCTOS)
    assert SHOPPING not in nav.urls


def test_en_modo_clasico_avisa_de_que_la_tienda_nueva_no_tendra_perfil_con_ia() -> None:
    m = montar(nuevo_almacen(perfil()), navegador(), google_activado=True)
    r = m.buscar(modo=Modo.CLASICO, descubrir=True)
    nuevas = next(p for p in pasos_google(r).pasos if p.nombre == "tiendas nuevas")
    assert "Auto o IA" in nuevas.detalle


class NavegadorColgado(NavegadorFalso):
    """Google no responde nunca."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        if "google.com" in url:
            await asyncio.Event().wait()
        return await super().obtener(url, js=js, grande=grande)


def test_el_descubrimiento_tiene_su_plazo_y_la_busqueda_sigue() -> None:
    nav = NavegadorColgado(paginas_sitio(), paginas_js={})
    m = montar(nuevo_almacen(perfil()), nav, google_activado=True, plazo_descubrir_s=0.05)
    r = m.buscar(modo=Modo.CLASICO, descubrir=True)
    [paso] = pasos_google(r).pasos
    assert paso.nombre == "descubrir" and paso.estado is EstadoPaso.ERROR
    assert "plazo" in paso.detalle.lower()
    assert len(ofertas(r)) == len(PRODUCTOS)


def test_una_tienda_ya_dada_de_alta_no_se_duplica() -> None:
    almacen = nuevo_almacen(perfil(), tiendas=(DOMINIO, NUEVA))
    nav = navegador()
    m = montar(almacen, nav, google_activado=True)
    r = m.buscar(modo=Modo.CLASICO, descubrir=True)
    assert [t.dominio for t in m.almacen.tiendas()].count(NUEVA) == 1
    assert FICHA_NUEVA not in nav.urls
    assert progreso(r, "Google").pasos
