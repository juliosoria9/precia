"""De extremo a extremo y sin red: web → FuenteReal → orquestador → navegador → tienda falsa.

Todo es el código real (rutas, `FuenteReal`, `Orquestador`, `Navegador` con `DescargadorHTTP`,
`ServicioRobots`, `LimitadorDominio` y `CachePaginas`, perfiles, extracción, emparejado y
SQLite) salvo lo que tocaría la red:

- el transporte HTTP, que sirve desde memoria la tienda de `tests.perfiles.sitio` (portada con
  redirección a `www.`, buscador, listado, fichas y robots.txt);
- el DNS del validador anti-SSRF (`validar_con_dns_publico`): las reglas son las de verdad;
- la IA, que es el `ProveedorSimulado` con respuestas guionizadas.

No se usa un servidor en 127.0.0.1 porque el validador y el listado solo admiten los puertos
80 y 443 y un dominio con nombre: haría falta relajar justo las defensas que se quieren probar.
"""

import asyncio
import io
import re
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
from precia.web.app import crear_app
from tests.asgi import ClienteASGI, Respuesta
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    DOMINIO,
    LISTADO_OK,
    PORTADA,
    PRODUCTOS,
    TIENDA,
    paginas_sitio,
    perfil,
)

AHORA = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
ORIGEN = {"Origin": "http://127.0.0.1:8000"}
CAPTCHA = (
    "<html><head><title>Control de seguridad</title></head><body>"
    "<p>Verify you are human</p><div class='g-recaptcha' data-sitekey='x'></div></body></html>"
)
ROTO = perfil(
    sel_resultado="div.resultado",
    sel_titulo="span.titulo",
    sel_enlace="a.ver @href",
    sel_precio_listado="b.pvp",
)


@dataclass
class TransporteTienda:
    """`navegador.http.Transporte` en memoria: la tienda falsa responde como un servidor."""

    paginas: dict[str, str]
    robots: str | None = None
    estados: Mapping[str, int] = field(default_factory=dict)
    pedidas: list[str] = field(default_factory=list)
    agentes: set[str] = field(default_factory=set)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        self.pedidas.append(url)
        self.agentes.add(cabeceras["User-Agent"])
        if url == f"https://{DOMINIO}/":
            return RespuestaCruda(301, {"location": PORTADA}, io.BytesIO(b""))
        if url.endswith("/robots.txt"):
            if self.robots is None:
                return RespuestaCruda(404, {"content-type": "text/plain"}, io.BytesIO(b""))
            cuerpo = self.robots.encode()
            return RespuestaCruda(200, {"content-type": "text/plain"}, io.BytesIO(cuerpo))
        html = self.paginas.get(url)
        if html is None:
            html, estado = "<html><body><h1>No encontrada</h1></body></html>", 404
        else:
            estado = self.estados.get(url, 200)
        tipo = {"content-type": "text/html; charset=utf-8"}
        return RespuestaCruda(estado, tipo, io.BytesIO(html.encode("utf-8")))

    @property
    def fichas(self) -> list[str]:
        return [u for u in self.pedidas if "/p/" in u]


@dataclass
class Montaje:
    fuente: FuenteReal
    web: ClienteASGI
    transporte: TransporteTienda
    proveedor: ProveedorSimulado | None

    def buscar(self, consulta: str = "texto=faro+golf&modo=clasico") -> Respuesta:
        respuesta = self.web.get(f"/buscar?{consulta}", cabeceras={"HX-Request": "true"})
        assert respuesta.estado == 200
        return respuesta

    def alta(self) -> None:
        respuesta = self.web.post(
            "/tiendas", {"dominio": DOMINIO, "nombre": "Recambios Prueba"}, cabeceras=ORIGEN
        )
        assert respuesta.estado == 303, respuesta.texto

    @property
    def peticiones_ia(self) -> list[str]:
        return [p.componente for p in self.proveedor.peticiones] if self.proveedor else []


@pytest.fixture
def montar(tmp_path: Path) -> Iterator[Any]:
    creados: list[FuenteReal] = []

    def crear(
        transporte: TransporteTienda | None = None,
        guion: Mapping[str, Iterable[Guion]] | None = None,
        *,
        clave: str | None = None,
    ) -> Montaje:
        transporte = transporte or TransporteTienda(paginas_sitio())
        proveedor = ProveedorSimulado(guion) if guion is not None else None

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
            clave_api=clave,
        )
        opciones: dict[str, Any] = {}
        if guion is not None:
            opciones["crear_proveedor"] = crear_proveedor
        fuente = FuenteReal(
            config,
            crear_descargadores=descargadores,
            validador=validar_con_dns_publico,
            reloj=lambda: AHORA,
            **opciones,
        )
        # Sin esperas de cortesía en el test (en la web el mínimo es 1 s).
        fuente.guardar_ajustes(replace(fuente.ajustes(), retardo_dominio_s=0.0))
        creados.append(fuente)
        return Montaje(fuente, ClienteASGI(crear_app(fuente)), transporte, proveedor)

    yield crear
    for fuente in creados:
        asyncio.run(fuente.cerrar())


def _ids_de_producto(html: str) -> list[str]:
    return re.findall(r'href="/productos/([^"]+)"', html)


