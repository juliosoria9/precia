"""Marca del JSON-LD o de los metadatos que no se ve en la página (causa R5-H de la campaña real).

Caso real (ronda 5, sesión S03): en la ficha de Carrefour del gel Greenatural, el JSON-LD declara
`"brand":{"@type":"Brand","name":"PLANETA HUERTO B2C"}`, que es el vendedor del marketplace y
solo está dentro de los `<script>`; el título visible dice «Greenatural» y el vendedor visible,
«PlanetaHuerto». Esa marca se daba por anclada con confianza 1. Ahora, si la marca declarada no
se ve en el texto, se avisa y la cascada sigue (metadatos, perfil, IA) por si hay una marca que
sí se ve; si no la hay, se conserva la declarada, con el aviso y menos confianza.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from precia.extraccion.ficha import CONFIANZA, extraer_ficha
from precia.extraccion.tipos import ExtractorIA, FichaExtraida
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import OrigenDato, ValorCampo
from precia.perfiles.tipos import Perfil

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
CARREFOUR = (
    "https://carrefour.es/gel-de-bano-delicado-aloe-vera-y-aceite-de-oliva-greenatural-1-l/"
    "8052400932582/p"
)
MARCA_JSONLD = "PLANETA HUERTO B2C"


class ExtractorFalso:
    """Extractor de IA guionizado que registra los campos que se le piden."""

    def __init__(self, respuesta: dict[str, str]) -> None:
        self.respuesta = respuesta
        self.llamadas: list[tuple[str, ...]] = []

    async def extraer(self, texto_pagina: str, campos: tuple[str, ...]) -> dict[str, str]:
        self.llamadas.append(campos)
        return self.respuesta


def _pagina(html: str, url: str = "https://tienda.es/p/1") -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html",
        obtenida=datetime(2026, 10, 24, tzinfo=UTC),
    )


def _carrefour(extra: str = "") -> Pagina:
    html = (REALES / "carrefour_ficha_marca_jsonld_del_vendedor.html").read_text(encoding="utf-8")
    return _pagina(html.replace("</body>", extra + "</body>"), CARREFOUR)


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


def _extraer(
    pagina: Pagina,
    campos: tuple[str, ...] = ("marca",),
    *,
    perfil: Perfil | None = None,
    ia: ExtractorIA | None = None,
) -> FichaExtraida:
    return asyncio.run(extraer_ficha(pagina, campos, perfil=perfil, extractor_ia=ia))


def _aviso_no_se_ve(ficha: FichaExtraida) -> bool:
    return any("marca" in a and MARCA_JSONLD in a and "no se ve" in a for a in ficha.avisos)


def test_carrefour_sin_alternativa_conserva_la_marca_del_jsonld_con_aviso() -> None:
    ficha = _extraer(_carrefour())
    marca = ficha.campos["marca"]
    assert (marca.valor, marca.origen, marca.anclado) == (MARCA_JSONLD, OrigenDato.JSONLD, True)
    assert marca.confianza < CONFIANZA[OrigenDato.PERFIL]
    assert _aviso_no_se_ve(ficha)
    assert ficha.precio is not None and str(ficha.precio.importe) == "19.33"


def test_carrefour_la_marca_visible_de_la_ia_gana_a_la_del_jsonld() -> None:
    ia = ExtractorFalso({"marca": "Greenatural"})
    ficha = _extraer(_carrefour(), ia=ia)
    assert ia.llamadas == [("marca",)]
    assert ficha.campos["marca"] == ValorCampo("Greenatural", OrigenDato.IA, confianza=0.7)
    assert _aviso_no_se_ve(ficha)


def test_carrefour_la_marca_visible_del_perfil_gana_a_la_del_jsonld() -> None:
    pagina = _carrefour('<span class="marca">Greenatural</span>')
    ia = ExtractorFalso({"marca": "Otra"})
    ficha = _extraer(pagina, perfil=_perfil(marca="span.marca"), ia=ia)
    assert ficha.campos["marca"] == ValorCampo("Greenatural", OrigenDato.PERFIL, confianza=0.9)
    assert ia.llamadas == []


def test_carrefour_una_marca_de_la_ia_que_no_se_ve_no_sustituye_a_la_del_jsonld() -> None:
    ia = ExtractorFalso({"marca": "Marca Inventada"})
    ficha = _extraer(_carrefour(), ia=ia)
    marca = ficha.campos["marca"]
    assert (marca.valor, marca.origen, marca.anclado) == (MARCA_JSONLD, OrigenDato.JSONLD, True)
    assert marca.confianza < CONFIANZA[OrigenDato.PERFIL]
    assert any("IA" in a and "marca" in a for a in ficha.avisos)


def test_marca_del_jsonld_visible_sigue_igual() -> None:
    html = '<script type="application/ld+json">{"@type": "Product", "name": "Faro",'
    html += ' "brand": "Valeo"}</script><p>Marca: Valeo</p><span class="m">Hella</span>'
    ia = ExtractorFalso({"marca": "Hella"})
    ficha = _extraer(_pagina(html), perfil=_perfil(marca="span.m"), ia=ia)
    assert ficha.campos["marca"] == ValorCampo("Valeo", OrigenDato.JSONLD)
    assert ficha.avisos == ()
    assert ia.llamadas == []


def test_marca_de_los_metadatos_visible_gana_a_la_del_jsonld_que_no_se_ve() -> None:
    html = '<script type="application/ld+json">{"@type": "Product", "name": "Faro",'
    html += ' "brand": "Vendedor SL"}</script>'
    html += '<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Faro</h1>'
    html += '<span itemprop="brand">Valeo</span></div>'
    ficha = _extraer(_pagina(html))
    assert ficha.campos["marca"] == ValorCampo("Valeo", OrigenDato.METADATOS, confianza=0.95)


def test_marca_de_los_metadatos_que_no_se_ve_cede_ante_el_perfil() -> None:
    html = '<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Faro</h1>'
    html += '<meta itemprop="brand" content="Vendedor SL"></div><b class="m">Valeo</b>'
    ficha = _extraer(_pagina(html), perfil=_perfil(marca="b.m"))
    assert ficha.campos["marca"] == ValorCampo("Valeo", OrigenDato.PERFIL, confianza=0.9)
    assert any("metadatos" in a and "Vendedor SL" in a for a in ficha.avisos)


def test_marca_de_los_metadatos_que_no_se_ve_y_sin_alternativa_se_conserva() -> None:
    html = '<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Faro</h1>'
    html += '<meta itemprop="brand" content="Vendedor SL"></div>'
    marca = _extraer(_pagina(html)).campos["marca"]
    assert (marca.valor, marca.origen, marca.anclado) == ("Vendedor SL", OrigenDato.METADATOS, True)
    assert marca.confianza < CONFIANZA[OrigenDato.PERFIL]


def test_los_demas_campos_del_jsonld_no_se_comprueban() -> None:
    producto = (
        '{"@type": "Product", "name": "Faro", "mpn": "1EL009", "description": "Faro LED",'
        ' "image": ["/a.jpg"], "itemCondition": "https://schema.org/UsedCondition"}'
    )
    html = f'<script type="application/ld+json">{producto}</script><p>Nada visible</p>'
    campos = ("referencia_oem", "descripcion", "fotos", "estado")
    ficha = _extraer(_pagina(html), campos)
    assert set(ficha.campos) == set(campos)
    assert all(v.origen is OrigenDato.JSONLD and v.confianza == 1.0 for v in ficha.campos.values())
    assert ficha.avisos == ()
