"""De extremo a extremo y sin red, con las mejoras de scraping de la nota 15 (§D).

Como `test_extremo_a_extremo`: todo es el código real (web, `FuenteReal`, orquestador,
`Navegador` con `DescargadorHTTP`, robots, cortesía, caché, perfiles, extracción, emparejado y
SQLite) salvo el transporte HTTP, el DNS del validador y la IA. Aquí el transporte sirve varias
tiendas, cada URL con su estado y su `Content-Type`, como lo haría cada servidor:

- una tienda WooCommerce que se configura por su API pública, sin IA;
- una tienda Next.js sin `<form>`, cuyo buscador se encuentra por un enlace de la portada y
  cuyo listado, tras un despliegue que cambia las clases, sale de `__NEXT_DATA__`;
- tiendas detrás de un WAF que responde con un 403: se para y no se evade.
"""

import asyncio
import io
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import ProveedorIA
from precia.navegador.http import DescargadorHTTP, RespuestaCruda
from precia.navegador.tipos import Descargador
from precia.nucleo.config import Config
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import Ajustes, EstadoPerfil
from precia.nucleo.orquestador import BLOQUEO, ESTRUCTURADOS
from precia.perfiles.tipos import TIPO_API_WOOCOMMERCE
from precia.web.app import crear_app
from tests.asgi import ClienteASGI, Respuesta
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.plataformas import WOO, WOO_API, WOO_PLANTILLA, WOO_PORTADA, WOO_TITULOS
from tests.perfiles.plataformas import fixture as fixture_fuentes
from tests.perfiles.sitio import CAMPOS_OK, PRODUCTOS, html_ficha, html_next

AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
ORIGEN = {"Origin": "http://127.0.0.1:8000"}
HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=UTF-8"
FIXTURES_NAVEGADOR = Path(__file__).resolve().parent / "fixtures" / "navegador"

NEXT = "recambios-next.es"
NEXT_BASE = f"https://{NEXT}"
NEXT_PORTADA = (
    "<html><head><title>Recambios Next</title></head><body><div id='__next'>"
    "<header><a href='/'>Recambios Next</a><a href='/es/search'>Buscar</a>"
    "<a href='/es/ofertas'>Ofertas</a></header><main><h1>Piezas para tu coche</h1></main></div>"
    '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{}}}</script>'
    "</body></html>"
)
NEXT_LISTADO = {
    "sel_resultado": "div.Tarjeta_tarjeta__k2J9s",
    "sel_titulo": "a.Tarjeta_enlace__aB3dE",
    "sel_enlace": "a.Tarjeta_enlace__aB3dE @href",
    "sel_precio_listado": "span.Tarjeta_precio__Zx81Q",
}

WAF = "recambios-waf.es"


@dataclass
class TransporteWeb:
    """`navegador.http.Transporte` en memoria: URL → (estado, Content-Type, cuerpo)."""

    respuestas: dict[str, tuple[int, str, str]]
    pedidas: list[str] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        self.pedidas.append(url)
        if url.endswith("/robots.txt"):
            return RespuestaCruda(404, {"content-type": "text/plain"}, io.BytesIO(b""))
        estado, tipo, cuerpo = self.respuestas.get(
            url, (404, HTML, "<html><body><h1>No encontrada</h1></body></html>")
        )
        return RespuestaCruda(estado, {"content-type": tipo}, io.BytesIO(cuerpo.encode("utf-8")))

    def sin_robots(self) -> list[str]:
        return [u for u in self.pedidas if not u.endswith("/robots.txt")]


