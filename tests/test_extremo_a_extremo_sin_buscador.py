"""De extremo a extremo y sin red: tiendas sin buscador (nota 16, ADR-018).

Como `test_extremo_a_extremo_plataformas`: todo es el código real (web, `FuenteReal`,
orquestador, `Navegador` con `DescargadorHTTP`, robots, cortesía, caché, perfiles, sitemaps,
extracción, emparejado y SQLite) salvo el transporte HTTP, el DNS del validador y la IA. El
transporte sirve varias tiendas, cada URL con su estado y su `Content-Type`, y el robots.txt de
cada una:

- (B) una tienda sin buscador que publica su `sitemap.xml`: el perfil `sitemap` sale sin IA
  porque las fichas traen JSON-LD, y la búsqueda va al catálogo local;
- (A2) una tienda sin buscador ni sitemap con la URL de un listado con `{pagina}`: la búsqueda
  recorre como mucho 5 páginas, filtra por la consulta y abre las fichas;
- (A1) una tienda cuyo buscador no se descubre, con la plantilla de búsqueda del usuario;
- un sitemap detrás de un WAF (403) o del desafío de Cloudflare: se para, sin ninguna petición
  más ni ninguna llamada a la IA.
"""

import asyncio
import io
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

import pytest

from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import ProveedorIA
from precia.navegador.http import DescargadorHTTP, RespuestaCruda
from precia.navegador.tipos import Descargador
from precia.nucleo.config import Config
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import Ajustes
from precia.nucleo.orquestador import BLOQUEO, CATALOGO, LISTADO, MAX_PAGINAS_LISTADO
from precia.perfiles.tipos import TIPO_HTML, TIPO_LISTADO, TIPO_SITEMAP, Perfil
from precia.web.app import crear_app
from tests.asgi import ClienteASGI
from tests.navegador.falsos import validar_con_dns_publico

AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
ORIGEN = {"Origin": "http://127.0.0.1:8000"}
HTML = "text/html; charset=utf-8"
XML = "application/xml; charset=UTF-8"
FIXTURES_NAVEGADOR = Path(__file__).resolve().parent / "fixtures" / "navegador"
NO_ENCONTRADA = (404, HTML, "<html><body><h1>No encontrada</h1></body></html>")


@dataclass(frozen=True, slots=True)
class Pieza:
    slug: str
    titulo: str
    precio: str
    """Como se ve en la página: «89,90 €»."""
    importe: str
    referencia: str


FAROS = (
    Pieza(
        "faro-delantero-izquierdo-golf-vi",
        "Faro delantero izquierdo Golf VI",
        "89,90 €",
        "89.90",
        "5K1941005E",
    ),
    Pieza(
        "faro-delantero-derecho-golf-vi",
        "Faro delantero derecho Golf VI",
        "95,00 €",
        "95.00",
        "5K1941006E",
    ),
    Pieza("faro-antiniebla-golf-vi", "Faro antiniebla Golf VI", "24,50 €", "24.50", "1T0941699"),
)
OTRAS = (
    Pieza(
        "retrovisor-derecho-seat-ibiza",
        "Retrovisor derecho Seat Ibiza",
        "45,00 €",
        "45.00",
        "6J1857508",
    ),
    Pieza("bomba-agua-opel-astra", "Bomba de agua Opel Astra", "38,90 €", "38.90", "1334145"),
)


def portada(nombre: str, *, enlaces: bool = True) -> str:
    """Una portada sin formulario ni enlaces de búsqueda: PrecIA no encuentra el buscador.

    Sin `enlaces` tampoco tiene pistas para que la IA proponga una plantilla de búsqueda.
    """
    cabecera = f"<a href='/'>{nombre}</a><a href='/contacto'>Contacto</a>" if enlaces else nombre
    return (
        f"<html><head><title>{nombre}</title></head><body><header>{cabecera}</header>"
        "<main><h1>Recambios de ocasión</h1><p>Llámanos para cualquier pieza.</p></main>"
        "</body></html>"
    )


