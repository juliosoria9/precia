"""Selector del perfil que casa varias filas «Etiqueta: valor» (causa R5-A de la campaña real).

Caso real (ronda 5, sesión S03): el perfil de desguacesdli.com guardó para `estado` el selector
`div.woocommerce-product-details__short-description h3`, que casa las 9 filas
`<h3><strong>Etiqueta</strong>: valor</h3>` de la descripción corta (RefID, Pieza, Referencia
pieza, Vehículo, Año fabricación, Estado…). Se leía la primera, «RefID: 402519», y pasaba
todos los filtros. Ahora, si las filas llevan etiquetas distintas, se queda la que nombra el
campo y, si ninguna lo nombra, el selector es ambiguo y se descarta (`sin_forma`).
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.tipos import FichaExtraida
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import OrigenDato
from precia.perfiles.tipos import Perfil

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
DLI = (
    "https://desguacesdli.com/recambios/"
    "faro-izquierdo-mercedes-benz-clase-ml-bm-166-ml-350-bluetec-166-004-402519/"
)
FILAS = "div.woocommerce-product-details__short-description h3"
CAMPOS = ("precio", "referencia_oem", "marca", "estado")


def _pagina(html: str, url: str = "https://tienda.es/p/1") -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html",
        obtenida=datetime(2026, 10, 24, tzinfo=UTC),
    )


def _perfil(**selectores: str) -> Perfil:
    return Perfil(
        dominio="tienda.es",
        version=1,
        url_busqueda="https://tienda.es/buscar?q={consulta}",
        sel_resultado=".resultado",
        sel_titulo="a",
        sel_enlace="a @href",
        sel_precio_listado=None,
        sel_campos=selectores,
        necesita_js=False,
        autor="manual",
        revisado=True,
        creado=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _extraer(pagina: Pagina, **selectores: str) -> FichaExtraida:
    return asyncio.run(extraer_ficha(pagina, CAMPOS, perfil=_perfil(**selectores)))


def _dli(**selectores: str) -> FichaExtraida:
    html = (REALES / "desguacesdli_ficha_filas_etiqueta_valor.html").read_bytes().decode("utf-8")
    return _extraer(_pagina(html, DLI), **selectores)


def _sintetica(cuerpo: str, **selectores: str) -> FichaExtraida:
    html = f"<html><body><h1>Producto</h1><p>Precio: 10,00 €</p>{cuerpo}</body></html>"
    return _extraer(_pagina(html), **selectores)


def test_el_estado_de_dli_es_la_fila_estado_y_no_la_primera() -> None:
    ficha = _dli(estado=FILAS)
    # La fila es «Estado: Material de segunda mano»; se guarda con el vocabulario común.
    assert ficha.campos["estado"].valor == "Usado"
    assert ficha.campos["estado"].origen is OrigenDato.PERFIL
    assert "estado" not in ficha.sin_forma


def test_la_referencia_de_dli_es_la_fila_referencia_pieza() -> None:
    # «RefID» no nombra la referencia: se compara por palabra entera, no por prefijo «ref».
    ficha = _dli(referencia_oem=FILAS)
    assert ficha.campos["referencia_oem"].valor == "Referencia pieza: A1668207259"
    assert "referencia_oem" not in ficha.sin_forma


def test_si_ninguna_fila_nombra_el_campo_el_selector_es_ambiguo() -> None:
    ficha = _dli(marca=FILAS)
    assert "marca" not in ficha.campos
    assert ficha.sin_forma == {"marca": "RefID: 402519"}
    assert any("«marca»" in a and "ambiguo" in a for a in ficha.avisos), ficha.avisos


def test_refid_no_cuenta_como_etiqueta_de_referencia() -> None:
    ficha = _sintetica(
        "<ul><li>RefID: 402519</li><li>Pieza: FARO IZQUIERDO</li></ul>", referencia_oem="li"
    )
    assert "referencia_oem" not in ficha.campos
    assert "referencia_oem" in ficha.sin_forma


def test_filas_de_ropa_con_etiquetas_ajenas_al_estado_se_descartan() -> None:
    ficha = _sintetica("<ul><li>Composición: 100% algodón</li><li>Talla: M</li></ul>", estado="li")
    assert "estado" not in ficha.campos
    assert ficha.sin_forma == {"estado": "Composición: 100% algodón"}


def test_la_fila_condicion_cuenta_como_estado() -> None:
    ficha = _sintetica(
        "<dl><div class='f'>Color: Negro</div><div class='f'>Condición: Nuevo</div></dl>",
        estado="div.f",
    )
    # La fila es «Condición: Nuevo»; se guarda con el vocabulario común.
    assert ficha.campos["estado"].valor == "Nuevo"


def test_varios_nodos_sin_etiqueta_se_queda_el_primero_como_antes() -> None:
    ficha = _sintetica("<p class='e'>Usado</p><p class='e'>Nuevo</p>", estado=".e")
    assert ficha.campos["estado"].valor == "Usado"


def test_un_solo_nodo_con_etiqueta_se_lee_como_antes() -> None:
    ficha = _sintetica("<p class='m'>Marca: Valeo</p><p>Talla: M</p>", marca="p.m")
    assert ficha.campos["marca"].valor == "Marca: Valeo"


def test_una_sola_fila_con_etiqueta_entre_nodos_sin_ella_se_queda_la_primera() -> None:
    ficha = _sintetica("<p class='e'>Usado</p><p class='e'>Envío: 24 h</p>", estado="p.e")
    assert ficha.campos["estado"].valor == "Usado"
