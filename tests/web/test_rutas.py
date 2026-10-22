from dataclasses import replace

import pytest

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    OrigenDato,
    ResultadoBusqueda,
    ResultadoEvaluacion,
    SerieHistorico,
    ValorCampo,
)
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
    respuesta = cliente.get("/buscar?texto=pastillas&modo=clasico&campos=precio&campos=marca")
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

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta)
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
    html = ClienteASGI(crear_app(FuenteSinPreciosAnclados())).get("/buscar?texto=pastillas").texto
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


# --- ADR-017: respetar o ignorar el robots.txt ------------------------------------------------


def test_ajustes_guardan_si_se_respeta_el_robots(cliente: ClienteASGI) -> None:
    origen = {"Origin": "http://127.0.0.1:8000"}
    assert 'name="respetar_robots"' in cliente.get("/ajustes").texto
    cliente.post("/ajustes", {"enviado": "1", "respetar_robots": "on"}, cabeceras=origen)
    assert 'name="respetar_robots" value="on" checked' in cliente.get("/ajustes").texto
    cliente.post("/ajustes", {"enviado": "1"}, cabeceras=origen)
    assert 'name="respetar_robots" value="on" checked' not in cliente.get("/ajustes").texto


def test_robots_ignorado_no_dice_que_la_tienda_no_se_visite(cliente: ClienteASGI) -> None:
    # Por defecto (ADR-017) se ignora: la etiqueta «robots.txt lo prohíbe» informa, no excluye.
    html = cliente.get("/").texto
    assert "robots.txt lo prohíbe · se ignora" in html


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