def ficha(base: str, pieza: Pieza, *, jsonld: bool = True) -> str:
    """La ficha de una pieza, con el precio visible y, si `jsonld`, con su JSON-LD `Product`."""
    datos = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": pieza.titulo,
        "mpn": pieza.referencia,
        "brand": {"@type": "Brand", "name": "Valeo"},
        "description": f"{pieza.titulo}, usado y probado.",
        "image": [f"{base}/img/{pieza.slug}.jpg"],
        "offers": {
            "@type": "Offer",
            "url": f"{base}/producto/{pieza.slug}/",
            "priceCurrency": "EUR",
            "price": pieza.importe,
            "itemCondition": "https://schema.org/UsedCondition",
        },
    }
    script = f'<script type="application/ld+json">{json.dumps(datos)}</script>' if jsonld else ""
    return (
        f"<html><head><title>{escape(pieza.titulo)}</title>{script}</head><body>"
        f'<h1 class="titulo">{escape(pieza.titulo)}</h1>'
        f'<p class="pvp">{pieza.precio}</p>'
        f'<p>Referencia: <span class="ref">{pieza.referencia}</span></p></body></html>'
    )


def tarjetas(piezas: Iterable[Pieza]) -> str:
    """Un listado o una página de resultados con las piezas (selectores de `SELECTORES`)."""
    filas = "".join(
        f'<article class="tarjeta"><a class="nombre" href="/producto/{p.slug}/">'
        f'{escape(p.titulo)}</a> <span class="importe">{p.precio}</span></article>'
        for p in piezas
    )
    return (
        "<html><head><title>Faros y recambios</title></head><body><h1>Faros y recambios</h1>"
        f'<section id="productos">{filas}</section></body></html>'
    )


SELECTORES: dict[str, Any] = {
    "sel_resultado": "#productos > article.tarjeta",
    "sel_titulo": "a.nombre",
    "sel_enlace": "a.nombre @href",
    "sel_precio_listado": "span.importe",
}
CAMPOS: dict[str, Any] = {"precio": "p.pvp"}
"""El JSON-LD de las fichas ya da los demás campos: a la IA solo se le pide el precio."""


def urlset(urls: Iterable[str]) -> str:
    entradas = "".join(f"<url><loc>{u}</loc><lastmod>2026-10-01</lastmod></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entradas}</urlset>'
    )


def indice(urls: Iterable[str]) -> str:
    hijos = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{hijos}'
        "</sitemapindex>"
    )


Respuestas = dict[str, tuple[int, str, str]]


@dataclass
class TransporteWeb:
    """`navegador.http.Transporte` en memoria: URL → (estado, Content-Type, cuerpo).

    Cada tienda puede tener su robots.txt (si no, un 404, que permite todo).
    """

    respuestas: Respuestas
    robots: dict[str, str] = field(default_factory=dict)
    pedidas: list[str] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        self.pedidas.append(url)
        if url.endswith("/robots.txt"):
            texto = self.robots.get(url)
            if texto is None:
                return RespuestaCruda(404, {"content-type": "text/plain"}, io.BytesIO(b""))
            cuerpo = io.BytesIO(texto.encode("utf-8"))
            return RespuestaCruda(200, {"content-type": "text/plain; charset=utf-8"}, cuerpo)
        estado, tipo, texto = self.respuestas.get(url, NO_ENCONTRADA)
        return RespuestaCruda(estado, {"content-type": tipo}, io.BytesIO(texto.encode("utf-8")))

    def sin_robots(self) -> list[str]:
        return [u for u in self.pedidas if not u.endswith("/robots.txt")]


@dataclass
class Montaje:
    fuente: FuenteReal
    web: ClienteASGI
    transporte: TransporteWeb
    proveedor: ProveedorSimulado

    def alta(self, dominio: str, entrada: str = "") -> None:
        datos = {"dominio": dominio, "nombre": dominio, "entrada": entrada}
        respuesta = self.web.post("/tiendas", datos, cabeceras=ORIGEN)
        assert respuesta.estado == 303, respuesta.texto

    def buscar(self, consulta: str) -> str:
        respuesta = self.web.get(f"/buscar?{consulta}", cabeceras={"HX-Request": "true"})
        assert respuesta.estado == 200
        return respuesta.texto

    def perfil(self, dominio: str) -> Perfil:
        vigente = self.fuente.almacen.perfil_vigente(dominio)
        assert vigente is not None
        return vigente

    @property
    def peticiones_ia(self) -> list[str]:
        return [p.componente for p in self.proveedor.peticiones]