@dataclass
class Montaje:
    fuente: FuenteReal
    web: ClienteASGI
    transporte: TransporteWeb
    proveedor: ProveedorSimulado

    def alta(self, dominio: str) -> None:
        respuesta = self.web.post(
            "/tiendas", {"dominio": dominio, "nombre": dominio}, cabeceras=ORIGEN
        )
        assert respuesta.estado == 303, respuesta.texto

    def buscar(self, consulta: str) -> str:
        return asyncio.run(self._buscar_hasta_el_final(consulta)).texto

    async def _buscar_hasta_el_final(self, consulta: str) -> Respuesta:
        """C1: lanza la búsqueda (como el formulario, con htmx) y sondea su estado hasta que
        termina, todo en un bucle: `asyncio.run` cancelaría la búsqueda al salir."""
        respuesta = await self.web.aget(f"/buscar?{consulta}", {"HX-Request": "true"})
        assert respuesta.estado == 200, respuesta.texto
        id_busqueda = respuesta.cabeceras["hx-push-url"].split("?")[0].rsplit("/", 1)[-1]
        for _ in range(500):
            if "every 1s" not in respuesta.texto:
                return respuesta
            await asyncio.sleep(0.01)
            respuesta = await self.web.aget(
                f"/busquedas/{id_busqueda}/estado", {"HX-Request": "true"}
            )
            assert respuesta.estado == 200, respuesta.texto
        raise AssertionError("La búsqueda no ha terminado")

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
            # La tienda falsa vende faros de Golf: es su consulta de prueba (A1).
            consultas_configuracion=("faro golf",),
            crear_proveedor=crear_proveedor,
        )
        fuente.guardar_ajustes(replace(fuente.ajustes(), retardo_dominio_s=0.0))
        creados.append(fuente)
        return Montaje(fuente, ClienteASGI(crear_app(fuente)), transporte, proveedor)

    yield crear
    for fuente in creados:
        asyncio.run(fuente.cerrar())


