"""Caso de referencia de B5: Oscaro por 1K0698151 con las fichas guardadas (sin red).

En las 5 fichas, la OEM buscada no está en el texto visible: solo en la pestaña oculta
«Referencias del fabricante (OEM)». Antes de B5 se descartaban todas.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from precia.emparejado.agrupador import CRITERIO_OEM_AMPLIADO, agrupar
from precia.emparejado.referencias import referencias_en
from precia.emparejado.tipos import OfertaCandidata
from precia.extraccion.ficha import extraer_ficha
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import Coincidencia, ConsultaBusqueda, Descartado, GrupoProducto, Modo

FIXTURES = Path(__file__).parent.parent / "fixtures" / "extraccion"
CAMPOS = ("precio", "referencia_oem", "marca")
URL = "https://www.oscaro.es/ficha-{}"


def _candidatas(referencias: tuple[str, ...]) -> list[OfertaCandidata]:
    candidatas = []
    for i, ruta in enumerate(sorted(FIXTURES.glob("oscaro_ficha_*.html"))):
        url = URL.format(i)
        pagina = Pagina(
            url, url, 200, ruta.read_text(encoding="utf-8"), "text/html", datetime.now(UTC)
        )
        ficha = asyncio.run(extraer_ficha(pagina, CAMPOS, referencias=referencias))
        candidatas.append(OfertaCandidata("oscaro.es", ficha, ficha.url))
    return candidatas


def _buscar(texto: str, *, citas: bool = True) -> tuple[list[GrupoProducto], list[Descartado]]:
    referencias = tuple(sorted(referencias_en(texto))) if citas else ()
    consulta = ConsultaBusqueda(texto, modo=Modo.CLASICO)
    return asyncio.run(agrupar(consulta, _candidatas(referencias)))


def test_oscaro_1K0698151_pasa_de_0_a_5_ofertas() -> None:
    grupos, descartados = _buscar("1K0698151")
    assert descartados == []
    (grupo,) = grupos
    assert grupo.id == "oem-1k0698151"
    assert grupo.criterio == CRITERIO_OEM_AMPLIADO
    assert {o.coincidencia for o in grupo.ofertas} == {Coincidencia.EQUIVALENTE}
    assert [o.precio for o in grupo.ofertas] == [
        Decimal(p) for p in ("28.28", "29.86", "31.52", "36.39", "56.74")
    ]


def test_oscaro_sin_citas_reproduce_el_fallo() -> None:
    # La medida del antes: sin mirar la pestaña de referencias, las 5 se descartan.
    grupos, descartados = _buscar("1K0698151", citas=False)
    assert grupos == []
    assert [d.motivo for d in descartados] == ["Referencia OEM distinta"] * 5


def test_oscaro_control_otra_referencia() -> None:
    grupos, descartados = _buscar("1K0941005")
    assert grupos == []
    assert len(descartados) == 5
