"""Controles de seguridad de la interfaz web (asignatura de ciberseguridad)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    GrupoProducto,
    OrigenDato,
    ResultadoBusqueda,
    Tienda,
    ValorCampo,
)
from precia.web.app import crear_app
from tests.asgi import ClienteASGI

ORIGEN = "http://127.0.0.1:8000"
CARGA_XSS = '<script>alert("xss")</script><img src=x onerror=alert(1)>'


@pytest.fixture
def cliente() -> ClienteASGI:
    return ClienteASGI(crear_app())


def test_cabeceras_de_seguridad(cliente: ClienteASGI) -> None:
    cab = cliente.get("/").cabeceras
    csp = cab["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert cab["x-content-type-options"] == "nosniff"
    assert cab["referrer-policy"] == "no-referrer"
    assert cab["x-frame-options"] == "DENY"


def test_htmx_endurecido(cliente: ClienteASGI) -> None:
    html = cliente.get("/").texto
    assert '"allowEval":false' in html
    assert '"allowScriptTags":false' in html
    assert '"includeIndicatorStyles":false' in html


def test_sin_scripts_ni_estilos_en_linea(cliente: ClienteASGI) -> None:
    html = cliente.get("/").texto
    assert "<script>" not in html and "<style" not in html
    assert " style=" not in html and " onclick=" not in html


class FuenteMaliciosa(FuenteDemo):
    """Tienda que devuelve HTML malicioso en los datos extraídos."""

    def producto(self, id_producto: str) -> GrupoProducto | None:
        grupo = super().producto(id_producto)
        if grupo is None:
            return None
        ofertas = tuple(replace(o, titulo=CARGA_XSS) for o in grupo.ofertas)
        return replace(grupo, nombre=CARGA_XSS, ofertas=ofertas)


def test_datos_de_tiendas_se_escapan() -> None:
    cliente = ClienteASGI(crear_app(FuenteMaliciosa()))
    html = cliente.get("/productos/pastillas-1k0698151").texto
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_host_no_permitido_se_rechaza() -> None:
    cliente = ClienteASGI(crear_app(), host="atacante.example")
    assert cliente.get("/").estado == 400


@pytest.mark.parametrize(
    "cabeceras",
    [
        {"Origin": "https://atacante.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null"},
    ],
)
def test_csrf_peticiones_de_otro_origen_se_rechazan(
    cliente: ClienteASGI, cabeceras: dict[str, str]
) -> None:
    respuesta = cliente.post("/ajustes", {"proveedor": "ollama"}, cabeceras=cabeceras)
    assert respuesta.estado == 403


def test_csrf_post_sin_origen_ni_sec_fetch_se_rechaza(cliente: ClienteASGI) -> None:
    assert cliente.post("/ajustes", {"proveedor": "ollama"}).estado == 403


def test_csrf_mismo_origen_se_acepta(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/ajustes", {"proveedor": "ollama"}, cabeceras={"Sec-Fetch-Site": "same-origin"}
    )
    assert respuesta.estado == 303


def test_cuerpo_demasiado_grande_se_rechaza(cliente: ClienteASGI) -> None:
    respuesta = cliente.post(
        "/alertas", {"producto_id": "x" * 20_000}, cabeceras={"Origin": ORIGEN}
    )
    assert respuesta.estado == 413


# --- Hallazgos de la revisión adversarial de H1W ---------------------------------------------


@pytest.mark.parametrize(
    "ruta",
    [
        "/",
        "/buscar?texto=pastillas",
        "/productos/pastillas-1k0698151",
        "/tiendas",
        "/tiendas/desguacesur.example",
        "/ia",
    ],
)
def test_ninguna_pantalla_tiene_codigo_en_linea(cliente: ClienteASGI, ruta: str) -> None:
    html = cliente.get(ruta).texto
    assert "<script>" not in html and "<style" not in html
    assert " style=" not in html and " onclick=" not in html and "hx-on" not in html


@pytest.mark.parametrize(
    ("ruta", "host", "estado"),
    [
        ("/no-existe", "127.0.0.1:8000", 404),
        ("/estaticos/estilos.css", "127.0.0.1:8000", 200),
        ("/", "atacante.example", 400),
    ],
)
def test_cabeceras_tambien_en_errores_y_estaticos(ruta: str, host: str, estado: int) -> None:
    respuesta = ClienteASGI(crear_app(), host=host).get(ruta)
    assert respuesta.estado == estado
    assert "content-security-policy" in respuesta.cabeceras


class FuenteRota(FuenteDemo):
    def tiendas(self) -> list[Tienda]:
        raise RuntimeError("fallo interno")


def test_error_500_lleva_cabeceras_y_no_filtra_detalles() -> None:
    respuesta = ClienteASGI(crear_app(FuenteRota())).get("/tiendas")
    assert respuesta.estado == 500
    assert "content-security-policy" in respuesta.cabeceras
    assert "fallo interno" not in respuesta.texto


def test_content_length_y_transfer_encoding_a_la_vez_se_rechaza(cliente: ClienteASGI) -> None:
    respuesta = cliente.peticion(
        "POST",
        "/ajustes",
        cuerpo=b"proveedor=ollama",
        cabeceras={
            "Origin": ORIGEN,
            "Content-Length": "5",
            "Transfer-Encoding": "chunked",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert respuesta.estado == 400


def test_cuerpo_mayor_que_el_declarado_se_corta(cliente: ClienteASGI) -> None:
    respuesta = cliente.peticion(
        "POST",
        "/alertas",
        cuerpo=b"producto_id=" + b"x" * 20_000,
        cabeceras={
            "Origin": ORIGEN,
            "Content-Length": "10",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert respuesta.estado == 413


@pytest.mark.parametrize("sitio", ["cross-site", "same-site"])
def test_busqueda_lanzada_desde_otra_web_se_rechaza(cliente: ClienteASGI, sitio: str) -> None:
    respuesta = cliente.get("/buscar?texto=freno&modo=ia", cabeceras={"Sec-Fetch-Site": sitio})
    assert respuesta.estado == 403


@pytest.mark.parametrize("sitio", ["same-origin", "none"])
def test_busqueda_propia_o_escrita_a_mano_se_acepta(cliente: ClienteASGI, sitio: str) -> None:
    respuesta = cliente.get("/buscar?texto=freno", cabeceras={"Sec-Fetch-Site": sitio})
    assert respuesta.estado == 200


class FuenteEspia(FuenteDemo):
    def __init__(self) -> None:
        super().__init__()
        self.consultas: list[ConsultaBusqueda] = []

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda:
        self.consultas.append(consulta)
        return await super().buscar(consulta)


def test_tiendas_desconocidas_no_llegan_a_la_fuente() -> None:
    # Desde H2 cada tienda es un dominio que se visita: aceptar cualquiera sería SSRF.
    espia = FuenteEspia()
    ClienteASGI(crear_app(espia)).get(
        "/buscar?texto=freno&tiendas=169.254.169.254&tiendas=localhost:22"
        "&tiendas=recambiosnorte.example"
    )
    assert espia.consultas[-1].tiendas == ("recambiosnorte.example",)


class FuenteMaliciosaEnBusqueda(FuenteDemo):
    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta)
        grupos = tuple(
            replace(
                g,
                ofertas=tuple(
                    replace(o, titulo=CARGA_XSS, url="javascript:alert(document.domain)")
                    for o in g.ofertas
                ),
            )
            for g in resultado.grupos
        )
        return replace(resultado, grupos=grupos)


def test_resultados_escapan_datos_y_neutralizan_enlaces_peligrosos() -> None:
    cliente = ClienteASGI(crear_app(FuenteMaliciosaEnBusqueda()))
    for cabeceras in ({}, {"HX-Request": "true"}):
        html = cliente.get("/buscar?texto=pastillas", cabeceras=cabeceras).texto
        assert "&lt;script&gt;" in html
        assert "<script>alert" not in html
        assert "javascript:" not in html


class FuentePrecioInyectado(FuenteDemo):
    """Una tienda cuyo «precio» extraído por la IA no aparece en la página."""

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta)
        grupo = resultado.grupos[0]
        trampa = replace(
            grupo.ofertas[0],
            tienda="trampa.example",
            precio=Decimal("1"),
            campos={"precio": ValorCampo("1,00 €", OrigenDato.IA, anclado=False)},
        )
        grupo = replace(grupo, ofertas=(*grupo.ofertas, trampa))
        return replace(resultado, grupos=(grupo, *resultado.grupos[1:]))


def test_precio_no_anclado_se_marca_y_no_es_el_mejor() -> None:
    html = (
        ClienteASGI(crear_app(FuentePrecioInyectado()))
        .get("/buscar?texto=pastillas&modo=ia", cabeceras={"HX-Request": "true"})
        .texto
    )
    mejor = html.split('class="mejor-precio">')[1].split("<")[0]
    assert mejor != "1,00 €"
    assert "no anclado" in html.split("trampa.example")[1].split("</tr>")[0]


def test_numeros_con_digitos_unicode_dan_422_y_no_500(cliente: ClienteASGI) -> None:
    for campo in ("tope_llamadas_busqueda", "retardo_dominio_s"):
        respuesta = cliente.post("/ajustes", {campo: "²"}, cabeceras={"Origin": ORIGEN})
        assert respuesta.estado == 422


def test_pedir_solo_tiendas_desconocidas_no_busca_en_todas() -> None:
    # Quitar las desconocidas dejaba la lista vacía, que para la fuente significa «todas»: una
    # URL con una tienda que no existe (o ya borrada) visitaba todas las tiendas y gastaba IA.
    espia = FuenteEspia()
    respuesta = ClienteASGI(crear_app(espia)).get(
        "/buscar?texto=freno&tiendas=169.254.169.254&tiendas=no-dada-de-alta.es"
    )
    assert espia.consultas == []
    assert respuesta.estado == 422
    assert "Ninguna de las tiendas elegidas está dada de alta" in respuesta.texto
