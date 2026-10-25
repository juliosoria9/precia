"""Descubrimiento de la búsqueda (nota 15 §D): OpenSearch → formularios → enlaces → plataforma →
patrones → IA, y el perfil `api_*` de WooCommerce y Shopify sin llamar a la IA."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from precia.extraccion.dom import analizar
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
    MAX_RUTAS_SIN_ACCION,
    NOMBRES_BUSQUEDA,
    ORIGEN_ENLACE,
    ORIGEN_OPENSEARCH,
    PARAMETROS_ENLACE,
    PATRONES,
    RECHAZOS,
    RUTAS_SIN_ACCION,
    enlace_opensearch,
    leer_plantillas_ia,
    parece_listado,
    plantilla_opensearch,
    plantillas_de_enlaces,
    plantillas_de_formularios,
    plantillas_sin_accion,
    resumen_portada,
)
from precia.perfiles.tipos import (
    TIPO_API_SHOPIFY,
    TIPO_API_WOOCOMMERCE,
    ConfiguracionFallida,
    Perfil,
)
from precia.perfiles.validador import url_de_busqueda
from tests.cronometro import proporcion_de_cpu, segundos_de_cpu
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
    html_vacio,
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
    plantilla = plantilla_opensearch(pagina(DESCRIPTOR, html), DOMINIO)
    assert plantilla is None or "aaaaaaaaaa" not in plantilla
    assert segundos_de_cpu(lambda: plantilla_opensearch(pagina(DESCRIPTOR, html), DOMINIO), 1) < 1


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

    def enlaces_y_resumen() -> None:
        plantillas_de_enlaces(portada, DOMINIO)
        resumen_portada(portada, DOMINIO)

    # Cada una construye el árbol una vez; lo demás es recorrer los enlaces, y eso tiene que
    # costar menos que otros tres árboles. Se compara con construir el árbol y no con un tope
    # fijo, que fallaba en la suite completa con la CPU más lenta o más cargada (bug 6).
    # Medido: unas 2 veces, con la máquina libre y con 8 procesos de carga; sin los topes de
    # enlaces (`MAX_ENLACES_REVISADOS`, `MAX_ENLACES` y `MAX_ENLACES_RESUMEN`), unas 12.
    proporcion = proporcion_de_cpu(enlaces_y_resumen, lambda: analizar(portada.html), 5)
    assert proporcion < 5


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


# --- Propuesta de la IA ------------------------------------------------------------------------


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
        # B7: la portada no da candidatas, así que la IA propone mientras se prueban las rutas y
        # su plantilla se prueba por HTTP antes que la ronda de Chromium.
        (propuesta, False),
        (woo, True),  # Dio 200: con JavaScript podría pintar los resultados.
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


# --- Formulario de búsqueda sin `action` (campaña multi-sector, causa C1) -----------------------

PCC = "pccomponentes.com"
PCC_PORTADA = f"https://www.{PCC}/"
REALES = Path(__file__).parent.parent / "fixtures" / "reales"
PCC_HTML = (REALES / "pccomponentes_portada_buscador_chromium.html").read_text(encoding="utf-8")


def test_formulario_sin_accion_prueba_tambien_las_rutas_de_busqueda() -> None:
    # PcComponentes (ronda 1): `<form role="search">` sin `action` con `name="query"`. Enviado
    # como lo haría el navegador va a la propia portada (`/?query=`, que devuelve la portada);
    # el buscador real, que pinta su JavaScript, está en `/buscar/?query=`.
    portada = pagina(PCC_PORTADA, PCC_HTML)
    assert plantillas_de_formularios(portada, PCC) == [f"{PCC_PORTADA}?query={{consulta}}"]
    assert plantillas_sin_accion(portada, PCC) == [
        f"https://www.{PCC}/buscar/?query={{consulta}}",
        f"https://www.{PCC}/search?query={{consulta}}",
        f"https://www.{PCC}/busqueda?query={{consulta}}",
    ]
    assert len(RUTAS_SIN_ACCION) == MAX_RUTAS_SIN_ACCION == 3


@pytest.mark.parametrize(
    "formulario",
    [
        # Con `action`: se envía ahí y ya es una candidata de `plantillas_de_formularios`.
        '<form action="/buscar"><input type="search" name="q"></form>',
        # Por POST, sin campo de búsqueda o con una contraseña (nunca un login).
        '<form method="post"><input type="search" name="q"></form>',
        '<form><input type="text" name="email"></form>',
        '<form><input type="text" name="q"><input type="password" name="clave"></form>',
        # El campo no tiene nombre (Doofinder, Empathy): no hay parámetro que probar.
        '<form role="search"><input type="text" id="seek" placeholder="Buscar"></form>',
    ],
)
def test_formularios_sin_rutas_de_busqueda_que_probar(formulario: str) -> None:
    portada = pagina(PORTADA, f"<html><body>{formulario}</body></html>")
    assert plantillas_sin_accion(portada, DOMINIO) == []


def test_formulario_con_contrasena_no_es_la_busqueda() -> None:
    html = '<form action="/entrar"><input name="q"><input type="password" name="clave"></form>'
    assert plantillas_de_formularios(pagina(PORTADA, html), DOMINIO) == []


def test_las_rutas_del_formulario_sin_accion_van_tras_los_patrones() -> None:
    # La tienda de prueba con el buscador de PcComponentes, que solo sale con Chromium. Las rutas
    # nuevas van detrás de los patrones: no retrasan a las tiendas que ya se configuraban.
    cuerpo = PCC_HTML.split("<header>", 1)[1].split("</header>", 1)[0]
    renderizada = SIN_BUSCADOR.replace("</header>", cuerpo + "</header>")
    busqueda = f"{TIENDA}/buscar/?query=faro+golf"
    sitio = paginas_sitio(formulario=False, busqueda=busqueda)
    sitio[PORTADA] = SIN_BUSCADOR
    sitio[f"{TIENDA}/?query=faro+golf"] = renderizada  # la propia portada, como en la tienda real
    navegador = NavegadorFalso(sitio, paginas_js={PORTADA: renderizada})
    perfil, _ = configurar_con(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.url_busqueda == f"{TIENDA}/buscar/?query={{consulta}}"
    assert not perfil.necesita_js
    rutas = [url_de_busqueda(f"{TIENDA}{p}", CONSULTA) for p in PATRONES]
    assert navegador.pedidas[:3] == [
        (PORTADA, False),
        (PORTADA, True),
        (f"{TIENDA}/?query=faro+golf", False),
    ]
    assert navegador.urls.index(busqueda) > max(navegador.urls.index(r) for r in rutas)


def test_resumen_para_la_ia_con_los_atributos_de_los_campos() -> None:
    # Coolmod (Doofinder) y PcComponentes: el campo de búsqueda no siempre tiene `name`. La IA
    # ve su id, placeholder y aria-label, y el `role` del formulario (datos no confiables).
    html = (
        "<html><body>"
        "<form id='FormSeeker'><input type='text' id='seek' placeholder='Buscar en la tienda'>"
        "</form>"
        "<form role='search'><input aria-label='Buscar' id='search' name='query'></form>"
        "</body></html>"
    )
    resumen = resumen_portada(pagina(PORTADA, html), DOMINIO)
    assert f"GET {PORTADA}: (sin nombre) (text, id=seek, placeholder=Buscar en la tienda)" in (
        resumen
    )
    assert f"GET {PORTADA} [role=search]: query (text, id=search, aria-label=Buscar)" in resumen


# --- Resultados que no dependen de la consulta (campaña multi-sector, causa C2) ----------------

TTL = "todostuslibros.com"
TTL_BUSQUEDA = f"https://www.{TTL}/busquedas?q=filtro"
TTL_HTML = (REALES / "todostuslibros_busqueda_filtro_ignorada.html").read_text(encoding="utf-8")
MENU = (
    "<nav><a href='/c/faros-y-pilotos'>Faros y pilotos</a> <a href='/c/frenos'>Frenos</a> "
    "<a href='/c/filtros'>Filtros</a></nav>"
)


def tarjeta(ruta: str, titulo: str, precio: str = "10,00 €") -> str:
    return f'<div class="tarjeta"><a href="{ruta}">{titulo}</a> <span>{precio}</span></div>'


def carrusel(cuantas: int) -> str:
    """Destacados de la portada: siempre los mismos, se busque lo que se busque."""
    return "".join(tarjeta(f"/p/llanta-{n}", f"Llanta R17 número {n}") for n in range(cuantas))


def pagina_de(titulo: str, cuerpo: str) -> str:
    return f"<html><head><title>{titulo}</title></head><body>{MENU}{cuerpo}</body></html>"


def descubrir_con(navegador: NavegadorFalso) -> Any:
    from precia.perfiles.descubrimiento import abrir_portada, descubrir

    async def probar() -> Any:
        host = f"www.{DOMINIO}"
        portada = await abrir_portada(host, DOMINIO, navegador, validador=validar_con_dns_publico)
        return await descubrir(
            portada, host, DOMINIO, navegador, CONSULTA, validador=validar_con_dns_publico
        )

    return asyncio.run(probar())


def test_busqueda_que_ignora_la_consulta_no_es_una_pagina_de_resultados() -> None:
    # Todos tus libros (ronda 1): `/busquedas?q=filtro` ignora `q` (su título es «Resultados
    # para:  -») y devuelve novedades. «filtro» solo sale en las etiquetas de los filtros («Libros
    # según filtros», «Filtros activos»): buscada en todo el texto de la página, la daba por buena.
    resultados = pagina(TTL_BUSQUEDA, TTL_HTML)
    # Sin `estricto` (el orquestador) se sigue buscando en toda la página, como antes.
    assert parece_listado(resultados, TTL, "filtro", portada=None)
    assert not parece_listado(resultados, TTL, "filtro", portada=None, estricto=True)


@pytest.mark.parametrize(
    ("html", "consulta"),
    [
        # En el título de la página.
        (
            pagina_de("Buscar «faro golf»", tarjeta("/p/x", "Pieza") + tarjeta("/p/y", "Otra")),
            CONSULTA,
        ),
        # En los resultados (título del producto o slug), con plurales.
        (pagina_de("Buscar", tarjeta("/p/x", "Faros Golf VI") + tarjeta("/p/y", "Pieza")), "faro"),
        (pagina_de("Buscar", tarjeta("/p/faro-golf", "Pieza") + tarjeta("/p/y", "Otra")), "faro"),
        # Una referencia se compara sin separadores (como `_encaje` en el orquestador).
        (
            pagina_de("Buscar", tarjeta("/p/x", "Bomba 1K0 121 011 B") + tarjeta("/p/y", "Otra")),
            "1K0-121-011-B",
        ),
    ],
    ids=["titulo", "tarjeta", "slug", "referencia"],
)
def test_resultados_que_nombran_la_consulta(html: str, consulta: str) -> None:
    resultados = pagina(BUSQUEDA, html)
    assert parece_listado(resultados, DOMINIO, consulta, portada=None, estricto=True)


def test_la_consulta_solo_en_menus_y_filtros_no_basta() -> None:
    # Ecooparts: «faro» sale en el menú de categorías; los resultados son llantas.
    html = pagina_de("Resultados", carrusel(3))
    resultados = pagina(BUSQUEDA, html)
    assert parece_listado(resultados, DOMINIO, "faro", portada=None)
    assert not parece_listado(resultados, DOMINIO, "faro", portada=None, estricto=True)


def test_se_salta_la_ruta_que_ignora_la_consulta() -> None:
    # La primera ruta habitual responde con novedades que no tienen nada que ver con lo buscado
    # («faro» solo está en el menú); la siguiente es la búsqueda de verdad.
    sitio = paginas_sitio(formulario=False, busqueda=None)
    sitio[PORTADA] = SIN_BUSCADOR
    ignora = url_de_busqueda(TIENDA + PATRONES[0], CONSULTA)
    sitio[ignora] = pagina_de("Resultados para: -", carrusel(4))
    buena = url_de_busqueda(TIENDA + PATRONES[1], CONSULTA)
    sitio[buena] = html_listado()
    busqueda = descubrir_con(NavegadorFalso(sitio))
    assert busqueda.plantilla == TIENDA + PATRONES[1]


def test_la_portada_con_unos_pocos_resultados_nuevos_no_es_un_listado() -> None:
    # Ecooparts (ronda 1): `?s=faro` da la portada, con su carrusel de destacados, y tres
    # productos más. Que no sean exactamente los mismos enlaces que la portada no basta: casi
    # todos lo son (allí, 3 nuevos de 276).
    portada = pagina(PORTADA, pagina_de("Recambios Prueba", carrusel(80)))
    nuevos = "".join(tarjeta(f"/p/faro-golf-{n}", f"Faro Golf {n}") for n in range(3))
    resultados = pagina(BUSQUEDA, pagina_de("Buscar faro golf", carrusel(80) + nuevos))
    assert parece_listado(resultados, DOMINIO, CONSULTA, portada=None, estricto=True)
    assert not parece_listado(resultados, DOMINIO, CONSULTA, portada=portada, estricto=True)
    assert not parece_listado(resultados, DOMINIO, CONSULTA, portada=portada)


def test_resultados_con_algunos_destacados_de_la_portada_si_son_un_listado() -> None:
    # Decathlon (ronda 1): por HTTP su búsqueda solo trae 3 enlaces junto a un precio, y los 3
    # están también en la portada; el resto de resultados son enlaces nuevos sin precio al lado.
    # Un umbral de solape solo con los enlaces con precio la rechazaría.
    portada = pagina(PORTADA, pagina_de("Recambios Prueba", carrusel(5)))
    sin_precio = "".join(f"<p><a href='/p/faro-golf-{n}'>Faro Golf {n}</a></p>" for n in range(20))
    resultados = pagina(
        BUSQUEDA, pagina_de("Resultados para «faro golf»", carrusel(3) + sin_precio)
    )
    assert parece_listado(resultados, DOMINIO, CONSULTA, portada=portada, estricto=True)


def test_candidata_por_chromium_se_compara_con_la_portada_renderizada() -> None:
    # La portada por HTTP trae el formulario (no hace falta renderizarla para descubrir) pero no
    # los destacados, que pinta su JavaScript. La búsqueda solo sale con Chromium y es la portada
    # renderizada con tres productos más: comparada con la de HTTP parecería toda nueva.
    sitio = paginas_sitio(formulario=True, busqueda=None)
    candidata = f"{TIENDA}/buscar?orden=relevancia&q=faro+golf"
    sitio[candidata] = html_vacio()
    renderizada = sitio[PORTADA].replace("</body>", carrusel(80) + "</body>")
    nuevos = "".join(tarjeta(f"/p/faro-golf-{n}", f"Faro Golf {n}") for n in range(3))
    con_js = renderizada.replace("</body>", nuevos + "</body>").replace(
        "<title>Recambios Prueba</title>", "<title>Buscar faro golf</title>"
    )
    navegador = NavegadorFalso(sitio, paginas_js={PORTADA: renderizada, candidata: con_js})
    with pytest.raises(ConfiguracionFallida):
        descubrir_con(navegador)
    # La portada renderizada se pide una sola vez, y solo porque hacía falta para comparar.
    assert navegador.pedidas.count((PORTADA, True)) == 1


# --- Campo de búsqueda con otro nombre (campaña multi-sector, causa C8) ------------------------

TTL_PORTADA = f"https://www.{TTL}/"
TTL_PORTADA_HTML = (REALES / "todostuslibros_portada_formulario_keyword.html").read_text(
    encoding="utf-8"
)


def test_formulario_con_campo_keyword_es_la_busqueda() -> None:
    # Todos tus libros (ronda 1): `<form method="GET" action=".../busquedas">` con `<input
    # type="text" name="keyword">`. Se descartaba porque `keyword` no era un nombre de búsqueda,
    # y la candidata salía del enlace `/busquedas?novedades=true` con `q`, que la tienda ignora.
    portada = pagina(TTL_PORTADA, TTL_PORTADA_HTML)
    assert plantillas_de_formularios(portada, TTL) == [
        f"https://www.{TTL}/busquedas?keyword={{consulta}}"
    ]


@pytest.mark.parametrize(
    "nombre",
    ["keyword", "Keywords", "searchTerm", "search_query", "term", "texto", "busqueda", "k"],
)
def test_nombres_habituales_del_campo_de_busqueda(nombre: str) -> None:
    html = f'<form action="/resultados"><input type="text" name="{nombre}"></form>'
    assert plantillas_de_formularios(pagina(PORTADA, html), DOMINIO) == [
        f"{TIENDA}/resultados?{nombre}={{consulta}}"
    ]


def test_los_nombres_de_los_enlaces_tambien_valen_en_los_formularios() -> None:
    # Las dos listas se habían desincronizado: `keyword` solo estaba en la de los enlaces. Se
    # comparan en minúsculas (`_de_busqueda` pasa el nombre a minúsculas antes de mirarlo).
    assert set(PARAMETROS_ENLACE) <= NOMBRES_BUSQUEDA
    assert all(nombre == nombre.lower() for nombre in NOMBRES_BUSQUEDA)


@pytest.mark.parametrize(
    "formulario",
    [
        # Un boletín por GET con un campo de texto: no es la búsqueda.
        '<form action="/suscribir"><input type="text" name="email"></form>',
        '<form action="/suscribir"><input type="text" name="nombre"></form>',
        '<form action="/suscribir"><input type="email" name="keyword"></form>',
    ],
)
def test_formularios_get_de_texto_que_no_son_la_busqueda(formulario: str) -> None:
    portada = pagina(PORTADA, f"<html><body>{formulario}</body></html>")
    assert plantillas_de_formularios(portada, DOMINIO) == []


def test_la_plantilla_del_formulario_va_antes_que_la_del_enlace() -> None:
    # La portada de Todos tus libros en la tienda de prueba: el formulario con `keyword` y el
    # enlace a `/busquedas?novedades=true`, que daría `/busquedas?q=` (la tienda ignora `q`).
    formulario = '<form method="GET" action="/busquedas"><input type="text" name="keyword"></form>'
    enlace = "<a href='/busquedas?novedades=true'>Novedades</a>"
    buena = f"{TIENDA}/busquedas?keyword=faro+golf"
    sitio = paginas_sitio(formulario=False, busqueda=buena)
    sitio[PORTADA] = SIN_BUSCADOR.replace("</header>", formulario + enlace + "</header>")
    ignora = f"{TIENDA}/busquedas?q=faro+golf"
    sitio[ignora] = pagina_de("Resultados para: -", carrusel(4))
    navegador = NavegadorFalso(sitio)
    busqueda = descubrir_con(navegador)
    assert busqueda.plantilla == f"{TIENDA}/busquedas?keyword={{consulta}}"
    assert navegador.pedidas[:2] == [(PORTADA, False), (buena, False)]
    assert ignora not in navegador.urls


ZALANDO = "zalando.es"
ZALANDO_BUSQUEDA = f"https://www.{ZALANDO}/catalogo/?q=filtro"
ZALANDO_HTML = (REALES / "zalando_busqueda_streaming_react.html").read_text(encoding="utf-8")


def test_enlaces_con_precio_dentro_de_un_oculto_no_cuentan_para_el_listado() -> None:
    # Causa R2-C9: si la página se acepta como listado, la IA tiene que poder ver los productos
    # (el resumen no enseña lo oculto). Un menú o un bloque `hidden` con precios no cuenta.
    ocultas = "".join(tarjeta(f"/p/filtro-{n}", f"Filtro de aceite {n}") for n in range(4))
    html = pagina_de(
        "Resultados de filtro", f'<div hidden><div class="rejilla">{ocultas}</div></div>'
    )
    assert not parece_listado(pagina(BUSQUEDA, html), DOMINIO, "filtro", portada=None)
    assert not parece_listado(
        pagina(BUSQUEDA, html), DOMINIO, "filtro", portada=None, estricto=True
    )
    visibles = pagina_de("Resultados de filtro", f'<div class="rejilla">{ocultas}</div>')
    assert parece_listado(
        pagina(BUSQUEDA, visibles), DOMINIO, "filtro", portada=None, estricto=True
    )


def test_zalando_con_streaming_de_react_es_un_listado_que_la_ia_puede_ver() -> None:
    from precia.extraccion.texto import resumen_para_ia

    resultados = pagina(ZALANDO_BUSQUEDA, ZALANDO_HTML)
    assert parece_listado(resultados, ZALANDO, "filtro", portada=None, estricto=True)
    assert resumen_para_ia(ZALANDO_HTML).count("<article") == 3
