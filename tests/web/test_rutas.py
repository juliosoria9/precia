from dataclasses import replace
from decimal import Decimal

import pytest

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import (
    Coincidencia,
    ConsultaBusqueda,
    Descartado,
    EstadoPaso,
    GrupoProducto,
    OrigenDato,
    Paso,
    ProgresoTienda,
    ResultadoBusqueda,
    ResultadoEvaluacion,
    Seguimiento,
    SerieHistorico,
    ValorCampo,
)
from precia.nucleo.orquestador import aviso_en_espera
from precia.web.app import crear_app
from tests.asgi import ClienteASGI


@pytest.fixture
def cliente() -> ClienteASGI:
    return ClienteASGI(crear_app())


@pytest.mark.parametrize(
    ("ruta", "texto"),
    [
        ("/", "Buscar"),
        ("/tiendas", "recambiosnorte.example"),
        ("/tiendas/piezasya.example", "Versiones del perfil"),
        ("/productos/pastillas-1k0698151", "Histórico de precio"),
        ("/alertas", "Alertas"),
        ("/ia", "Registro de IA"),
        ("/evaluacion", "Banco de evaluación"),
        ("/ajustes", "Ajustes"),
    ],
)
def test_pantallas_responden(cliente: ClienteASGI, ruta: str, texto: str) -> None:
    respuesta = cliente.get(ruta)
    assert respuesta.estado == 200
    assert texto in respuesta.texto
    assert "DEMO" in respuesta.texto


def test_busqueda_htmx_devuelve_solo_el_fragmento(cliente: ClienteASGI) -> None:
    respuesta = cliente.get("/buscar?texto=1K0698151", cabeceras={"HX-Request": "true"})
    assert respuesta.estado == 200
    assert "<html" not in respuesta.texto
    assert "1K0698151" in respuesta.texto


def test_busqueda_sin_htmx_devuelve_pagina_completa(cliente: ClienteASGI) -> None:
    respuesta = cliente.get(
        "/buscar?texto=pastillas&modo=clasico&campos=precio&campos=marca", seguir=True
    )
    assert respuesta.estado == 200
    assert "<html" in respuesta.texto
    assert 'value="pastillas"' in respuesta.texto


def test_busqueda_sin_resultados(cliente: ClienteASGI) -> None:
    respuesta = cliente.get("/buscar?texto=zzzz", cabeceras={"HX-Request": "true"})
    assert "Sin resultados" in respuesta.texto


def test_busqueda_vacia_pide_texto(cliente: ClienteASGI) -> None:
    respuesta = cliente.get("/buscar?texto=", cabeceras={"HX-Request": "true"})
    assert respuesta.estado == 422
    assert "Escribe" in respuesta.texto


def test_rutas_inexistentes_dan_404(cliente: ClienteASGI) -> None:
    assert cliente.get("/no-existe").estado == 404
    assert cliente.get("/productos/no-existe").estado == 404
    assert cliente.get("/tiendas/no-existe.example").estado == 404


def test_crear_alerta_desde_formulario(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/alertas",
        {"producto_id": "pastillas-1k0698151", "umbral": "30,50", "canal": "email"},
        cabeceras={"HX-Request": "true", "Origin": "http://127.0.0.1:8000"},
    )
    assert respuesta.estado == 200
    assert "30,50 €" in respuesta.texto


def test_crear_alerta_con_umbral_invalido(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/alertas",
        {"producto_id": "pastillas-1k0698151", "umbral": "gratis", "canal": "email"},
        cabeceras={"Origin": "http://127.0.0.1:8000"},
    )
    assert respuesta.estado == 422


def test_guardar_ajustes(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/ajustes",
        {"proveedor": "ollama", "presupuesto_mensual_usd": "5", "tope_llamadas_busqueda": "10"},
        cabeceras={"Origin": "http://127.0.0.1:8000"},
    )
    assert respuesta.estado == 303
    assert 'value="ollama" checked' in cliente.get("/ajustes").texto


def test_htmx_se_sirve_en_local(cliente: ClienteASGI) -> None:
    respuesta = cliente.get("/estaticos/htmx.min.js")
    assert respuesta.estado == 200
    assert b'version:"2.0.4"' in respuesta.cuerpo


