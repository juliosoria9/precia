"""Validador de los perfiles de tiendas sin buscador (nota 16): `listado` (A2) y `sitemap` (B).

- `listado`: la página 1 del listado da al menos `MIN_RESULTADOS_LISTADO` resultados de la tienda
  y 2 de 3 fichas tienen precio anclado, sin exigir que los resultados nombren la consulta.
- `sitemap`: no hay listado que leer; se abren las fichas del catálogo que se le pasan
  (`fichas=`) y valen 2 de 3 con precio anclado, sin IA si bastan el JSON-LD o los metadatos.
"""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest

from precia.navegador.tipos import Bloqueado
from precia.perfiles.tipos import TIPO_LISTADO, TIPO_SITEMAP, InformeValidacion, Perfil
from precia.perfiles.validador import (
    MARCADOR_PAGINA,
    MAX_FICHAS,
    MIN_RESULTADOS_LISTADO,
    problemas_de_url_listado,
    url_de_listado,
    validar,
)
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    LISTADO,
    LISTADO_1,
    PRODUCTOS,
    SITEMAP,
    TIENDA,
    NavegadorFalso,
    html_ficha,
    html_listado,
    paginas_sitio,
    perfil,
    perfil_listado,
    perfil_sitemap,
)


def ejecutar(
    p: Perfil,
    navegador: NavegadorFalso,
    consulta: str = CONSULTA,
    *,
    fichas: Sequence[str] = (),
) -> InformeValidacion:
    return asyncio.run(
        validar(p, navegador, consulta, validador=validar_con_dns_publico, fichas=fichas)
    )


def sitio_listado(**opciones: Any) -> dict[str, str]:
    """La tienda falsa con su listado de faros en la página 1 (y sin búsqueda)."""
    productos = opciones.pop("productos", PRODUCTOS)
    sitio = paginas_sitio(busqueda=None, **opciones)
    sitio[LISTADO_1] = html_listado(productos)
    return sitio


# --- URL del listado ----------------------------------------------------------------------------


def test_url_de_listado() -> None:
    assert MARCADOR_PAGINA == "{pagina}"
    assert url_de_listado(LISTADO, 1) == LISTADO_1
    assert url_de_listado(f"{TIENDA}/faros/page/{{pagina}}/", 3) == f"{TIENDA}/faros/page/3/"
    # Sin `{pagina}` solo hay una página: la URL tal cual.
    assert url_de_listado(f"{TIENDA}/faros", 1) == f"{TIENDA}/faros"


@pytest.mark.parametrize(
    "plantilla",
    [
        f"{TIENDA}/faros",
        LISTADO,
        f"{TIENDA}/faros/page/{{pagina}}/",
        f"https://{DOMINIO}/faros?orden=precio&p={{pagina}}",
    ],
)
def test_urls_de_listado_validas(plantilla: str) -> None:
    assert problemas_de_url_listado(plantilla, DOMINIO) == []


@pytest.mark.parametrize(
    ("plantilla", "motivo"),
    [
        (f"{TIENDA}/buscar?q={{consulta}}", "{consulta}"),
        (f"{TIENDA}/faros?p={{pagina}}&q={{pagina}}", "{pagina}"),
        (f"{TIENDA}/faros?p={{pagina}}&orden={{orden}}", "llave"),
        (f"{TIENDA}/faros?p={{pagina", "llave"),
        (f"https://{{pagina}}.{DOMINIO}/faros", "host"),
        ("https://otra-tienda.es/faros?p={pagina}", DOMINIO),
        (f"https://{DOMINIO}:8080/faros", DOMINIO),
        (f"https://usuario@{DOMINIO}/faros", DOMINIO),
        (f"ftp://{DOMINIO}/faros", DOMINIO),
        ("/faros?p={pagina}", DOMINIO),
        (f"{TIENDA}/faros#p={{pagina}}", "#"),
        (f"{TIENDA}/faros con espacios", "caracteres"),
        (f"{TIENDA}/faros\\@otra-tienda.es", "caracteres"),
        (f"{TIENDA}/faros-ñ", "caracteres"),
        (f"{TIENDA}/" + "a" * 600, "larga"),
    ],
)
def test_urls_de_listado_no_validas(plantilla: str, motivo: str) -> None:
    problemas = problemas_de_url_listado(plantilla, DOMINIO)
    assert problemas
    assert motivo in " ".join(problemas)


# --- Perfil listado (A2) -------------------------------------------------------------------------


def test_listado_valido() -> None:
    navegador = NavegadorFalso(sitio_listado())
    informe = ejecutar(perfil_listado(), navegador)
    assert informe.valido, informe.problemas
    assert informe.resultados == len(PRODUCTOS)
    assert (informe.fichas_ok, informe.fichas_total) == (3, 3)
    # La página 1 del listado (no una búsqueda) y las tres primeras fichas.
    assert navegador.pedidas == [(LISTADO_1, False)] + [(p.url, False) for p in PRODUCTOS[:3]]


def test_listado_no_exige_que_los_resultados_nombren_la_consulta() -> None:
    informe = ejecutar(perfil_listado(), NavegadorFalso(sitio_listado()), "amortiguador bilstein")
    assert informe.valido


