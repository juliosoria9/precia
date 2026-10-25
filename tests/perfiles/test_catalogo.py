"""Catálogo de una tienda sin buscador por su `sitemap.xml` (nota 16 §B, `perfiles.catalogo`).

Sin red: el `NavegadorFalso` sirve los sitemaps (ya descomprimidos, como los entrega
`Navegador.obtener(..., grande=True)` con un `.xml.gz`) y las líneas `Sitemap:` del robots.txt.
Todo lo que sale de un sitemap es dato de la web: solo se descarga lo de la propia tienda.
"""

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from precia.fuentes.plataformas import Plataforma
from precia.fuentes.sitemap import (
    MAX_FICHEROS_SITEMAP,
    MAX_HIJOS_BUSQUEDA,
    hijos_de_producto,
    leer_sitemap,
    palabras_url,
)
from precia.navegador.tipos import (
    Bloqueado,
    DemasiadoGrande,
    ErrorDescarga,
    Pagina,
    URLNoPermitida,
)
from precia.perfiles import catalogo as modulo
from precia.perfiles.catalogo import (
    MIN_CON_PALABRAS,
    Catalogo,
    UrlCatalogo,
    catalogos,
    descubrir_catalogo,
    elegir_fichas,
    leer_catalogo,
    leer_hijos,
)
from precia.perfiles.validador import MAX_FICHAS
from tests.cronometro import segundos_de_cpu
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    DOMINIO,
    PORTADA,
    PRODUCTOS,
    TIENDA,
    NavegadorFalso,
    xml_indice,
    xml_urlset,
)

INDICE = f"{TIENDA}/sitemaps/indice.xml"
DE_PRODUCTOS = f"{TIENDA}/sitemaps/product-sitemap.xml.gz"
DE_PAGINAS = f"{TIENDA}/sitemaps/page-sitemap.xml"
FICHAS = [p.url for p in PRODUCTOS]


def descubrir(
    navegador: NavegadorFalso,
    plataforma: Plataforma | None = None,
    **opciones: Any,
) -> Catalogo | None:
    opciones.setdefault("validador", validar_con_dns_publico)
    return asyncio.run(descubrir_catalogo(TIENDA, DOMINIO, navegador, plataforma, **opciones))


def con_indice(**extra: str) -> NavegadorFalso:
    """Tienda con un índice declarado en robots.txt: productos (`.xml.gz`) y páginas."""
    paginas = {
        INDICE: xml_indice([DE_PAGINAS, DE_PRODUCTOS]),
        DE_PRODUCTOS: xml_urlset(FICHAS),
        DE_PAGINAS: xml_urlset([f"{TIENDA}/aviso-legal", f"{TIENDA}/contacto"]),
        **extra,
    }
    return NavegadorFalso(paginas, declarados=(INDICE,))


@dataclass
class NavegadorConEstado(NavegadorFalso):
    """El sitio falso, pero algunas URL responden con un estado HTTP concreto."""

    estados: dict[str, int] = field(default_factory=dict)

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        respuesta = await super().obtener(url, js=js, grande=grande)
        if (estado := self.estados.get(respuesta.url)) is None:
            return respuesta
        return replace(respuesta, estado=estado)


# --- Encontrar el sitemap ------------------------------------------------------------------------


def test_indice_declarado_en_robots_con_un_xml_gz_de_productos() -> None:
    navegador = con_indice()
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert catalogo.sitemap == INDICE
    assert [e.url for e in catalogo.entradas] == FICHAS
    assert catalogo.entradas[0] == UrlCatalogo(FICHAS[0], palabras_url(FICHAS[0]), "2026-10-19")
    assert not catalogo.parcial
    assert catalogo.ficheros == 2
    # El robots.txt de la tienda, el índice y su sitemap de productos, siempre como fichero
    # grande (sin Chromium ni caché); el de páginas no se descarga.
    assert navegador.robots == [PORTADA]
    assert navegador.grandes == [INDICE, DE_PRODUCTOS]
    assert navegador.pedidas == [(INDICE, False), (DE_PRODUCTOS, False)]
    assert catalogo.con_palabras == 1.0


def test_sin_declarar_prueba_las_rutas_habituales_en_orden() -> None:
    navegador = NavegadorFalso({f"{TIENDA}/sitemap_index.xml": xml_urlset(FICHAS)})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert catalogo.sitemap == f"{TIENDA}/sitemap_index.xml"
    # `/sitemap.xml` da 404 y cuenta como una descarga.
    assert navegador.grandes == [f"{TIENDA}/sitemap.xml", f"{TIENDA}/sitemap_index.xml"]
    assert catalogo.ficheros == 2


def test_rutas_de_la_plataforma() -> None:
    productos = f"{TIENDA}/product-sitemap.xml"
    navegador = NavegadorFalso({productos: xml_urlset(FICHAS)})
    catalogo = descubrir(navegador, Plataforma.WOOCOMMERCE)
    assert catalogo is not None
    assert catalogo.sitemap == productos


def test_sin_sitemap() -> None:
    navegador = NavegadorFalso({PORTADA: "<html></html>"})
    assert descubrir(navegador) is None
    assert navegador.grandes == [
        f"{TIENDA}/sitemap.xml",
        f"{TIENDA}/sitemap_index.xml",
        f"{TIENDA}/wp-sitemap.xml",
    ]


def test_una_pagina_html_con_200_no_es_un_sitemap() -> None:
    # Muchas tiendas devuelven la portada con un 200 para cualquier ruta.
    html = "<html><body><a href='/p/faro'>Faro</a></body></html>"
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": html, f"{TIENDA}/wp-sitemap.xml": html})
    assert descubrir(navegador) is None


def test_sitemap_de_otro_dominio_declarado_en_robots_se_ignora() -> None:
    fuera = "https://otra-tienda.es/sitemap.xml"
    navegador = NavegadorFalso(
        {fuera: xml_urlset(["https://otra-tienda.es/p/faro-golf-vi"])},
        declarados=(fuera, "/sitemap-relativo.xml", "javascript:alert(1)"),
    )
    assert descubrir(navegador) is None
    assert all("otra-tienda" not in u for u in navegador.urls)
    assert f"{TIENDA}/sitemap-relativo.xml" not in navegador.urls


