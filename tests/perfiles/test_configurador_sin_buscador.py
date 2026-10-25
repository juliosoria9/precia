"""IA · Configurador de tiendas sin buscador (ADR-018, nota 16), de extremo a extremo y sin red.

Orden: API de la plataforma → plantilla de búsqueda del usuario (A1) → buscador descubierto →
catálogo por sitemap (B) → listado del usuario (A2) → `ConfiguracionFallida` que pide la URL de
entrada. Las páginas reales de `tests/fixtures/reales/` (webscraper.io y books.toscrape.com) sirven
para A2.
"""

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen
from precia.fuentes.sitemap import MAX_FICHEROS_SITEMAP
from precia.ia.simulado import Guion
from precia.navegador.tipos import Bloqueado
from precia.nucleo.modelos import CLAVES_CAMPOS
from precia.perfiles.catalogo import MAX_SITEMAPS_PROBADOS
from precia.perfiles.configurador import COMPONENTE, configurar
from precia.perfiles.descubrimiento import ESQUEMA_PLANTILLAS, ORIGEN_USUARIO
from precia.perfiles.tipos import (
    TIPO_API_WOOCOMMERCE,
    TIPO_HTML,
    TIPO_LISTADO,
    TIPO_SITEMAP,
    ConfiguracionFallida,
    Perfil,
)
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.plataformas import WOO, WOO_API, paginas_woo
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    INSTANTE,
    LISTADO,
    LISTADO_1,
    LISTADO_OK,
    PORTADA,
    PRODUCTOS,
    TIENDA,
    CatalogoFalso,
    NavegadorFalso,
    html_listado,
    paginas_sitio,
    sesion,
    xml_indice,
    xml_urlset,
)

NULOS = dict.fromkeys(CLAVES_CAMPOS)
REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
SIN_PROPUESTA: Guion = {"plantillas": []}
"""La IA no sabe proponer una plantilla de búsqueda (la tienda no tiene buscador)."""
INDICE = f"{TIENDA}/sitemaps/indice.xml"
DE_PRODUCTOS = f"{TIENDA}/sitemaps/product-sitemap.xml.gz"
FICHAS = [p.url for p in PRODUCTOS]


def ejecutar(
    navegador: NavegadorFalso,
    guion: list[Guion],
    *,
    dominio: str = f"www.{DOMINIO}",
    consulta: str = CONSULTA,
    **opciones: Any,
) -> tuple[Perfil, list[Any]]:
    sesion_ia, proveedor = sesion({COMPONENTE: guion})
    perfil = asyncio.run(
        configurar(
            dominio,
            navegador,
            sesion_ia,
            consulta,
            validador=validar_con_dns_publico,
            reloj=lambda: INSTANTE,
            **opciones,
        )
    )
    return perfil, proveedor.peticiones


def fallo(
    navegador: NavegadorFalso,
    guion: list[Guion],
    *,
    dominio: str = f"www.{DOMINIO}",
    **opciones: Any,
) -> tuple[str, list[Any]]:
    sesion_ia, proveedor = sesion({COMPONENTE: guion})
    with pytest.raises(ConfiguracionFallida) as error:
        asyncio.run(
            configurar(
                dominio,
                navegador,
                sesion_ia,
                CONSULTA,
                validador=validar_con_dns_publico,
                **opciones,
            )
        )
    return str(error.value), proveedor.peticiones


def sin_buscador(**opciones: Any) -> dict[str, str]:
    """La tienda falsa sin formulario de búsqueda y sin página de resultados."""
    return paginas_sitio(formulario=False, busqueda=None, **opciones)


def con_sitemap(sitio: dict[str, str], urls: list[str] = FICHAS) -> NavegadorFalso:
    """Con un índice declarado en robots.txt: productos (`.xml.gz`) y páginas."""
    sitio[INDICE] = xml_indice([f"{TIENDA}/sitemaps/page-sitemap.xml", DE_PRODUCTOS])
    sitio[DE_PRODUCTOS] = xml_urlset(urls)
    return NavegadorFalso(sitio, declarados=(INDICE,))


# --- A1: plantilla de búsqueda del usuario ---------------------------------------------------

ENTRADA_A1 = f"{TIENDA}/catalogo/buscar?texto={{consulta}}"
BUSQUEDA_A1 = f"{TIENDA}/catalogo/buscar?texto=faro+golf"


