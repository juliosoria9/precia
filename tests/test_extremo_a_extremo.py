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
import sqlite3
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
from precia.nucleo.modelos import Ajustes, ConsultaBusqueda, EstadoPerfil, Modo
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
            estado = self.estados.get(url, 200)
            # Un desafío anti-bot (Cloudflare) llega como HTML con un 403, no como texto.
            tipo_robots = "text/plain" if estado < 400 else "text/html; charset=utf-8"
            return RespuestaCruda(estado, {"content-type": tipo_robots}, io.BytesIO(cuerpo))
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
        return asyncio.run(self._buscar_hasta_el_final(consulta))

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
            # La tienda falsa vende faros de Golf: es su consulta de prueba (A1).
            consultas_configuracion=("faro golf",),
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


def test_la_segunda_busqueda_no_vuelve_a_llamar_al_extractor(montar: Any) -> None:
    # A3, con FuenteReal de verdad (que da al orquestador el TTL de la caché de páginas). Como
    # en Oscaro: la IA solo busca «estado» y no lo encuentra. 1.ª búsqueda: 1 llamada (antes,
    # una por ficha); 2.ª, con las páginas y el extractor en caché: ninguna (antes, otras 4).
    m = montar(
        TransporteTienda(paginas_sitio(jsonld=())),
        guion={
            "extractor": [{"estado": None} for _ in PRODUCTOS],
            # La revisión de los resultados de Auto: todo es lo buscado.
            "revision": [
                {
                    "clasificaciones": [
                        {"indice": i, "clase": "misma_pieza", "motivo": ""}
                        for i in range(len(PRODUCTOS))
                    ]
                }
            ],
        },
    )
    m.alta()
    sin_estado = {k: v for k, v in CAMPOS_OK.items() if k != "estado"}
    m.fuente.almacen.guardar_perfil(perfil(sel_campos=sin_estado))
    consulta = ConsultaBusqueda("faro golf", campos=("precio", "estado"), modo=Modo.AUTO)
    primera = asyncio.run(m.fuente.buscar(consulta))
    # Auto revisa además los resultados con la IA (ajuste activado por defecto).
    assert m.peticiones_ia == ["extractor", "revision"]
    assert len([o for g in primera.grupos for o in g.ofertas]) == len(PRODUCTOS)
    pedidas = len(m.transporte.pedidas)
    segunda = asyncio.run(m.fuente.buscar(consulta))
    assert len(m.transporte.pedidas) == pedidas
    # La segunda búsqueda igual tampoco revisa con la IA: sale de la caché de revisiones.
    assert m.peticiones_ia == ["extractor", "revision"]
    assert (segunda.llamadas_ia, segunda.ia_desde_cache) == (0, 1)


def test_repetir_la_busqueda_no_duplica_el_historico(montar: Any, tmp_path: Path) -> None:
    # La 2.ª búsqueda sale de la caché de páginas: sus ofertas son las mismas y con la misma
    # fecha de obtención, así que no añaden filas (antes pasaban de 4 a 8).
    m = montar()
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())

    def filas() -> int:
        con = sqlite3.connect(tmp_path / "datos" / "precia.sqlite3")
        try:
            return int(con.execute("SELECT count(*) FROM precios").fetchone()[0])
        finally:
            con.close()

    m.buscar()
    antes = filas()
    assert antes == len(PRODUCTOS)
    m.buscar()
    assert filas() == antes


def test_auto_sin_perfil_lo_configura_pendiente_de_revision(montar: Any) -> None:
    m = montar(guion={"configurador": [LISTADO_OK, CAMPOS_OK]})
    m.alta()
    html = m.buscar("texto=faro+golf&modo=auto").texto

    # Auto revisa además los resultados con la IA (ajuste activado por defecto).
    assert m.peticiones_ia == ["configurador", "configurador", "revision"]
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

    # Auto revisa además los resultados con la IA (ajuste activado por defecto).
    assert m.peticiones_ia == ["reparador", "revision"]
    assert "Reparador" in html and "v2" in html
    assert PRODUCTOS[0].titulo in html
    ficha = m.web.get(f"/tiendas/{DOMINIO}").texto
    assert "v2" in ficha and "reparador IA" in ficha and "Pendiente de revisión" in ficha


def test_perfil_roto_en_clasico_no_llama_a_la_ia(montar: Any) -> None:
    m = montar(guion={})
    m.alta()
    m.fuente.almacen.guardar_perfil(ROTO)
    html = m.buscar().texto
    assert "Perfil roto" in html and "paso-error" in html
    assert m.peticiones_ia == []
    tienda = m.fuente.tienda(DOMINIO)
    assert tienda is not None and tienda.estado is EstadoPerfil.ROTO


def test_robots_que_lo_prohibe_todo_se_busca_igual_y_se_avisa(montar: Any) -> None:
    # ADR-017, enmienda del 2026-10-25: ya no hay opción de respetarlo; solo queda el aviso.
    transporte = TransporteTienda(paginas_sitio(), robots="User-agent: *\nDisallow: /\n")
    m = montar(transporte)
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    html = m.buscar().texto
    assert "Lo prohíbe; se ignora por decisión del usuario (ADR-017)" in html
    assert "paso-omitido" not in html
    assert BUSQUEDA in transporte.pedidas and transporte.fichas
    tienda = m.fuente.tienda(DOMINIO)
    assert tienda is not None and not tienda.robots_permite
    assert "robots.txt lo prohíbe · se ignora" in m.web.get("/").texto


def test_robots_que_prohibe_la_busqueda_se_ignora_y_se_avisa(montar: Any) -> None:
    # ADR-017: decisión de Julio. Se busca igual, pero el progreso lo enseña.
    transporte = TransporteTienda(paginas_sitio(), robots="User-agent: *\nDisallow: /buscar\n")
    m = montar(transporte)
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    html = m.buscar().texto
    assert "se ignora por decisión del usuario (ADR-017)" in html
    assert BUSQUEDA in transporte.pedidas and transporte.fichas
    assert "/productos/" in html


def test_un_robots_con_desafio_anti_bot_no_corta_la_busqueda(montar: Any) -> None:
    # Causas R3-B (notino.es, campaña ronda 3) y oscaro.es: la única petición era el robots.txt.
    # El robots.txt se ignora (ADR-017): el desafío es «no hay robots.txt» y se busca; cada
    # página detecta su propio bloqueo (y tiene su rescate con Chromium, ADR-023).
    transporte = TransporteTienda(
        paginas_sitio(), robots=CAPTCHA, estados={f"{TIENDA}/robots.txt": 403}
    )
    m = montar(transporte)
    m.alta()
    m.fuente.almacen.guardar_perfil(perfil())
    html = m.buscar().texto
    assert "Bloqueo (CAPTCHA o anti-bot)" not in html
    assert BUSQUEDA in transporte.pedidas and transporte.fichas
    # No se ha leído nada que prohíba: no se enseña «lo prohíbe; se ignora».
    assert "se ignora por decisión del usuario (ADR-017)" not in html


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