def test_hijos_y_entradas_de_otro_dominio_no_se_usan() -> None:
    fuera = "https://otra-tienda.es/product-sitemap.xml"
    urls = [*FICHAS, "https://otra-tienda.es/p/faro-golf", f"https://{DOMINIO}/p/faro-sin-www"]
    navegador = con_indice()
    navegador.paginas[INDICE] = xml_indice([fuera, DE_PRODUCTOS])
    navegador.paginas[DE_PRODUCTOS] = xml_urlset(urls)
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert [e.url for e in catalogo.entradas] == [*FICHAS, f"https://{DOMINIO}/p/faro-sin-www"]
    assert all("otra-tienda" not in u for u in navegador.urls)


def test_sitemap_que_redirige_fuera_no_cuenta() -> None:
    fuera = "https://otra-tienda.es/sitemap.xml"
    navegador = NavegadorFalso(
        {fuera: xml_urlset(FICHAS)}, redirecciones={f"{TIENDA}/sitemap.xml": fuera}
    )
    assert descubrir(navegador) is None


@pytest.mark.parametrize(
    "error",
    [ErrorDescarga("no responde"), URLNoPermitida("privada")],
    ids=["descarga", "url"],
)
def test_un_sitemap_que_no_se_puede_descargar_se_salta(error: Exception) -> None:
    navegador = con_indice(**{f"{TIENDA}/sitemap.xml": xml_urlset(FICHAS[:2])})
    navegador.errores = {INDICE: error}
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert catalogo.sitemap == f"{TIENDA}/sitemap.xml"


@pytest.mark.parametrize("estado", [403, 404, 500])
def test_un_sitemap_que_no_responde_bien_se_salta(estado: int) -> None:
    sitio = {INDICE: xml_urlset(FICHAS[:1]), f"{TIENDA}/sitemap.xml": xml_urlset(FICHAS)}
    navegador = NavegadorConEstado(sitio, declarados=(INDICE,), estados={INDICE: estado})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert len(catalogo.entradas) == len(FICHAS)


def test_cada_url_pasa_por_el_validador() -> None:
    rechazadas: list[str] = []

    async def validador(url: str) -> str:
        if url == INDICE:
            rechazadas.append(url)
            raise URLNoPermitida("apunta a una red interna")
        return await validar_con_dns_publico(url)

    navegador = con_indice(**{f"{TIENDA}/sitemap.xml": xml_urlset(FICHAS)})
    catalogo = descubrir(navegador, validador=validador)
    assert catalogo is not None
    assert rechazadas == [INDICE]
    assert INDICE not in navegador.urls


# --- Bloqueos: se para -------------------------------------------------------------------------


def test_sitemap_bloqueado_se_propaga() -> None:
    navegador = con_indice()
    navegador.errores = {INDICE: Bloqueado(INDICE, "Cloudflare")}
    with pytest.raises(Bloqueado):
        descubrir(navegador)
    # No se sigue con las rutas habituales: ante un bloqueo, se para.
    assert navegador.grandes == [INDICE]


def test_hijo_bloqueado_se_propaga() -> None:
    navegador = con_indice()
    navegador.errores = {DE_PRODUCTOS: Bloqueado(DE_PRODUCTOS, "DataDome")}
    with pytest.raises(Bloqueado):
        descubrir(navegador)


def test_robots_bloqueado_se_propaga() -> None:
    navegador = con_indice()
    navegador.error_robots = Bloqueado(f"{TIENDA}/robots.txt", "Just a moment")
    with pytest.raises(Bloqueado):
        descubrir(navegador)
    assert navegador.grandes == []


@pytest.mark.parametrize("error", [ErrorDescarga("x"), URLNoPermitida("x")])
def test_robots_que_no_se_puede_leer_no_impide_las_rutas_habituales(error: Exception) -> None:
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": xml_urlset(FICHAS)}, error_robots=error)
    catalogo = descubrir(navegador)
    assert catalogo is not None


# --- Topes: parcial -------------------------------------------------------------------------


def test_tope_de_ficheros() -> None:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(1, 16)]
    paginas = {INDICE: xml_indice(hijos)}
    for n, hijo in enumerate(hijos):
        paginas[hijo] = xml_urlset([f"{TIENDA}/p/faro-golf-modelo-{n}"])
    navegador = NavegadorFalso(paginas, declarados=(INDICE,))
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert len(navegador.grandes) == MAX_FICHEROS_SITEMAP == 10
    assert catalogo.ficheros == MAX_FICHEROS_SITEMAP
    assert len(catalogo.entradas) == MAX_FICHEROS_SITEMAP - 1
    assert catalogo.parcial


def test_el_tope_de_ficheros_cuenta_tambien_los_que_fallan() -> None:
    declarados = tuple(f"{TIENDA}/no-existe-{n}.xml" for n in range(20))
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": xml_urlset(FICHAS)}, declarados=declarados)
    assert descubrir(navegador) is None
    assert len(navegador.grandes) == MAX_FICHEROS_SITEMAP


def test_sitemap_recortado_es_parcial() -> None:
    recortado = xml_urlset(FICHAS).replace("</urlset>", "<url><loc>https://www.recambios")
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": recortado})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert len(catalogo.entradas) == len(FICHAS)
    assert catalogo.parcial


def test_tope_de_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modulo, "MAX_URLS_CATALOGO", 3)
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": xml_urlset(FICHAS)})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert [e.url for e in catalogo.entradas] == FICHAS[:3]
    assert catalogo.parcial


def test_indice_dentro_de_un_indice() -> None:
    segundo = f"{TIENDA}/sitemaps/product-index.xml"
    navegador = con_indice()
    navegador.paginas[INDICE] = xml_indice([segundo])
    navegador.paginas[segundo] = xml_indice([DE_PRODUCTOS, INDICE])
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert [e.url for e in catalogo.entradas] == FICHAS
    # El índice que se enlaza a sí mismo no se vuelve a pedir.
    assert navegador.grandes == [INDICE, segundo, DE_PRODUCTOS]


def test_el_sitemap_de_productos_declarado_antes_que_el_de_categorias() -> None:
    # Coolmod (ronda 1 de la campaña multi-sector): su robots.txt declara primero `/sitemap.xml`,
    # que solo trae categorías sin precio, y el catálogo se quedaba con él.
    general, de_productos = f"{TIENDA}/sitemap.xml", f"{TIENDA}/sitemap_products.xml"
    categorias_xml = f"{TIENDA}/sitemap_categories.xml"
    declarados = (general, f"{TIENDA}/sitemap2.xml", categorias_xml, de_productos)
    categorias = [f"{TIENDA}/faros-y-pilotos/", f"{TIENDA}/frenos-y-discos/"]
    paginas = {general: xml_urlset(categorias), de_productos: xml_urlset(FICHAS)}
    navegador = NavegadorFalso(paginas, declarados=declarados)
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert catalogo.sitemap == de_productos
    assert [e.url for e in catalogo.entradas] == FICHAS
    assert navegador.grandes == [de_productos]