def test_a1_la_plantilla_del_usuario_se_prueba_la_primera() -> None:
    sitio = paginas_sitio()  # La portada tiene su formulario, pero el usuario manda.
    sitio[BUSQUEDA_A1] = html_listado()
    navegador = NavegadorFalso(sitio)
    perfil, peticiones = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK], entrada=ENTRADA_A1)
    assert perfil.tipo == TIPO_HTML
    assert perfil.url_busqueda == ENTRADA_A1
    assert perfil.nota.startswith("URL de entrada del usuario. Válido:")
    assert len(peticiones) == 2
    assert navegador.urls[:2] == [PORTADA, BUSQUEDA_A1]
    assert BUSQUEDA not in navegador.urls
    assert ORIGEN_USUARIO == "URL de entrada del usuario"


def test_a1_si_no_da_un_listado_sigue_el_flujo_de_siempre() -> None:
    navegador = NavegadorFalso(paginas_sitio())  # La plantilla del usuario da 404.
    perfil, _ = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK], entrada=ENTRADA_A1)
    assert perfil.url_busqueda.endswith("/buscar?orden=relevancia&q={consulta}")
    assert navegador.urls.index(BUSQUEDA_A1) < navegador.urls.index(BUSQUEDA)


@pytest.mark.parametrize(
    "entrada",
    [
        "https://otra-tienda.es/buscar?q={consulta}",
        "https://{consulta}.recambios-prueba.es/",
        f"{TIENDA}/buscar?q={{consulta}}&c={{categoria}}",
        f"{TIENDA}/buscar#q={{consulta}}",
    ],
)
def test_a1_una_plantilla_que_no_es_de_la_tienda_no_se_descarga(entrada: str) -> None:
    navegador = NavegadorFalso(paginas_sitio())
    perfil, _ = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK], entrada=entrada)
    assert perfil.url_busqueda.endswith("/buscar?orden=relevancia&q={consulta}")
    assert all("otra-tienda" not in u and "categoria" not in u for u in navegador.urls)
    assert navegador.urls[1] == BUSQUEDA


def test_la_api_va_antes_que_la_plantilla_del_usuario() -> None:
    navegador = NavegadorFalso(paginas_woo())
    perfil, peticiones = ejecutar(
        navegador, [], dominio=WOO, entrada=f"https://{WOO}/buscar?q={{consulta}}"
    )
    assert perfil.tipo == TIPO_API_WOOCOMMERCE
    assert peticiones == []
    assert WOO_API in navegador.urls
    assert f"https://{WOO}/buscar?q=faro+golf" not in navegador.urls


# --- B: catálogo por sitemap -------------------------------------------------------------------


def test_b_sitemap_declarado_en_robots_sin_ia() -> None:
    navegador = con_sitemap(sin_buscador(jsonld=(0, 1, 2, 3)))
    catalogo = CatalogoFalso()
    perfil, peticiones = ejecutar(navegador, [SIN_PROPUESTA], catalogo=catalogo)
    assert perfil == Perfil(
        dominio=DOMINIO,
        version=1,
        url_busqueda=INDICE,
        sel_resultado="",
        sel_titulo="",
        sel_enlace="",
        sel_precio_listado=None,
        sel_campos={},
        necesita_js=False,
        autor="configurador IA",
        revisado=False,
        creado=INSTANTE,
        nota=perfil.nota,
        tipo=TIPO_SITEMAP,
    )
    assert perfil.es_sitemap
    assert perfil.nota.startswith("Catálogo por sitemap (4 URL), sin IA. Válido:")
    # Solo la propuesta de plantillas del buscador (antes que el sitemap): los selectores de la
    # ficha no hacen falta porque el JSON-LD basta.
    assert [p.esquema for p in peticiones] == [ESQUEMA_PLANTILLAS]
    ((dominio, sitemap, entradas, parcial),) = catalogo.guardados
    assert (dominio, sitemap, parcial) == (DOMINIO, INDICE, False)
    assert [(url, lastmod) for url, _, lastmod in entradas] == [(u, "2026-10-19") for u in FICHAS]
    assert entradas[0][1] == "p faro izquierdo golf vi"
    assert navegador.grandes == [INDICE, DE_PRODUCTOS]
    # Las fichas que se probaron son las que encajan con la consulta de prueba.
    assert set(navegador.urls) >= set(FICHAS[:3])


