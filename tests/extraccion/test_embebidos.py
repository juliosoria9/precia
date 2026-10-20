"""Resultados de búsqueda desde datos estructurados: JSON-LD `ItemList` y JSON embebido."""

import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from precia.extraccion.embebidos import (
    MAX_CANDIDATOS,
    MAX_EVALUADOS,
    MAX_JSON,
    listado_estructurado,
)
from precia.extraccion.tipos import Precio, ResultadoListado
from precia.navegador.tipos import Pagina

FIXTURES = Path(__file__).parent.parent / "fixtures" / "extraccion"
TIENDA = "https://tienda.es"
URL_BUSQUEDA = f"{TIENDA}/buscar?q=filtro"


def _pagina(html: str, url: str = URL_BUSQUEDA) -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html; charset=utf-8",
        obtenida=datetime(2026, 10, 20, tzinfo=UTC),
    )


def _fixture(nombre: str, url: str) -> Pagina:
    return _pagina((FIXTURES / nombre).read_text(encoding="utf-8"), url)


def _json(dato: Any, tipo: str = "application/json") -> str:
    return f'<script type="{tipo}">{json.dumps(dato)}</script>'


def _estado(dato: Any, variable: str = "__INITIAL_STATE__") -> str:
    return f"<script>window.{variable} = {json.dumps(dato)};</script>"


def _listado(html: str, dominio: str = "tienda.es", **opciones: int) -> list[ResultadoListado]:
    return listado_estructurado(_pagina(html), dominio, **opciones)


def _productos(*productos: dict[str, Any]) -> str:
    return _json({"props": {"pageProps": {"products": list(productos)}}})


def test_jsonld_itemlist_de_una_pagina_de_resultados() -> None:
    base = "https://www.desguaces-ejemplo.es"
    pagina = _fixture("listado_jsonld_itemlist.html", f"{base}/buscar?q=faro+golf")
    assert listado_estructurado(pagina, "desguaces-ejemplo.es") == [
        ResultadoListado(
            "Faro delantero izquierdo Golf VI",
            f"{base}/pieza/faro-delantero-izquierdo-golf-vi-77123",
        ),
        ResultadoListado(
            "Faro delantero derecho Golf VI & regulador",
            f"{base}/pieza/faro-delantero-derecho-golf-vi-77124",
            Precio(Decimal("95.00"), "EUR", "95.00"),
        ),
        # El anuncio de otra tienda y el enlace `javascript:` no pasan.
        ResultadoListado(
            "Piloto trasero Golf VI",
            f"{base}/pieza/piloto-trasero-golf-vi-77130",
            Precio(Decimal("35.50"), "EUR", "35,50"),
        ),
    ]


def test_next_data_con_precios_en_varios_formatos() -> None:
    base = "https://www.tienda-next.es"
    pagina = _fixture("listado_next_data.html", f"{base}/buscar?q=filtro+aceite")
    assert listado_estructurado(pagina, "tienda-next.es") == [
        ResultadoListado(
            "Filtro de aceite MANN W 712/75",
            f"{base}/p/filtro-aceite-mann-w712-75",
            Precio(Decimal("12.9"), "EUR", "12.9"),
        ),
        ResultadoListado(
            "Filtro de aceite BOSCH F 026 407 157",
            f"{base}/p/filtro-bosch-f026407157",
            Precio(Decimal("12.90"), "EUR", "12,90 €"),
        ),
        # En céntimos, como la Store API de WooCommerce.
        ResultadoListado(
            "Filtro de aceite MAHLE OC 456",
            f"{base}/p/filtro-mahle-oc456",
            Precio(Decimal("10.95"), "EUR", "1095"),
        ),
        ResultadoListado(
            "Filtro de aceite Purflux LS867",
            f"{base}/p/purflux-ls867",
            Precio(Decimal("8.75"), "EUR", "8.75"),
        ),
    ]


def test_initial_state_de_una_spa() -> None:
    base = "https://suspensiones-ejemplo.es"
    pagina = _fixture("listado_initial_state.html", f"{base}/buscar?q=amortiguador+kyb")
    assert listado_estructurado(pagina, "suspensiones-ejemplo.es") == [
        ResultadoListado(
            "Amortiguador delantero KYB Excel-G 334834",
            f"{base}/amortiguador-delantero-kyb-334834.html",
            Precio(Decimal("54.20"), "EUR", "54.20"),
        ),
        ResultadoListado(
            "Amortiguador trasero KYB Excel-G 343411",
            f"{base}/amortiguador-trasero-kyb-343411.html",
            Precio(Decimal("41.35"), "EUR", "41.35"),
        ),
        ResultadoListado(
            "Copela de amortiguador KYB SM5207",
            f"{base}/copela-kyb-sm5207.html",
            Precio(Decimal("18.90"), "EUR", "18.90"),
        ),
    ]


