"""Búsqueda en segundo plano desde la web (C1): lanzar, sondear, cancelar y volver atrás.

Las búsquedas siguen después de responder, así que los escenarios con una búsqueda en curso van
en un solo bucle con la API asíncrona del cliente (`aget`, `apost`): `asyncio.run` cancela al
salir las tareas que quedan.
"""

import asyncio
import re
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any

from markupsafe import escape

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    EstadoTienda,
    Modo,
    ResultadoBusqueda,
    Seguimiento,
)
from precia.nucleo.tareas import GestorBusquedas
from precia.web.app import crear_app
from precia.web.formularios import parametros_de_consulta
from tests.asgi import ClienteASGI, Respuesta

HX = {"HX-Request": "true"}
ORIGEN = {"Origin": "http://127.0.0.1:8000"}
OEM = "1K0698151"
NORTE = "recambiosnorte.example"
YA = "piezasya.example"
TITULO_NORTE = "Pastillas freno delanteras TRW GDB1550"
TITULO_YA = "Juego pastillas freno del. Bosch 0986494524"
_ID = re.compile(r"^/busquedas/([0-9a-f]{16})\?")


def _solo(resultado: ResultadoBusqueda, tiendas: set[str]) -> ResultadoBusqueda:
    """El resultado con las ofertas de `tiendas`, como un parcial del orquestador."""
    grupos = tuple(
        replace(g, ofertas=tuple(o for o in g.ofertas if o.tienda in tiendas))
        for g in resultado.grupos
        if any(o.tienda in tiendas for o in g.ofertas)
    )
    progreso = tuple(p for p in resultado.progreso if p.tienda in tiendas)
    return replace(resultado, grupos=grupos, progreso=progreso, provisional=True)


class FuenteLenta(FuenteDemo):
    """Va tienda a tienda: avisa DESCARGANDO, espera a que el test la suelte, avisa HECHA y
    publica un parcial con las ofertas de las tiendas terminadas. Al final, el de la demo."""

    def __init__(
        self, *, en_espera: frozenset[str] = frozenset(), bloqueadas: frozenset[str] = frozenset()
    ) -> None:
        super().__init__()
        self.en_espera = en_espera
        self.bloqueadas = bloqueadas
        self.consultas: list[ConsultaBusqueda] = []
        self.orden: list[str] = []
        self._soltar: dict[str, asyncio.Event] = {}

    def _evento(self, dominio: str) -> asyncio.Event:
        return self._soltar.setdefault(dominio, asyncio.Event())

    def suelta(self, *dominios: str) -> None:
        for dominio in dominios or [t.dominio for t in self.tiendas()]:
            self._evento(dominio).set()

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        assert seguimiento is not None
        self.consultas.append(consulta)
        final = await super().buscar(consulta)
        hechas: set[str] = set()
        dominios = consulta.tiendas or tuple(t.dominio for t in self.tiendas())
        for dominio in dominios:
            if dominio in self.en_espera:
                seguimiento.tienda(dominio, EstadoTienda.EN_ESPERA)
        try:
            for dominio in dominios:
                if dominio in self.en_espera:
                    continue
                seguimiento.tienda(dominio, EstadoTienda.DESCARGANDO)
                await self._evento(dominio).wait()
                hechas.add(dominio)
                fin = EstadoTienda.BLOQUEADA if dominio in self.bloqueadas else EstadoTienda.HECHA
                seguimiento.tienda(dominio, fin)
                seguimiento.parcial(_solo(final, hechas))
        except asyncio.CancelledError:
            self.orden.append("cancelada")
            raise
        return final

    async def cerrar(self) -> None:
        self.orden.append("cerrar")


class FuenteEspia(FuenteDemo):
    def __init__(self) -> None:
        super().__init__()
        self.consultas: list[ConsultaBusqueda] = []

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        self.consultas.append(consulta)
        return await super().buscar(consulta, seguimiento=seguimiento)


def _cliente(fuente: FuenteDemo | None = None, **opciones: object) -> ClienteASGI:
    return ClienteASGI(crear_app(fuente, **opciones))  # type: ignore[arg-type]


def _id(respuesta: Respuesta) -> str:
    url = respuesta.cabeceras.get("hx-push-url") or respuesta.cabeceras["location"]
    encontrado = _ID.match(url)
    assert encontrado, url
    return encontrado.group(1)


def _version(html: str) -> str:
    encontrada = re.search(r"/estado\?v=(\d+)", html)
    assert encontrada, html
    return encontrada.group(1)