def test_b_sin_datos_estructurados_una_llamada_a_la_ia_para_la_ficha() -> None:
    navegador = con_sitemap(sin_buscador(jsonld=()))
    catalogo = CatalogoFalso()
    perfil, peticiones = ejecutar(navegador, [SIN_PROPUESTA, CAMPOS_OK], catalogo=catalogo)
    assert perfil.tipo == TIPO_SITEMAP
    assert perfil.sel_campos == CAMPOS_OK
    assert perfil.nota.startswith("Catálogo por sitemap (4 URL), selectores de la ficha por IA. ")
    assert len(peticiones) == 2
    ficha = peticiones[1]
    assert "precio" in ficha.esquema["properties"]
    assert PRODUCTOS[0].referencia in ficha.datos
    assert len(catalogo.guardados) == 1


def test_b_si_tampoco_vale_con_la_ia_no_se_guarda_el_catalogo() -> None:
    navegador = con_sitemap(sin_buscador(jsonld=(), sin_precio=(0, 1, 2, 3)))
    catalogo = CatalogoFalso()
    motivo, peticiones = fallo(
        navegador, [SIN_PROPUESTA, {**NULOS, "precio": "span.no-existe"}], catalogo=catalogo
    )
    assert catalogo.guardados == []
    assert "sitemap" in motivo
    assert "URL de entrada" in motivo
    assert len(peticiones) == 2


def test_b_con_el_almacen_de_verdad(tmp_path: Path) -> None:
    almacen = Almacen(tmp_path / "precia.db", reloj=lambda: INSTANTE)
    almacen.alta_tienda(DOMINIO, "Recambios Prueba")
    urls = [*FICHAS, "https://otra-tienda.es/p/faro-golf", f"{TIENDA}/p/faro?utm_source=x"]
    navegador = con_sitemap(sin_buscador(jsonld=(0, 1, 2, 3)), urls)
    perfil, _ = ejecutar(navegador, [SIN_PROPUESTA], catalogo=almacen)
    info = almacen.catalogo(DOMINIO)
    assert info is not None
    assert (info.sitemap, info.total, info.parcial) == (INDICE, 5, False)
    assert almacen.buscar_catalogo(DOMINIO, "faro antiniebla", limite=10) == [FICHAS[2]]
    guardado = almacen.guardar_perfil(perfil)
    assert guardado.es_sitemap


def test_b_parcial_va_en_la_nota() -> None:
    navegador = con_sitemap(sin_buscador(jsonld=(0, 1, 2, 3)))
    navegador.paginas[DE_PRODUCTOS] = xml_urlset(FICHAS).replace("</urlset>", "<url><loc>")
    catalogo = CatalogoFalso()
    perfil, _ = ejecutar(navegador, [SIN_PROPUESTA], catalogo=catalogo)
    assert perfil.nota.startswith("Catálogo por sitemap (4 URL, parcial), sin IA.")
    assert catalogo.guardados[0][3] is True


def test_b_sin_donde_guardar_el_catalogo_no_se_mira_el_sitemap() -> None:
    navegador = con_sitemap(sin_buscador(jsonld=(0, 1, 2, 3)))
    motivo, _ = fallo(navegador, [SIN_PROPUESTA])
    assert navegador.grandes == []
    assert navegador.robots == []
    assert "URL de entrada" in motivo


def test_b_sitemap_bloqueado_se_propaga() -> None:
    navegador = con_sitemap(sin_buscador(jsonld=(0, 1, 2, 3)))
    navegador.errores = {DE_PRODUCTOS: Bloqueado(DE_PRODUCTOS, "Cloudflare")}
    catalogo = CatalogoFalso()
    sesion_ia, _ = sesion({COMPONENTE: [SIN_PROPUESTA]})
    with pytest.raises(Bloqueado):
        asyncio.run(
            configurar(
                DOMINIO,
                navegador,
                sesion_ia,
                CONSULTA,
                validador=validar_con_dns_publico,
                catalogo=catalogo,
                entrada=LISTADO,
            )
        )
    assert catalogo.guardados == []
    # Ante un bloqueo se para: ni el listado del usuario.
    assert LISTADO_1 not in navegador.urls