@pytest.mark.parametrize(
    ("ruta", "tipo"),
    [
        ("/estaticos/htmx.min.js", "text/javascript"),
        ("/estaticos/estilos.css", "text/css"),
        ("/estaticos/icono.svg", "image/svg+xml"),
    ],
)
def test_estaticos_con_tipo_correcto(cliente: ClienteASGI, ruta: str, tipo: str) -> None:
    # Con `nosniff`, un .js servido como text/plain no se ejecutaría en el navegador.
    assert cliente.get(ruta).cabeceras["content-type"].startswith(tipo)


# --- Hallazgos de la revisión adversarial de H1W ---------------------------------------------


def test_restaurar_historial_devuelve_pagina_completa(cliente: ClienteASGI) -> None:
    # Con historyCacheSize=0, al pulsar «Atrás» HTMX pide la URL con estas dos cabeceras y
    # pone la respuesta en <body>: tiene que ser la página entera, no el fragmento.
    respuesta = cliente.get(
        "/buscar?texto=freno",
        cabeceras={"HX-Request": "true", "HX-History-Restore-Request": "true"},
        seguir=True,
    )
    assert "<html" in respuesta.texto


def test_busqueda_varia_segun_hx_request(cliente: ClienteASGI) -> None:
    assert "hx-request" in cliente.get("/buscar?texto=freno").cabeceras["vary"].lower()


def test_error_de_alerta_sin_htmx_devuelve_pagina_completa(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/alertas",
        {"producto_id": "pastillas-1k0698151", "umbral": "gratis", "canal": "email"},
        cabeceras={"Origin": "http://127.0.0.1:8000"},
    )
    assert respuesta.estado == 422
    assert "<html" in respuesta.texto and "Escribe un importe" in respuesta.texto


def test_error_de_alerta_con_htmx_no_borra_la_lista(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/alertas",
        {"producto_id": "pastillas-1k0698151", "umbral": "gratis", "canal": "email"},
        cabeceras={"HX-Request": "true", "Origin": "http://127.0.0.1:8000"},
    )
    assert respuesta.estado == 422
    assert respuesta.cabeceras["hx-retarget"] == "#error-alerta"


def test_error_de_ajustes_usa_la_etiqueta_visible(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/ajustes",
        {"tope_llamadas_busqueda": "500"},
        cabeceras={"Origin": "http://127.0.0.1:8000"},
    )
    assert respuesta.estado == 422
    mensaje = respuesta.texto.split('role="alert">')[1].split("<")[0]
    assert "Tope de llamadas por búsqueda" in mensaje


class FuenteConSerieVacia(FuenteDemo):
    def historico(self, id_producto: str) -> list[SerieHistorico]:
        return [*super().historico(id_producto), SerieHistorico("vacia.example", ())]


def test_producto_con_una_tienda_sin_historico() -> None:
    cliente = ClienteASGI(crear_app(FuenteConSerieVacia()))
    assert cliente.get("/productos/pastillas-1k0698151").estado == 200


def test_google_desactivado_no_se_puede_marcar(cliente: ClienteASGI) -> None:
    casilla = cliente.get("/").texto.split('name="descubrir"')[1].split(">")[0]
    assert "disabled" in casilla


def test_google_pedido_pero_desactivado_avisa_en_los_resultados(cliente: ClienteASGI) -> None:
    html = cliente.get("/buscar?texto=freno&descubrir=on", cabeceras={"HX-Request": "true"}).texto
    assert "mensaje-aviso" in html and "Google" in html


class FuenteSinPreciosAnclados(FuenteDemo):
    """Un grupo cuyos precios no aparecen en la página: no entra en el histórico."""

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta, seguimiento=seguimiento)
        grupo = resultado.grupos[0]
        ofertas = tuple(
            replace(
                o,
                campos={
                    **o.campos,
                    "precio": ValorCampo(str(o.precio), OrigenDato.IA, anclado=False),
                },
            )
            for o in grupo.ofertas
        )
        return replace(resultado, grupos=(replace(grupo, id="txt-sin-historico", ofertas=ofertas),))