def test_preloaded_state_y_json_sin_window() -> None:
    html = _estado(
        {"items": [{"title": "Faro", "url": "/p/faro", "price": 10}]}, "__PRELOADED_STATE__"
    )
    html += (
        '<script>__INITIAL_STATE__={"r":[{"name":"Piloto","href":"/p/piloto","price":5}]}</script>'
    )
    assert [r.titulo for r in _listado(html)] == ["Faro", "Piloto"]


def test_maximo_y_parametros_raros() -> None:
    productos = [{"name": f"Filtro {i}", "url": f"/p/{i}", "price": i + 1} for i in range(30)]
    html = _productos(*productos)
    assert len(_listado(html)) == 10
    assert [r.titulo for r in _listado(html, maximo=2)] == ["Filtro 0", "Filtro 1"]
    assert _listado(html, maximo=0) == []
    assert _listado(html, dominio="") == []
    assert _listado(html, dominio="otra.example") == []


def test_url_final_es_la_base() -> None:
    pagina = replace(
        _pagina(_productos({"name": "Faro", "url": "p/faro", "price": 3})),
        url_final="https://www.tienda.es/es/buscar",
    )
    # Una ruta relativa sin «/» es ambigua (¿respecto a qué?): no se adivina.
    assert listado_estructurado(pagina, "tienda.es") == []
    pagina = replace(pagina, html=_productos({"name": "Faro", "url": "/p/faro", "price": 3}))
    assert listado_estructurado(pagina, "tienda.es")[0].url == "https://www.tienda.es/p/faro"


def test_sin_precios_se_devuelven_todos_los_candidatos() -> None:
    html = _productos({"name": "Faro", "url": "/p/faro"}, {"title": "Piloto", "slug": "/p/piloto"})
    assert _listado(html) == [
        ResultadoListado("Faro", f"{TIENDA}/p/faro"),
        ResultadoListado("Piloto", f"{TIENDA}/p/piloto"),
    ]


def test_con_precios_los_que_no_tienen_se_descartan() -> None:
    html = _json({"menu": [{"title": "Contacto", "href": "/contacto"}]})
    html += _productos({"name": "Faro", "url": "/p/faro", "price": "45,90 €"})
    assert [r.titulo for r in _listado(html)] == ["Faro"]


def test_el_itemlist_no_se_filtra_por_precio_y_va_primero() -> None:
    lista = {
        "@type": "ItemList",
        "itemListElement": [{"@type": "ListItem", "url": "/p/a", "name": "A sin precio"}],
    }
    html = _json(lista, "application/ld+json")
    html += _productos({"name": "B", "url": "/p/b", "price": 3}, {"name": "A", "url": "/p/a"})
    assert [r.titulo for r in _listado(html)] == ["A sin precio", "B"]


def test_offercatalog_con_productos_directos_y_ofertas_en_lista() -> None:
    catalogo = {
        "@type": "OfferCatalog",
        "itemListElement": {
            "@type": "Product",
            "name": "Faro",
            "url": f"{TIENDA}/p/faro",
            "offers": [{"price": "abc"}, {"price": 20, "priceCurrency": "usd"}],
        },
    }
    assert _listado(_json(catalogo, "application/ld+json")) == [
        ResultadoListado("Faro", f"{TIENDA}/p/faro", Precio(Decimal("20"), "USD", "20"))
    ]


def test_sin_repetir_y_sin_la_propia_pagina_ni_imagenes() -> None:
    html = _productos(
        {"name": "Faro", "url": "/p/faro", "price": 3},
        {"name": "Faro otra vez", "url": "/p/faro#detalles", "price": 3},
        {"name": "Esta búsqueda", "url": "/buscar?q=filtro", "price": 3},
        {"name": "Foto", "url": "/img/faro.JPG", "price": 3},
        {"name": "Foto", "url": "/img/faro.webp?v=2", "price": 3},
    )
    assert [r.titulo for r in _listado(html)] == ["Faro"]


