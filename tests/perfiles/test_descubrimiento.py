"""Descubrimiento de la búsqueda (nota 15 §D): OpenSearch → formularios → enlaces → plataforma →
patrones → 🤖 IA, y el perfil `api_*` de WooCommerce y Shopify sin llamar a la IA."""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from precia.ia.simulado import Guion
from precia.ia.tipos import IANoDisponible, PresupuestoAgotado
from precia.navegador.bloqueo import ESTADOS_SIN_RESCATE
from precia.navegador.tipos import Bloqueado, Pagina
from precia.perfiles.configurador import AUTOR, COMPONENTE, configurar
from precia.perfiles.descubrimiento import (
    ESQUEMA_PLANTILLAS,
    MAX_ENLACES,
    MAX_PLANTILLAS_ENLACES,
    MAX_PLANTILLAS_IA,
    ORIGEN_ENLACE,
    ORIGEN_OPENSEARCH,
    PARAMETROS_ENLACE,
    PATRONES,
    RECHAZOS,
    enlace_opensearch,
    leer_plantillas_ia,
    parece_listado,
    plantilla_opensearch,
    plantillas_de_enlaces,
    resumen_portada,
)
from precia.perfiles.tipos import (
    TIPO_API_SHOPIFY,
    TIPO_API_WOOCOMMERCE,
    ConfiguracionFallida,
    Perfil,
)
from precia.perfiles.validador import url_de_busqueda
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.plataformas import (
    SHOPIFY,
    SHOPIFY_API,
    SHOPIFY_PLANTILLA,
    SHOPIFY_PORTADA,
    WOO,
    WOO_API,
    WOO_PLANTILLA,
    WOO_PORTADA,
    paginas_shopify,
    paginas_woo,
)
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    INYECCION,
    LISTADO_OK,
    PLANTILLA,
    PORTADA,
    TIENDA,
    NavegadorFalso,
    html_listado,
    html_portada,
    pagina,
    paginas_sitio,
    sesion,
)

DESCRIPTOR = f"{TIENDA}/opensearch.xml"
ENLACE_OPENSEARCH = (
    '<link rel="search" type="application/opensearchdescription+xml" '
    'title="Recambios Prueba" href="/opensearch.xml">'
)
SIN_BUSCADOR = (
    "<html><head><title>Recambios Prueba</title></head><body>"
    "<header><a href='/'>Recambios Prueba</a><a href='/contacto'>Contacto</a></header>"
    "<h2>Bienvenido</h2></body></html>"
)


def descriptor(*urls: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        "<ShortName>Recambios Prueba</ShortName>" + "".join(urls) + "</OpenSearchDescription>"
    )


def url_os(template: str, tipo: str = "text/html", extra: str = "") -> str:
    return f'<Url type="{tipo}" template="{template}"{extra}/>'


def con_cabeza(html: str, cabeza: str) -> str:
    return html.replace("</head>", cabeza + "</head>", 1)


