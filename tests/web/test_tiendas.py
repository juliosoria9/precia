"""Alta de tiendas y revisión del perfil desde la web (memoria §4: la persona fija el perfil)."""

from dataclasses import replace

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


# --- URL de entrada (nota 16 §A) ------------------------------------------------------------

BUSQUEDA = "https://nueva.es/buscar?q={consulta}"
NORTE = "recambiosnorte.example"


def _formulario(html: str, accion: str) -> str:
    return html.split(f'action="{accion}"')[1].split("</form>")[0]


def test_el_alta_tiene_el_campo_opcional_de_entrada_con_su_ayuda(cliente: ClienteASGI) -> None:
    formulario = _formulario(cliente.get("/tiendas").texto, "/tiendas")
    campo = formulario.split('name="entrada"')[1].split(">")[0]
    assert "required" not in campo and 'maxlength="500"' in campo
    assert "{consulta}" in formulario and "{pagina}" in formulario
    assert "no encuentra el buscador" in formulario
    # La ayuda está enlazada al campo para los lectores de pantalla.
    assert 'aria-describedby="ayuda-entrada"' in campo and 'id="ayuda-entrada"' in formulario


def test_alta_con_url_de_entrada(cliente: ClienteASGI, fuente: FuenteDemo) -> None:
    escrita = "  HTTPS://NUEVA.ES/buscar?q={consulta}  "
    respuesta = cliente.post(
        "/tiendas", {"dominio": "nueva.es", "nombre": "Nueva", "entrada": escrita}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 303
    tienda = fuente.tienda("nueva.es")
    assert tienda is not None and tienda.entrada == BUSQUEDA
    assert f"<code>{BUSQUEDA}</code>" in cliente.get("/tiendas/nueva.es").texto


def test_alta_sin_url_de_entrada(cliente: ClienteASGI, fuente: FuenteDemo) -> None:
    respuesta = cliente.post("/tiendas", {"dominio": "nueva.es", "entrada": ""}, cabeceras=ORIGEN)
    assert respuesta.estado == 303
    tienda = fuente.tienda("nueva.es")
    assert tienda is not None and tienda.entrada is None


@pytest.mark.parametrize(
    ("entrada", "mensaje"),
    [
        ("https://otra.es/buscar?q={consulta}", "nueva.es"),
        ("https://nueva.es.atacante.com/buscar?q={consulta}", "nueva.es"),
        ("https://usuario@nueva.es/listado", "usuario"),
        ("https://nueva.es:8080/listado", "puerto"),
        ("javascript:alert(1)", "https://"),
        ("https://nueva.es/b/{consulta}?q={consulta}", "una vez"),
        ("https://nueva.es/b?q={consulta}&p={pagina}", "no las dos"),
        ("https://nueva.es/b?q={query}", "llaves"),
        ("https://nueva.es/categoría", "ASCII"),
        ("https://nueva.es/" + "a" * 600, "500"),
        ("https://[nueva.es/listado", "nueva.es"),
    ],
)
def test_alta_con_url_de_entrada_no_valida(
    cliente: ClienteASGI, fuente: FuenteDemo, entrada: str, mensaje: str
) -> None:
    antes = len(fuente.tiendas())
    respuesta = cliente.post(
        "/tiendas", {"dominio": "nueva.es", "nombre": "Nueva", "entrada": entrada}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 422
    assert mensaje in _alerta(respuesta)
    assert len(fuente.tiendas()) == antes and fuente.tienda("nueva.es") is None
    # Se conserva lo escrito para corregirlo.
    assert 'value="nueva.es"' in respuesta.texto


def test_alta_conserva_la_entrada_escapada(cliente: ClienteASGI) -> None:
    carga = '"><script>alert(1)</script>'
    respuesta = cliente.post(
        "/tiendas", {"dominio": "nueva.es", "entrada": carga}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 422
    assert "<script>alert(1)" not in respuesta.texto
    assert "&#34;&gt;&lt;script&gt;" in respuesta.texto


def test_la_ficha_ensena_la_entrada_y_el_tipo_de_perfil(cliente: ClienteASGI) -> None:
    html = cliente.get(f"/tiendas/{NORTE}").texto
    assert "URL de entrada" in html and "Tipo de perfil" in html
    assert "html (buscador de la tienda)" in html
    sin_perfil = cliente.get("/tiendas/autodescuento.example").texto
    assert "<dt>Tipo de perfil</dt><dd>—</dd>" in sin_perfil


def test_la_ficha_ensena_los_tipos_sin_buscador() -> None:
    sitemap = "sitemap (sin buscador: catálogo de URL del sitemap)"
    base = FuenteDemo().tienda(NORTE)
    assert base is not None and base.version_actual is not None
    version = replace(
        base.version_actual,
        url_busqueda=f"https://{NORTE}/sitemap.xml",
        selectores={"tipo": sitemap},
    )
    tienda = replace(base, versiones=(version,), entrada=f"https://{NORTE}/catalogo")
    cliente = ClienteASGI(crear_app(FuenteDemo(_tiendas=[tienda])))
    html = cliente.get(f"/tiendas/{NORTE}").texto
    assert f"<dt>Tipo de perfil</dt><dd>{sitemap}</dd>" in html
    assert f"<code>https://{NORTE}/catalogo</code>" in html


def test_la_ficha_tiene_el_formulario_para_cambiar_la_entrada(cliente: ClienteASGI) -> None:
    html = cliente.get(f"/tiendas/{NORTE}").texto
    assert f'method="post" action="/tiendas/{NORTE}/entrada"' in html
    formulario = _formulario(html, f"/tiendas/{NORTE}/entrada")
    campo = formulario.split('name="entrada"')[1].split(">")[0]
    assert 'maxlength="500"' in campo and "required" not in campo
    assert "{consulta}" in formulario and "{pagina}" in formulario


def test_cambiar_la_entrada_desde_la_ficha(cliente: ClienteASGI, fuente: FuenteDemo) -> None:
    entrada = f"https://www.{NORTE}/catalogo?page={{pagina}}"
    respuesta = cliente.post(f"/tiendas/{NORTE}/entrada", {"entrada": entrada}, cabeceras=ORIGEN)
    assert respuesta.estado == 303
    assert respuesta.cabeceras["location"] == f"/tiendas/{NORTE}?entrada=1"
    tienda = fuente.tienda(NORTE)
    assert tienda is not None and tienda.entrada == entrada
    html = cliente.get(f"/tiendas/{NORTE}?entrada=1").texto
    assert "URL de entrada guardada" in html
    assert f"<code>{entrada}</code>" in html
    assert f'value="{entrada}"' in _formulario(html, f"/tiendas/{NORTE}/entrada")


def test_quitar_la_entrada_desde_la_ficha(cliente: ClienteASGI, fuente: FuenteDemo) -> None:
    fuente.cambiar_entrada(NORTE, f"https://{NORTE}/listado")
    html = cliente.get(f"/tiendas/{NORTE}").texto
    assert html.count(f'action="/tiendas/{NORTE}/entrada"') == 2
    quitar = html.split(f'action="/tiendas/{NORTE}/entrada"')[2].split("</form>")[0]
    assert '<input type="hidden" name="entrada" value="">' in quitar
    respuesta = cliente.post(f"/tiendas/{NORTE}/entrada", {"entrada": ""}, cabeceras=ORIGEN)
    assert respuesta.estado == 303
    tienda = fuente.tienda(NORTE)
    assert tienda is not None and tienda.entrada is None
    html = cliente.get(f"/tiendas/{NORTE}?entrada=1").texto
    assert "URL de entrada quitada" in html
    # Sin entrada no hay nada que quitar.
    assert html.count(f'action="/tiendas/{NORTE}/entrada"') == 1


@pytest.mark.parametrize(
    ("entrada", "mensaje"),
    [
        ("https://piezasya.example/buscar?q={consulta}", NORTE),
        (f"https://{NORTE}@atacante.example/", "usuario"),
        (f"data:text/html,{NORTE}", "https://"),
        (f"https://{NORTE}/b?q={{consulta}}#x", "#"),
        (f"https://{NORTE}/" + "a" * 600, "500"),
    ],
)
def test_cambiar_la_entrada_a_una_no_valida(
    cliente: ClienteASGI, fuente: FuenteDemo, entrada: str, mensaje: str
) -> None:
    respuesta = cliente.post(f"/tiendas/{NORTE}/entrada", {"entrada": entrada}, cabeceras=ORIGEN)
    assert respuesta.estado == 422
    assert mensaje in _alerta(respuesta)
    tienda = fuente.tienda(NORTE)
    assert tienda is not None and tienda.entrada is None
    # La ficha sigue entera (versiones del perfil) y conserva lo escrito.
    assert "Versiones del perfil" in respuesta.texto
    if len(entrada) <= 500:
        assert f'value="{entrada}"' in respuesta.texto


def test_cambiar_la_entrada_conserva_lo_escrito_escapado(cliente: ClienteASGI) -> None:
    carga = '"><img src=x onerror=alert(1)>'
    respuesta = cliente.post(f"/tiendas/{NORTE}/entrada", {"entrada": carga}, cabeceras=ORIGEN)
    assert respuesta.estado == 422
    assert "<img src=x" not in respuesta.texto
    assert "&#34;&gt;&lt;img src=x" in respuesta.texto


@pytest.mark.parametrize(
    "cabeceras",
    [{}, {"Origin": "https://atacante.example"}, {"Sec-Fetch-Site": "cross-site"}],
    ids=["sin-origen", "otro-origen", "otro-sitio"],
)
def test_cambiar_la_entrada_desde_otra_web_se_rechaza(
    cliente: ClienteASGI, fuente: FuenteDemo, cabeceras: dict[str, str]
) -> None:
    entrada = f"https://{NORTE}/listado"
    respuesta = cliente.post(f"/tiendas/{NORTE}/entrada", {"entrada": entrada}, cabeceras=cabeceras)
    assert respuesta.estado == 403
    tienda = fuente.tienda(NORTE)
    assert tienda is not None and tienda.entrada is None


def test_cambiar_la_entrada_con_un_cuerpo_enorme_se_rechaza(
    cliente: ClienteASGI, fuente: FuenteDemo
) -> None:
    respuesta = cliente.post(
        f"/tiendas/{NORTE}/entrada", {"entrada": "a" * 20_000}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 413
    tienda = fuente.tienda(NORTE)
    assert tienda is not None and tienda.entrada is None


def test_cambiar_la_entrada_de_una_tienda_que_no_existe(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/tiendas/no-existe.example/entrada", {"entrada": ""}, cabeceras=ORIGEN
    )
    assert respuesta.estado == 404


def test_cambiar_la_entrada_por_get_no_existe(cliente: ClienteASGI) -> None:
    assert cliente.get(f"/tiendas/{NORTE}/entrada").estado in (404, 405)