def test_tienda_woocommerce_se_configura_por_su_api_sin_ia(montar: Any) -> None:
    transporte = TransporteWeb(
        {
            WOO_PORTADA: (200, HTML, fixture_fuentes("portada_woocommerce.html")),
            WOO_API: (200, JSON, fixture_fuentes("woocommerce_store_api.json")),
        }
    )
    m = montar(transporte, {})
    m.alta(WOO)
    html = m.buscar("texto=faro+golf&modo=auto")

    # La IA no configura nada: la tienda tiene API pública y responde con productos.
    # Auto revisa además los resultados con la IA (ajuste activado por defecto).
    assert m.peticiones_ia == ["revision"]
    perfil = m.fuente.almacen.perfil_vigente(WOO)
    assert perfil is not None
    assert (perfil.tipo, perfil.url_busqueda, perfil.revisado) == (
        TIPO_API_WOOCOMMERCE,
        WOO_PLANTILLA,
        False,
    )
    assert "API pública de WooCommerce" in html and "pendiente de revisión" in html
    assert "Faro delantero izquierdo VW Golf VI" in html and "89,90 €" in html
    # Las pastillas no nombran «faro golf» (A2, bug 2).
    assert WOO_TITULOS[1] not in html
    assert "API tienda" in html  # El origen de cada valor.
    # La portada y la API (una vez al configurar y otra, ya de la caché, al buscar); ni el
    # formulario HTML ni ninguna ficha.
    assert transporte.sin_robots() == [WOO_PORTADA, WOO_API]
    assert all("/producto/" not in u for u in transporte.pedidas)

    # Quien revisa el perfil ve que es de la API y lo fija.
    ficha = m.web.get(f"/tiendas/{WOO}").texto
    assert TIPO_API_WOOCOMMERCE in ficha and "Pendiente de revisión" in ficha
    respuesta = m.web.post(f"/tiendas/{WOO}/revisar", {"version": "1"}, cabeceras=ORIGEN)
    assert respuesta.estado == 303
    tienda = m.fuente.tienda(WOO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def _tienda_next() -> dict[str, tuple[int, str, str]]:
    respuestas = {
        f"{NEXT_BASE}/": (200, HTML, NEXT_PORTADA),
        f"{NEXT_BASE}/es/search?query=faro+golf": (200, HTML, html_next(tarjetas=True)),
    }
    for producto in PRODUCTOS:
        respuestas[f"{NEXT_BASE}{producto.ruta}"] = (200, HTML, html_ficha(producto))
    return respuestas


def test_tienda_nextjs_sin_formulario_y_listado_de_next_data(montar: Any) -> None:
    respuestas = _tienda_next()
    transporte = TransporteWeb(respuestas)
    m = montar(transporte, {"configurador": [NEXT_LISTADO, CAMPOS_OK]})
    m.alta(NEXT)

    # 1. Sin <form>: el buscador sale del enlace «Buscar» de la cabecera, probando los
    #    nombres de parámetro habituales hasta dar con el que devuelve un listado.
    html = m.buscar("texto=faro+golf&modo=auto")
    # Auto revisa además los resultados con la IA (ajuste activado por defecto).
    assert m.peticiones_ia == ["configurador", "configurador", "revision"]
    perfil = m.fuente.almacen.perfil_vigente(NEXT)
    assert perfil is not None and perfil.url_busqueda == f"{NEXT_BASE}/es/search?query={{consulta}}"
    assert transporte.sin_robots()[:4] == [
        f"{NEXT_BASE}/",
        f"{NEXT_BASE}/es/search?q=faro+golf",
        f"{NEXT_BASE}/es/search?s=faro+golf",
        f"{NEXT_BASE}/es/search?query=faro+golf",
    ]
    for producto in PRODUCTOS:
        assert producto.titulo in html

    # 2. La tienda despliega una versión nueva: cambian los hash de las clases de CSS modules y
    #    los selectores ya no encuentran nada, pero `__NEXT_DATA__` trae los mismos resultados.
    nueva = html_next(tarjetas=True).replace("Tarjeta_tarjeta__k2J9s", "Tarjeta_tarjeta__Q9w8E")
    respuestas[f"{NEXT_BASE}/es/search?query=faro+golf+vi"] = (200, HTML, nueva)
    html = m.buscar("texto=faro+golf+vi&modo=auto")
    assert ESTRUCTURADOS in html
    for producto in PRODUCTOS:
        assert producto.titulo in html
    # Ni reparador ni perfil roto: las ofertas salen con precio anclado. Solo otra revisión de
    # los resultados de Auto (la consulta es otra).
    assert m.peticiones_ia == ["configurador", "configurador", "revision", "revision"]
    vigente = m.fuente.almacen.perfil_vigente(NEXT)
    assert vigente is not None and vigente.version == 1
    tienda = m.fuente.tienda(NEXT)
    assert tienda is not None and tienda.estado is EstadoPerfil.PENDIENTE_REVISION


WAF_PORTADA = f"https://{WAF}/"
WAF_API = f"https://{WAF}/wp-json/wc/store/v1/products?search=faro+golf&per_page=10"


def _tienda_con_waf(donde: str) -> dict[str, tuple[int, str, str]]:
    """El WAF responde con un 403 en la portada (Akamai) o en la API de WooCommerce (DataDome)."""
    if donde == "portada":
        akamai = (FIXTURES_NAVEGADOR / "akamai_access_denied.html").read_text("utf-8")
        return {WAF_PORTADA: (403, HTML, akamai)}
    portada = fixture_fuentes("portada_woocommerce.html").replace(WOO, WAF)
    datadome = (FIXTURES_NAVEGADOR / "datadome_api_403.json").read_text("utf-8")
    return {WAF_PORTADA: (200, HTML, portada), WAF_API: (403, JSON, datadome)}


@pytest.mark.parametrize(("donde", "bloqueada"), [("portada", WAF_PORTADA), ("api", WAF_API)])
def test_tienda_con_waf_que_responde_403_se_para(montar: Any, donde: str, bloqueada: str) -> None:
    transporte = TransporteWeb(_tienda_con_waf(donde))
    m = montar(transporte, {"configurador": [NEXT_LISTADO, CAMPOS_OK]})
    m.alta(WAF)
    html = m.buscar("texto=faro+golf&modo=auto")

    assert BLOQUEO in html and "paso-error" in html
    # Se para en la respuesta del WAF: ni reintentos, ni otras rutas, ni IA.
    assert transporte.sin_robots()[-1] == bloqueada
    assert transporte.pedidas.count(bloqueada) == 1
    assert m.peticiones_ia == []
    tienda = m.fuente.tienda(WAF)
    assert tienda is not None and tienda.bloqueos == 1
    assert m.fuente.almacen.perfil_vigente(WAF) is None