@pytest.mark.parametrize(
    "enlace",
    [
        "javascript:alert(1)",
        " JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "https://otra.example/p/faro",
        "https://tienda.es@otra.example/p/faro",
        "https://tienda.es.otra.example/p/faro",
        "http://tienda.es:8080/p/faro",
        "ftp://tienda.es/p/faro",
        "faro",
        "",
    ],
)
def test_enlaces_a_otra_web_o_peligrosos(enlace: str) -> None:
    html = _productos({"name": "Faro", "url": enlace, "price": 3})
    lista = {"@type": "ItemList", "itemListElement": [{"url": enlace, "name": "F"}]}
    html += _json(lista, "application/ld+json")
    assert _listado(html) == []


@pytest.mark.parametrize(
    "precio",
    [-3, 0, "-3,00 €", "NaN", "1e309", "Infinity", 10**15, "9" * 60, True, None, [], {}],
)
def test_precios_absurdos(precio: object) -> None:
    html = _productos({"name": "Faro", "url": "/p/faro", "price": precio})
    assert _listado(html) == [ResultadoListado("Faro", f"{TIENDA}/p/faro")]


@pytest.mark.parametrize("literal", ["NaN", "-Infinity", "Infinity", "1e309", "-1e309"])
def test_literales_json_no_finitos(literal: str) -> None:
    html = (
        '<script type="application/json">{"products":[{"name":"Faro","url":"/p/faro",'
        f'"price":{literal}}}]}}</script>'
    )
    assert _listado(html) == [ResultadoListado("Faro", f"{TIENDA}/p/faro")]


@pytest.mark.parametrize(
    ("precio", "importe"),
    [
        ({"amount": 1290, "currency_minor_unit": 2}, "12.90"),
        ({"price": "1290", "currency_minor_unit": 2, "currency_code": "EUR"}, "12.90"),
        ({"centAmount": 4590, "currencyCode": "EUR", "fractionDigits": 2}, "45.90"),
        ({"amount": "15", "currency_minor_unit": 0}, "15"),
        ({"value": "7,50"}, "7.50"),
    ],
)
def test_precios_anidados_y_en_unidades_menores(precio: dict[str, Any], importe: str) -> None:
    [resultado] = _listado(_productos({"name": "Faro", "url": "/p/faro", "price": precio}))
    assert resultado.precio is not None and resultado.precio.importe == Decimal(importe)


@pytest.mark.parametrize(
    "precio",
    [
        {"amount": "12a0", "currency_minor_unit": 2},
        {"amount": 1290, "currency_minor_unit": 40},
        {"amount": 1290, "currency_minor_unit": True},
        {"amount": -1290, "currency_minor_unit": 2},
    ],
)
def test_unidades_menores_raras(precio: dict[str, Any]) -> None:
    [resultado] = _listado(_productos({"name": "Faro", "url": "/p/faro", "price": precio}))
    assert resultado.precio is None


def test_value_suelto_no_es_un_precio() -> None:
    # Un filtro o un contador ({"title": "Bosch", "url": "/marca/bosch", "value": 12}) no tiene
    # precio por llevar «value»: solo cuenta dentro de un objeto de precio.
    html = _productos({"title": "Bosch", "url": "/marca/bosch", "value": 12})
    assert _listado(html) == [ResultadoListado("Bosch", f"{TIENDA}/marca/bosch")]


def test_titulos_limpios_y_recortados() -> None:
    html = _productos(
        {"name": "  Faro &amp; <b>piloto</b>\n Golf ", "url": "/p/1", "price": 1},
        {"title": "x" * 1_000, "url": "/p/2", "price": 1},
        {"name": "   ", "url": "/p/3", "price": 1},
        {"name": 12, "url": "/p/4", "price": 1},
        {"name": "Válido", "url": {"href": "/p/5"}, "price": 1},
    )
    resultados = _listado(html)
    assert [r.url for r in resultados] == [f"{TIENDA}/p/1", f"{TIENDA}/p/2"]
    assert resultados[0].titulo == "Faro & piloto Golf"
    assert len(resultados[1].titulo) <= 300


@pytest.mark.parametrize(
    "script",
    [
        "<script>window.__INITIAL_STATE__ = {router: {path: '/buscar'}};</script>",
        "<script>window.__INITIAL_STATE__ = JSON.parse('{}');</script>",
        "<script>window.__INITIAL_STATE__ = ;</script>",
        "<script>window.__INITIAL_STATE__ = 42;</script>",
        '<script type="application/json">{"roto": </script>',
        '<script type="application/json"></script>',
        '<script type="application/ld+json">[1, 2, {"@type": "ItemList"}]</script>',
        '<script type="application/ld+json">{"@type": "ItemList", "itemListElement": 7}</script>',
    ],
)
def test_json_roto_o_inesperado_se_ignora(script: str) -> None:
    assert _listado(script) == []