def test_grupo_sin_precios_anclados_no_enlaza_a_un_historico_que_no_existe() -> None:
    # El orquestador nunca guarda un precio no anclado: el enlace llevaría a un 404.
    cliente = ClienteASGI(crear_app(FuenteSinPreciosAnclados()))
    html = cliente.get("/buscar?texto=pastillas", seguir=True).texto
    assert "/productos/txt-sin-historico" not in html
    assert "no se guarda en el histórico" in html


def test_evaluacion_no_promete_casos_que_no_se_guardan() -> None:
    # Todavía no se guarda ningún caso etiquetado: la página no puede decir lo contrario.
    html = ClienteASGI(crear_app(FuenteSinEvaluacion())).get("/evaluacion").texto
    assert "desde H2" not in html
    assert "H8" in html


class FuenteSinEvaluacion(FuenteDemo):
    def evaluacion(self) -> list[ResultadoEvaluacion]:
        return []


# --- ADR-017: el robots.txt siempre se ignora (enmienda del 2026-10-25) ------------------------


def test_ajustes_ya_no_tienen_la_casilla_de_respetar_el_robots(cliente: ClienteASGI) -> None:
    # Julio: «quiero que quites que si lo prohibe el robot.txt no lo hagas».
    assert "respetar_robots" not in cliente.get("/ajustes").texto
    origen = {"Origin": "http://127.0.0.1:8000"}
    # Un formulario antiguo que aún la envíe no rompe nada: se ignora.
    respuesta = cliente.post(
        "/ajustes", {"enviado": "1", "respetar_robots": "on"}, cabeceras=origen
    )
    assert respuesta.estado in (200, 303)
    assert "respetar_robots" not in cliente.get("/ajustes").texto


def test_robots_ignorado_no_dice_que_la_tienda_no_se_visite(cliente: ClienteASGI) -> None:
    # ADR-017: la etiqueta «robots.txt lo prohíbe» informa, no excluye.
    html = cliente.get("/").texto
    assert "robots.txt lo prohíbe · se ignora" in html
    ficha = cliente.get("/tiendas/autodescuento.example").texto
    assert "PrecIA no la visita" not in ficha
    assert "Esta tienda no tiene perfil." in ficha


def test_tiendas_siguen_ensenando_el_aviso_del_robots(cliente: ClienteASGI) -> None:
    # Julio: «no quiero que quites el aviso». La fila del robots.txt se queda en la lista y en
    # la ficha (en rojo si lo prohíbe); solo se fue la frase de que PrecIA no la visita.
    aviso = "Lo prohíbe; se ignora por decisión del usuario (ADR-017)"
    for ruta in ("/tiendas", "/tiendas/autodescuento.example"):
        html = cliente.get(ruta).texto
        assert "<dt>robots.txt</dt>" in html
        assert f'<dd class="texto-error">{aviso}</dd>' in html


# --- ADR-022: cliente HTTP con la huella de Chrome (curl) o urllib ------------------------------


def test_ajustes_muestran_el_selector_del_cliente_http(cliente: ClienteASGI) -> None:
    texto = cliente.get("/ajustes").texto
    assert 'name="transporte_http"' in texto
    assert '<option value="curl" selected>' in texto
    assert '<option value="urllib">' in texto


@pytest.mark.parametrize("transporte", ["urllib", "curl"])
def test_ajustes_guardan_el_cliente_http(cliente: ClienteASGI, transporte: str) -> None:
    respuesta = cliente.post(
        "/ajustes", {"transporte_http": transporte}, cabeceras={"Origin": "http://127.0.0.1:8000"}
    )
    assert respuesta.estado == 303
    assert f'<option value="{transporte}" selected>' in cliente.get("/ajustes").texto


@pytest.mark.parametrize("transporte", ["requests", "CURL", ""])
def test_ajustes_rechazan_otro_cliente_http(cliente: ClienteASGI, transporte: str) -> None:
    respuesta = cliente.post(
        "/ajustes", {"transporte_http": transporte}, cabeceras={"Origin": "http://127.0.0.1:8000"}
    )
    assert respuesta.estado == 422
    assert "Cliente HTTP no válido" in respuesta.texto