def test_clasico_con_un_perfil_manual_da_resultados_reales(montar: Any) -> None:
    m = montar()
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    html = m.buscar().texto

    for producto in PRODUCTOS:
        assert producto.titulo in html
    assert "89,90 €" in html
    assert "DEMO" not in html
    assert "Similitud de texto" in html
    # Solo se visitan la búsqueda y las fichas (y el robots.txt); nunca el anuncio de otra web.
    assert BUSQUEDA in m.transporte.pedidas
    assert sorted(m.transporte.fichas) == sorted(p.url for p in PRODUCTOS)
    assert all("otra-tienda" not in u for u in m.transporte.pedidas)
    assert m.transporte.agentes == {Ajustes().user_agent}


def test_el_historico_queda_guardado_y_se_ve_en_el_producto(montar: Any) -> None:
    m = montar()
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    ids = _ids_de_producto(m.buscar().texto)
    assert ids
    for id_producto in ids:
        pagina = m.web.get(f"/productos/{id_producto}")
        assert pagina.estado == 200
        assert "Histórico de precio" in pagina.texto
        assert DOMINIO in pagina.texto and "sin histórico" not in pagina.texto
        assert m.fuente.historico(id_producto)[0].puntos[0].fecha == AHORA.date()
    # Con el histórico guardado ya se puede crear una alerta del producto.
    respuesta = m.web.post(
        "/alertas", {"producto_id": ids[0], "umbral": "50", "canal": "email"}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 303


def test_la_segunda_busqueda_sale_de_la_cache(montar: Any) -> None:
    m = montar()
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    m.buscar()
    pedidas = len(m.transporte.pedidas)
    html = m.buscar().texto
    assert len(m.transporte.pedidas) == pedidas
    assert "(caché)" in html


def test_auto_sin_perfil_lo_configura_pendiente_de_revision(montar: Any) -> None:
    m = montar(guion={"configurador": [LISTADO_OK, CAMPOS_OK]})
    m.alta()
    html = m.buscar("texto=faro+golf&modo=auto").texto

    assert m.peticiones_ia == ["configurador", "configurador"]
    assert "Configurador" in html and "pendiente de revisión" in html
    for producto in PRODUCTOS:
        assert producto.titulo in html
    nuevo = m.fuente.almacen.perfil_vigente(DOMINIO)
    assert nuevo is not None and not nuevo.revisado
    assert nuevo.url_busqueda == f"{TIENDA}/buscar?orden=relevancia&q={{consulta}}"

    # La persona revisa el perfil en la web y lo fija.
    ficha = m.web.get(f"/tiendas/{DOMINIO}").texto
    assert "Pendiente de revisión" in ficha and f"/tiendas/{DOMINIO}/revisar" in ficha
    respuesta = m.web.post(f"/tiendas/{DOMINIO}/revisar", {"version": "1"}, cabeceras=ORIGEN)
    assert respuesta.estado == 303
    tienda = m.fuente.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ACTIVO


def test_perfil_roto_lo_repara_la_ia(montar: Any) -> None:
    m = montar(guion={"reparador": [{**LISTADO_OK, "sel_campos": CAMPOS_OK}]})
    m.alta()
    m.fuente.almacen.guardar_perfil(ROTO)
    html = m.buscar("texto=faro+golf&modo=auto").texto

    assert m.peticiones_ia == ["reparador"]
    assert "Reparador" in html and "v2" in html
    assert PRODUCTOS[0].titulo in html
    ficha = m.web.get(f"/tiendas/{DOMINIO}").texto
    assert "v2" in ficha and "🤖 reparador" in ficha and "Pendiente de revisión" in ficha


def test_perfil_roto_en_clasico_no_llama_a_la_ia(montar: Any) -> None:
    m = montar(guion={})
    m.alta()
    m.fuente.almacen.guardar_perfil(ROTO)
    html = m.buscar().texto
    assert "Perfil roto" in html and "paso-error" in html
    assert m.peticiones_ia == []
    tienda = m.fuente.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO


def test_robots_que_prohibe_la_busqueda_omite_la_tienda(montar: Any) -> None:
    transporte = TransporteTienda(paginas_sitio(), robots="User-agent: *\nDisallow: /buscar\n")
    m = montar(transporte)
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    html = m.buscar().texto
    assert "paso-omitido" in html and "robots.txt" in html
    assert BUSQUEDA not in transporte.pedidas and transporte.fichas == []
    tienda = m.fuente.tienda(DOMINIO)
    assert tienda is not None and not tienda.robots_permite


def test_bloqueo_se_para_y_no_se_evade(montar: Any) -> None:
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = CAPTCHA
    transporte = TransporteTienda(sitio, estados={BUSQUEDA: 403})
    m = montar(transporte)
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    html = m.buscar().texto
    assert "Bloqueo (CAPTCHA o anti-bot): no se evade" in html
    # Ni reintentos ni fichas: una sola petición a la búsqueda.
    assert transporte.pedidas.count(BUSQUEDA) == 1 and transporte.fichas == []
    tienda = m.fuente.tienda(DOMINIO)
    assert tienda is not None and tienda.bloqueos == 1


def test_sin_clave_avisa_de_que_no_hay_ia(montar: Any) -> None:
    m = montar(clave=None)
    m.alta()
    html = m.buscar("texto=faro+golf&modo=auto&filtro=solo+usadas").texto
    assert "IA no disponible" in html and "ANTHROPIC_API_KEY" in html
    assert "Sin perfil" in html
    assert "no se aplica" in html
    # Sin perfil y sin IA no se visita la tienda.
    assert m.transporte.pedidas == []


def test_sin_tiendas_la_busqueda_invita_a_anadir_una(montar: Any) -> None:
    m = montar()
    assert "Añade una tienda" in m.web.get("/").texto
    assert "añade una" in m.buscar().texto