def test_json_gigante_se_ignora_sin_tardar() -> None:
    relleno = [{"name": "Relleno", "sku": "x" * 90}] * 200_000  # unos 20 MB
    texto = json.dumps({"products": [{"name": "Faro", "url": "/p/faro", "price": 3}, *relleno]})
    assert len(texto) > 20_000_000
    inicio = time.perf_counter()
    assert _listado(f'<script id="__NEXT_DATA__" type="application/json">{texto}</script>') == []
    assert time.perf_counter() - inicio < 10


def test_json_justo_por_encima_del_limite_se_ignora() -> None:
    producto = {"name": "Faro", "url": "/p/faro", "price": 3}
    texto = json.dumps({"products": [producto], "relleno": "x" * MAX_JSON})
    assert _listado(f'<script type="application/json">{texto}</script>') == []
    assert _listado(_estado(json.loads(texto))) == []


def test_anidamiento_de_diez_mil_niveles() -> None:
    profundo = '{"a":' * 10_000 + "1" + "}" * 10_000
    estado = "[" * 10_000 + "]" * 10_000
    inicio = time.perf_counter()
    assert _listado(f'<script type="application/json">{profundo}</script>') == []
    assert _listado(f"<script>window.__INITIAL_STATE__ = {estado};</script>") == []
    assert time.perf_counter() - inicio < 5


def _anidado(niveles: int) -> dict[str, Any]:
    dato: dict[str, Any] = {"name": "Faro", "url": "/p/faro", "price": 3}
    for _ in range(niveles):
        dato = {"hijo": dato}
    return dato


def test_profundidad_maxima() -> None:
    assert len(_listado(_json(_anidado(30)))) == 1
    # Más allá de 40 niveles no se busca, pero no se rompe.
    assert _listado(_json(_anidado(60))) == []


def test_numero_de_nodos_limitado() -> None:
    relleno = [{"a": 1}] * 60_000
    html = _json({"relleno": relleno, "products": [{"name": "Faro", "url": "/p/f", "price": 1}]})
    inicio = time.perf_counter()
    assert _listado(html) == []
    assert time.perf_counter() - inicio < 5


def test_entero_de_miles_de_cifras_no_rompe() -> None:
    html = '<script type="application/json">{"products":[{"name":"Faro","url":"/p/f","price":'
    html += "9" * 5_000 + "}]}</script>"
    assert _listado(html) == []


def test_marcadores_repetidos_no_son_cuadraticos() -> None:
    hostil = "<script>" + "window.__INITIAL_STATE__ " * 200_000 + "</script>"
    hostil += "<script>" + "__PRELOADED_STATE__=" * 200_000 + "</script>"
    inicio = time.perf_counter()
    assert _listado(hostil) == []
    assert time.perf_counter() - inicio < 5


def test_muchos_scripts_json() -> None:
    html = '<script type="application/json">{"a": 1}</script>' * 5_000
    html += _productos({"name": "Tarde", "url": "/p/t", "price": 1})
    inicio = time.perf_counter()
    assert _listado(html) == []
    assert time.perf_counter() - inicio < 5


# --- Revisión: contenedores, otras webs y unidades menores ------------------------------------


def test_un_contenedor_sin_precio_no_tapa_los_productos() -> None:
    # Next.js con el bloque de resultados como objeto con título y ruta propios: no es una ficha
    # y los productos están dentro.
    html = _json(
        {
            "props": {
                "pageProps": {
                    "search": {
                        "title": "Resultados para «faro»",
                        "path": "/resultados/faro",
                        "products": [
                            {"name": "Faro izquierdo", "url": "/p/faro-izq", "price": 120},
                            {"name": "Faro derecho", "url": "/p/faro-der", "price": 95.5},
                        ],
                    }
                }
            }
        }
    )
    assert [r.titulo for r in _listado(html)] == ["Faro izquierdo", "Faro derecho"]


def test_un_producto_con_precio_no_mete_sus_variantes() -> None:
    variantes = [{"title": "Izquierdo", "url": "/p/faro?v=1", "price": 120}]
    html = _productos({"name": "Faro", "url": "/p/faro", "price": 120, "variants": variantes})
    assert [r.url for r in _listado(html)] == [f"{TIENDA}/p/faro"]