def test_b_sitemap_de_otro_dominio_en_robots_se_ignora() -> None:
    fuera = "https://otra-tienda.es/sitemap.xml"
    sitio = sin_buscador(jsonld=(0, 1, 2, 3))
    sitio[fuera] = xml_urlset(["https://otra-tienda.es/p/faro-golf"])
    sitio[f"{TIENDA}/sitemap.xml"] = xml_urlset(FICHAS)
    navegador = NavegadorFalso(sitio, declarados=(fuera,))
    perfil, _ = ejecutar(navegador, [SIN_PROPUESTA], catalogo=CatalogoFalso())
    assert perfil.url_busqueda == f"{TIENDA}/sitemap.xml"
    assert all("otra-tienda" not in u for u in navegador.urls)


def test_b_slugs_sin_palabras_pasa_al_listado_del_usuario() -> None:
    ids = [f"{TIENDA}/p/{n}" for n in range(100_000, 100_010)]
    sitio = sin_buscador()
    sitio[LISTADO_1] = html_listado()
    navegador = con_sitemap(sitio, ids)
    catalogo = CatalogoFalso()
    perfil, _ = ejecutar(
        navegador, [SIN_PROPUESTA, LISTADO_OK, CAMPOS_OK], catalogo=catalogo, entrada=LISTADO
    )
    assert perfil.tipo == TIPO_LISTADO
    assert catalogo.guardados == []
    # El catálogo no sirve: ni siquiera se abre una ficha suya.
    assert all(u not in navegador.urls for u in ids)


def test_b_slugs_sin_palabras_y_sin_entrada_falla() -> None:
    ids = [f"{TIENDA}/p/{n}" for n in range(100_000, 100_010)]
    navegador = con_sitemap(sin_buscador(), ids)
    motivo, _ = fallo(navegador, [SIN_PROPUESTA], catalogo=CatalogoFalso())
    assert "palabras" in motivo
    assert "URL de entrada" in motivo


def test_b_va_antes_que_el_listado_del_usuario() -> None:
    sitio = sin_buscador(jsonld=(0, 1, 2, 3))
    sitio[LISTADO_1] = html_listado()
    navegador = con_sitemap(sitio)
    perfil, _ = ejecutar(navegador, [SIN_PROPUESTA], catalogo=CatalogoFalso(), entrada=LISTADO)
    assert perfil.tipo == TIPO_SITEMAP
    assert LISTADO_1 not in navegador.urls


def test_b_tienda_sin_sitemap_ni_entrada_pide_la_url_de_entrada() -> None:
    navegador = NavegadorFalso(sin_buscador())
    motivo, peticiones = fallo(navegador, [SIN_PROPUESTA], catalogo=CatalogoFalso())
    assert "URL de entrada" in motivo
    assert "{consulta}" in motivo
    assert "{pagina}" in motivo
    assert len(navegador.grandes) == 3  # Las rutas habituales: 404.
    assert [p.esquema for p in peticiones] == [ESQUEMA_PLANTILLAS]


# --- A2: listado del usuario ----------------------------------------------------------------


def test_a2_listado_del_usuario() -> None:
    sitio = sin_buscador()
    sitio[LISTADO_1] = html_listado()
    navegador = NavegadorFalso(sitio)
    perfil, peticiones = ejecutar(
        navegador, [SIN_PROPUESTA, LISTADO_OK, CAMPOS_OK], entrada=LISTADO
    )
    assert perfil.tipo == TIPO_LISTADO
    assert perfil.es_listado
    assert perfil.url_busqueda == LISTADO
    assert (perfil.sel_resultado, perfil.sel_campos) == (LISTADO_OK["sel_resultado"], CAMPOS_OK)
    assert perfil.nota.startswith("Listado de la URL de entrada (sin buscador). Válido:")
    _, listado, ficha = peticiones
    assert "listado o categoría" in listado.instrucciones
    assert '<li class="producto">' in listado.datos
    assert PRODUCTOS[0].referencia in ficha.datos
    # La página 1 del listado, por HTTP.
    assert (LISTADO_1, False) in navegador.pedidas