@pytest.fixture
def montar(tmp_path: Path) -> Iterator[Any]:
    creados: list[FuenteReal] = []

    def crear(transporte: TransporteWeb, guion: Mapping[str, Iterable[Guion]]) -> Montaje:
        proveedor = ProveedorSimulado(guion)

        def descargadores(ajustes: Ajustes) -> tuple[Descargador, Descargador | None]:
            http = DescargadorHTTP(
                ajustes.user_agent, validador=validar_con_dns_publico, transporte=transporte
            )
            return http, None

        def crear_proveedor(_: Ajustes) -> tuple[ProveedorIA | None, str]:
            return proveedor, ""

        config = Config(
            raiz=tmp_path,
            ruta_bd=tmp_path / "datos" / "precia.sqlite3",
            ruta_cache=tmp_path / "cache",
            demo=False,
            clave_api=None,
        )
        fuente = FuenteReal(
            config,
            crear_descargadores=descargadores,
            validador=validar_con_dns_publico,
            reloj=lambda: AHORA,
            crear_proveedor=crear_proveedor,
        )
        fuente.guardar_ajustes(replace(fuente.ajustes(), retardo_dominio_s=0.0))
        creados.append(fuente)
        return Montaje(fuente, ClienteASGI(crear_app(fuente)), transporte, proveedor)

    yield crear
    for fuente in creados:
        asyncio.run(fuente.cerrar())


def fichas(base: str, piezas: Sequence[Pieza], *, jsonld: bool = True) -> Respuestas:
    return {
        f"{base}/producto/{p.slug}/": (200, HTML, ficha(base, p, jsonld=jsonld)) for p in piezas
    }


# --- B. Catálogo por sitemap -------------------------------------------------------------------

LIBRERIA = "piezas-sin-buscador.es"
LIBRERIA_BASE = f"https://{LIBRERIA}"
INDICE = f"{LIBRERIA_BASE}/sitemap_index.xml"
SITEMAP_PRODUCTOS = f"{LIBRERIA_BASE}/product-sitemap.xml"
SITEMAP_PAGINAS = f"{LIBRERIA_BASE}/page-sitemap.xml"


def _tienda_con_sitemap() -> TransporteWeb:
    productos = [f"{LIBRERIA_BASE}/producto/{p.slug}/" for p in (*FAROS, *OTRAS)]
    respuestas: Respuestas = {
        f"{LIBRERIA_BASE}/": (200, HTML, portada("Piezas sin buscador", enlaces=False)),
        INDICE: (200, XML, indice([SITEMAP_PAGINAS, SITEMAP_PRODUCTOS])),
        # Un sitemap de producto también puede enlazar fuera: eso nunca se guarda ni se visita.
        SITEMAP_PRODUCTOS: (200, XML, urlset([*productos, "https://otra-web.es/producto/faro/"])),
        SITEMAP_PAGINAS: (200, XML, urlset([f"{LIBRERIA_BASE}/contacto/"])),
        **fichas(LIBRERIA_BASE, (*FAROS, *OTRAS)),
    }
    robots = {f"{LIBRERIA_BASE}/robots.txt": f"User-agent: *\nAllow: /\n\nSitemap: {INDICE}\n"}
    return TransporteWeb(respuestas, robots)


def test_tienda_sin_buscador_con_sitemap_busca_en_el_catalogo_sin_ia(montar: Any) -> None:
    transporte = _tienda_con_sitemap()
    m = montar(transporte, {})
    m.alta(LIBRERIA)
    html = m.buscar("texto=faro+golf&modo=auto")

    # Sin IA: las fichas traen JSON-LD con el precio, así que no hacen falta selectores.
    assert m.peticiones_ia == []
    perfil = m.perfil(LIBRERIA)
    assert (perfil.tipo, perfil.url_busqueda, perfil.revisado) == (TIPO_SITEMAP, INDICE, False)
    info = m.fuente.almacen.catalogo(LIBRERIA)
    assert info is not None and (info.sitemap, info.total) == (INDICE, len(FAROS) + len(OTRAS))
    # El catálogo solo guarda URL, palabras y fecha: nada de precios (nota 16, art. 133 TRLPI).
    assert CATALOGO in html and f"{len(FAROS)} coincidencias" in html
    for pieza in FAROS:
        assert pieza.titulo in html and pieza.precio in html
    for pieza in OTRAS:
        assert pieza.titulo not in html
    # Se leen el índice y el sitemap de productos; ni el de páginas ni nada de otro host.
    assert INDICE in transporte.pedidas and SITEMAP_PRODUCTOS in transporte.pedidas
    assert SITEMAP_PAGINAS not in transporte.pedidas
    assert all(u.startswith(LIBRERIA_BASE) for u in transporte.pedidas)
    # Las fichas que no encajan con la consulta no se abren al buscar.
    for pieza in OTRAS:
        assert f"{LIBRERIA_BASE}/producto/{pieza.slug}/" not in transporte.pedidas