def configurar_con(
    navegador: NavegadorFalso, guion: list[Guion], *, dominio: str = DOMINIO, tope: int = 20
) -> tuple[Perfil, list[Any]]:
    sesion_ia, proveedor = sesion({COMPONENTE: guion}, tope=tope)
    perfil = asyncio.run(
        configurar(dominio, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
    )
    return perfil, proveedor.peticiones


def fallo_con(
    navegador: NavegadorFalso, guion: list[Guion], *, dominio: str = DOMINIO
) -> tuple[str, list[Any]]:
    sesion_ia, proveedor = sesion({COMPONENTE: guion})
    with pytest.raises(ConfiguracionFallida) as error:
        asyncio.run(
            configurar(dominio, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
        )
    return str(error.value), proveedor.peticiones


# --- OpenSearch ---------------------------------------------------------------------------------


def test_enlace_opensearch_de_la_portada() -> None:
    portada = pagina(PORTADA, con_cabeza(html_portada(), ENLACE_OPENSEARCH))
    assert enlace_opensearch(portada, DOMINIO) == DESCRIPTOR


@pytest.mark.parametrize(
    "enlace",
    [
        '<link rel="search" type="application/opensearchdescription+xml" '
        'href="https://otra-tienda.es/opensearch.xml">',
        '<link rel="alternate" type="application/opensearchdescription+xml" href="/os.xml">',
        '<link rel="search" type="text/html" href="/buscar">',
        '<link rel="search" type="application/opensearchdescription+xml" href="javascript:x">',
    ],
)
def test_enlaces_opensearch_que_no_valen(enlace: str) -> None:
    portada = pagina(PORTADA, con_cabeza(html_portada(), enlace))
    assert enlace_opensearch(portada, DOMINIO) is None


@pytest.mark.parametrize(
    ("urls", "esperada"),
    [
        (
            [url_os(f"{TIENDA}/buscar?q={{searchTerms}}&amp;pagina={{startPage?}}")],
            f"{TIENDA}/buscar?q={{consulta}}",
        ),
        (
            # Las sugerencias en JSON no son la página de resultados: se usa la de HTML.
            [
                url_os(f"{TIENDA}/sugerir?q={{searchTerms}}", "application/x-suggestions+json"),
                url_os(f"{TIENDA}/buscar/{{searchTerms}}"),
            ],
            f"{TIENDA}/buscar/{{consulta}}",
        ),
        (
            # Una ruta relativa se resuelve contra el descriptor.
            [url_os("/catalogo?texto={searchTerms}&amp;orden=precio")],
            f"{TIENDA}/catalogo?texto={{consulta}}&orden=precio",
        ),
    ],
)
def test_plantilla_opensearch(urls: list[str], esperada: str) -> None:
    assert plantilla_opensearch(pagina(DESCRIPTOR, descriptor(*urls)), DOMINIO) == esperada


@pytest.mark.parametrize(
    "url",
    [
        url_os("https://otra-tienda.es/buscar?q={searchTerms}"),
        url_os(f"{TIENDA}/buscar?q={{searchTerms}}&amp;idioma={{language}}"),
        url_os(f"{TIENDA}/buscar?q={{searchTerms}}&amp;b={{searchTerms}}"),
        url_os(f"{TIENDA}/buscar?q=fijo"),
        url_os(f"{TIENDA}/buscar?q={{searchTerms}}", extra=' method="post"'),
        url_os(f"https://usuario@{DOMINIO}/buscar?q={{searchTerms}}"),
        url_os(f"{TIENDA}/buscar#q={{searchTerms}}"),
        url_os(f"{TIENDA}/buscar?q={{searchTerms}}&amp;x=a b"),
        url_os(f"{TIENDA}/buscar/a b/{{searchTerms}}"),
        url_os(f"https://otra-tienda.es\\@{DOMINIO}/buscar?q={{searchTerms}}"),
    ],
    ids=[
        "otro-dominio",
        "obligatorio",
        "dos-veces",
        "sin-terminos",
        "post",
        "usuario",
        "fragmento",
        "espacio-en-la-consulta",
        "espacio-en-la-ruta",
        "barra-invertida",
    ],
)
def test_plantillas_opensearch_que_no_valen(url: str) -> None:
    assert plantilla_opensearch(pagina(DESCRIPTOR, descriptor(url)), DOMINIO) is None


def test_opensearch_con_entidades_no_las_expande() -> None:
    # Un «billion laughs» en el descriptor: el analizador propio no define entidades.
    entidades = '<!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    html = entidades + descriptor(url_os(f"{TIENDA}/buscar?q={{searchTerms}}&amp;x=&b;"))
    inicio = time.perf_counter()
    plantilla = plantilla_opensearch(pagina(DESCRIPTOR, html), DOMINIO)
    assert time.perf_counter() - inicio < 1
    assert plantilla is None or "aaaaaaaaaa" not in plantilla


def test_opensearch_va_antes_que_el_formulario() -> None:
    sitio = paginas_sitio()
    sitio[PORTADA] = con_cabeza(sitio[PORTADA], ENLACE_OPENSEARCH)
    sitio[DESCRIPTOR] = descriptor(url_os(f"{TIENDA}/catalogo?texto={{searchTerms}}"))
    sitio[f"{TIENDA}/catalogo?texto=faro+golf"] = html_listado()
    navegador = NavegadorFalso(sitio)
    perfil, _ = configurar_con(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.url_busqueda == f"{TIENDA}/catalogo?texto={{consulta}}"
    assert navegador.urls[:3] == [PORTADA, DESCRIPTOR, f"{TIENDA}/catalogo?texto=faro+golf"]
    assert BUSQUEDA not in navegador.urls


# --- Enlaces de búsqueda -----------------------------------------------------------------------


def test_enlace_sin_parametro_prueba_los_nombres_habituales() -> None:
    portada = pagina(PORTADA, "<html><body><a href='/es/search'>Buscar</a></body></html>")
    assert plantillas_de_enlaces(portada, DOMINIO) == [
        f"{TIENDA}/es/search?{nombre}={{consulta}}" for nombre in PARAMETROS_ENLACE
    ]
    assert PARAMETROS_ENLACE == ("q", "s", "query", "search", "keyword", "text")


def test_enlace_con_parametro_conserva_los_demas() -> None:
    html = "<a href='/buscar?cat=3&q=frenos&q=otra&utm_source=x'>Frenos</a>"
    portada = pagina(PORTADA, f"<html><body>{html}</body></html>")
    assert plantillas_de_enlaces(portada, DOMINIO) == [f"{TIENDA}/buscar?cat=3&q={{consulta}}"]


@pytest.mark.parametrize(
    "href",
    [
        "https://otra-tienda.es/search",
        "javascript:search()",
        "/blog/como-elegir-un-faro",
        "/p/faro?ref=search",
        "#search",
        f"https://usuario@{DOMINIO}/search",
    ],
)
def test_enlaces_que_no_son_de_busqueda(href: str) -> None:
    portada = pagina(PORTADA, f"<html><body><a href='{href}'>Buscar</a></body></html>")
    assert plantillas_de_enlaces(portada, DOMINIO) == []


def test_los_enlaces_de_busqueda_tienen_tope() -> None:
    enlaces = "".join(f"<a href='/buscar/seccion{n}?q=x'>{n}</a>" for n in range(50))
    portada = pagina(PORTADA, f"<html><body>{enlaces}</body></html>")
    plantillas = plantillas_de_enlaces(portada, DOMINIO)
    assert plantillas == [f"{TIENDA}/buscar/seccion{n}?q={{consulta}}" for n in range(MAX_ENLACES)]


def test_dos_enlaces_sin_parametro_no_multiplican_las_descargas() -> None:
    # Cada plantilla cuesta dos descargas (HTTP y Chromium): dos enlaces «desnudos» darían 12.
    html = "<a href='/es/search'>Buscar</a><a href='/buscador/avanzado'>Avanzada</a>"
    portada = pagina(PORTADA, f"<html><body>{html}</body></html>")
    plantillas = plantillas_de_enlaces(portada, DOMINIO)
    assert len(plantillas) == MAX_PLANTILLAS_ENLACES == len(PARAMETROS_ENLACE)
    assert all("/es/search?" in p for p in plantillas)


def test_portada_enorme_de_enlaces_no_tarda() -> None:
    relleno = "".join(f"<a href='/categoria/searchable-{n}/'>x</a>" for n in range(60_000))
    portada = pagina(PORTADA, f"<html><body>{relleno}</body></html>")
    inicio = time.perf_counter()
    plantillas_de_enlaces(portada, DOMINIO)
    resumen_portada(portada, DOMINIO)
    assert time.perf_counter() - inicio < 3


def test_tienda_sin_formulario_encuentra_la_busqueda_por_un_enlace() -> None:
    # Una tienda Next.js: el buscador se pinta con JavaScript, pero la cabecera enlaza a él.
    sitio = paginas_sitio(formulario=False, busqueda=f"{TIENDA}/es/search?query=faro+golf")
    sitio[PORTADA] = sitio[PORTADA].replace("</header>", "<a href='/es/search'>Buscar</a></header>")
    navegador = NavegadorFalso(sitio)
    perfil, _ = configurar_con(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.url_busqueda == f"{TIENDA}/es/search?query={{consulta}}"
    assert navegador.pedidas[:4] == [
        (PORTADA, False),
        (f"{TIENDA}/es/search?q=faro+golf", False),
        (f"{TIENDA}/es/search?s=faro+golf", False),
        (f"{TIENDA}/es/search?query=faro+golf", False),
    ]
    assert ORIGEN_ENLACE != ORIGEN_OPENSEARCH


def test_sin_formulario_ni_enlaces_mira_la_portada_con_chromium() -> None:
    sitio = paginas_sitio()
    renderizada = sitio[PORTADA]
    sitio[PORTADA] = SIN_BUSCADOR
    navegador = NavegadorFalso(sitio, paginas_js={PORTADA: renderizada})
    perfil, _ = configurar_con(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.url_busqueda == PLANTILLA
    assert navegador.pedidas[:3] == [(PORTADA, False), (PORTADA, True), (BUSQUEDA, False)]
    assert not perfil.necesita_js


def test_con_formulario_no_se_renderiza_la_portada() -> None:
    navegador = NavegadorFalso(paginas_sitio(), paginas_js={})
    configurar_con(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert (PORTADA, True) not in navegador.pedidas


def test_las_rutas_de_la_plataforma_van_antes_que_los_patrones() -> None:
    magento = f"{TIENDA}/catalogsearch/result/?q=faro+golf"
    sitio = paginas_sitio(formulario=False, busqueda=magento)
    sitio[PORTADA] = con_cabeza(
        SIN_BUSCADOR, '<script type="text/x-magento-init">{"*": {}}</script>'
    ).replace("<body>", "<body data-mage-init='{\"x\": {}}'>")
    navegador = NavegadorFalso(sitio)
    perfil, _ = configurar_con(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.url_busqueda == f"{TIENDA}/catalogsearch/result/?q={{consulta}}"
    # Portada, portada con Chromium (no hay) y la ruta de Magento antes que PATRONES[0].
    assert navegador.pedidas[:3] == [(PORTADA, False), (PORTADA, True), (magento, False)]
    assert PATRONES[0] != "/catalogsearch/result/?q={consulta}"


# --- 🤖 Propuesta de la IA ----------------------------------------------------------------------


def test_leer_plantillas_de_la_ia() -> None:
    datos = {
        "plantillas": [
            "/catalogo?busca={consulta}",
            "https://otra-tienda.es/buscar?q={consulta}",
            f"{TIENDA}/buscar?q={{consulta}}#{{consulta}}",
        ]
    }
    assert leer_plantillas_ia(datos, PORTADA, DOMINIO) == [f"{TIENDA}/catalogo?busca={{consulta}}"]


@pytest.mark.parametrize(
    "plantilla",
    [
        "https://otra-tienda.es/buscar?q={consulta}",
        f"https://{DOMINIO}.otra-tienda.es/buscar?q={{consulta}}",
        f"https://otra-tienda.es\\@{DOMINIO}/buscar?q={{consulta}}",
        f"https://{DOMINIO}@otra-tienda.es/buscar?q={{consulta}}",
        f"{TIENDA}/buscar?q=fijo",
        f"{TIENDA}/buscar?q={{consulta}}&b={{consulta}}",
        f"{TIENDA}/buscar?q={{consulta}}&idioma={{language}}",
        f"{TIENDA}/buscar#q={{consulta}}",
        f"{TIENDA}/buscar?q={{consulta}} OR 1=1",
        f'{TIENDA}/buscar?q={{consulta}}"><script>',
        f"{TIENDA}:8080/buscar?q={{consulta}}",
        "javascript:alert(1)//{consulta}",
        "file:///etc/passwd?{consulta}",
        f"{TIENDA}/búsqueda?q={{consulta}}",
        f"{TIENDA}/buscar?q={{consulta}}\x00",
        "x" * 600 + "{consulta}",
        # {consulta} en el host: con «prueba» da recambios-prueba.es, pero con otra consulta
        # sería otra web (recambios-x.es). Revisión adversarial.
        "https://recambios-{consulta}.es/buscar?q=faro",
        "https://www.recambios-{consulta}.es/",
        "//recambios-{consulta}.es/buscar",
    ],
)
def test_plantillas_de_la_ia_que_se_descartan(plantilla: str) -> None:
    assert leer_plantillas_ia({"plantillas": [plantilla]}, PORTADA, DOMINIO) == []


@pytest.mark.parametrize("datos", [{}, {"plantillas": "x"}, {"plantillas": [3, None, {}]}])
def test_respuesta_de_la_ia_con_otra_forma(datos: dict[str, Any]) -> None:
    assert leer_plantillas_ia(datos, PORTADA, DOMINIO) == []


def test_como_mucho_tres_plantillas_de_la_ia() -> None:
    datos = {"plantillas": [f"/b{n}?q={{consulta}}" for n in range(10)]}
    plantillas = leer_plantillas_ia(datos, PORTADA, DOMINIO)
    assert len(plantillas) == MAX_PLANTILLAS_IA == 3


def test_resumen_de_la_portada_para_la_ia() -> None:
    html = (
        "<html><body><form action='/buscar' method='get'><input type='search' name='q'></form>"
        "<a href='/catalogo'>Catálogo de piezas</a>"
        "<a href='https://otra-tienda.es/espia'>Otra</a>"
        f"<p>{INYECCION}</p></body></html>"
    )
    resumen = resumen_portada(pagina(PORTADA, html), DOMINIO)
    assert f"GET {TIENDA}/buscar: q (search)" in resumen
    assert f"{TIENDA}/catalogo · Catálogo de piezas" in resumen
    assert "otra-tienda" not in resumen
    # Solo formularios y enlaces: el texto suelto de la página (y lo que inyecta) no va.
    assert INYECCION not in resumen


def test_la_ia_propone_la_busqueda_como_ultimo_recurso() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=f"{TIENDA}/catalogo?busca=faro+golf")
    sitio[PORTADA] = SIN_BUSCADOR.replace("</header>", "<a href='/catalogo'>Catálogo</a></header>")
    navegador = NavegadorFalso(sitio)
    propuesta = {
        "plantillas": ["https://otra-tienda.es/buscar?q={consulta}", "/catalogo?busca={consulta}"]
    }
    perfil, peticiones = configurar_con(
        navegador, [propuesta, LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}"
    )
    assert perfil.url_busqueda == f"{TIENDA}/catalogo?busca={{consulta}}"
    assert perfil.nota.startswith("URL de búsqueda propuesta por la IA")
    assert peticiones[0].esquema == ESQUEMA_PLANTILLAS
    assert len(peticiones) == 3
    assert f"{TIENDA}/catalogo · Catálogo" in peticiones[0].datos
    assert "Catálogo" not in peticiones[0].instrucciones
    assert all("otra-tienda" not in url for url in navegador.urls)
    # Antes se probaron todas las vías clásicas, por HTTP y con Chromium (aquí no hay).
    assert navegador.urls.index(f"{TIENDA}/catalogo?busca=faro+golf") > len(PATRONES)


def test_plantilla_de_la_ia_que_no_da_un_listado_no_vale() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    sitio[f"{TIENDA}/contacto?q=faro+golf"] = SIN_BUSCADOR  # Responde, pero no es un listado.
    navegador = NavegadorFalso(sitio)
    motivo, peticiones = fallo_con(
        navegador, [{"plantillas": ["/contacto?q={consulta}"]}], dominio=f"www.{DOMINIO}"
    )
    assert "búsqueda" in motivo
    assert len(peticiones) == 1
    assert f"{TIENDA}/contacto?q=faro+golf" in navegador.urls


def test_sin_portada_no_se_pregunta_a_la_ia() -> None:
    navegador = NavegadorFalso({})
    _, peticiones = fallo_con(navegador, [{"plantillas": ["/buscar?q={consulta}"]}])
    assert peticiones == []


def test_presupuesto_agotado_en_la_propuesta_se_propaga() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None)
    navegador = NavegadorFalso(sitio)
    agotado = PresupuestoAgotado("Se ha alcanzado el tope de llamadas")
    sesion_ia, _ = sesion({COMPONENTE: [agotado]})
    with pytest.raises(PresupuestoAgotado):
        asyncio.run(
            configurar(
                f"www.{DOMINIO}", navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico
            )
        )


def test_otro_error_de_la_ia_en_la_propuesta_es_una_configuracion_fallida() -> None:
    navegador = NavegadorFalso(paginas_sitio(formulario=False, busqueda=None))
    caida = IANoDisponible("No responde la API")
    motivo, _ = fallo_con(navegador, [caida], dominio=f"www.{DOMINIO}")
    assert "búsqueda" in motivo and "No responde la API" in motivo


# --- Perfiles api_* -----------------------------------------------------------------------------


def test_woocommerce_con_api_se_configura_sin_ia() -> None:
    navegador = NavegadorFalso(paginas_woo())
    perfil, peticiones = configurar_con(navegador, [], dominio=WOO)
    assert peticiones == []
    assert perfil == Perfil(
        dominio=WOO,
        version=1,
        url_busqueda=WOO_PLANTILLA,
        sel_resultado="",
        sel_titulo="",
        sel_enlace="",
        sel_precio_listado=None,
        sel_campos={},
        necesita_js=False,
        autor=AUTOR,
        revisado=False,
        creado=perfil.creado,
        nota=perfil.nota,
        tipo=TIPO_API_WOOCOMMERCE,
    )
    assert perfil.nota.startswith("API pública de WooCommerce, sin IA. Válido: 3 resultados")
    # La portada y la API: ni el formulario ni las fichas.
    assert navegador.pedidas == [(WOO_PORTADA, False), (WOO_API, False)]


def test_shopify_con_api_se_configura_sin_ia() -> None:
    navegador = NavegadorFalso(paginas_shopify())
    perfil, peticiones = configurar_con(navegador, [], dominio=SHOPIFY)
    assert peticiones == []
    assert (perfil.tipo, perfil.url_busqueda) == (TIPO_API_SHOPIFY, SHOPIFY_PLANTILLA)
    assert navegador.urls == [SHOPIFY_PORTADA, SHOPIFY_API, SHOPIFY_PORTADA]


@pytest.mark.parametrize("respuesta", ["[]", '{"code":"rest_no_route","data":{"status":404}}'])
def test_api_sin_productos_sigue_por_el_html(respuesta: str) -> None:
    paginas = paginas_woo()
    paginas[WOO_API] = respuesta
    navegador = NavegadorFalso(paginas)
    _, peticiones = fallo_con(navegador, [{"plantillas": []}], dominio=WOO)
    # Después de la API, el formulario de la portada (WooCommerce: `?s=` y `post_type`).
    assert navegador.urls[:3] == [
        WOO_PORTADA,
        WOO_API,
        f"https://{WOO}/?s=faro+golf&post_type=product",
    ]
    assert len(peticiones) == 1


def test_api_que_no_existe_sigue_por_el_html() -> None:
    paginas = paginas_woo()
    del paginas[WOO_API]
    navegador = NavegadorFalso(paginas)
    fallo_con(navegador, [{"plantillas": []}], dominio=WOO)
    assert navegador.urls[1] == WOO_API and len(navegador.urls) > 2


def test_tienda_sin_plataforma_no_prueba_ninguna_api() -> None:
    navegador = NavegadorFalso(paginas_sitio(), redirecciones={f"https://{DOMINIO}/": PORTADA})
    configurar_con(navegador, [LISTADO_OK, CAMPOS_OK])
    assert all("wp-json" not in u and "suggest.json" not in u for u in navegador.urls)


def test_bloqueo_en_la_api_al_configurar_se_propaga() -> None:
    navegador = NavegadorFalso(paginas_woo(), errores={WOO_API: Bloqueado(WOO_API, "DataDome")})
    sesion_ia, proveedor = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK]})
    with pytest.raises(Bloqueado):
        asyncio.run(
            configurar(WOO, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
        )
    assert proveedor.peticiones == []
    assert navegador.urls == [WOO_PORTADA, WOO_API]


def test_portada_sin_formularios_ni_enlaces_propios_no_se_pregunta_a_la_ia() -> None:
    # Revisión adversarial (coste): una portada «Próximamente» o una página aparcada no tiene
    # nada de lo que deducir la búsqueda; la IA solo podría inventar, y se pagaría la llamada.
    sitio = {
        PORTADA: "<html><body><h1>Próximamente</h1>"
        "<a href='https://instagram.com/recambios'>Instagram</a></body></html>"
    }
    _, peticiones = fallo_con(
        NavegadorFalso(sitio), [{"plantillas": ["/buscar?q={consulta}"]}], dominio=f"www.{DOMINIO}"
    )
    assert peticiones == []


def test_formulario_que_envia_a_otra_web_no_es_una_pista_para_la_ia() -> None:
    # El boletín de un servicio externo: la IA solo vería «(otra web)».
    sitio = {
        PORTADA: "<html><body><h1>Próximamente</h1>"
        "<form action='https://boletines.example/alta' method='post'>"
        "<input type='email' name='correo'></form></body></html>"
    }
    _, peticiones = fallo_con(
        NavegadorFalso(sitio), [{"plantillas": ["/buscar?q={consulta}"]}], dominio=f"www.{DOMINIO}"
    )
    assert peticiones == []


# --- Sin repetir con Chromium lo que no puede cambiar ------------------------------------------

BOOKS = "books.toscrape.com"
BOOKS_PORTADA = f"https://{BOOKS}/"
BOOKS_HTML = (
    Path(__file__).parent.parent / "fixtures" / "reales" / "books_portada.html"
).read_text(encoding="utf-8")


@dataclass
class NavegadorConEstados(NavegadorFalso):
    """El sitio falso, pero algunas URL responden con un estado HTTP concreto (y una página)."""

    estados: dict[str, int] = field(default_factory=dict)

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        respuesta = await super().obtener(url, js=js, grande=grande)
        estado = self.estados.get(respuesta.url)
        if estado is None:
            return respuesta
        return pagina(respuesta.url, "<html><body><h1>Error</h1></body></html>", estado=estado)


def test_books_real_sin_buscador_no_repite_con_chromium_los_404() -> None:
    # Prueba real del 2026-10-20: books.toscrape.com no tiene buscador. Su portada ignora los
    # parámetros (`/?s=...` da la portada con un 200) y lo demás da 404. Antes se repetían con
    # Chromium las seis rutas habituales y el tope de 12 peticiones se agotaba sin preguntar.
    woo = f"{BOOKS_PORTADA}?s=sapiens&post_type=product"
    navegador = NavegadorFalso({BOOKS_PORTADA: BOOKS_HTML, woo: BOOKS_HTML}, paginas_js={})
    sesion_ia, proveedor = sesion({COMPONENTE: [{"plantillas": ["/catalogue/?q={consulta}"]}]})
    with pytest.raises(ConfiguracionFallida):
        asyncio.run(
            configurar(BOOKS, navegador, sesion_ia, "sapiens", validador=validar_con_dns_publico)
        )
    rutas = [url_de_busqueda(f"https://{BOOKS}{p}", "sapiens") for p in PATRONES]
    propuesta = f"https://{BOOKS}/catalogue/?q=sapiens"
    assert navegador.pedidas == [
        (BOOKS_PORTADA, False),
        # La portada sigue mirándose con Chromium: sin formulario de búsqueda en el HTML, el
        # buscador puede montarlo un script (books llama a `search.init()`).
        (BOOKS_PORTADA, True),
        *((ruta, False) for ruta in rutas),
        (woo, True),  # Dio 200: con JavaScript podría pintar los resultados.
        (propuesta, False),
    ]
    assert len(proveedor.peticiones) == 1


# La lista va escrita a mano: con `sorted(ESTADOS_DEFINITIVOS)`, quitar un estado del conjunto
# quitaba también su caso y el test seguía en verde.
@pytest.mark.parametrize("estado", [400, 404, 410, 414, 501])
def test_estado_definitivo_por_http_no_se_repite_con_chromium(estado: int) -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    primera = url_de_busqueda(TIENDA + PATRONES[0], CONSULTA)
    sitio[primera] = html_listado()  # Con Chromium sería un listado, pero no se llega a pedir.
    navegador = NavegadorConEstados(sitio, paginas_js={}, estados={primera: estado})
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    assert (primera, False) in navegador.pedidas
    assert (primera, True) not in navegador.pedidas


# Un rechazo que no llega como `Bloqueado` (página grande o con señales de producto) tampoco se
# repite con Chromium: credenciales (401), límite de peticiones (429) o motivo legal (451).
@pytest.mark.parametrize("estado", [401, 429, 451])
def test_rechazo_por_http_no_se_repite_con_chromium(estado: int) -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    primera = url_de_busqueda(TIENDA + PATRONES[0], CONSULTA)
    sitio[primera] = html_listado()
    navegador = NavegadorConEstados(sitio, paginas_js={}, estados={primera: estado})
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    assert (primera, False) in navegador.pedidas
    assert (primera, True) not in navegador.pedidas


@pytest.mark.parametrize("estado", [200, 403, 500, 502, 503, 504])
def test_otros_estados_por_http_si_se_repiten_con_chromium(estado: int) -> None:
    # Un 200 que no es un listado puede ser una tienda que pinta los resultados con JavaScript;
    # un 5xx puede ser pasajero; un 403 suele ser el WAF rechazando la huella del cliente HTTP
    # (ADR-023), y con Chromium puede pasar.
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    primera = url_de_busqueda(TIENDA + PATRONES[0], CONSULTA)
    sitio[primera] = SIN_BUSCADOR
    navegador = NavegadorConEstados(sitio, paginas_js={}, estados={primera: estado})
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    assert (primera, True) in navegador.pedidas


def test_estados_sin_rescate_son_rechazos() -> None:
    # Lo que el navegador nunca rescata con Chromium tampoco lo repite el descubrimiento.
    assert ESTADOS_SIN_RESCATE <= RECHAZOS
    assert 403 not in RECHAZOS


def test_un_404_con_resultados_pintados_no_es_un_listado() -> None:
    # Por eso no se repite con Chromium: aunque el JavaScript pintara resultados sobre la página
    # de error (el truco del 404.html de algunas SPA), el estado del documento sigue siendo 404.
    listado = pagina(BUSQUEDA, html_listado(), estado=404)
    assert not parece_listado(listado, DOMINIO, CONSULTA, portada=None)


def test_404_de_la_propuesta_de_la_ia_tampoco_se_repite() -> None:
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    navegador = NavegadorFalso(sitio, paginas_js={})
    fallo_con(navegador, [{"plantillas": ["/contacto?q={consulta}"]}], dominio=f"www.{DOMINIO}")
    assert (f"{TIENDA}/contacto?q=faro+golf", False) in navegador.pedidas
    assert all(not js for url, js in navegador.pedidas if url != PORTADA)


@pytest.mark.parametrize(
    "respuesta",
    [None, descriptor(url_os(f"{TIENDA}/sugerir?q={{searchTerms}}", "application/json"))],
    ids=["descriptor-404", "solo-sugerencias"],
)
def test_descriptor_opensearch_que_no_vale_no_se_pide_otra_vez(respuesta: str | None) -> None:
    # La portada declara OpenSearch, pero el descriptor da 404 o no trae una plantilla HTML, y no
    # hay formularios ni enlaces: se mira la portada con Chromium, que trae el mismo `<link>` en
    # la cabecera. Ese descriptor ya se pidió y no dio nada: pedirlo otra vez es el mismo fallo.
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = con_cabeza(SIN_BUSCADOR, ENLACE_OPENSEARCH)
    if respuesta is not None:
        sitio[DESCRIPTOR] = respuesta
    navegador = NavegadorFalso(sitio, paginas_js={})
    fallo_con(navegador, [{"plantillas": []}], dominio=f"www.{DOMINIO}")
    assert (PORTADA, True) in navegador.pedidas
    assert navegador.urls.count(DESCRIPTOR) == 1


# --- Tienda rescatada con Chromium (ADR-023), con el Navegador real -------------------------------


def test_tienda_rescatada_no_repite_candidatas_con_chromium() -> None:
    # La portada por HTTP es un desafío de Cloudflare: el navegador la rescata con Chromium y
    # marca la tienda. Desde ahí, js=False ya va por Chromium, así que ni la portada ni las
    # candidatas (las que dan un 200 que no es listado, ni las que fallan) se repiten con js=True.
    from precia.navegador.cortesia import LimitadorDominio
    from precia.navegador.navegador import Navegador
    from precia.navegador.robots import ServicioRobots
    from precia.perfiles.descubrimiento import abrir_portada, descubrir
    from tests.navegador.falsos import DescargadorFalso, Reloj
    from tests.navegador.falsos import pagina as pagina_navegador

    desafio = (
        Path(__file__).parent.parent / "fixtures" / "navegador" / "cloudflare_just_a_moment.html"
    ).read_text(encoding="utf-8")
    robots = f"{TIENDA}/robots.txt"
    candidatas = [url_de_busqueda(TIENDA + patron, CONSULTA) for patron in PATRONES]
    reloj = Reloj()
    robots_http = DescargadorFalso(
        {robots: pagina_navegador(robots, "User-agent: *\n", tipo="text/plain")}
    )
    http = DescargadorFalso({PORTADA: pagina_navegador(PORTADA, desafio, estado=403)})
    chromium = DescargadorFalso(
        {
            PORTADA: pagina_navegador(PORTADA, SIN_BUSCADOR),
            candidatas[0]: pagina_navegador(candidatas[0], SIN_BUSCADOR),  # 200, no es listado
            candidatas[1]: pagina_navegador(candidatas[1], SIN_BUSCADOR, estado=503),
        }
    )  # el resto de candidatas falla (`ErrorDescarga`)
    navegador = Navegador(
        http=http,
        chromium=chromium,
        robots=ServicioRobots(robots_http, "PrecIA-test", reloj=reloj),
        limitador=LimitadorDominio(0.0, reloj=reloj, dormir=reloj.dormir),
        validador=validar_con_dns_publico,
    )

    async def probar() -> None:
        host = f"www.{DOMINIO}"
        portada = await abrir_portada(host, DOMINIO, navegador, validador=validar_con_dns_publico)
        assert portada is not None and portada.por_chromium
        with pytest.raises(ConfiguracionFallida):
            await descubrir(
                portada, host, DOMINIO, navegador, CONSULTA, validador=validar_con_dns_publico
            )

    asyncio.run(probar())
    assert http.pedidas == [PORTADA]
    assert chromium.pedidas == [PORTADA, *candidatas]  # una sola vez cada una