def test_a2_entrada_de_otro_dominio_no_se_descarga() -> None:
    entrada = "https://otra-tienda.es/faros?p={pagina}"
    navegador = NavegadorFalso(sin_buscador())
    motivo, _ = fallo(navegador, [SIN_PROPUESTA], entrada=entrada)
    assert "URL de entrada" in motivo
    assert all("otra-tienda" not in u for u in navegador.urls)


def test_a2_entrada_que_no_responde() -> None:
    navegador = NavegadorFalso(sin_buscador())
    motivo, peticiones = fallo(navegador, [SIN_PROPUESTA], entrada=LISTADO)
    assert "URL de entrada" in motivo
    assert "404" in motivo
    # No se le pide nada a la IA con una página de error.
    assert [p.esquema for p in peticiones] == [ESQUEMA_PLANTILLAS]


def test_a2_con_pocos_resultados_falla() -> None:
    sitio = sin_buscador()
    sitio[LISTADO_1] = html_listado(PRODUCTOS[:2])
    navegador = NavegadorFalso(sitio)
    motivo, _ = fallo(
        navegador,
        [SIN_PROPUESTA, LISTADO_OK, CAMPOS_OK, LISTADO_OK, CAMPOS_OK],
        entrada=LISTADO,
    )
    assert "al menos 3" in motivo


# --- A2 con páginas reales -------------------------------------------------------------------

WEBSCRAPER = "webscraper.io"
SIN_MICRODATOS = ("referencia_oem", "marca", "estado")
LAPTOPS = f"https://{WEBSCRAPER}/test-sites/e-commerce/static/computers/laptops"


def _real(nombre: str) -> str:
    return (REALES / nombre).read_text(encoding="utf-8")


def test_a2_real_webscraper_listado_de_portatiles() -> None:
    ficha_31 = _real("webscraper_ficha_31.html")
    ficha_33 = ficha_31.replace("Packard 255 G2", "ThinkPad T540p").replace("$416.99", "$1178.99")
    producto = f"https://{WEBSCRAPER}/test-sites/e-commerce/static/product"
    sitio = {
        f"https://{WEBSCRAPER}/": "<html><body><h1>Web Scraper</h1>"
        "<a href='/test-sites'>Test sites</a></body></html>",
        f"{LAPTOPS}?page=1": _real("webscraper_listado_laptops.html"),
        f"{producto}/31": ficha_31,
        f"{producto}/32": _real("webscraper_ficha_32.html"),
        f"{producto}/33": ficha_33,
    }
    navegador = NavegadorFalso(sitio)
    listado = {
        "sel_resultado": "div.card.thumbnail",
        "sel_titulo": "a.title",
        "sel_enlace": "a.title @href",
        "sel_precio_listado": "h4.price span",
    }
    perfil, peticiones = ejecutar(
        navegador,
        # Los microdatos de la ficha ya dan la descripción y las fotos: no se piden.
        [SIN_PROPUESTA, listado, {"precio": "h4.price span", **dict.fromkeys(SIN_MICRODATOS)}],
        dominio=WEBSCRAPER,
        consulta="packard",
        entrada=f"{LAPTOPS}?page={{pagina}}",
        catalogo=CatalogoFalso(),
    )
    assert perfil.tipo == TIPO_LISTADO
    assert perfil.url_busqueda == f"{LAPTOPS}?page={{pagina}}"
    assert re.search(r"Válido: \d+ resultados, 3/3 fichas", perfil.nota), perfil.nota
    assert len(peticiones) == 3
    assert "Packard 255 G2" in peticiones[1].datos


BOOKS = "books.toscrape.com"
LIBROS = (
    ("a-light-in-the-attic_1000", "A Light in the Attic", "£51.77"),
    ("tipping-the-velvet_999", "Tipping the Velvet", "£53.74"),
    ("soumission_998", "Soumission", "£50.10"),
)


