"""Alta de tiendas y revisión del perfil desde la web (memoria §4: la persona fija el perfil)."""

import pytest

from precia.nucleo.fuente_demo import FuenteDemo
from precia.web.app import crear_app
from tests.asgi import ClienteASGI, Respuesta

ORIGEN = {"Origin": "http://127.0.0.1:8000"}
PENDIENTE = "desguacesur.example"


@pytest.fixture
def fuente() -> FuenteDemo:
    return FuenteDemo()


@pytest.fixture
def cliente(fuente: FuenteDemo) -> ClienteASGI:
    return ClienteASGI(crear_app(fuente))


def _alerta(respuesta: Respuesta) -> str:
    return respuesta.texto.split('role="alert">')[1].split("<")[0]


# --- Añadir tienda -----------------------------------------------------------------------------


def test_la_pantalla_de_tiendas_tiene_el_formulario_de_alta(cliente: ClienteASGI) -> None:
    html = cliente.get("/tiendas").texto
    assert 'action="/tiendas"' in html and 'method="post"' in html
    assert 'name="dominio"' in html and 'name="nombre"' in html
    assert "Disponible en el hito H4" not in html


def test_alta_de_tienda_redirige_a_su_ficha(cliente: ClienteASGI, fuente: FuenteDemo) -> None:
    respuesta = cliente.post(
        "/tiendas", {"dominio": "WWW.Nueva-Tienda.es", "nombre": "Nueva"}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 303
    assert respuesta.cabeceras["location"] == "/tiendas/nueva-tienda.es?alta=1"
    assert fuente.tienda("nueva-tienda.es") is not None
    pagina = cliente.get("/tiendas/nueva-tienda.es?alta=1")
    assert pagina.estado == 200 and "Tienda añadida" in pagina.texto
    assert "nueva-tienda.es" in cliente.get("/tiendas").texto


@pytest.mark.parametrize(
    ("datos", "mensaje"),
    [
        ({"dominio": "localhost", "nombre": "x"}, "dominio"),
        ({"dominio": "https://otra.es/admin", "nombre": "x"}, "dominio"),
        ({"dominio": "169.254.169.254", "nombre": "x"}, "dominio"),
        ({"dominio": "", "nombre": "x"}, "dominio"),
        ({"dominio": "a" * 300 + ".es", "nombre": "x"}, "dominio"),
        ({"dominio": "nueva.es", "nombre": "n" * 101}, "nombre"),
        ({"dominio": "nueva.es", "nombre": "con\u0000nulo"}, "nombre"),
        ({"dominio": "recambiosnorte.example", "nombre": "x"}, "ya está dada de alta"),
    ],
)
def test_alta_no_valida_explica_el_error(
    cliente: ClienteASGI, fuente: FuenteDemo, datos: dict[str, str], mensaje: str
) -> None:
    antes = len(fuente.tiendas())
    respuesta = cliente.post("/tiendas", datos, cabeceras=ORIGEN)
    assert respuesta.estado == 422
    assert mensaje in _alerta(respuesta)
    assert len(fuente.tiendas()) == antes


def test_alta_conserva_lo_escrito_escapado(cliente: ClienteASGI) -> None:
    carga = '"><script>alert(1)</script>'
    respuesta = cliente.post("/tiendas", {"dominio": carga, "nombre": carga}, cabeceras=ORIGEN)
    assert respuesta.estado == 422
    assert "<script>alert(1)" not in respuesta.texto
    assert "&lt;script&gt;" in respuesta.texto


def test_nombre_con_html_se_escapa_en_el_listado(cliente: ClienteASGI) -> None:
    carga = "<img src=x onerror=alert(1)>"
    cliente.post("/tiendas", {"dominio": "nueva.es", "nombre": carga}, cabeceras=ORIGEN)
    html = cliente.get("/tiendas").texto
    assert carga not in html and "&lt;img src=x" in html


@pytest.mark.parametrize(
    "cabeceras",
    [{}, {"Origin": "https://atacante.example"}, {"Sec-Fetch-Site": "cross-site"}],
    ids=["sin-origen", "otro-origen", "otro-sitio"],
)
def test_alta_desde_otra_web_se_rechaza(
    cliente: ClienteASGI, fuente: FuenteDemo, cabeceras: dict[str, str]
) -> None:
    respuesta = cliente.post("/tiendas", {"dominio": "nueva.es"}, cabeceras=cabeceras)
    assert respuesta.estado == 403
    assert fuente.tienda("nueva.es") is None


# --- Revisar y fijar perfil -------------------------------------------------------------------


def test_perfil_pendiente_tiene_el_boton_de_revisar(cliente: ClienteASGI) -> None:
    html = cliente.get(f"/tiendas/{PENDIENTE}").texto
    formulario = html.split(f'action="/tiendas/{PENDIENTE}/revisar"')[1].split("</form>")[0]
    assert 'name="version" value="1"' in formulario
    assert "disabled" not in formulario
    assert "Disponible en el hito H4" not in html


def test_perfil_ya_revisado_no_se_puede_volver_a_fijar(cliente: ClienteASGI) -> None:
    html = cliente.get("/tiendas/recambiosnorte.example").texto
    assert "/revisar" not in html


def test_revisar_fija_el_perfil(cliente: ClienteASGI, fuente: FuenteDemo) -> None:
    respuesta = cliente.post(f"/tiendas/{PENDIENTE}/revisar", {"version": "1"}, cabeceras=ORIGEN)
    assert respuesta.estado == 303
    assert respuesta.cabeceras["location"] == f"/tiendas/{PENDIENTE}?revisado=1"
    tienda = fuente.tienda(PENDIENTE)
    assert tienda is not None and tienda.estado.value == "activo"
    html = cliente.get(f"/tiendas/{PENDIENTE}?revisado=1").texto
    assert "Perfil revisado y fijado" in html
    assert "/revisar" not in html


@pytest.mark.parametrize(
    ("version", "mensaje"),
    [("2", "versión actual"), ("abc", "versión"), ("", "versión"), ("9" * 30, "versión")],
)
def test_revisar_una_version_que_no_vale(
    cliente: ClienteASGI, fuente: FuenteDemo, version: str, mensaje: str
) -> None:
    respuesta = cliente.post(
        f"/tiendas/{PENDIENTE}/revisar", {"version": version}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 422
    assert mensaje in _alerta(respuesta)
    tienda = fuente.tienda(PENDIENTE)
    assert tienda is not None and tienda.estado.value == "pendiente_revision"


def test_revisar_una_tienda_que_no_existe(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/tiendas/no-existe.example/revisar", {"version": "1"}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 404


def test_revisar_desde_otra_web_se_rechaza(cliente: ClienteASGI, fuente: FuenteDemo) -> None:
    respuesta = cliente.post(
        f"/tiendas/{PENDIENTE}/revisar",
        {"version": "1"},
        cabeceras={"Origin": "https://atacante.example"},
    )
    assert respuesta.estado == 403
    tienda = fuente.tienda(PENDIENTE)
    assert tienda is not None and tienda.estado.value == "pendiente_revision"


def test_revisar_por_get_no_existe(cliente: ClienteASGI) -> None:
    assert cliente.get(f"/tiendas/{PENDIENTE}/revisar").estado in (404, 405)


def test_volver_a_validar_no_promete_un_hito_ya_pasado(cliente: ClienteASGI) -> None:
    html = cliente.get("/tiendas/motorpiezas.example").texto
    assert "H4" not in html
