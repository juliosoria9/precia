"""Cascada de extracción de una ficha: JSON-LD → metadatos → perfil → IA, con anclaje."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.extraccion.ficha import CONFIANZA, extraer_ficha, formatear_precio
from precia.extraccion.tipos import ExtractorIA, FichaExtraida, Precio
from precia.ia.tipos import IANoDisponible, PresupuestoAgotado
from precia.navegador.tipos import Pagina
from precia.nucleo.modelos import OrigenDato, ValorCampo
from precia.perfiles.tipos import Perfil

FIXTURES = Path(__file__).parent.parent / "fixtures" / "extraccion"
TODOS = ("precio", "referencia_oem", "marca", "estado", "descripcion", "fotos")
POR_DEFECTO = ("precio", "referencia_oem", "marca", "estado")


class ExtractorFalso:
    """Extractor de IA guionizado que registra lo que se le pide."""

    def __init__(self, respuesta: object = None, error: Exception | None = None) -> None:
        self.respuesta = respuesta
        self.error = error
        self.llamadas: list[tuple[str, tuple[str, ...]]] = []

    async def extraer(self, texto_pagina: str, campos: tuple[str, ...]) -> dict[str, str]:
        self.llamadas.append((texto_pagina, campos))
        if self.error is not None:
            raise self.error
        return self.respuesta  # type: ignore[return-value]


def _pagina(html: str, url: str = "https://tienda.es/p/1") -> Pagina:
    return Pagina(
        url=url,
        url_final=url,
        estado=200,
        html=html,
        tipo_contenido="text/html",
        obtenida=datetime(2026, 10, 19, tzinfo=UTC),
    )


def _fixture(nombre: str) -> Pagina:
    return _pagina((FIXTURES / nombre).read_text(encoding="utf-8"))


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
    campos: tuple[str, ...] = POR_DEFECTO,
    *,
    perfil: Perfil | None = None,
    extractor_ia: ExtractorIA | None = None,
) -> FichaExtraida:
    return asyncio.run(extraer_ficha(pagina, campos, perfil=perfil, extractor_ia=extractor_ia))


def _jsonld(producto: dict[str, object]) -> str:
    return f'<script type="application/ld+json">{json.dumps(producto)}</script>'


def test_ficha_de_desguace_solo_con_jsonld() -> None:
    ia = ExtractorFalso({})
    ficha = _extraer(_fixture("ficha_desguace.html"), TODOS, extractor_ia=ia)
    assert ficha.titulo == "Faro delantero izquierdo Volkswagen Golf VI (5K1) 2008-2012"
    assert ficha.precio == Precio(Decimal("89.90"), "EUR", "89.90")
    jsonld = OrigenDato.JSONLD
    assert ficha.campos == {
        "precio": ValorCampo("89,90 €", jsonld),
        "referencia_oem": ValorCampo("5K1941005E", jsonld),
        "marca": ValorCampo("VALEO", jsonld),
        "estado": ValorCampo("Usado", jsonld),
        "descripcion": ValorCampo(
            "Faro delantero izquierdo de Volkswagen Golf VI. Pieza usada, probada y en buen "
            "estado. Incluye motor de regulación.",
            jsonld,
        ),
        "fotos": ValorCampo("3 fotos", jsonld),
    }
    assert ficha.avisos == ()
    assert ficha.url == "https://tienda.es/p/1"
    assert ia.llamadas == []


def test_woocommerce_con_ia_para_lo_que_falta() -> None:
    ia = ExtractorFalso({"referencia_oem": "0 986 494 527", "marca": "Otra", "color": "rojo"})
    ficha = _extraer(_fixture("ficha_woocommerce_graph.html"), extractor_ia=ia)
    assert ficha.precio == Precio(Decimal("32.45"), "EUR", "32.45")
    assert ficha.campos["marca"] == ValorCampo("Bosch", OrigenDato.JSONLD)
    assert ficha.campos["estado"] == ValorCampo("Nuevo", OrigenDato.JSONLD)
    assert ficha.campos["referencia_oem"] == ValorCampo(
        "0 986 494 527", OrigenDato.IA, anclado=True, confianza=0.7
    )
    [(texto, campos)] = ia.llamadas
    assert campos == ("referencia_oem",)
    assert "Pastillas de freno delanteras Bosch" in texto
    assert "<" not in texto


def test_perfil_en_una_tienda_sin_jsonld() -> None:
    perfil = _perfil(
        precio="td.precio",
        referencia_oem="td.ref",
        marca="td.marca",
        estado="td.estado",
        descripcion="p.desc",
        fotos=".fotos img.mini @src",
    )
    ficha = _extraer(_fixture("ficha_sin_jsonld.html"), TODOS, perfil=perfil)
    assert ficha.titulo == "Alternador Renault Megane II 1.5 dCi"
    assert ficha.precio == Precio(Decimal("145.00"), "EUR", "145,00 €")
    perfil_ = OrigenDato.PERFIL
    assert ficha.campos == {
        "precio": ValorCampo("145,00 €", perfil_, confianza=0.9),
        "referencia_oem": ValorCampo("8200 660 034", perfil_, confianza=0.9),
        "marca": ValorCampo("Valeo", perfil_, confianza=0.9),
        "estado": ValorCampo("Usado", perfil_, confianza=0.9),
        "descripcion": ValorCampo(
            "Alternador de 110 A desmontado de vehículo con 98.000 km. Probado en banco. "
            "Garantía de 6 meses. Envío en 24-48 horas.",
            perfil_,
            confianza=0.9,
        ),
        "fotos": ValorCampo("2 fotos", perfil_, confianza=0.9),
    }
    assert ficha.avisos == ()


def test_precio_del_perfil_oculto_no_se_toma_y_pasa_a_la_ia() -> None:
    perfil = _perfil(precio="span.precio-viejo")
    ia = ExtractorFalso({"precio": "145,00 € (IVA incluido)"})
    ficha = _extraer(_fixture("ficha_sin_jsonld.html"), ("precio",), perfil=perfil, extractor_ia=ia)
    assert ficha.precio == Precio(Decimal("145.00"), "EUR", "145,00 €")
    assert ficha.campos["precio"] == ValorCampo("145,00 €", OrigenDato.IA, confianza=0.7)
    assert any("perfil" in aviso and "precio" in aviso for aviso in ficha.avisos)


def test_prompt_injection_el_precio_de_la_ia_no_anclado() -> None:
    ia = ExtractorFalso(
        {"precio": "1,00 €", "marca": "Bosch", "estado": "Nuevo", "referencia_oem": "038253014G"}
    )
    ficha = _extraer(_fixture("ficha_inyeccion.html"), extractor_ia=ia)
    assert ficha.precio is None
    ia_ = OrigenDato.IA
    assert ficha.campos["precio"] == ValorCampo("1,00 €", ia_, anclado=False, confianza=0.0)
    # El texto oculto con `display:none` no cuenta para anclar.
    assert ficha.campos["estado"] == ValorCampo("Nuevo", ia_, anclado=False, confianza=0.0)
    assert ficha.campos["referencia_oem"] == ValorCampo("038253014G", ia_, confianza=0.7)
    # Límite conocido: texto visible pero camuflado con CSS (blanco sobre blanco) sí ancla.
    # El anclaje demuestra que el valor está en la página, no que sea verdad.
    assert ficha.campos["marca"].anclado
    assert any("precio" in aviso and "no aparece" in aviso for aviso in ficha.avisos)


def test_el_precio_de_la_ia_es_el_de_la_pagina() -> None:
    ia = ExtractorFalso({"precio": "349 €"})
    ficha = _extraer(_fixture("ficha_inyeccion.html"), ("precio",), extractor_ia=ia)
    assert ficha.precio == Precio(Decimal("349"), "EUR", "349 €")
    assert ficha.campos["precio"] == ValorCampo("349,00 €", OrigenDato.IA, confianza=0.7)


@pytest.mark.parametrize(
    "respuesta",
    [
        None,
        "precio: 1 €",
        ["1 €"],
        {"precio": 1},
        {"precio": None},
        {"precio": "   "},
        {"precio": "gratis"},
    ],
)
def test_respuestas_raras_de_la_ia(respuesta: object) -> None:
    ia = ExtractorFalso(respuesta)
    ficha = _extraer(_pagina("<h1>Pieza</h1><p>12,00 €</p>"), ("precio",), extractor_ia=ia)
    assert ficha.precio is None
    assert "precio" not in ficha.campos


def test_valores_de_la_ia_se_normalizan_y_recortan() -> None:
    largo = "Bosch " + "x" * 10_000
    ia = ExtractorFalso({"marca": "  Bosch\n\t ", "descripcion": largo})
    ficha = _extraer(_pagina("<p>Marca Bosch</p>"), ("marca", "descripcion"), extractor_ia=ia)
    assert ficha.campos["marca"] == ValorCampo("Bosch", OrigenDato.IA, confianza=0.7)
    assert len(ficha.campos["descripcion"].valor) <= 5_000
    assert not ficha.campos["descripcion"].anclado


@pytest.mark.parametrize(
    "error", [IANoDisponible("Sin clave de API."), PresupuestoAgotado("Tope.")]
)
def test_error_de_la_ia_queda_en_avisos(error: Exception) -> None:
    ia = ExtractorFalso(error=error)
    ficha = _extraer(_pagina("<h1>Pieza</h1>"), ("marca",), extractor_ia=ia)
    assert ficha.campos == {}
    assert any(str(error) in aviso for aviso in ficha.avisos)


def test_el_texto_para_la_ia_esta_limitado() -> None:
    ia = ExtractorFalso({})
    _extraer(_pagina("<p>" + "palabra " * 50_000 + "</p>"), ("marca",), extractor_ia=ia)
    [(texto, _)] = ia.llamadas
    assert len(texto) <= 60_000


def test_precio_jsonld_que_no_se_ve_en_la_pagina() -> None:
    html = (
        _jsonld({"@type": "Product", "name": "Faro", "offers": {"price": "50.00"}}) + "<p>45 €</p>"
    )
    ficha = _extraer(_pagina(html), ("precio",))
    # Viene del JSON-LD de la propia página, así que vale, pero se avisa de la discrepancia.
    assert ficha.precio == Precio(Decimal("50.00"), "EUR", "50.00")
    assert ficha.campos["precio"] == ValorCampo("50,00 €", OrigenDato.JSONLD)
    assert any("JSON-LD" in aviso for aviso in ficha.avisos)


def test_producto_principal_entre_varios() -> None:
    html = (
        _jsonld({"@type": "Product", "name": "Relacionado sin precio"})
        + _jsonld({"@type": "Product", "name": "Otro", "offers": {"price": 9}, "url": "/otro"})
        + _jsonld({"@type": "Product", "name": "Este", "offers": {"price": 20}, "url": "/p/1"})
    )
    ficha = _extraer(_pagina(html + "<p>20 € 9 €</p>"), ("precio",))
    assert ficha.titulo == "Este"
    assert ficha.precio is not None and ficha.precio.importe == 20


def test_precio_siempre_se_calcula_aunque_no_se_pida() -> None:
    html = _jsonld({"@type": "Product", "name": "Faro", "offers": {"price": 10}}) + "<p>10 €</p>"
    ficha = _extraer(_pagina(html), ("marca",))
    assert ficha.precio == Precio(Decimal("10"), "EUR", "10")
    assert ficha.campos == {}


def test_campos_desconocidos_y_repetidos() -> None:
    ia = ExtractorFalso({"marca": "Bosch"})
    ficha = _extraer(_pagina("<p>Bosch</p>"), ("color", "marca", "marca"), extractor_ia=ia)
    assert list(ficha.campos) == ["marca"]
    assert ia.llamadas[0][1] == ("marca",)
    assert any("color" in aviso for aviso in ficha.avisos)


def test_selector_del_perfil_no_valido() -> None:
    perfil = _perfil(marca="td:has(b)", estado="td.estado")
    ficha = _extraer(_fixture("ficha_sin_jsonld.html"), ("marca", "estado"), perfil=perfil)
    assert list(ficha.campos) == ["estado"]
    assert any("marca" in aviso for aviso in ficha.avisos)


def test_jsonld_tiene_prioridad_sobre_el_perfil() -> None:
    html = _jsonld({"@type": "Product", "name": "Faro", "brand": "Valeo"})
    html += '<span class="marca">Hella</span>'
    ficha = _extraer(_pagina(html), ("marca",), perfil=_perfil(marca=".marca"))
    assert ficha.campos["marca"] == ValorCampo("Valeo", OrigenDato.JSONLD)


def test_titulo_de_respaldo() -> None:
    # « | Tienda» es el nombre del sitio (el dominio es tienda.es): se quita.
    assert _extraer(_pagina("<title> Faro | Tienda </title>"), ()).titulo == "Faro"
    html = '<meta property="og:title" content="Faro OG"><title>T</title>'
    assert _extraer(_pagina(html), ()).titulo == "Faro OG"
    assert _extraer(_pagina("<h1>Faro H1</h1><title>T</title>"), ()).titulo == "Faro H1"
    assert _extraer(_pagina(""), ()).titulo == ""
    ficha = _extraer(_pagina("<h1>H1</h1><b class=t>Perfil</b>"), (), perfil=_perfil(titulo="b.t"))
    assert ficha.titulo == "Perfil"


def test_fotos_desde_el_perfil_sin_atributo_y_vacias() -> None:
    html = '<div class="g"><img src="/1.jpg"><img src="/2.jpg"><img src="/3.jpg"></div>'
    ficha = _extraer(_pagina(html), ("fotos",), perfil=_perfil(fotos=".g img"))
    assert ficha.campos["fotos"].valor == "3 fotos"
    ficha = _extraer(
        _pagina('<div class="g"><img src="/1.jpg"></div>'),
        ("fotos",),
        perfil=_perfil(fotos=".g img @src"),
    )
    assert ficha.campos["fotos"].valor == "1 foto"
    ficha = _extraer(
        _pagina('<div class="g"><img></div>'), ("fotos",), perfil=_perfil(fotos=".g img @src")
    )
    assert "fotos" not in ficha.campos


def test_sin_perfil_ni_ia_ni_jsonld() -> None:
    ficha = _extraer(_pagina("<h1>Pieza</h1><p>12,00 €</p>"), TODOS)
    assert ficha.precio is None
    assert ficha.campos == {}


def test_pagina_con_url_final_distinta() -> None:
    pagina = replace(_pagina("<h1>X</h1>"), url_final="https://tienda.es/p/1-redirigida")
    assert _extraer(pagina, ()).url == "https://tienda.es/p/1-redirigida"


def test_url_malformada_en_el_jsonld_no_rompe_la_ficha() -> None:
    # Revisión: «http://[abc» hacía que `urljoin` lanzase ValueError y la ficha entera fallaba.
    producto: dict[str, object] = {"@type": "Product", "url": "http://[abc"}
    producto["offers"] = {"price": 10}
    ficha = _extraer(_pagina(_jsonld(producto) + "<p>10 €</p>"), ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == 10


@pytest.mark.parametrize("contenido", ["89.9", "89.90", "89"])
def test_precio_del_perfil_en_atributo_con_formato_de_maquina(contenido: str) -> None:
    # Revisión: PrestaShop publica `<span itemprop="price" content="89.9">89,90 €</span>`; el
    # atributo va en formato de máquina y `leer_precio` (conservador) no lo aceptaba.
    importe = Decimal(contenido)
    html = (
        f'<h1>Bomba</h1><span itemprop="price" content="{contenido}">'
        f"{formatear_precio(Precio(importe))}</span>"
    )
    perfil = _perfil(precio="[itemprop=price] @content")
    ficha = _extraer(_pagina(html), ("precio",), perfil=perfil)
    assert ficha.precio is not None and ficha.precio.importe == importe
    assert ficha.campos["precio"].origen == OrigenDato.PERFIL
    # Sigue anclándose: un atributo con un importe que no se ve en la página no vale.
    html = '<h1>Bomba</h1><span itemprop="price" content="1.00">89,90 €</span>'
    ficha = _extraer(_pagina(html), ("precio",), perfil=perfil)
    assert ficha.precio is None


def test_descripcion_larga_del_perfil_se_recorta_sin_partir_palabras() -> None:
    # El anclaje exige palabras completas: un recorte a mitad de palabra dejaría la descripción
    # sin anclar aunque esté entera en la página.
    texto = " ".join(f"palabra{i}" for i in range(1_000))
    html = f'<h1>Faro</h1><div class="desc">{texto}</div>'
    ficha = _extraer(_pagina(html), ("descripcion",), perfil=_perfil(descripcion=".desc"))
    valor = ficha.campos["descripcion"].valor
    assert len(valor) <= 5_000
    assert texto.startswith(valor + " ")


# --- Metadatos (OpenGraph y microdatos) ---------------------------------------------------------


def _microdatos(cuerpo: str) -> str:
    return f'<div itemscope itemtype="https://schema.org/Product">{cuerpo}</div>'


def test_confianza_de_los_metadatos() -> None:
    assert CONFIANZA[OrigenDato.METADATOS] == 0.95
    assert CONFIANZA[OrigenDato.JSONLD] > CONFIANZA[OrigenDato.METADATOS]
    assert CONFIANZA[OrigenDato.METADATOS] > CONFIANZA[OrigenDato.PERFIL]


def test_prestashop_sin_jsonld_sale_de_los_microdatos() -> None:
    ia = ExtractorFalso({})
    ficha = _extraer(_fixture("ficha_prestashop_microdatos.html"), TODOS, extractor_ia=ia)
    assert ficha.titulo == "Bomba de agua SEAT Ibiza 1.4 TDI"
    assert ficha.precio == Precio(Decimal("45.9"), "EUR", "45.9")
    metadatos = OrigenDato.METADATOS
    assert ficha.campos == {
        "precio": ValorCampo("45,90 €", metadatos, confianza=0.95),
        "referencia_oem": ValorCampo("045121011H", metadatos, confianza=0.95),
        "marca": ValorCampo("SKF", metadatos, confianza=0.95),
        "estado": ValorCampo("Nuevo", metadatos, confianza=0.95),
        "descripcion": ValorCampo(
            "Bomba de agua con junta para motores 1.4 TDI (BNM, BNV).", metadatos, confianza=0.95
        ),
        "fotos": ValorCampo("3 fotos", metadatos, confianza=0.95),
    }
    assert ficha.avisos == ()
    assert ia.llamadas == []


def test_shopify_con_og_price_y_la_ia_para_lo_que_falta() -> None:
    ia = ExtractorFalso({"marca": "MANN-FILTER"})
    ficha = _extraer(_fixture("ficha_shopify_og.html"), TODOS, extractor_ia=ia)
    assert ficha.titulo == "Filtro de aceite MANN-FILTER W 712/75"
    assert ficha.precio == Precio(Decimal("12.90"), "EUR", "12,90")
    assert ficha.campos["precio"] == ValorCampo("12,90 €", OrigenDato.METADATOS, confianza=0.95)
    assert ficha.campos["fotos"] == ValorCampo("2 fotos", OrigenDato.METADATOS, confianza=0.95)
    assert ficha.campos["marca"] == ValorCampo("MANN-FILTER", OrigenDato.IA, confianza=0.7)
    [(_, campos)] = ia.llamadas
    assert campos == ("referencia_oem", "marca", "estado")
    assert ficha.avisos == ()


def test_orden_de_la_cascada_jsonld_metadatos_perfil() -> None:
    perfil = _perfil(marca=".marca", estado=".estado", referencia_oem=".ref")
    html = _jsonld({"@type": "Product", "name": "Faro", "brand": "Valeo"})
    html += _microdatos(
        '<h1 itemprop="name">Faro</h1><meta itemprop="brand" content="Hella">'
        '<meta itemprop="mpn" content="1EL009">'
    )
    html += '<p class="marca">Bosch</p><p class="ref">999</p><p class="estado">Usado</p>'
    ficha = _extraer(_pagina(html), ("marca", "referencia_oem", "estado"), perfil=perfil)
    assert ficha.campos == {
        "marca": ValorCampo("Valeo", OrigenDato.JSONLD),
        "referencia_oem": ValorCampo("1EL009", OrigenDato.METADATOS, confianza=0.95),
        "estado": ValorCampo("Usado", OrigenDato.PERFIL, confianza=0.9),
    }


def test_precio_de_los_metadatos_que_no_se_ve() -> None:
    html = '<meta property="og:type" content="product">'
    html += '<meta property="product:price:amount" content="50.00"><p>45 €</p>'
    ficha = _extraer(_pagina(html), ("precio",))
    # Viene del marcado de la propia página: vale, pero se avisa de la discrepancia.
    assert ficha.precio == Precio(Decimal("50.00"), "EUR", "50.00")
    assert ficha.campos["precio"] == ValorCampo("50,00 €", OrigenDato.METADATOS, confianza=0.95)
    assert any("metadatos" in aviso and "no se ve" in aviso for aviso in ficha.avisos)


def test_precio_absurdo_en_los_metadatos_pasa_al_perfil() -> None:
    html = _microdatos('<span itemprop="name">Faro</span><meta itemprop="price" content="-1">')
    html += '<b class="precio">45,90 €</b>'
    ficha = _extraer(_pagina(html), ("precio",), perfil=_perfil(precio=".precio"))
    assert ficha.campos["precio"] == ValorCampo("45,90 €", OrigenDato.PERFIL, confianza=0.9)


def test_titulo_desde_los_microdatos_antes_que_el_h1() -> None:
    html = _microdatos('<span itemprop="name">Faro Golf</span>') + "<title>Tienda</title>"
    assert _extraer(_pagina(html), ()).titulo == "Faro Golf"
    # El `<h1>` puede ser el del sitio (webscraper.io pone «Test Sites» en todas las páginas);
    # el nombre de los microdatos es el que la tienda declara para el producto.
    html = "<h1>Novedades</h1>" + html
    assert _extraer(_pagina(html), ()).titulo == "Faro Golf"


def test_los_microdatos_de_los_relacionados_no_dan_el_precio_de_la_ficha() -> None:
    ficha = _extraer(
        _fixture("ficha_relacionados_microdatos.html"),
        ("precio", "marca", "estado"),
        perfil=_perfil(precio=".current-price-value"),
    )
    assert ficha.titulo == "Faro delantero izquierdo Golf VI"
    assert ficha.precio == Precio(Decimal("120.00"), "EUR", "120,00 €")
    # Ni la marca («Osram», «Valeo») ni el estado de las miniaturas son los de la ficha.
    assert ficha.campos == {"precio": ValorCampo("120,00 €", OrigenDato.PERFIL, confianza=0.9)}


def test_sin_canonical_la_url_de_la_pagina_descarta_los_relacionados() -> None:
    relacionado = _microdatos(
        '<meta itemprop="url" content="/p/bombilla-h7"><span itemprop="name">Bombilla H7</span>'
        '<meta itemprop="brand" content="Osram">'
        '<div itemprop="offers" itemscope itemtype="https://schema.org/Offer">'
        '<span itemprop="price" content="4.90">4,90 €</span></div>'
    )
    html = f'<h1>Faro</h1><b class="precio">120,00 €</b><section>{relacionado}</section>'
    pagina = _pagina(html, "https://tienda.es/p/faro")
    ficha = _extraer(pagina, ("precio", "marca"), perfil=_perfil(precio=".precio"))
    assert ficha.titulo == "Faro"
    assert ficha.campos == {"precio": ValorCampo("120,00 €", OrigenDato.PERFIL, confianza=0.9)}