def test_a2_real_books_portada_como_listado() -> None:
    sitio = {f"https://{BOOKS}/": _real("books_portada.html")}
    for ruta, titulo, precio in LIBROS:
        sitio[f"https://{BOOKS}/catalogue/{ruta}/index.html"] = (
            f"<html><head><title>{titulo}</title></head><body><div class='product_main'>"
            f"<h1>{titulo}</h1><p class='price_color'>{precio}</p>"
            "<p class='instock availability'>In stock</p></div></body></html>"
        )
    navegador = NavegadorFalso(sitio)
    listado = {
        "sel_resultado": "article.product_pod",
        "sel_titulo": "h3 a @title",
        "sel_enlace": "h3 a @href",
        "sel_precio_listado": "p.price_color",
    }
    catalogo = CatalogoFalso()
    perfil, peticiones = ejecutar(
        navegador,
        [SIN_PROPUESTA, listado, {**NULOS, "precio": ".product_main p.price_color"}],
        dominio=BOOKS,
        consulta="sapiens",
        entrada=f"https://{BOOKS}/",
        catalogo=catalogo,
    )
    assert perfil.tipo == TIPO_LISTADO
    assert perfil.url_busqueda == f"https://{BOOKS}/"
    assert perfil.nota.startswith("Listado de la URL de entrada (sin buscador). Válido: 10 ")
    assert "3/3 fichas" in perfil.nota
    assert len(peticiones) == 3
    # Antes del listado se buscó el sitemap (books no tiene): rutas habituales, sin guardar nada.
    assert navegador.grandes == [
        f"https://{BOOKS}/sitemap.xml",
        f"https://{BOOKS}/sitemap_index.xml",
        f"https://{BOOKS}/wp-sitemap.xml",
    ]
    assert catalogo.guardados == []


# --- Mensaje final -----------------------------------------------------------------------------


def test_sin_buscador_ni_entrada_pide_la_url_de_entrada() -> None:
    motivo, _ = fallo(NavegadorFalso(sin_buscador()), [SIN_PROPUESTA])
    assert "búsqueda" in motivo
    assert "URL de entrada" in motivo


def test_b_guarda_los_hijos_que_no_caben_en_el_tope(tmp_path: Path) -> None:
    # R2-C14b: los hijos del índice que no se leyeron quedan en el almacén para que una búsqueda
    # descargue después los que nombran la consulta.
    almacen = Almacen(tmp_path / "precia.db", reloj=lambda: INSTANTE)
    almacen.alta_tienda(DOMINIO, "Recambios Prueba")
    navegador = con_sitemap(sin_buscador(jsonld=(0, 1, 2, 3)))
    extra = [f"{TIENDA}/sitemaps/product-sitemap{n}.xml" for n in range(2, 14)]
    navegador.paginas[INDICE] = xml_indice([DE_PRODUCTOS, *extra])
    ejecutar(navegador, [SIN_PROPUESTA], catalogo=almacen)
    info = almacen.catalogo(DOMINIO)
    assert info is not None and info.parcial
    leidos = MAX_FICHEROS_SITEMAP - 2  # El índice y el de productos ya cuentan.
    assert almacen.hijos_pendientes(DOMINIO) == extra[leidos:]


# --- B: causa R3-C de la campaña, si un sitemap no da fichas con precio se prueba otro ---------

INFORMATIVAS = f"{TIENDA}/_includes/sitemap.xml"
COMIDA = f"{TIENDA}/crs/cdn-static/sitemap-food/index.xml"
HTML_INFORMATIVA = (
    "<html><head><title>Calidad y origen</title></head><body><h1>Más información</h1>"
    "<p>Conoce cómo elegimos los productos de nuestra marca.</p></body></html>"
)


def _informativas(sitio: dict[str, str]) -> list[str]:
    """El `_includes/sitemap.xml` real de Carrefour (ronda 3, recortado: 3 páginas informativas),
    servido en la tienda falsa con sus páginas sin precio. Devuelve sus URL."""
    texto = (REALES / "carrefour_sitemap_includes_informativas.xml").read_bytes().decode("utf-8")
    texto = texto.replace("https://www.carrefour.es", TIENDA)
    sitio[INFORMATIVAS] = texto
    urls = re.findall(r"<loc>([^<]+)</loc>", texto)
    for url in urls:
        sitio[url] = HTML_INFORMATIVA
    return urls


