"""Catálogo de una tienda sin buscador por su `sitemap.xml` (nota 16 §B, `perfiles.catalogo`).

Sin red: el `NavegadorFalso` sirve los sitemaps (ya descomprimidos, como los entrega
`Navegador.obtener(..., grande=True)` con un `.xml.gz`) y las líneas `Sitemap:` del robots.txt.
Todo lo que sale de un sitemap es dato de la web: solo se descarga lo de la propia tienda.
"""

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from precia.fuentes.plataformas import Plataforma
from precia.fuentes.sitemap import MAX_FICHEROS_SITEMAP, palabras_url
from precia.navegador.tipos import (
    Bloqueado,
    ErrorDescarga,
    Pagina,
    ProhibidoPorRobots,
    URLNoPermitida,
)
from precia.perfiles import catalogo as modulo
from precia.perfiles.catalogo import (
    MIN_CON_PALABRAS,
    Catalogo,
    UrlCatalogo,
    descubrir_catalogo,
    elegir_fichas,
    leer_catalogo,
)
from precia.perfiles.validador import MAX_FICHAS
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
    [ErrorDescarga("no responde"), ProhibidoPorRobots("no"), URLNoPermitida("privada")],
    ids=["descarga", "robots", "url"],
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


# --- Casos hostiles cronometrados -----------------------------------------------------------


def test_sitemap_de_50000_urls_en_poco_tiempo() -> None:
    urls = [f"{TIENDA}/p/faro-golf-vi-modelo-{n}-{n * 7}" for n in range(50_000)]
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": xml_urlset(urls)})
    inicio = time.perf_counter()
    catalogo = descubrir(navegador)
    duracion = time.perf_counter() - inicio
    assert catalogo is not None
    assert len(catalogo.entradas) == 50_000
    # Medido: unos 5-7 s con la máquina libre (casi todo en `palabras_url` y `url_de_la_tienda`).
    # El tope es holgado para que no falle con carga; un coste cuadrático no bajaría de minutos.
    assert duracion < 30, duracion
    inicio = time.perf_counter()
    elegir_fichas(catalogo, "faro golf modelo 49999")
    assert time.perf_counter() - inicio < 2


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
    navegador = NavegadorFalso(
        {f"{TIENDA}/sitemap.xml": risas, f"{TIENDA}/sitemap_index.xml": xml_urlset(FICHAS)}
    )
    inicio = time.perf_counter()
    catalogo = descubrir(navegador)
    assert time.perf_counter() - inicio < 1
    assert catalogo is not None
    assert catalogo.sitemap == f"{TIENDA}/sitemap_index.xml"
    assert all("jaja" not in e.url for e in catalogo.entradas)


def test_anidamiento_profundo_no_rompe_nada() -> None:
    profundo = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{FICHAS[0]}</loc></url>" + "<x>" * 200_000 + "</urlset>"
    )
    navegador = NavegadorFalso({f"{TIENDA}/sitemap.xml": profundo})
    inicio = time.perf_counter()
    catalogo = descubrir(navegador)
    assert time.perf_counter() - inicio < 2
    assert catalogo is not None
    assert [e.url for e in catalogo.entradas] == FICHAS[:1]
    assert catalogo.parcial


def test_indice_con_miles_de_hijos_no_multiplica_las_descargas() -> None:
    hijos = [f"{TIENDA}/product-sitemap{n}.xml" for n in range(50_000)]
    paginas = {INDICE: xml_indice(hijos)}
    paginas.update({h: xml_urlset([f"{TIENDA}/p/faro-golf-{n}"]) for n, h in enumerate(hijos[:20])})
    navegador = NavegadorFalso(paginas, declarados=(INDICE,))
    inicio = time.perf_counter()
    catalogo = descubrir(navegador)
    assert time.perf_counter() - inicio < 3
    assert catalogo is not None
    assert len(navegador.grandes) == MAX_FICHEROS_SITEMAP
    assert catalogo.parcial
