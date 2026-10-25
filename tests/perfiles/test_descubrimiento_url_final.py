"""Causa R3-F2 de la campaña multi-sector: candidatas que acaban en la misma página sin la consulta.

En desguacesdli.com, `/recambios?s=filtro`, `/buscar?q=filtro`, `/search?q=filtro` y las demás
rutas habituales redirigen al catálogo general `/recambios/`, sin la consulta. El descubrimiento
las bajaba todas por HTTP y repetía todas con Chromium (y `/buscar/filtro`, que acaba en una
ficha), 25 peticiones que agotaban el plazo de 120 s antes de llegar al sitemap. Ahora:

- una candidata que redirige a una URL que ya no lleva la consulta no se repite con Chromium y
  no se acepta nunca, aunque la página liste productos que la nombren (el catálogo general no es
  una búsqueda);
- de las candidatas que acaban en la misma URL final por HTTP, solo una se repite con Chromium.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from precia.navegador.tipos import Pagina
from precia.perfiles import descubrimiento
from precia.perfiles.descubrimiento import abrir_portada, descubrir
from precia.perfiles.tipos import ConfiguracionFallida
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import NavegadorFalso

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
CATALOGO_HTML = (REALES / "desguacesdli_catalogo_recambios_sin_consulta.html").read_text(
    encoding="utf-8"
)

DOMINIO = "desguacesdli.com"
TIENDA = f"https://{DOMINIO}"
PORTADA = f"{TIENDA}/"
CONSULTA = "filtro"
CATALOGO = f"{TIENDA}/recambios/"
BUSQUEDA = f"{TIENDA}/recambios/?q=filtro"
FICHA = f"{TIENDA}/recambios/filtro-aceite-bmw-serie-3-berlina-e36-325tds-116263/"
WOOCOMMERCE = f"{TIENDA}/?s=filtro&post_type=product"
FORMULARIO_PORTADA = f"{TIENDA}/?s=filtro"
FORMULARIO_CATALOGO = f"{TIENDA}/recambios?s=filtro"
AL_CATALOGO = (
    FORMULARIO_CATALOGO,
    f"{TIENDA}/busqueda?controller=search&s=filtro",
    f"{TIENDA}/buscar?q=filtro",
    f"{TIENDA}/search?q=filtro",
    f"{TIENDA}/catalogsearch/result/?q=filtro",
)

PORTADA_HTML = (
    "<html><head><title>Desguaces D.L.I.</title></head><body>"
    '<form action="/recambios"><input type="text" name="s"></form>'
    '<form action="/"><input type="text" name="s"></form>'
    "</body></html>"
)
BUSQUEDA_VACIA = (
    "<html><head><title>Recambios - filtro</title></head><body><p>Buscando…</p></body></html>"
)
FICHA_HTML = (
    "<html><head><title>FILTRO ACEITE BMW SERIE 3</title></head><body>"
    "<h1>FILTRO ACEITE BMW SERIE 3</h1><span>36,30 €</span></body></html>"
)


def redirecciones() -> dict[str, str]:
    """Como en la tienda: todo al catálogo, salvo WooCommerce (búsqueda) y `/buscar/` (ficha)."""
    return {
        **dict.fromkeys(AL_CATALOGO, CATALOGO),
        WOOCOMMERCE: BUSQUEDA,
        FORMULARIO_PORTADA: BUSQUEDA,
        f"{TIENDA}/buscar/filtro": FICHA,
    }


def tienda(**cambios: Any) -> NavegadorFalso:
    paginas = {PORTADA: PORTADA_HTML, CATALOGO: CATALOGO_HTML, BUSQUEDA: BUSQUEDA_VACIA}
    paginas[FICHA] = FICHA_HTML
    datos: dict[str, Any] = {"paginas_js": {}, "redirecciones": redirecciones(), **cambios}
    return NavegadorFalso(paginas, **datos)


def descubrir_con(navegador: NavegadorFalso, consulta: str = CONSULTA) -> Any:
    async def probar() -> Any:
        portada = await abrir_portada(
            DOMINIO, DOMINIO, navegador, validador=validar_con_dns_publico
        )
        return await descubrir(
            portada, DOMINIO, DOMINIO, navegador, consulta, validador=validar_con_dns_publico
        )

    return asyncio.run(probar())


def con_chromium(navegador: NavegadorFalso) -> list[str]:
    return [url for url, js in navegador.pedidas if js and url != PORTADA]


def test_desguacesdli_no_repite_con_chromium_lo_que_pierde_la_consulta() -> None:
    navegador = tienda()
    with pytest.raises(ConfiguracionFallida):
        descubrir_con(navegador)
    # Las que acaban en el catálogo o en la ficha sin la consulta se piden solo por HTTP.
    assert {(url, False) for url in AL_CATALOGO} <= set(navegador.pedidas)
    assert (f"{TIENDA}/buscar/filtro", False) in navegador.pedidas
    # De las dos que acaban en `/recambios/?q=filtro`, solo la primera va por Chromium.
    assert con_chromium(navegador) == [FORMULARIO_PORTADA]


def test_el_catalogo_general_no_se_acepta_aunque_parezca_un_listado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simula un `parece_listado` que ya ve las tarjetas del catálogo (con «FILTRO GASOIL…»): ni
    # así es la búsqueda, porque la tienda ha tirado la consulta al redirigir.
    original = descubrimiento.parece_listado

    def laxo(pagina: Pagina, dominio: str, consulta: str, **opciones: Any) -> bool:
        return original(pagina, dominio, consulta, portada=None)

    monkeypatch.setattr(descubrimiento, "parece_listado", laxo)
    with pytest.raises(ConfiguracionFallida):
        descubrir_con(tienda())


def test_una_busqueda_que_conserva_la_consulta_sigue_aceptandose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regresión: el catálogo con `?q=filtro` en la URL final sí es la búsqueda.
    original = descubrimiento.parece_listado

    def laxo(pagina: Pagina, dominio: str, consulta: str, **opciones: Any) -> bool:
        return original(pagina, dominio, consulta, portada=None)

    monkeypatch.setattr(descubrimiento, "parece_listado", laxo)
    redir = {url: BUSQUEDA for url in AL_CATALOGO}
    navegador = tienda(redirecciones=redir)
    navegador.paginas[BUSQUEDA] = CATALOGO_HTML
    busqueda = descubrir_con(navegador)
    assert busqueda.plantilla == f"{TIENDA}/recambios?s={{consulta}}"
    assert not busqueda.necesita_js


def test_una_busqueda_movida_se_sigue_repitiendo_con_chromium() -> None:
    # `/buscar?q=x` → `/search?q=x`: la consulta sigue en la URL final, así que por HTTP no es un
    # listado pero con Chromium sí (lo pinta el JavaScript).
    listado = "".join(
        f'<div><a href="/recambios/filtro-{n}">Filtro de aceite {n}</a> <span>{n},99 €</span></div>'
        for n in range(1, 5)
    )
    movida = f"{TIENDA}/search?q=filtro"
    navegador = NavegadorFalso(
        {PORTADA: "<html><body>Portada</body></html>", movida: BUSQUEDA_VACIA},
        paginas_js={movida: f"<html><title>filtro</title><body>{listado}</body></html>"},
        redirecciones={f"{TIENDA}/buscar?q=filtro": movida},
    )
    busqueda = descubrir_con(navegador)
    assert busqueda.plantilla == f"{TIENDA}/buscar?q={{consulta}}"
    assert busqueda.necesita_js
    # `/search?q=` acaba en la misma URL final por HTTP: no se repite con Chromium.
    assert (movida, True) not in navegador.pedidas


def _pagina(url: str, final: str) -> Pagina:
    return Pagina(url, final, 200, "<html></html>", "text/html", datetime.now(UTC))


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        (f"{TIENDA}/recambios/?q=filtro", True),
        (f"{TIENDA}/recambios/?q=Filtros+de+aceite", True),
        (f"{TIENDA}/buscar/filtro", True),
        (f"{TIENDA}/es/buscar/filtro/", True),
        (f"{TIENDA}/recambios/", False),
        (f"{TIENDA}/recambios/filtro-aceite-bmw-116263/", False),
        (f"{TIENDA}/antifiltro?q=nada", False),
        (f"{TIENDA}/#/buscar?q=filtro", False),  # El fragmento no lo ve el servidor.
    ],
)
def test_consulta_en_url(url: str, esperado: bool) -> None:
    from precia.perfiles.descubrimiento import consulta_en_url

    assert consulta_en_url(url, CONSULTA) is esperado


@pytest.mark.parametrize(
    ("url", "final", "esperado"),
    [
        (FORMULARIO_CATALOGO, CATALOGO, True),  # Misma ruta, consulta tirada.
        (f"{TIENDA}/buscar/filtro", FICHA, True),
        (WOOCOMMERCE, BUSQUEDA, False),  # La consulta sigue.
        (f"{TIENDA}/buscar?q=filtro", f"{TIENDA}/search?q=filtro", False),
        (f"{TIENDA}/search?q=filtro", f"{TIENDA}/search?q=filtro", False),  # Sin redirigir.
        (f"{TIENDA}/search?q=filtro", PORTADA, False),  # A la portada: se deja como estaba.
        (f"{TIENDA}/search?q=filtro", f"{TIENDA}/es-es/", False),  # Prefijo de idioma.
        (f"{TIENDA}/#/buscar?q=filtro", PORTADA, False),  # Plantilla con fragmento (SPA).
        (f"{TIENDA}/buscar/filtro.html", CATALOGO, False),  # No llevaba la consulta entera.
    ],
)
def test_perdio_la_consulta(url: str, final: str, esperado: bool) -> None:
    from precia.perfiles.descubrimiento import perdio_la_consulta

    assert perdio_la_consulta(_pagina(url, final), CONSULTA) is esperado