def test_b_si_las_fichas_del_primer_sitemap_no_tienen_precio_prueba_el_siguiente() -> None:
    # Carrefour: declara primero `_includes/sitemap.xml` (páginas informativas) y el catálogo del
    # supermercado (`sitemap-food`) después; se validaba el primero y, sin precio, se fallaba.
    sitio = sin_buscador(jsonld=(0, 1, 2, 3))
    informativas = _informativas(sitio)
    sitio[COMIDA] = xml_urlset(FICHAS)
    navegador = NavegadorFalso(sitio, declarados=(INFORMATIVAS, COMIDA))
    catalogo = CatalogoFalso()
    perfil, peticiones = ejecutar(
        navegador, [SIN_PROPUESTA, {**NULOS, "precio": "span.no-existe"}], catalogo=catalogo
    )
    assert perfil.tipo == TIPO_SITEMAP
    assert perfil.url_busqueda == COMIDA
    assert perfil.sel_campos == {}
    assert perfil.nota.startswith("Catálogo por sitemap (4 URL), sin IA. Válido:")
    ((dominio, sitemap, entradas, _),) = catalogo.guardados
    assert (dominio, sitemap) == (DOMINIO, COMIDA)
    assert [url for url, _, _ in entradas] == FICHAS
    assert navegador.grandes[:2] == [INFORMATIVAS, COMIDA]
    assert set(informativas[:2]) <= set(navegador.urls)
    # Las páginas informativas no parecen un producto: la IA no se gasta en ellas (R5-F) y el
    # segundo sitemap vale sin IA, así que solo hay la propuesta de plantillas del buscador.
    assert len(peticiones) == 1


def test_b_si_las_fichas_del_primer_sitemap_dan_404_prueba_el_siguiente_sin_ia() -> None:
    # El Corte Inglés: el sitemap de entradas de teatro da fichas 404; el de productos va detrás.
    sitio = sin_buscador(jsonld=(0, 1, 2, 3))
    entradas = f"{TIENDA}/entradas/sitemaps/sitemap.xml"
    variantes = f"{TIENDA}/sgfm/statics-front/contents/sitemap-variants/sitemap.xml.gz"
    teatro = [f"{TIENDA}/entradas/teatro/entradas-faro-golf-{c}/" for c in ("bilbao", "madrid")]
    sitio[entradas] = xml_urlset(teatro)
    sitio[variantes] = xml_urlset(FICHAS)
    navegador = NavegadorFalso(sitio, declarados=(entradas, variantes))
    catalogo = CatalogoFalso()
    perfil, peticiones = ejecutar(navegador, [SIN_PROPUESTA], catalogo=catalogo)
    assert perfil.url_busqueda == variantes
    assert [s for _, s, _, _ in catalogo.guardados] == [variantes]
    assert set(teatro) <= set(navegador.urls)
    # Ninguna ficha del primero se abrió: no se gasta la IA en él.
    assert len(peticiones) == 1


def test_b_un_primer_sitemap_valido_no_descarga_el_segundo() -> None:
    sitio = sin_buscador(jsonld=(0, 1, 2, 3))
    segundo = f"{TIENDA}/sitemaps/sitemap-otro.xml"
    sitio[DE_PRODUCTOS] = xml_urlset(FICHAS)
    sitio[segundo] = xml_urlset(FICHAS)
    navegador = NavegadorFalso(sitio, declarados=(DE_PRODUCTOS, segundo))
    perfil, _ = ejecutar(navegador, [SIN_PROPUESTA], catalogo=CatalogoFalso())
    assert perfil.url_busqueda == DE_PRODUCTOS
    assert navegador.grandes == [DE_PRODUCTOS]


def test_b_como_mucho_max_sitemaps_probados_y_el_motivo_junta_los_de_todos() -> None:
    sitio = sin_buscador()
    declarados = tuple(
        f"{TIENDA}/seccion{n}/sitemap-{n}.xml" for n in range(MAX_SITEMAPS_PROBADOS + 2)
    )
    for n, declarado in enumerate(declarados):
        sitio[declarado] = xml_urlset(
            [f"{TIENDA}/seccion{n}/faro-golf-{c}" for c in ("rojo", "azul", "negro")]
        )
    navegador = NavegadorFalso(sitio, declarados=declarados)
    catalogo = CatalogoFalso()
    motivo, peticiones = fallo(navegador, [SIN_PROPUESTA], catalogo=catalogo)
    assert catalogo.guardados == []
    probados = declarados[:MAX_SITEMAPS_PROBADOS]
    assert [g for g in navegador.grandes if g in declarados] == list(probados)
    assert f"{MAX_SITEMAPS_PROBADOS} sitemaps" in motivo
    assert all(p in motivo for p in probados)
    assert "URL de entrada" in motivo
    assert len(peticiones) == 1