def test_los_hijos_de_producto_se_leen_antes_en_cualquier_nivel() -> None:
    # PcComponentes (ronda 1): el índice raíz enlaza comparativas, black friday y el índice de
    # artículos; este, cientos de feeds de categoría. Se leían las comparativas antes que los
    # feeds y, de estos, solo los dos que casaban por accidente con «articulos» y «productos».
    raiz = f"{TIENDA}/sitemap/sitemap.xml"
    articulos = f"{TIENDA}/sitemap/sitemap_articles.xml"
    comparativas = f"{TIENDA}/sitemap/comparatives.xml"
    black_friday = f"{TIENDA}/async-feed/black-friday-sitemap-es.xml"
    feed = f"{TIENDA}/async-feed/sitemap-{{}}-es.xml"
    limpieza = feed.format("articulos-de-limpieza")
    faros, frenos = feed.format("faros"), feed.format("frenos")
    paginas = {
        raiz: xml_indice([comparativas, black_friday, articulos]),
        articulos: xml_indice([faros, limpieza, frenos]),
        comparativas: xml_urlset([f"{TIENDA}/comparador/faro-a-vs-faro-b"]),
        black_friday: xml_urlset([f"{TIENDA}/black-friday"]),
        limpieza: xml_urlset([f"{TIENDA}/p/limpiador-de-llantas"]),
        faros: xml_urlset(FICHAS[:2]),
        frenos: xml_urlset(FICHAS[2:]),
    }
    navegador = NavegadorFalso(paginas, declarados=(raiz,))
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert catalogo.sitemap == raiz
    assert navegador.grandes == [
        raiz,
        articulos,
        limpieza,
        faros,
        frenos,
        comparativas,
        black_friday,
    ]
    urls = [e.url for e in catalogo.entradas]
    assert urls[1:] == [*FICHAS, f"{TIENDA}/comparador/faro-a-vs-faro-b", f"{TIENDA}/black-friday"]


def test_urls_repetidas_cuentan_una_vez() -> None:
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": xml_urlset([*FICHAS, *FICHAS])})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert len(catalogo.entradas) == len(FICHAS)


# --- Slugs con palabras --------------------------------------------------------------------


def test_slugs_sin_palabras() -> None:
    assert MIN_CON_PALABRAS == 0.6
    ids = [f"{TIENDA}/p/{n}" for n in range(100_000, 100_010)]
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": xml_urlset(ids)})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert catalogo.con_palabras < MIN_CON_PALABRAS


# --- Refresco desde un sitemap conocido ----------------------------------------------------------


def test_leer_catalogo_desde_la_raiz_conocida() -> None:
    navegador = con_indice()
    catalogo = asyncio.run(
        leer_catalogo(INDICE, DOMINIO, navegador, validador=validar_con_dns_publico)
    )
    assert catalogo is not None
    assert [e.url for e in catalogo.entradas] == FICHAS
    # Sin robots.txt: la raíz ya se conoce (es la `url_busqueda` del perfil).
    assert navegador.robots == []


def test_leer_catalogo_de_otro_dominio_no_descarga() -> None:
    navegador = con_indice()
    resultado = asyncio.run(
        leer_catalogo(
            "https://otra-tienda.es/sitemap.xml",
            DOMINIO,
            navegador,
            validador=validar_con_dns_publico,
        )
    )
    assert resultado is None
    assert navegador.pedidas == []


# --- Elegir las fichas que se prueban --------------------------------------------------------


def _catalogo(urls: list[str]) -> Catalogo:
    return Catalogo(
        sitemap=f"{TIENDA}/sitemap.xml",
        entradas=tuple(UrlCatalogo(u, palabras_url(u)) for u in urls),
        parcial=False,
        ficheros=1,
    )


def test_elegir_fichas_primero_las_que_encajan() -> None:
    urls = [
        f"{TIENDA}/",
        f"{TIENDA}/aviso-legal",
        f"{TIENDA}/p/amortiguador-delantero-bilstein",
        f"{TIENDA}/p/faro-izquierdo-golf-vi",
        f"{TIENDA}/p/piloto-golf-vi",
        f"{TIENDA}/p/faro-derecho-golf-vi",
    ]
    elegidas = elegir_fichas(_catalogo(urls), "faro golf")
    assert elegidas == [urls[3], urls[5], urls[4]]
    assert len(elegidas) == MAX_FICHAS


def test_elegir_fichas_sin_coincidencias_las_primeras_con_palabras() -> None:
    urls = [
        f"{TIENDA}/",
        f"{TIENDA}/p/amortiguador-delantero-bilstein",
        f"{TIENDA}/123",
        f"{TIENDA}/p/bateria-varta-70ah",
        f"{TIENDA}/p/disco-freno-brembo",
        f"{TIENDA}/p/pastillas-freno-bosch",
    ]
    assert elegir_fichas(_catalogo(urls), "zzz") == urls[1:2] + urls[3:5]


def test_elegir_fichas_slug_con_marcador_final_antes_que_un_id() -> None:
    # Campaña multi-sector (causa R4-A): `/<slug>/<EAN>/p` es un slug legible; `/p/123456`, no.
    urls = [
        f"{TIENDA}/p/123456",
        f"{TIENDA}/vacuum-agua-06033d12w0/3165140894265/p",
    ]
    assert elegir_fichas(_catalogo(urls), "zzz") == [urls[1], urls[0]]


def _kenay() -> Catalogo:
    # Campaña multi-sector (causa R4-I): Kenay (PrestaShop) pone en el mismo sitemap 251
    # categorías sin extensión (`/es/1695-muebles-de-jardin-y-exterior`) delante de 1 390
    # fichas `.html`; en el recorte, 4 categorías y 12 fichas.
    sitemap = leer_sitemap(_real("kenay_sitemap_es_categorias_delante.xml"))
    return _catalogo([e.url for e in sitemap.entradas])