# --- A2. Listado del usuario con {pagina} ------------------------------------------------------

LISTADO_TIENDA = "listado-sin-buscador.es"
LISTADO_BASE = f"https://{LISTADO_TIENDA}"
ENTRADA_LISTADO = f"{LISTADO_BASE}/tienda/faros?page={{pagina}}"
RELLENO = tuple(
    Pieza(f"alternador-seat-{n}", f"Alternador Seat modelo {n}", "60,00 €", "60.00", f"0{n}12345")
    for n in range(1, 25)
)


def _pagina_listado(numero: int) -> str:
    return ENTRADA_LISTADO.replace("{pagina}", str(numero))


def _tienda_con_listado() -> TransporteWeb:
    """Siete páginas de listado: los faros en la 1 y en la 7 (que nunca se llega a pedir)."""
    paginas = [
        [FAROS[0], RELLENO[0], FAROS[1], RELLENO[1]],
        *([RELLENO[3 * n + 2], RELLENO[3 * n + 3], RELLENO[3 * n + 4]] for n in range(5)),
        [FAROS[2], OTRAS[0], OTRAS[1]],
    ]
    respuestas: Respuestas = {
        f"{LISTADO_BASE}/": (200, HTML, portada("Listado sin buscador", enlaces=False))
    }
    for numero, piezas in enumerate(paginas, start=1):
        respuestas[_pagina_listado(numero)] = (200, HTML, tarjetas(piezas))
        respuestas.update(fichas(LISTADO_BASE, piezas))
    return TransporteWeb(respuestas)


def test_tienda_sin_buscador_con_listado_recorre_cinco_paginas(montar: Any) -> None:
    transporte = _tienda_con_listado()
    m = montar(transporte, {"configurador": [SELECTORES, CAMPOS]})
    m.alta(LISTADO_TIENDA, ENTRADA_LISTADO)
    tienda = m.fuente.tienda(LISTADO_TIENDA)
    assert tienda is not None and tienda.entrada == ENTRADA_LISTADO

    html = m.buscar("texto=faro+golf&modo=auto")
    perfil = m.perfil(LISTADO_TIENDA)
    assert (perfil.tipo, perfil.url_busqueda) == (TIPO_LISTADO, ENTRADA_LISTADO)
    assert m.peticiones_ia == ["configurador", "configurador"]
    # Como mucho 5 páginas: la 6 y la 7 no se piden nunca.
    assert MAX_PAGINAS_LISTADO == 5
    for numero in (6, 7):
        assert _pagina_listado(numero) not in transporte.pedidas
    assert f"{LISTADO}: 5 páginas, 2 coincidencias de 16" in html
    # Filtra por la consulta: solo se abren (y se enseñan) los faros.
    assert FAROS[0].titulo in html and FAROS[1].titulo in html
    assert RELLENO[0].titulo not in html
    # Ninguna ficha de las páginas 2 a 5 (que no nombran la consulta) se abre.
    for pieza in RELLENO[2:14]:
        assert f"{LISTADO_BASE}/producto/{pieza.slug}/" not in transporte.pedidas


# --- A1. Plantilla de búsqueda del usuario ------------------------------------------------------

PLANTILLA_TIENDA = "plantilla-usuario.es"
PLANTILLA_BASE = f"https://{PLANTILLA_TIENDA}"
ENTRADA_BUSQUEDA = f"{PLANTILLA_BASE}/catalogo/consulta.jsp?texto={{consulta}}"