async def _sondear_hasta(
    cliente: ClienteASGI, id_busqueda: str, condicion: Callable[[str], bool]
) -> str:
    """Pide el estado (sin `v`: siempre el fragmento) hasta que `condicion` se cumple."""
    for _ in range(500):
        html = (await cliente.aget(f"/busquedas/{id_busqueda}/estado", HX)).texto
        if condicion(html):
            return html
        await asyncio.sleep(0)
    raise AssertionError("El estado no ha llegado a cumplir la condición")


def _correr(escenario: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(escenario())


# --- Lanzar ----------------------------------------------------------------------------------


def test_buscar_sin_htmx_redirige_a_la_busqueda() -> None:
    respuesta = _cliente().get("/buscar?texto=freno")
    assert respuesta.estado == 303
    assert re.match(r"^/busquedas/[0-9a-f]{16}\?texto=freno", respuesta.cabeceras["location"])
    assert "hx-request" in respuesta.cabeceras["vary"].lower()


def test_cliente_sigue_el_303_en_el_mismo_bucle() -> None:
    respuesta = _cliente().get(f"/buscar?texto={OEM}", seguir=True)
    assert respuesta.estado == 200
    assert "<html" in respuesta.texto and TITULO_NORTE in respuesta.texto


def test_buscar_con_htmx_devuelve_el_progreso_y_empuja_la_url() -> None:
    fuente = FuenteLenta()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        respuesta = await cliente.aget(f"/buscar?texto={OEM}", HX)
        assert respuesta.estado == 200
        assert respuesta.cabeceras["hx-push-url"].startswith("/busquedas/")
        assert f"texto={OEM}" in respuesta.cabeceras["hx-push-url"]
        assert "<html" not in respuesta.texto
        assert 'id="busqueda"' in respuesta.texto and 'hx-trigger="every 1s"' in respuesta.texto
        fuente.suelta()

    _correr(escenario)


def test_busqueda_rapida_se_pinta_ya_terminada() -> None:
    respuesta = _cliente().get(f"/buscar?texto={OEM}", cabeceras=HX)
    assert respuesta.estado == 200
    assert respuesta.cabeceras["hx-push-url"].startswith("/busquedas/")
    assert TITULO_NORTE in respuesta.texto and OEM in respuesta.texto
    assert "every 1s" not in respuesta.texto
    assert "Histórico y alertas" in respuesta.texto


def test_sin_resultados_por_htmx_no_dice_que_no_hay_tiendas() -> None:
    # Revisión: el fragmento no recibía `tiendas` y, sin resultados, decía «Todavía no hay
    # tiendas» aunque la demo tiene cinco.
    for texto in ("zzzzqqq", "9Z9999999"):
        respuesta = _cliente().get(f"/buscar?texto={texto}", cabeceras=HX)
        assert respuesta.estado == 200
        assert "Sin resultados" in respuesta.texto
        assert "Todavía no hay tiendas" not in respuesta.texto


def test_f5_y_atras_no_relanzan_el_scraping() -> None:
    espia = FuenteEspia()
    cliente = _cliente(espia)

    async def escenario() -> None:
        lanzada = await cliente.aget("/buscar?texto=freno")
        ubicacion = lanzada.cabeceras["location"]
        f5 = await cliente.aget(ubicacion)
        atras = await cliente.aget(
            ubicacion, {**HX, "HX-History-Restore-Request": "true", "HX-Current-URL": ubicacion}
        )
        for respuesta in (f5, atras):
            assert respuesta.estado == 200
            assert "<html" in respuesta.texto and 'value="freno"' in respuesta.texto
        assert len(espia.consultas) == 1

    _correr(escenario)


def test_busqueda_desconocida_da_404_con_el_formulario_relleno() -> None:
    cliente = _cliente()
    respuesta = cliente.get("/busquedas/0123456789abcdef?texto=freno&modo=clasico")
    assert respuesta.estado == 404
    assert "ya no está" in respuesta.texto and 'value="freno"' in respuesta.texto
    assert 'value="clasico" checked' in respuesta.texto
    assert cliente.get("/busquedas/xx").estado == 404
    assert cliente.get("/busquedas/0123456789ABCDEF").estado == 404


def test_navegacion_marca_buscar_en_la_busqueda() -> None:
    html = _cliente().get("/buscar?texto=freno", seguir=True).texto
    assert '<a href="/" aria-current="page">' in html


def test_demasiadas_busquedas_da_429_junto_al_formulario() -> None:
    fuente = FuenteLenta()
    gestor = GestorBusquedas(fuente, simultaneas=1, max_en_cola=0)
    cliente = _cliente(fuente, espera_inicial_s=0, gestor=gestor)

    async def escenario() -> None:
        assert (await cliente.aget("/buscar?texto=faro", HX)).estado == 200
        segunda = await cliente.aget("/buscar?texto=freno", HX)
        assert segunda.estado == 429
        assert "demasiadas búsquedas" in segunda.texto
        sin_htmx = await cliente.aget("/buscar?texto=freno")
        assert sin_htmx.estado == 429 and 'value="freno"' in sin_htmx.texto
        fuente.suelta()

    _correr(escenario)


# --- Progreso y sondeo -------------------------------------------------------------------------


def test_progreso_por_tienda_con_sondeo() -> None:
    fuente = FuenteLenta()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "Descargando" in h)
        assert 'hx-trigger="every 1s"' in html
        assert f"/busquedas/{id_busqueda}/estado?v=" in html
        assert "En cola" in html
        assert "0 de 5 tiendas terminadas" in html
        assert html.count('aria-live="polite"') == 1
        fuente.suelta()
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "every 1s" not in h)
        assert "Hecha" in html and "Descargando" not in html and "En cola" not in html
        assert "hx-trigger" not in html
        assert "Histórico y alertas" in html

    _correr(escenario)