def test_elegir_fichas_sin_coincidencias_prefiere_la_forma_dominante() -> None:
    catalogo = _kenay()
    fichas = [e.url for e in catalogo.entradas if e.url.endswith(".html")]
    assert len(fichas) == 12
    assert elegir_fichas(catalogo, "filtro") == fichas[:MAX_FICHAS]


def test_elegir_fichas_la_forma_no_adelanta_a_las_que_encajan_mejor() -> None:
    # La forma solo desempata dentro de un mismo número de palabras de la consulta.
    elegidas = elegir_fichas(_kenay(), "sillas jardin")
    assert elegidas[0] == "https://kenayhome.com/es/1696-sillas-de-jardin"


def test_elegir_fichas_sin_forma_dominante_no_reordena() -> None:
    urls = [
        f"{TIENDA}/es/10-sillas-de-jardin",
        f"{TIENDA}/es/11-mesas-de-jardin",
        f"{TIENDA}/es/7882-buggy-carrito-rosa.html",
        f"{TIENDA}/es/7883-buggy-carrito-menta.html",
    ]
    assert elegir_fichas(_catalogo(urls), "zzz") == urls[:MAX_FICHAS]


def test_elegir_fichas_los_filtros_con_query_van_detras_de_las_fichas() -> None:
    # Una categoría filtrada (`?prefn1=…`, SFCC) tiene otra forma que las fichas `.html`.
    urls = [
        f"{TIENDA}/gafas-de-sol/?prefn1=hk_badge_marketing&prefv1=BEST%20SELLERS",
        *(f"{TIENDA}/gafas-de-sol/modelo-numero-{n}.html" for n in range(1, 5)),
    ]
    assert elegir_fichas(_catalogo(urls), "zzz") == urls[1 : 1 + MAX_FICHAS]


# --- Casos hostiles cronometrados -----------------------------------------------------------


def test_sitemap_de_50000_urls_en_poco_tiempo() -> None:
    urls = [f"{TIENDA}/p/faro-golf-vi-modelo-{n}-{n * 7}" for n in range(50_000)]
    paginas = {f"{TIENDA}/sitemap.xml": xml_urlset(urls)}
    catalogos: list[Catalogo | None] = []
    # Medido: unos 5-7 s con la máquina libre (casi todo en `palabras_url` y `url_de_la_tienda`).
    # El tope es holgado para que no falle con carga; un coste cuadrático no bajaría de minutos.
    # Segundos de CPU, el mejor de hasta 3 intentos (bug 6).
    cpu = segundos_de_cpu(lambda: catalogos.append(descubrir(NavegadorFalso(paginas))), 30)
    catalogo = catalogos[-1]
    assert catalogo is not None
    assert len(catalogo.entradas) == 50_000
    assert cpu < 30, cpu
    assert segundos_de_cpu(lambda: elegir_fichas(catalogo, "faro golf modelo 49999"), 2) < 2


def test_urls_hostiles_del_sitemap_no_entran() -> None:
    hostiles = [
        f"{TIENDA}/p/faro\\@otra-tienda.es",
        f"https://usuario:clave@{DOMINIO}/p/faro",
        f"https://{DOMINIO}:8443/p/faro",
        "http://127.0.0.1/p/faro",
        f"{TIENDA}/" + "a" * 3000,
        "file:///etc/passwd",
    ]
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": xml_urlset([*hostiles, FICHAS[0]])})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    urls = [e.url for e in catalogo.entradas]
    assert FICHAS[0] in urls
    for url in urls:
        assert url.startswith(TIENDA) or url.startswith(f"https://{DOMINIO}/")
        assert "@" not in url.split("/")[2] and "\\" not in url
        assert len(url) <= 2000


def test_entidades_y_dtd_no_se_expanden() -> None:
    # «Billion laughs»: el sitemap con DTD se trata como vacío y se pasa al siguiente candidato.
    risas = (
        '<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY a "jajajajaja">'
        + "".join(f'<!ENTITY a{n} "{"&a;" * 10}">' for n in range(1, 10))
        + ']><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://www.recambios-prueba.es/p/&a9;</loc></url></urlset>"
    )
    paginas = {f"{TIENDA}/sitemap.xml": risas, f"{TIENDA}/sitemap_index.xml": xml_urlset(FICHAS)}
    catalogos: list[Catalogo | None] = []
    assert segundos_de_cpu(lambda: catalogos.append(descubrir(NavegadorFalso(paginas))), 1) < 1
    catalogo = catalogos[-1]
    assert catalogo is not None
    assert catalogo.sitemap == f"{TIENDA}/sitemap_index.xml"
    assert all("jaja" not in e.url for e in catalogo.entradas)


def test_anidamiento_profundo_no_rompe_nada() -> None:
    profundo = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{FICHAS[0]}</loc></url>" + "<x>" * 200_000 + "</urlset>"
    )
    paginas = {f"{TIENDA}/sitemap.xml": profundo}
    catalogos: list[Catalogo | None] = []
    assert segundos_de_cpu(lambda: catalogos.append(descubrir(NavegadorFalso(paginas))), 2) < 2
    catalogo = catalogos[-1]
    assert catalogo is not None
    assert [e.url for e in catalogo.entradas] == FICHAS[:1]
    assert catalogo.parcial


def test_indice_con_miles_de_hijos_no_multiplica_las_descargas() -> None:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(50_000)]
    paginas = {INDICE: xml_indice(hijos)}
    paginas.update({h: xml_urlset([f"{TIENDA}/p/faro-golf-{n}"]) for n, h in enumerate(hijos[:20])})
    navegadores: list[NavegadorFalso] = []
    catalogos: list[Catalogo | None] = []

    def descubrir_con_uno_nuevo() -> None:
        # Un navegador nuevo en cada intento: el falso apunta lo que se descarga.
        navegadores.append(NavegadorFalso(paginas, declarados=(INDICE,)))
        catalogos.append(descubrir(navegadores[-1]))

    # Unos 1,2 s de CPU en la suite completa; el tope deja más de 3 veces eso (bug 6). Leer los
    # 50 000 hijos, o compararlos entre sí, no bajaría de decenas de segundos.
    assert segundos_de_cpu(descubrir_con_uno_nuevo, 4) < 4
    navegador, catalogo = navegadores[-1], catalogos[-1]
    assert catalogo is not None
    assert len(navegador.grandes) == MAX_FICHEROS_SITEMAP
    assert catalogo.parcial