def test_tienda_con_plantilla_de_busqueda_del_usuario(montar: Any) -> None:
    busqueda = ENTRADA_BUSQUEDA.replace("{consulta}", "faro+golf")
    transporte = TransporteWeb(
        {
            f"{PLANTILLA_BASE}/": (200, HTML, portada("Plantilla del usuario")),
            busqueda: (200, HTML, tarjetas(FAROS)),
            **fichas(PLANTILLA_BASE, FAROS),
        }
    )
    m = montar(transporte, {"configurador": [SELECTORES, CAMPOS]})
    m.alta(PLANTILLA_TIENDA, ENTRADA_BUSQUEDA)
    html = m.buscar("texto=faro+golf&modo=auto")

    perfil = m.perfil(PLANTILLA_TIENDA)
    assert (perfil.tipo, perfil.url_busqueda) == (TIPO_HTML, ENTRADA_BUSQUEDA)
    assert m.peticiones_ia == ["configurador", "configurador"]
    # La plantilla del usuario se prueba la primera, antes que los patrones habituales.
    assert transporte.sin_robots()[:2] == [f"{PLANTILLA_BASE}/", busqueda]
    for pieza in FAROS:
        assert pieza.titulo in html and pieza.precio in html


# --- Bloqueos en el sitemap ---------------------------------------------------------------------

WAF = "sitemap-con-waf.es"
WAF_BASE = f"https://{WAF}"
WAF_SITEMAP = f"{WAF_BASE}/sitemap.xml"
BLOQUEOS = {
    "waf-403": (403, HTML, (FIXTURES_NAVEGADOR / "akamai_access_denied.html").read_text("utf-8")),
    "cloudflare": (
        503,
        HTML,
        (FIXTURES_NAVEGADOR / "cloudflare_just_a_moment.html").read_text("utf-8"),
    ),
}


def _tienda_con_sitemap_bloqueado(bloqueo: str) -> TransporteWeb:
    respuestas: Respuestas = {
        f"{WAF_BASE}/": (200, HTML, portada("Sitemap con WAF", enlaces=False)),
        WAF_SITEMAP: BLOQUEOS[bloqueo],
        **fichas(WAF_BASE, FAROS),
    }
    robots = {f"{WAF_BASE}/robots.txt": f"User-agent: *\nAllow: /\nSitemap: {WAF_SITEMAP}\n"}
    return TransporteWeb(respuestas, robots)


@pytest.mark.parametrize("bloqueo", sorted(BLOQUEOS))
def test_sitemap_bloqueado_al_configurar_se_para(montar: Any, bloqueo: str) -> None:
    transporte = _tienda_con_sitemap_bloqueado(bloqueo)
    m = montar(transporte, {"configurador": [SELECTORES, CAMPOS]})
    m.alta(WAF)
    html = m.buscar("texto=faro+golf&modo=auto")

    assert BLOQUEO in html and "paso-error" in html
    # Se para en el sitemap: ni otros sitemaps, ni fichas, ni reintentos, ni IA.
    assert transporte.sin_robots()[-1] == WAF_SITEMAP
    assert transporte.pedidas.count(WAF_SITEMAP) == 1
    assert all("/producto/" not in u for u in transporte.pedidas)
    assert m.peticiones_ia == []
    tienda = m.fuente.tienda(WAF)
    assert tienda is not None and tienda.bloqueos == 1
    assert m.fuente.almacen.perfil_vigente(WAF) is None


@pytest.mark.parametrize("bloqueo", sorted(BLOQUEOS))
def test_sitemap_bloqueado_al_rehacer_el_catalogo_se_para(montar: Any, bloqueo: str) -> None:
    transporte = _tienda_con_sitemap_bloqueado(bloqueo)
    m = montar(transporte, {"configurador": [SELECTORES, CAMPOS]})
    m.alta(WAF)
    # La tienda ya tiene un perfil `sitemap`, pero aún no su catálogo: hay que hacerlo al buscar.
    m.fuente.almacen.guardar_perfil(
        Perfil(
            dominio=WAF,
            version=1,
            url_busqueda=WAF_SITEMAP,
            sel_resultado="",
            sel_titulo="",
            sel_enlace="",
            sel_precio_listado=None,
            sel_campos={},
            necesita_js=False,
            autor="manual",
            revisado=True,
            creado=AHORA,
            tipo=TIPO_SITEMAP,
        )
    )
    html = m.buscar("texto=faro+golf&modo=auto")

    assert BLOQUEO in html and "paso-error" in html
    assert transporte.sin_robots() == [WAF_SITEMAP]
    assert m.peticiones_ia == []
    tienda = m.fuente.tienda(WAF)
    assert tienda is not None and tienda.bloqueos == 1
    assert m.fuente.almacen.catalogo(WAF) is None