# --- Coincidencia con la referencia buscada y alertas (B5) -------------------------------------

ORIGEN = {"Origin": "http://127.0.0.1:8000"}
PASTILLAS = "pastillas-1k0698151"


def _alerta(cliente: ClienteASGI, **extra: str) -> str:
    formulario = {"producto_id": PASTILLAS, "umbral": "30,50", "canal": "email", **extra}
    respuesta = cliente.post("/alertas", formulario, cabeceras={"HX-Request": "true", **ORIGEN})
    assert respuesta.estado == 200
    return respuesta.texto


def test_crear_alerta_es_solo_de_exactas_por_defecto() -> None:
    datos = FuenteDemo()
    _alerta(ClienteASGI(crear_app(datos)))
    assert datos.alertas()[-1].solo_exactas is True


def test_crear_alerta_con_equivalentes() -> None:
    datos = FuenteDemo()
    html = _alerta(ClienteASGI(crear_app(datos)), equivalentes="1")
    assert datos.alertas()[-1].solo_exactas is False
    assert "incluye equivalentes y revisiones" in html


def test_formulario_de_alerta_ofrece_incluir_equivalentes(cliente: ClienteASGI) -> None:
    html = cliente.get(f"/productos/{PASTILLAS}").texto
    assert 'name="equivalentes"' in html
    assert "aún no tiene ofertas con la referencia exacta" not in html


def _con(grupo: GrupoProducto, coincidencias: dict[int, Coincidencia]) -> GrupoProducto:
    ofertas = tuple(
        replace(o, coincidencia=coincidencias.get(i, o.coincidencia))
        for i, o in enumerate(grupo.ofertas)
    )
    return replace(grupo, ofertas=ofertas)


class FuenteSoloEquivalentes(FuenteDemo):
    """Las ofertas de cada producto solo citan la referencia buscada entre sus equivalencias."""

    def producto(self, id_producto: str) -> GrupoProducto | None:
        grupo = super().producto(id_producto)
        if grupo is None:
            return None
        return _con(grupo, dict.fromkeys(range(len(grupo.ofertas)), Coincidencia.EQUIVALENTE))


def test_alerta_sin_ofertas_exactas_avisa_de_que_no_saltara() -> None:
    html = ClienteASGI(crear_app(FuenteSoloEquivalentes())).get(f"/productos/{PASTILLAS}").texto
    assert "aún no tiene ofertas con la referencia exacta" in html


TRASERAS = "https://piezasya.example/p/pastillas-traseras"


class FuenteConEquivalentes(FuenteDemo):
    """Pastillas con una oferta equivalente (la más barata) y una revisión; el disco, por texto."""

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta, seguimiento=seguimiento)
        grupos = []
        for grupo in resultado.grupos:
            if grupo.id == PASTILLAS:
                por_precio = {
                    Decimal("18.00"): Coincidencia.EQUIVALENTE,
                    Decimal("35.20"): Coincidencia.REVISION,
                }
                indices = {
                    i: por_precio[o.precio]
                    for i, o in enumerate(grupo.ofertas)
                    if o.precio in por_precio
                }
                grupos.append(_con(grupo, indices))
            else:
                grupos.append(replace(grupo, id="txt-disco", referencia_oem=None))
        descartado = Descartado(
            "piezasya.example",
            "Pastillas traseras",
            "Referencia OEM distinta",
            url=TRASERAS,
            precio=Decimal("24.50"),
        )
        return replace(resultado, grupos=tuple(grupos), descartados=(descartado,))


def _buscar_html(fuente: FuenteDemo, texto: str) -> str:
    cliente = ClienteASGI(crear_app(fuente))
    return cliente.get(f"/buscar?texto={texto}", cabeceras={"HX-Request": "true"}).texto


def test_resultados_etiquetan_la_coincidencia() -> None:
    html = _buscar_html(FuenteConEquivalentes(), "freno")
    assert ">equivalente</span>" in html
    assert ">revisión</span>" in html
    assert ">exacta</span>" in html
    assert 'title="La ficha cita la referencia buscada entre sus equivalencias' in html
    assert 'title="La referencia de la ficha es la buscada con un sufijo de revisión' in html
    # La columna solo sale en el grupo con referencia OEM, no en el agrupado por texto.
    assert html.count('<th scope="col">Coincidencia</th>') == 1