# --- Hijos del índice sin leer (R2-C14b) ---------------------------------------------------------

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
PCC = "https://www.pccomponentes.com"
PCC_INDICE = f"{PCC}/sitemap/sitemap_articles.xml"


def _indice_pccomponentes() -> str:
    return (REALES / "pccomponentes_sitemap_indice_articulos.xml").read_text(encoding="utf-8")


def test_los_hijos_que_no_caben_en_el_tope_quedan_pendientes() -> None:
    # PcComponentes (ronda 2): 424 feeds por categoría y solo se leían los primeros; el de
    # portátiles se perdía sin dejar rastro. Ahora queda en la lista de pendientes, en el orden
    # en que se habría leído.
    hijos = hijos_de_producto(leer_sitemap(_indice_pccomponentes()))
    paginas = {PCC_INDICE: _indice_pccomponentes()}
    for hijo in hijos:
        nombre = hijo.rsplit("sitemap-", 1)[1].removesuffix("-es.xml")
        paginas[hijo] = xml_urlset([f"{PCC}/{nombre}-modelo-x"])
    navegador = NavegadorFalso(paginas, declarados=(PCC_INDICE,))
    catalogo = asyncio.run(
        descubrir_catalogo(
            f"{PCC}/", "pccomponentes.com", navegador, None, validador=validar_con_dns_publico
        )
    )
    assert catalogo is not None and catalogo.parcial
    leidos = MAX_FICHEROS_SITEMAP - 1
    assert navegador.grandes == [PCC_INDICE, *hijos[:leidos]]
    assert catalogo.pendientes == tuple(hijos[leidos:])
    assert f"{PCC}/async-feed/sitemap-portatiles-es.xml" in catalogo.pendientes


def test_pendientes_solo_de_producto_y_de_la_tienda() -> None:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(1, 13)]
    ajenos = [f"{TIENDA}/page-sitemap.xml", "https://otra-tienda.es/product-sitemap.xml"]
    paginas = {INDICE: xml_indice([*ajenos, *hijos])}
    navegador = NavegadorFalso(paginas, declarados=(INDICE,))
    catalogo = descubrir(navegador)
    # Los hijos dan 404: no hay catálogo, pero se ve que tampoco se pidió ninguno ajeno.
    assert catalogo is None
    assert navegador.grandes == [INDICE, *hijos[: MAX_FICHEROS_SITEMAP - 1]]


def test_sin_llegar_al_tope_no_hay_pendientes() -> None:
    catalogo = descubrir(con_indice())
    assert catalogo is not None and catalogo.pendientes == ()


def test_leer_hijos_lee_solo_los_pedidos() -> None:
    faros = f"{TIENDA}/sitemaps/sitemap-faros.xml"
    otro_indice = f"{TIENDA}/sitemaps/sitemap-frenos-index.xml"
    frenos = f"{TIENDA}/sitemaps/sitemap-frenos-1.xml"
    paginas = {
        faros: xml_urlset([*FICHAS[:2], "https://otra-tienda.es/p/faro-golf"]),
        otro_indice: xml_indice([frenos, DE_PAGINAS, "https://otra-tienda.es/s.xml"]),
    }
    navegador = NavegadorFalso(paginas)
    leido = asyncio.run(
        leer_hijos(
            INDICE, [faros, otro_indice], DOMINIO, navegador, validador=validar_con_dns_publico
        )
    )
    assert leido is not None
    assert navegador.grandes == [faros, otro_indice]
    assert [e.url for e in leido.entradas] == FICHAS[:2]
    # Los hijos de un índice dentro del índice no se descargan ahora: quedan pendientes.
    assert leido.pendientes == (frenos,)
    assert (leido.sitemap, leido.ficheros, leido.parcial) == (INDICE, 2, False)


def test_leer_hijos_con_tope_y_solo_de_la_tienda() -> None:
    hijos = [f"{TIENDA}/sitemaps/sitemap-{n}.xml" for n in range(5)]
    navegador = NavegadorFalso({h: xml_urlset(FICHAS[:1]) for h in hijos})
    leido = asyncio.run(
        leer_hijos(
            INDICE,
            ["https://otra-tienda.es/sitemap-faros.xml", *hijos],
            DOMINIO,
            navegador,
            validador=validar_con_dns_publico,
        )
    )
    assert leido is not None
    assert navegador.grandes == hijos[:MAX_HIJOS_BUSQUEDA]
    assert [e.url for e in leido.entradas] == FICHAS[:1]


def test_leer_hijos_que_fallan_o_no_son_sitemaps() -> None:
    roto, html = f"{TIENDA}/sitemaps/sitemap-roto.xml", f"{TIENDA}/sitemaps/sitemap-html.xml"
    navegador = NavegadorFalso({html: "<html><body>Hola</body></html>"})
    leido = asyncio.run(
        leer_hijos(INDICE, [roto, html], DOMINIO, navegador, validador=validar_con_dns_publico)
    )
    assert leido is not None and leido.entradas == () and leido.pendientes == ()
    assert navegador.grandes == [roto, html]


def test_leer_hijos_bloqueado_se_propaga() -> None:
    faros = f"{TIENDA}/sitemaps/sitemap-faros.xml"
    navegador = NavegadorFalso({}, errores={faros: Bloqueado(faros, "Desafío de Cloudflare")})
    with pytest.raises(Bloqueado):
        asyncio.run(
            leer_hijos(INDICE, [faros], DOMINIO, navegador, validador=validar_con_dns_publico)
        )


def test_leer_hijos_de_otro_dominio_no_descarga() -> None:
    navegador = NavegadorFalso({})
    assert asyncio.run(leer_hijos("https://otra-tienda.es/s.xml", [], DOMINIO, navegador)) is None


# --- Sitemap declarado en un subdominio de la tienda (causa R4-B de la campaña) ---------------

ESTATICO = f"https://static.{DOMINIO}"


def test_sitemap_declarado_en_un_subdominio_de_la_tienda() -> None:
    # Como Xtralife: `Sitemap: https://static.xtralife.com/sitemap.xml` en su robots.txt, con
    # los hijos también en `static.`. Solo entran en el catálogo las URL de la tienda: una ficha
    # en `static.` o en otro dominio se descarta.
    indice = f"{ESTATICO}/sitemap.xml"
    hijo = f"{ESTATICO}/sitemaps/product-sitemap.xml"
    paginas = {
        indice: xml_indice([hijo, "https://otra-tienda.es/product-sitemap.xml"]),
        hijo: xml_urlset([*FICHAS, f"{ESTATICO}/p/faro-golf", "https://otra-tienda.es/p/x"]),
    }
    navegador = NavegadorFalso(paginas, declarados=(indice,))
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert catalogo.sitemap == indice
    assert [e.url for e in catalogo.entradas] == FICHAS
    assert navegador.grandes == [indice, hijo]
    assert all("otra-tienda" not in u for u in navegador.urls)