def test_estado_sin_cambios_responde_204() -> None:
    fuente = FuenteLenta()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "Descargando" in h)
        actual = await cliente.aget(f"/busquedas/{id_busqueda}/estado?v={_version(html)}", HX)
        assert actual.estado == 204 and actual.cuerpo == b""
        vieja = await cliente.aget(f"/busquedas/{id_busqueda}/estado?v=0", HX)
        assert vieja.estado == 200 and 'id="busqueda"' in vieja.texto
        fuente.suelta()

    _correr(escenario)


def test_los_resultados_aparecen_por_tienda_segun_terminan() -> None:
    fuente = FuenteLenta()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        fuente.suelta(NORTE)
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: TITULO_NORTE in h)
        assert "1 de 5 tiendas terminadas" in html
        assert "provisional" in html
        assert TITULO_YA not in html
        assert "Histórico y alertas" not in html and "/productos/" not in html
        assert "every 1s" in html
        fuente.suelta()

    _correr(escenario)


def test_estado_de_busqueda_desconocida_da_404_y_corta_el_sondeo() -> None:
    respuesta = _cliente().get("/busquedas/0123456789abcdef/estado?v=3", cabeceras=HX)
    assert respuesta.estado == 404
    assert 'id="busqueda"' in respuesta.texto and "ya no está" in respuesta.texto
    assert "hx-trigger" not in respuesta.texto and "<html" not in respuesta.texto


# --- Cancelar, actualizar, reintentar y sin JavaScript -----------------------------------------


def test_cancelar_desde_la_web() -> None:
    fuente = FuenteLenta()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        fuente.suelta(NORTE)
        await _sondear_hasta(cliente, id_busqueda, lambda h: TITULO_NORTE in h)
        ruta = f"/busquedas/{id_busqueda}/cancelar"
        sin_origen = await cliente.apost(ruta, {"confirmar": "1"}, HX)
        assert sin_origen.estado == 403
        respuesta = await cliente.apost(ruta, {"confirmar": "1"}, {**HX, **ORIGEN})
        assert respuesta.estado == 200
        html = respuesta.texto
        assert "Cancelada" in html and "hx-trigger" not in html
        assert TITULO_NORTE in html
        assert re.search(rf"{YA}</span>\s*<span[^>]*>Cancelada", html)
        assert fuente.orden == ["cancelada"]
        sin_htmx = await cliente.apost(ruta, {"confirmar": "1"}, ORIGEN)
        assert sin_htmx.estado == 303
        assert sin_htmx.cabeceras["location"].startswith(f"/busquedas/{id_busqueda}?texto=")
        desconocida = await cliente.apost(
            "/busquedas/0123456789abcdef/cancelar", {"confirmar": "1"}, ORIGEN
        )
        assert desconocida.estado == 404

    _correr(escenario)


def test_actualizar_precios_relanza_la_misma_consulta() -> None:
    html = _cliente().get(f"/buscar?texto={OEM}&modo=clasico", cabeceras=HX).texto
    consulta = ConsultaBusqueda(OEM, modo=Modo.CLASICO)
    assert f'href="/buscar?{escape(parametros_de_consulta(consulta))}"' in html
    assert "Actualizar precios" in html


def test_tienda_en_espera_ensena_reintentar() -> None:
    fuente = FuenteLenta(en_espera=frozenset({YA}))
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "En espera" in h)
        assert f'action="/tiendas/{YA}/reintentar"' in html
        assert html.count("/reintentar") == 1
        fuente.suelta()

    _correr(escenario)