@pytest.mark.parametrize("clave", ["url", "href", "link", "permalink"])
def test_un_objeto_que_enlaza_a_otra_web_no_cuenta_aunque_traiga_ruta(clave: str) -> None:
    # Anuncio o bloque de afiliados: la ficha es de otra tienda aunque haya una ruta local
    # (redirección de seguimiento) en otra clave.
    anuncio: dict[str, Any] = {"name": "Faro (otra tienda)", clave: "https://otra.example/p/faro"}
    anuncio |= {"path": "/out/123", "price": 80}
    propio = {"name": "Faro", "url": "/p/faro", "price": 120}
    assert [r.titulo for r in _listado(_productos(anuncio, propio))] == ["Faro"]


def test_en_el_itemlist_un_producto_de_otra_web_no_cuenta() -> None:
    lista = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "url": "https://tienda.es/out?id=9",
                "item": {
                    "@type": "Product",
                    "name": "Faro patrocinado",
                    "url": "https://otra.example/p/9",
                },
            },
            {"@type": "ListItem", "position": 2, "url": "/p/faro", "name": "Faro"},
        ],
    }
    html = _json(lista, "application/ld+json")
    assert [r.titulo for r in _listado(html)] == ["Faro"]


def test_unidades_menores_con_tres_decimales() -> None:
    html = _productos(
        {"name": "Faro", "url": "/p/faro", "price": {"amount": 12900, "fractionDigits": 3}}
    )
    assert _listado(html) == [
        ResultadoListado("Faro", f"{TIENDA}/p/faro", Precio(Decimal("12.900"), "EUR", "12900"))
    ]


@pytest.mark.parametrize("precio", ["1.500", "12.900"])
def test_precio_de_maquina_ambiguo_no_se_adivina(precio: str) -> None:
    html = _productos({"name": "Faro", "url": "/p/faro", "price": precio})
    assert _listado(html) == [ResultadoListado("Faro", f"{TIENDA}/p/faro")]


def test_un_menu_largo_sin_precios_no_agota_los_candidatos() -> None:
    # El layout de Next.js trae el árbol de categorías antes que los resultados: cientos de
    # objetos con título y enlace, sin precio.
    menu = [{"title": f"Categoría {i}", "url": f"/c/{i}"} for i in range(MAX_CANDIDATOS + 50)]
    productos = [{"name": "Faro", "url": "/p/faro", "price": 120}]
    html = _json({"props": {"pageProps": {"menu": menu, "search": {"products": productos}}}})
    assert _listado(html) == [
        ResultadoListado("Faro", f"{TIENDA}/p/faro", Precio(Decimal("120"), "EUR", "120"))
    ]


@pytest.mark.parametrize(
    "objeto",
    [
        {"name": "<b>Faro</b>", "url": "https://otra.example/p"},
        {"name": "<b>Faro</b>", "url": "/img/faro.jpg"},
        {"name": "<b>Faro</b>"},
        {"url": "/p/faro"},
    ],
)
def test_miles_de_objetos_que_no_son_fichas_no_tardan(objeto: dict[str, str]) -> None:
    objetos = json.dumps([objeto] * 25_000)
    html = "".join(f'<script type="application/json">{objetos}</script>' for _ in range(5))
    inicio = time.perf_counter()
    assert _listado(html) == []
    assert time.perf_counter() - inicio < 0.8


@pytest.mark.parametrize("formato", ["itemlist", "embebido"])
def test_miles_de_enlaces_validos_con_titulo_vacio_no_tardan(formato: str) -> None:
    # Cada uno pasa los filtros baratos y obliga a normalizar la URL y a analizar el título;
    # sin un cupo total de objetos evaluados, unos MB de esto tardaban varios segundos.
    elemento = {"name": "<b></b>", "url": "/p/faro"}
    if formato == "itemlist":
        dato: Any = [{"@type": "ItemList", "itemListElement": [elemento] * 200}] * 300
        tipo = "application/ld+json"
    else:
        dato, tipo = [elemento] * 25_000, "application/json"
    html = _json(dato, tipo) * 2
    inicio = time.perf_counter()
    assert _listado(html) == []
    assert time.perf_counter() - inicio < 1


def test_un_selector_de_vehiculos_enorme_no_agota_el_cupo() -> None:
    # Tiendas de recambios: el selector de marca y modelo viaja en scripts JSON propios, con
    # miles de entradas, antes que los datos de la página.
    mitad = MAX_EVALUADOS // 2 + 10  # Cada script cabe en MAX_NODOS; los dos pasan del cupo.
    selectores = "".join(
        _json({"modelos": [{"name": f"Modelo {i}", "slug": f"m-{i}"} for i in range(mitad)]})
        for _ in range(2)
    )
    html = selectores + _productos({"name": "Faro", "url": "/p/faro", "price": 120})
    assert [r.titulo for r in _listado(html)] == ["Faro"]