@pytest.mark.parametrize(
    "fuera",
    ["https://otra-tienda.es/sitemap.xml", f"https://evil-{DOMINIO}/sitemap.xml"],
    ids=["otro-dominio", "parecido"],
)
def test_sitemap_del_subdominio_que_redirige_fuera_no_cuenta(fuera: str) -> None:
    indice = f"{ESTATICO}/sitemap.xml"
    navegador = NavegadorFalso(
        {fuera: xml_urlset(FICHAS)}, declarados=(indice,), redirecciones={indice: fuera}
    )
    assert descubrir(navegador) is None


def test_sitemap_parecido_al_dominio_no_se_descarga() -> None:
    parecidos = (f"https://evil-{DOMINIO}/sitemap.xml", f"https://{DOMINIO}.evil.net/s.xml")
    navegador = NavegadorFalso({u: xml_urlset(FICHAS) for u in parecidos}, declarados=parecidos)
    assert descubrir(navegador) is None
    assert not any("evil" in u for u in navegador.urls)


def test_leer_catalogo_rehace_el_de_un_sitemap_en_el_subdominio() -> None:
    # El perfil `sitemap` guarda la URL de `static.`: al caducar, el catálogo se puede rehacer.
    indice = f"{ESTATICO}/sitemap.xml"
    hijo = f"{ESTATICO}/sitemaps/product-sitemap.xml"
    navegador = NavegadorFalso(
        {indice: xml_indice([hijo]), hijo: xml_urlset([*FICHAS, f"{ESTATICO}/p/faro-golf"])}
    )
    leido = asyncio.run(
        leer_catalogo(indice, DOMINIO, navegador, validador=validar_con_dns_publico)
    )
    assert leido is not None
    assert leido.sitemap == indice
    assert [e.url for e in leido.entradas] == FICHAS
    assert navegador.grandes == [indice, hijo]


def test_leer_hijos_en_el_subdominio() -> None:
    indice = f"{ESTATICO}/sitemap.xml"
    faros = f"{ESTATICO}/sitemaps/sitemap-faros.xml"
    fuera = f"https://evil-{DOMINIO}/sitemaps/sitemap-faros.xml"
    navegador = NavegadorFalso({faros: xml_urlset(FICHAS[:2]), fuera: xml_urlset(FICHAS)})
    leido = asyncio.run(
        leer_hijos(indice, [fuera, faros], DOMINIO, navegador, validador=validar_con_dns_publico)
    )
    assert leido is not None
    assert navegador.grandes == [faros]
    assert [e.url for e in leido.entradas] == FICHAS[:2]


# --- catalogos: un candidato detrás de otro (causa R3-C de la campaña) -------------------------

RUTAS_HABITUALES = [
    f"{TIENDA}/sitemap.xml",
    f"{TIENDA}/sitemap_index.xml",
    f"{TIENDA}/wp-sitemap.xml",
]


def _todos(navegador: NavegadorFalso, maximo: int = 10) -> list[Catalogo]:
    async def recoger() -> list[Catalogo]:
        leidos: list[Catalogo] = []
        async for leido in catalogos(
            TIENDA, DOMINIO, navegador, None, validador=validar_con_dns_publico
        ):
            leidos.append(leido)
            if len(leidos) >= maximo:
                break
        return leidos

    return asyncio.run(recoger())


def test_catalogos_da_cada_candidato_con_url_en_orden() -> None:
    informativas = f"{TIENDA}/_includes/sitemap.xml"
    comida = f"{TIENDA}/crs/sitemap-food/index.xml"
    navegador = NavegadorFalso(
        {
            informativas: xml_urlset([f"{TIENDA}/club/mas-info", f"{TIENDA}/calidad/mas-info"]),
            comida: xml_indice([DE_PRODUCTOS]),
            DE_PRODUCTOS: xml_urlset(FICHAS),
        },
        declarados=(informativas, comida),
    )
    leidos = _todos(navegador)
    assert [c.sitemap for c in leidos] == [informativas, comida]
    assert [e.url for e in leidos[1].entradas] == FICHAS
    # El tope de ficheros es el de toda la lectura: el segundo cuenta los del primero.
    assert leidos[1].ficheros == 3
    # Las rutas habituales (404) también se probaron, pero no dan catálogo.
    assert navegador.grandes == [informativas, comida, DE_PRODUCTOS, *RUTAS_HABITUALES]


def test_catalogos_no_repite_un_sitemap_ya_leido() -> None:
    # `DE_PRODUCTOS` (de producto) va primero; el índice que lo trae como hijo ya no lo descarga
    # otra vez y, sin más hijos de producto, no da un segundo catálogo.
    navegador = con_indice()
    navegador.declarados = (INDICE, DE_PRODUCTOS)
    leidos = _todos(navegador)
    assert [c.sitemap for c in leidos] == [DE_PRODUCTOS]
    assert INDICE in navegador.grandes
    assert navegador.grandes.count(DE_PRODUCTOS) == 1


def test_catalogos_comparte_el_tope_de_ficheros() -> None:
    declarados = tuple(f"{TIENDA}/s{n}/sitemap.xml" for n in range(MAX_FICHEROS_SITEMAP + 5))
    paginas = {d: xml_urlset([f"{TIENDA}/s{n}/faro-golf"]) for n, d in enumerate(declarados)}
    navegador = NavegadorFalso(paginas, declarados=declarados)
    leidos = _todos(navegador, maximo=100)
    assert len(leidos) == MAX_FICHEROS_SITEMAP
    assert len(navegador.grandes) == MAX_FICHEROS_SITEMAP


# --- Otros países e idiomas (causa R4-C de la campaña) -------------------------------------------


def _real(nombre: str) -> str:
    return (REALES / nombre).read_text(encoding="utf-8")