def test_tienda_bloqueada_ensena_reintentar() -> None:
    # A8: una tienda bloqueada se recuerda un rato; «Reintentar» la vuelve a probar antes.
    fuente = FuenteLenta(bloqueadas=frozenset({YA}))
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        fuente.suelta()
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "Bloqueada" in h)
        assert f'action="/tiendas/{YA}/reintentar"' in html
        assert html.count("/reintentar") == 1

    _correr(escenario)


def test_sin_js_la_pagina_en_curso_se_refresca() -> None:
    fuente = FuenteLenta()
    cliente = _cliente(fuente, espera_inicial_s=0)
    refresco = '<noscript><meta http-equiv="refresh" content="2"></noscript>'

    async def escenario() -> None:
        ubicacion = (await cliente.aget(f"/buscar?texto={OEM}")).cabeceras["location"]
        en_curso = (await cliente.aget(ubicacion)).texto
        assert refresco in en_curso
        assert "Cancelar" in en_curso
        fuente.suelta()
        id_busqueda = _ID.match(ubicacion)
        assert id_busqueda
        await _sondear_hasta(cliente, id_busqueda.group(1), lambda h: "every 1s" not in h)
        terminada = (await cliente.aget(ubicacion)).texto
        assert refresco not in terminada

    _correr(escenario)


# --- A10: progreso fino por tienda; A1: lo que retira la revisión al terminar ------------------


class FuenteDetallada(FuenteLenta):
    """Como `FuenteLenta`, pero cuenta en qué está cada tienda mientras descarga (A10)."""

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        assert seguimiento is not None
        seguimiento.detalle(NORTE, "Primera vez en esta tienda: suele tardar 40-90 s")
        return await super().buscar(consulta, seguimiento=seguimiento)


def test_cada_tienda_en_marcha_ensena_en_que_esta_y_cuanto_lleva() -> None:
    fuente = FuenteDetallada()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "Descargando" in h)
        assert re.search(
            r'class="estado-tienda-detalle[^"]*">\s*Primera vez en esta tienda: suele tardar '
            r"40-90 s · \d+ s\s*<",
            html,
        ), html
        fuente.suelta()
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "every 1s" not in h)
        # Terminada, ya no se enseña el detalle de en qué estaba.
        assert "estado-tienda-detalle" not in html

    _correr(escenario)


def test_en_cola_dice_que_espera_a_alguna_de_las_que_estan_en_marcha() -> None:
    fuente = FuenteLenta()
    gestor = GestorBusquedas(fuente, simultaneas=1)
    cliente = _cliente(fuente, espera_inicial_s=0, gestor=gestor)

    async def escenario() -> None:
        await cliente.aget(f"/buscar?texto={OEM}", HX)
        segunda = await cliente.aget("/buscar?texto=freno", HX)
        assert "En cola: empieza cuando termine alguna de las búsquedas en marcha." in segunda.texto
        fuente.suelta()

    _correr(escenario)


class FuenteConRevision(FuenteDemo):
    """Un parcial con todas las ofertas y un final en el que la revisión descarta una."""

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        from precia.nucleo.modelos import Descartado

        assert seguimiento is not None
        final = await super().buscar(consulta)
        seguimiento.parcial(replace(final, provisional=True))
        await asyncio.sleep(0)
        retirada = next(o for g in final.grupos for o in g.ofertas if o.titulo == TITULO_YA)
        grupos = tuple(
            replace(g, ofertas=tuple(o for o in g.ofertas if o is not retirada))
            for g in final.grupos
        )
        descartado = Descartado(
            retirada.tienda, retirada.titulo, "Es un accesorio", retirada.url, origen="ia"
        )
        return replace(
            final, grupos=tuple(g for g in grupos if g.ofertas), descartados=(descartado,)
        )


def test_las_ofertas_vistas_que_retira_la_revision_se_dicen_al_terminar() -> None:
    fuente = FuenteConRevision()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: "every 1s" not in h)
        assert re.search(
            r"La revisión ha retirado 1 oferta que se veía en los resultados provisionales: "
            r'está en <a href="#descartados-ia">',
            html,
        ), html
        assert "Es un accesorio" in html

    _correr(escenario)


def test_los_resultados_provisionales_avisan_de_la_revision_pendiente() -> None:
    fuente = FuenteLenta()
    cliente = _cliente(fuente, espera_inicial_s=0)

    async def escenario() -> None:
        id_busqueda = _id(await cliente.aget(f"/buscar?texto={OEM}", HX))
        fuente.suelta(NORTE)
        html = await _sondear_hasta(cliente, id_busqueda, lambda h: TITULO_NORTE in h)
        assert "sin revisar" in html and "«Descartados»" in html
        fuente.suelta()

    _correr(escenario)