def test_mejor_precio_equivalente_lo_dice() -> None:
    html = _buscar_html(FuenteConEquivalentes(), "freno")
    cabecera = html.split('<div class="mejor">', 1)[1].split("</div>", 1)[0]
    assert "18,00 €" in cabecera
    assert "equivalente" in cabecera


def test_mejor_precio_exacto_no_lleva_etiqueta(cliente: ClienteASGI) -> None:
    html = cliente.get("/buscar?texto=1K0698151", cabeceras={"HX-Request": "true"}).texto
    cabecera = html.split('<div class="mejor">', 1)[1].split("</div>", 1)[0]
    assert "exacta" not in cabecera and "equivalente" not in cabecera


def test_descartados_con_enlace_y_precio() -> None:
    html = _buscar_html(FuenteConEquivalentes(), "freno")
    assert f'href="{TRASERAS}"' in html
    assert "24,50 €" in html


def test_sin_resultados_con_oem_no_sugiere_una_oem(cliente: ClienteASGI) -> None:
    html = cliente.get("/buscar?texto=ZZ99999", cabeceras={"HX-Request": "true"}).texto
    assert "Sin resultados" in html
    assert "Prueba con una referencia OEM" not in html
    assert "ninguna ficha lleva ni cita la referencia" in html
    sin_oem = cliente.get("/buscar?texto=zzzz", cabeceras={"HX-Request": "true"}).texto
    assert "Prueba con una referencia OEM" in sin_oem


def test_producto_muestra_la_coincidencia() -> None:
    html = ClienteASGI(crear_app(FuenteSoloEquivalentes())).get(f"/productos/{PASTILLAS}").texto
    assert '<th scope="col">Coincidencia</th>' in html
    assert ">equivalente</span>" in html


class FuenteConUsoDeIA(FuenteDemo):
    """Una búsqueda que ha usado la IA para configurar y extraer, con aciertos de caché (A3)."""

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta, seguimiento=seguimiento)
        return replace(
            resultado,
            llamadas_ia=3,
            llamadas_por_componente={"extractor": 1, "configurador": 2},
            ia_desde_cache=4,
        )


def test_resultados_dicen_para_que_se_uso_la_ia() -> None:
    cliente = ClienteASGI(crear_app(FuenteConUsoDeIA()))
    for cabeceras in ({}, {"HX-Request": "true"}):
        ruta = "/buscar?texto=pastillas&modo=ia"
        html = cliente.get(ruta, cabeceras=cabeceras, seguir=True).texto
        # En orden alfabético, para que el resumen no cambie de una búsqueda a otra.
        assert "configurador 2 · extractor 1" in html
        assert "4 desde la caché" in html


def test_resultados_sin_ia_no_ensenan_el_desglose(cliente: ClienteASGI) -> None:
    html = cliente.get("/buscar?texto=pastillas&modo=clasico", seguir=True).texto
    assert "desde la caché" not in html


# --- Tiendas en espera tras una configuración fallida (A1) ----------------------------------------

# El que genera el orquestador, no una copia: la copia fijaba el «1 tiendas» (revisión).
AVISO_ESPERA = aviso_en_espera(["caida.example"])


class FuenteConTiendaEnEspera(FuenteDemo):
    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta, seguimiento=seguimiento)
        general = ProgresoTienda("PrecIA", (Paso("en espera", EstadoPaso.AVISO, AVISO_ESPERA),))
        return replace(resultado, progreso=(general, *resultado.progreso))


def test_los_resultados_avisan_de_las_tiendas_en_espera_fuera_del_desplegable() -> None:
    cliente = ClienteASGI(crear_app(FuenteConTiendaEnEspera()))
    html = cliente.get("/buscar?texto=1K0698151", seguir=True).texto
    despues = html.split("</details>", 1)[1]
    aviso = despues.split('<p class="mensaje mensaje-aviso">', 1)[1].split("</p>")[0]
    assert AVISO_ESPERA in aviso
    assert 'href="/tiendas"' in aviso