def test_listado_sin_pagina_usa_la_url_tal_cual() -> None:
    sitio = paginas_sitio(busqueda=None)
    sitio[f"{TIENDA}/faros"] = html_listado()
    navegador = NavegadorFalso(sitio)
    informe = ejecutar(perfil_listado(url_busqueda=f"{TIENDA}/faros"), navegador)
    assert informe.valido
    assert navegador.urls[0] == f"{TIENDA}/faros"


def test_listado_con_pocos_resultados_no_vale_y_no_abre_fichas() -> None:
    assert MIN_RESULTADOS_LISTADO == 3
    navegador = NavegadorFalso(sitio_listado(productos=PRODUCTOS[:2]))
    informe = ejecutar(perfil_listado(), navegador)
    assert not informe.valido
    assert informe.resultados == 2
    assert any("al menos 3" in p for p in informe.problemas)
    assert navegador.urls == [LISTADO_1]


def test_listado_sin_resultados() -> None:
    navegador = NavegadorFalso(sitio_listado())
    informe = ejecutar(perfil_listado(sel_resultado="ul.no-existe > li"), navegador)
    assert not informe.valido
    assert any("sel_resultado" in p for p in informe.problemas)
    assert navegador.urls == [LISTADO_1]


def test_listado_con_fichas_sin_precio_no_vale() -> None:
    sitio = sitio_listado(jsonld=(), sin_precio=(0, 1))
    informe = ejecutar(perfil_listado(), NavegadorFalso(sitio))
    assert not informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (1, 3)


@pytest.mark.parametrize(
    "url",
    [
        f"{TIENDA}/buscar?q={{consulta}}",
        f"{TIENDA}/faros?p={{pagina}}&q={{pagina}}",
        "https://otra-tienda.es/faros?p={pagina}",
    ],
)
def test_listado_con_url_no_valida_no_descarga_nada(url: str) -> None:
    navegador = NavegadorFalso(sitio_listado())
    informe = ejecutar(perfil_listado(url_busqueda=url), navegador)
    assert not informe.valido
    assert navegador.pedidas == []


def test_listado_sin_selectores_no_vale() -> None:
    navegador = NavegadorFalso(sitio_listado())
    informe = ejecutar(perfil_listado(sel_resultado="", sel_enlace="a:has(b)"), navegador)
    assert not informe.valido
    assert navegador.pedidas == []


def test_listado_que_redirige_fuera() -> None:
    sitio = sitio_listado()
    sitio["https://otra-tienda.es/faros"] = html_listado()
    navegador = NavegadorFalso(sitio, redirecciones={LISTADO_1: "https://otra-tienda.es/faros"})
    informe = ejecutar(perfil_listado(), navegador)
    assert not informe.valido
    assert any("otro dominio" in p for p in informe.problemas)


def test_listado_bloqueado_se_propaga() -> None:
    navegador = NavegadorFalso(sitio_listado(), errores={LISTADO_1: Bloqueado(LISTADO_1, "x")})
    with pytest.raises(Bloqueado):
        ejecutar(perfil_listado(), navegador)


# --- Perfil sitemap (B) --------------------------------------------------------------------------

FICHAS = [p.url for p in PRODUCTOS]


def test_sitemap_valido_con_json_ld_sin_selectores() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=(0, 1, 2)))
    informe = ejecutar(perfil_sitemap(), navegador, fichas=FICHAS)
    assert informe.valido, informe.problemas
    assert (informe.fichas_ok, informe.fichas_total) == (3, 3)
    assert informe.resultados == len(FICHAS)
    # Solo las fichas (como mucho tres), nunca el sitemap: su descarga es cosa del configurador.
    assert navegador.pedidas == [(u, False) for u in FICHAS[:MAX_FICHAS]]
    assert SITEMAP not in navegador.urls


def test_sitemap_dos_de_tres_bastan() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=(0, 2)))
    informe = ejecutar(perfil_sitemap(), navegador, fichas=FICHAS)
    assert informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (2, 3)


def test_sitemap_sin_datos_estructurados_ni_selectores_no_vale() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=(0,)))
    informe = ejecutar(perfil_sitemap(), navegador, fichas=FICHAS)
    assert not informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (1, 3)
    assert any("precio anclado" in p for p in informe.problemas)


def test_sitemap_con_selector_de_precio() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=()))
    p = perfil_sitemap(sel_campos={"precio": CAMPOS_OK["precio"]})
    informe = ejecutar(p, navegador, fichas=FICHAS)
    assert informe.valido


def _con_opengraph(producto: Any) -> str:
    """Ficha sin JSON-LD, pero con el precio en OpenGraph (metadatos)."""
    meta = (
        f'<meta property="og:title" content="{producto.titulo}">'
        f'<meta property="og:url" content="{producto.url}">'
        f'<meta property="product:price:amount" content="{producto.importe}">'
        '<meta property="product:price:currency" content="EUR">'
    )
    return html_ficha(producto).replace("</head>", meta + "</head>")


