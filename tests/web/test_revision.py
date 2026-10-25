"""La revisión de los resultados en la web: descartados plegados por origen y su ajuste."""

from dataclasses import replace
from decimal import Decimal

import pytest

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import ConsultaBusqueda, Descartado, ResultadoBusqueda, Seguimiento
from precia.web.app import crear_app
from tests.asgi import ClienteASGI

CERRADURA = "Cerradura de la puerta delantera derecha"
MOTIVO_IA = "IA · Pieza o accesorio de lo buscado: Es la cerradura, no la puerta"
MOTIVO_REGLA = "Parece una pieza o accesorio de «puerta» (empieza por «moldura»)"
ORIGEN = {"Origin": "http://127.0.0.1:8000"}


class FuenteConDescartes(FuenteDemo):
    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta, seguimiento=seguimiento)
        descartados = (
            Descartado(
                "a.example",
                CERRADURA,
                MOTIVO_IA,
                url="https://a.example/c",
                precio=Decimal("35"),
                origen="ia",
            ),
            Descartado("b.example", "Moldura para puerta", MOTIVO_REGLA, origen="regla"),
            Descartado("c.example", "Puerta sin precio", "Sin precio"),
        )
        return replace(resultado, descartados=descartados)


def _html() -> str:
    cliente = ClienteASGI(crear_app(FuenteConDescartes()))
    return cliente.get("/buscar?texto=puerta", cabeceras={"HX-Request": "true"}).texto


def _seccion(html: str, clase: str) -> str:
    inicio = html.index(f'<details class="descartados descartados-{clase} tarjeta"')
    return html[inicio : html.index("</details>", inicio)]


def test_tres_secciones_plegadas_por_origen_con_el_motivo() -> None:
    html = _html()
    ia = _seccion(html, "ia")
    assert "Descartados por la IA" in ia and "(1)" in ia
    assert CERRADURA in ia and "Es la cerradura, no la puerta" in ia
    assert "Moldura" not in ia
    regla = _seccion(html, "regla")
    assert "Descartados por la regla" in regla and "Moldura para puerta" in regla
    assert "empieza por «moldura»" in regla
    resto = _seccion(html, "otros")
    assert "Puerta sin precio" in resto and CERRADURA not in resto
    # Plegadas por defecto: ninguna lleva `open`.
    assert html.count('<details class="descartados') == 3
    assert "<details open" not in html and 'tarjeta" open' not in html


def test_el_resumen_enlaza_a_los_descartados_por_la_ia() -> None:
    assert 'href="#descartados-ia">1 descartada por la IA</a>' in _html()


def test_sin_descartes_no_hay_secciones() -> None:
    cliente = ClienteASGI(crear_app(FuenteDemo()))
    html = cliente.get("/buscar?texto=zzzz", cabeceras={"HX-Request": "true"}).texto
    assert "descartados-ia" not in html


@pytest.fixture
def cliente() -> ClienteASGI:
    return ClienteASGI(crear_app(FuenteDemo()))


def test_ajustes_revisar_resultados_activado_por_defecto_y_se_guarda(cliente: ClienteASGI) -> None:
    assert 'name="revisar_resultados" value="on" checked' in cliente.get("/ajustes").texto
    cliente.post("/ajustes", {"enviado": "1"}, cabeceras=ORIGEN)
    assert 'name="revisar_resultados" value="on" checked' not in cliente.get("/ajustes").texto
    cliente.post("/ajustes", {"enviado": "1", "revisar_resultados": "on"}, cabeceras=ORIGEN)
    assert 'name="revisar_resultados" value="on" checked' in cliente.get("/ajustes").texto