def _catalogo_de(portada: str, dominio: str, navegador: NavegadorFalso) -> Catalogo:
    catalogo = asyncio.run(
        descubrir_catalogo(portada, dominio, navegador, None, validador=validar_con_dns_publico)
    )
    assert catalogo is not None
    return catalogo


DECANTALO = "https://www.decantalo.com"
KENAY = "https://kenayhome.com"
HAWKERS = "https://www.hawkersco.com"


def test_con_prefijo_en_la_portada_se_lee_primero_su_pais() -> None:
    # Decántalo: portada en /es/es/ y un índice raíz con 27 sitemaps de países (/au/en/,
    # /bg/en/…). Se leían en el orden del índice y el tope se gastaba en los demás países. Ahora
    # van detrás, sin borrarlos: los que no se leen quedan pendientes.
    raiz = f"{DECANTALO}/sitemap.xml"
    lg = DECANTALO + "/{}/module/lgsitemaps/sitemap?{}"
    es_indice = lg.format("es/es", "name=sitemap_1")
    es_fichas = lg.format("es/es", "fc=module&name=product_1_1")
    au_indice = lg.format("au/en", "name=sitemap_6")
    au_fichas = lg.format("au/en", "fc=module&name=product_6_1")
    vinos = [
        f"{DECANTALO}/es/es/vina-mayor-reserva.html",
        f"{DECANTALO}/es/es/alphonse-mellot-pouilly-fume-reserve.html",
    ]
    navegador = NavegadorFalso(
        {
            raiz: _real("decantalo_sitemap_indice_paises.xml"),
            es_indice: xml_indice([es_fichas]),
            es_fichas: xml_urlset(vinos),
            au_indice: xml_indice([au_fichas]),
            au_fichas: xml_urlset([f"{DECANTALO}/au/en/vina-mayor-reserva.html"]),
        },
        declarados=(raiz,),
    )
    catalogo = _catalogo_de(f"{DECANTALO}/es/es/", "decantalo.com", navegador)
    assert [e.url for e in catalogo.entradas] == vinos
    assert navegador.grandes[:4] == [raiz, es_indice, es_fichas, au_indice]
    assert au_fichas in catalogo.pendientes
    assert lg.format("fi/en", "name=sitemap_38") in catalogo.pendientes


def test_con_prefijo_en_la_portada_no_se_guardan_las_urls_de_otro_idioma() -> None:
    # Kenay: portada en /es/; `sitemap-pt-1.xml` cuelga de la raíz, sin prefijo, pero dentro solo
    # trae /pt/. Las URL sin prefijo de idioma (/module/…) y las de su idioma se guardan, también
    # con un tramo de dos letras detrás (/es/tv/…).
    raiz = f"{KENAY}/1_index_sitemap.xml"
    es = f"{KENAY}/sitemap-es-1.xml"
    pt = f"{KENAY}/sitemap-pt-1.xml"
    it = f"{KENAY}/it/sitemap-it-2.xml"
    muebles = [
        f"{KENAY}/es/1696-sillas-de-jardin",
        f"{KENAY}/es/tv/mueble-tv-roble",
        f"{KENAY}/module/kenay/novedades",
    ]
    navegador = NavegadorFalso(
        {
            raiz: _real("kenay_sitemap_indice_idiomas.xml"),
            es: xml_urlset(muebles),
            pt: _real("kenay_sitemap_pt_portugues.xml"),
            it: xml_urlset([f"{KENAY}/it/1696-sedie-da-giardino"]),
        },
        declarados=(raiz,),
    )
    catalogo = _catalogo_de(f"{KENAY}/es/", "kenayhome.com", navegador)
    assert [e.url for e in catalogo.entradas] == muebles
    assert navegador.grandes[:3] == [raiz, es, pt]


def test_sin_prefijo_en_la_portada_se_quitan_las_traducciones_de_sus_fichas() -> None:
    # Hawkers: portada sin prefijo (en español) y un sitemap con cada ficha también bajo /fr/ y
    # /pt/ (mismo último tramo). Esos prefijos son traducciones y salen enteros, también la ficha
    # traducida con otro nombre (HBDHS). El sitemap de categorías filtradas no se descarga.
    raiz = f"{HAWKERS}/sitemap_index.xml"
    fichas = f"{HAWKERS}/sitemap_1.xml"
    filtradas = (
        f"{HAWKERS}/sitemap-customsitemap-filteredcategoriesurl-HawkersWorldwide-aggregated.xml"
    )
    navegador = NavegadorFalso(
        {
            raiz: _real("hawkers_sitemap_indice.xml"),
            fichas: _real("hawkers_sitemap_productos_traducidos.xml"),
            filtradas: xml_urlset([f"{HAWKERS}/co/gafas-de-sol/hombre/polarizadas/"]),
        },
        declarados=(raiz,),
    )
    catalogo = _catalogo_de(f"{HAWKERS}/", "hawkersco.com", navegador)
    assert [e.url for e in catalogo.entradas] == [
        f"{HAWKERS}/gafas-de-sol/black---blue-chrome-omnia-ON02.html",
        f"{HAWKERS}/gafas-de-sol/silver---pink-omnia-ON06.html",
        f"{HAWKERS}/lentillas/lentillas-de-prueba-gratuita-HBDHS.html",
    ]
    assert filtradas not in navegador.grandes


def test_sin_pruebas_un_tramo_de_dos_letras_no_es_un_idioma() -> None:
    # Electrónica o recambios: /tv/, /pc/ son secciones. Sin prefijo en la portada y sin que la
    # mayoría de sus fichas estén repetidas fuera de él, no se pierde ninguna URL (aunque una
    # ficha salga en dos sitios).
    urls = [
        f"{TIENDA}/tv/samsung-qe55-oled",
        f"{TIENDA}/tv/lg-oled-c3",
        f"{TIENDA}/tv/sony-bravia-x90",
        f"{TIENDA}/pc/portatil-hp-15",
        f"{TIENDA}/faro-golf-vii",
        f"{TIENDA}/ofertas/samsung-qe55-oled",
    ]
    navegador = NavegadorFalso({DE_PRODUCTOS: xml_urlset(urls)}, declarados=(DE_PRODUCTOS,))
    catalogo = _catalogo_de(PORTADA, DOMINIO, navegador)
    assert [e.url for e in catalogo.entradas] == urls


# --- Un 403 seco en una ruta habitual no es un bloqueo de la tienda (causa R4-D) -----------------


def _seco(url: str) -> Bloqueado:
    return Bloqueado(url, "La tienda rechaza el acceso automático (HTTP 403).", seco=True)