def test_sitemap_valido_con_metadatos() -> None:
    sitio = paginas_sitio(busqueda=None, jsonld=())
    for producto in PRODUCTOS[:3]:
        sitio[producto.url] = _con_opengraph(producto)
    informe = ejecutar(perfil_sitemap(), NavegadorFalso(sitio), fichas=FICHAS)
    assert informe.valido, informe.problemas


def test_sitemap_sin_fichas_no_vale_y_no_descarga_nada() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=(0, 1, 2)))
    informe = ejecutar(perfil_sitemap(), navegador)
    assert not informe.valido
    assert any("fichas del catálogo" in p for p in informe.problemas)
    assert navegador.pedidas == []


def test_sitemap_las_fichas_de_otro_dominio_no_se_abren() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=(0, 1, 2)))
    fuera = ["https://otra-tienda.es/p/faro", "javascript:alert(1)", f"https://{DOMINIO}:8443/p/x"]
    informe = ejecutar(perfil_sitemap(), navegador, fichas=[*fuera, *FICHAS])
    assert informe.valido
    assert navegador.urls == FICHAS[:MAX_FICHAS]


def test_sitemap_ficha_que_redirige_fuera_no_cuenta() -> None:
    sitio = paginas_sitio(busqueda=None, jsonld=(0, 1, 2))
    sitio["https://otra-tienda.es/p/faro"] = html_ficha(PRODUCTOS[0], jsonld=True)
    navegador = NavegadorFalso(
        sitio,
        redirecciones={
            FICHAS[0]: "https://otra-tienda.es/p/faro",
            FICHAS[1]: "https://otra-tienda.es/p/faro",
        },
    )
    informe = ejecutar(perfil_sitemap(), navegador, fichas=FICHAS)
    assert not informe.valido
    assert any("otro dominio" in p for p in informe.problemas)


@pytest.mark.parametrize(
    "cambios",
    [
        {"url_busqueda": f"{TIENDA}/sitemap.xml?q={{consulta}}"},
        {"url_busqueda": "https://otra-tienda.es/sitemap.xml"},
        {"url_busqueda": "sitemap.xml"},
        {"sel_resultado": "li.producto"},
        {"sel_enlace": "a @href"},
        {"sel_precio_listado": ".precio"},
        {"sel_campos": {"precio": "span:has(b)"}},
        {"sel_campos": {"comando": "body"}},
    ],
)
def test_sitemap_mal_formado_no_descarga_nada(cambios: dict[str, Any]) -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=(0, 1, 2)))
    informe = ejecutar(perfil_sitemap(**cambios), navegador, fichas=FICHAS)
    assert not informe.valido
    assert navegador.pedidas == []


def test_sitemap_bloqueo_en_una_ficha_se_propaga() -> None:
    sitio = paginas_sitio(busqueda=None, jsonld=(0, 1, 2))
    navegador = NavegadorFalso(sitio, errores={FICHAS[1]: Bloqueado(FICHAS[1], "DataDome")})
    with pytest.raises(Bloqueado):
        ejecutar(perfil_sitemap(), navegador, fichas=FICHAS)


def test_las_fichas_no_cambian_un_perfil_html() -> None:
    # `fichas` es solo para los perfiles `sitemap`: uno `html` sigue buscando.
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(), navegador, fichas=["https://otra-tienda.es/p/x"])
    assert informe.valido
    assert "https://otra-tienda.es/p/x" not in navegador.urls


def test_tipos() -> None:
    assert perfil_listado().tipo == TIPO_LISTADO
    assert perfil_sitemap().tipo == TIPO_SITEMAP


def _landing(numero: int) -> str:
    """Landing de marca o SEO: solo un JSON-LD `ItemList` de dos productos de otras URL."""
    elementos = [
        {
            "@type": "ListItem",
            "position": n,
            "item": {
                "@type": "Product",
                "name": f"Faro {numero}-{n}",
                "url": f"{TIENDA}/otro-{numero}-{n}.html",
                "offers": {"price": f"{40 + n}.00", "priceCurrency": "EUR"},
            },
        }
        for n in (1, 2)
    ]
    bloque = json.dumps({"@type": "ItemList", "itemListElement": elementos})
    return (
        f'<html><head><script type="application/ld+json">{bloque}</script></head>'
        f"<body><h1>Faros {numero}</h1><p>41,00 €</p><p>42,00 €</p></body></html>"
    )


def test_sitemap_de_landings_con_itemlist_no_vale() -> None:
    # Campaña multi-sector (causa R2-C10): con el sitemap de landings SEO de Brico Dépôt, las tres
    # «fichas» eran listados con un `ItemList`; se contaban con el precio del primer producto y el
    # perfil salía válido (3/3).
    sitio = paginas_sitio(busqueda=None, jsonld=(0, 1, 2))
    for numero, url in enumerate(FICHAS[:3], 1):
        sitio[url] = _landing(numero)
    informe = ejecutar(perfil_sitemap(), NavegadorFalso(sitio), fichas=FICHAS)
    assert not informe.valido
    assert informe.fichas_ok == 0
    esperado = f"Ficha 1 ({FICHAS[0]}): es un listado (ItemList con 2 productos), no una ficha."
    assert esperado in informe.problemas