def test_un_rechazo_seco_en_una_ruta_habitual_tras_un_200_se_salta() -> None:
    # La tienda ya ha respondido (el índice declarado da un catálogo): un 403 seco en una ruta
    # que nadie declaró es un sitemap que no existe, como el 403 en XML de un bucket de S3.
    navegador = con_indice()
    navegador.errores = {RUTAS_HABITUALES[0]: _seco(RUTAS_HABITUALES[0])}
    leidos = _todos(navegador)
    assert [c.sitemap for c in leidos] == [INDICE]
    assert navegador.grandes[-3:] == RUTAS_HABITUALES  # sigue con las demás rutas habituales


def test_un_rechazo_seco_sin_ninguna_respuesta_buena_antes_se_propaga() -> None:
    # Una tienda que rechaza en seco todo al cliente automático sigue saliendo como bloqueada.
    navegador = NavegadorFalso({}, errores={RUTAS_HABITUALES[0]: _seco(RUTAS_HABITUALES[0])})
    with pytest.raises(Bloqueado):
        _todos(navegador)


def test_un_desafio_en_una_ruta_habitual_tras_un_200_se_propaga() -> None:
    navegador = con_indice()
    ruta = RUTAS_HABITUALES[0]
    navegador.errores = {ruta: Bloqueado(ruta, "Desafío anti-bot de Cloudflare.")}
    with pytest.raises(Bloqueado):
        _todos(navegador)


def test_un_rechazo_seco_en_un_sitemap_declarado_se_propaga() -> None:
    otro = f"{TIENDA}/sitemaps/otros-sitemap.xml"
    navegador = con_indice()
    navegador.declarados = (INDICE, otro)
    navegador.errores = {otro: _seco(otro)}
    with pytest.raises(Bloqueado):
        _todos(navegador)


def test_una_ruta_habitual_declarada_con_rechazo_seco_se_propaga() -> None:
    # Si el robots.txt declara la misma /sitemap.xml, cuenta como declarada (R3-B).
    ruta = RUTAS_HABITUALES[0]
    navegador = con_indice()
    navegador.declarados = (INDICE, ruta)
    navegador.errores = {ruta: _seco(ruta)}
    with pytest.raises(Bloqueado):
        _todos(navegador)


def test_un_rechazo_seco_en_el_hijo_de_una_ruta_habitual_se_propaga() -> None:
    hijo = f"{TIENDA}/sitemaps/sitemap-2.xml"
    navegador = con_indice(**{RUTAS_HABITUALES[0]: xml_indice([hijo])})
    navegador.errores = {hijo: _seco(hijo)}
    with pytest.raises(Bloqueado):
        _todos(navegador)


def test_leer_catalogo_con_rechazo_seco_en_su_raiz_se_propaga() -> None:
    raiz = RUTAS_HABITUALES[0]
    navegador = NavegadorFalso({}, errores={raiz: _seco(raiz)})
    with pytest.raises(Bloqueado):
        asyncio.run(leer_catalogo(raiz, DOMINIO, navegador, validador=validar_con_dns_publico))


def test_kenay_el_403_de_sitemap_xml_tras_su_indice_no_bloquea_la_tienda() -> None:
    # Kenay: el índice declarado da un catálogo que el configurador descarta (fichas sin precio)
    # y /sitemap.xml contesta 403 text/html de 548 bytes (nginx). Antes, la tienda quedaba
    # «bloqueada» una hora; ahora no hay más catálogos y el configurador sigue con lo demás.
    raiz = f"{KENAY}/1_index_sitemap.xml"
    es = f"{KENAY}/sitemap-es-1.xml"
    ruta = f"{KENAY}/sitemap.xml"
    navegador = NavegadorFalso(
        {
            raiz: _real("kenay_sitemap_indice_idiomas.xml"),
            es: xml_urlset([f"{KENAY}/es/1695-muebles-de-jardin-y-exterior"]),
        },
        declarados=(raiz,),
        errores={ruta: _seco(ruta)},
    )

    async def recoger() -> list[Catalogo]:
        return [
            c
            async for c in catalogos(
                f"{KENAY}/es/", "kenayhome.com", navegador, None, validador=validar_con_dns_publico
            )
        ]

    leidos = asyncio.run(recoger())
    assert [c.sitemap for c in leidos] == [raiz]
    assert ruta in navegador.grandes


# --- A5 del plan de velocidad: hijos demasiado grandes ------------------------------------------


def _indice_con(errores: dict[int, Exception], total: int = 5) -> tuple[NavegadorFalso, list[str]]:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(total)]
    paginas = {INDICE: xml_indice(hijos)}
    for n, hijo in enumerate(hijos):
        paginas[hijo] = xml_urlset([f"{TIENDA}/p/faro-golf-modelo-{n}"])
    fallos = {hijos[n]: error for n, error in errores.items()}
    return NavegadorFalso(paginas, declarados=(INDICE,), errores=fallos), hijos


def _grande() -> DemasiadoGrande:
    return DemasiadoGrande("La página es demasiado grande.")


def test_tras_dos_hijos_demasiado_grandes_seguidos_el_resto_queda_pendiente() -> None:
    # Decathlon (ronda 7): seis hijos seguidos de su índice pasaban del tope de 20 MiB; cada uno
    # se descargaba entero y gastaba una de las 10 descargas, más su cortesía (11,6 s de 58,2).
    navegador, hijos = _indice_con({1: _grande(), 2: _grande()})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert navegador.grandes == [INDICE, *hijos[:3]]
    assert catalogo.pendientes == tuple(hijos[3:])
    assert catalogo.parcial
    assert [e.url for e in catalogo.entradas] == [f"{TIENDA}/p/faro-golf-modelo-0"]


def test_hijos_demasiado_grandes_no_seguidos_no_cortan_el_indice() -> None:
    navegador, hijos = _indice_con({0: _grande(), 2: _grande()})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert navegador.grandes == [INDICE, *hijos]
    assert catalogo.pendientes == ()


def test_otros_fallos_de_descarga_no_cortan_el_indice() -> None:
    caida = ErrorDescarga("Se agotó el tiempo de espera de la tienda.")
    navegador, hijos = _indice_con({0: caida, 1: caida, 2: caida})
    catalogo = descubrir(navegador)
    assert catalogo is not None
    assert navegador.grandes == [INDICE, *hijos]
