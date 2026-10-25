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
    # La marca del JSON-LD se ve en la página; si no, la cascada sigue (R5-H).
    html = _jsonld({"@type": "Product", "name": "Faro", "brand": "Valeo"})
    html += '<p>Faro Valeo</p><span class="marca">Hella</span>'
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
    html += "<p>Faro Valeo</p>"  # la marca del JSON-LD se ve (R5-H)
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


@pytest.mark.parametrize("selector", ["p.price", ".price"])
def test_precio_del_perfil_en_oferta_es_el_vigente(selector: str) -> None:
    # Sin JSON-LD ni microdatos, el precio sale del perfil: en una oferta de WooCommerce el
    # `<del>` va antes que el `<ins>` y se leía el precio antiguo.
    html = (
        "<h1>Faro derecho Golf</h1>"
        '<p class="price"><del><bdi>59,90&nbsp;€</bdi></del> <ins><bdi>39,90&nbsp;€</bdi></ins></p>'
        '<section class="related"><span class="price">12,00 €</span></section>'
    )
    ficha = _extraer(_pagina(html), ("precio",), perfil=_perfil(precio=selector))
    assert ficha.precio is not None
    assert ficha.precio.importe == Decimal("39.90")
    assert ficha.campos["precio"] == ValorCampo(
        "39,90 €", OrigenDato.PERFIL, True, CONFIANZA[OrigenDato.PERFIL]
    )
    assert ficha.avisos == ()


# --- Referencias citadas en la ficha (B5) -----------------------------------------------------

OSCARO_VALEO = "oscaro_ficha_valeo_301635.html"
CAMPOS_OSCARO = ("precio", "referencia_oem", "marca")


def test_ficha_de_oscaro_cita_la_oem_en_la_pestana_de_referencias() -> None:
    # La OEM no está en el texto visible: solo en la pestaña oculta «Referencias del fabricante».
    pagina = _fixture(OSCARO_VALEO)
    sin = asyncio.run(extraer_ficha(pagina, CAMPOS_OSCARO))
    con = asyncio.run(extraer_ficha(pagina, CAMPOS_OSCARO, referencias=("1K0698151", "1K0941005")))
    assert con.referencias_citadas == frozenset({"1K0698151"})
    assert con.precio is not None
    assert con.precio.importe == Decimal("31.52")
    assert con.campos["referencia_oem"].valor == "301635"
    assert con.campos["marca"].valor == "VALEO"
    # La cita no cambia nada más: los campos y el precio se anclan igual que sin referencias.
    assert (con.titulo, con.precio, con.campos, con.avisos) == (
        sin.titulo,
        sin.precio,
        sin.campos,
        sin.avisos,
    )


def test_sin_referencias_no_se_buscan_citas() -> None:
    ficha = asyncio.run(extraer_ficha(_fixture(OSCARO_VALEO), CAMPOS_OSCARO, referencias=()))
    assert ficha.referencias_citadas == frozenset()


@pytest.mark.parametrize(
    ("panel", "citada"),
    [
        ("<li>1K0698151E</li>", False),  # Otra referencia (una revisión): palabra completa.
        ("<li>1K0 698 151</li>", True),  # Por grupos: la comparación compacta del anclaje.
        ("<li>VW 1K0698151</li>", True),
        # Revisión: con la misma normalización que `referencias_en` (guiones, puntos y sufijo
        # de revisión por grupos). Antes, con guiones no era cita y por grupos con «E», sí.
        ("<li>VW 1K0-698-151</li>", True),
        ("<li>VW 1K0.698.151, 5K0698151</li>", True),
        ("<li>VW 1K0 698 151 E</li>", False),
        ("<li>VW 1K0-698-151-E</li>", False),
    ],
)
def test_la_cita_es_palabra_completa(panel: str, citada: bool) -> None:
    html = f'<h1>Pastillas</h1><p>20,00 €</p><div role="tabpanel" hidden><ul>{panel}</ul></div>'
    ficha = asyncio.run(extraer_ficha(_pagina(html), ("precio",), referencias=("1K0698151",)))
    assert ficha.referencias_citadas == (frozenset({"1K0698151"}) if citada else frozenset())


def test_cita_en_un_script_no_cuenta() -> None:
    html = (
        "<h1>Pastillas</h1><p>20,00 €</p>"
        '<script type="application/json">{"oem": ["1K0698151"]}</script>'
    )
    ficha = asyncio.run(extraer_ficha(_pagina(html), ("precio",), referencias=("1K0698151",)))
    assert ficha.referencias_citadas == frozenset()


REALES = Path(__file__).parent.parent / "fixtures" / "reales"


def test_entre_los_productos_de_la_url_de_la_pagina_gana_el_que_tiene_precio() -> None:
    html = _jsonld({"@type": "Product", "name": "Exterior sin oferta", "url": "/p/1"}) + _jsonld(
        {"@type": "Product", "name": "Ejemplar", "@id": "/p/1", "offers": {"price": 20}}
    )
    ficha = _extraer(_pagina(html + "<p>20 €</p>"), ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == 20


def test_sin_precio_en_ninguno_de_la_url_se_queda_el_primero_que_casa() -> None:
    html = (
        _jsonld({"@type": "Product", "name": "Otro", "url": "/otro", "offers": {"price": 9}})
        + _jsonld({"@type": "Product", "name": "Este", "url": "/p/1"})
        + _jsonld({"@type": "Product", "name": "Tambien", "url": "/p/1"})
    )
    ficha = _extraer(_pagina(html + "<p>9 €</p>"), ("precio",))
    assert ficha.titulo == "Este"
    assert ficha.precio is None


@pytest.mark.parametrize(
    ("fichero", "url", "importe"),
    [
        (
            "todostuslibros_ficha_jsonld_book.html",
            "https://www.todostuslibros.com/libros/la-puerta-de-no-retorno_978-84-8393-395-4",
            Decimal("20"),
        ),
        (
            "casadellibro_audiolibro_jsonld.html",
            "https://www.casadellibro.com/audiolibro-clasicos-sin-filtros-audiolibro/9788491999133/18238326",
            Decimal("18.04"),
        ),
    ],
)
def test_fichas_reales_de_libros_toman_el_precio_del_jsonld(
    fichero: str, url: str, importe: Decimal
) -> None:
    # Campaña multi-sector (causa C6): ninguna ficha de libro sacaba precio del JSON-LD.
    html = (REALES / fichero).read_text(encoding="utf-8")
    ficha = _extraer(_pagina(html, url), ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == importe
    assert ficha.campos["precio"].origen is OrigenDato.JSONLD


def test_ficha_real_con_variantes_toma_la_de_la_pagina() -> None:
    # Campaña multi-sector (causa C7): footlocker declara 15 colores en el JSON-LD con la url
    # /product/<slug>/<sku>.html y la página es /es/product/~/<sku>.html. Ninguna casaba exacta y
    # salía la primera variante con precio (115 €) en vez de la de la página (100 €).
    html = (REALES / "footlocker_ficha_variantes_jsonld.html").read_text(encoding="utf-8")
    pagina = _pagina(html, "https://www.footlocker.es/es/product/~/314218739804.html")
    ficha = _extraer(pagina, ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == 100
    assert ficha.campos["precio"].origen is OrigenDato.JSONLD
    assert not any("JSON-LD" in aviso for aviso in ficha.avisos)


def _variantes(*urls: tuple[str, int]) -> str:
    return "".join(
        _jsonld(
            {"@type": "Product", "name": f"Variante {n}", "url": url, "offers": {"price": precio}}
        )
        for n, (url, precio) in enumerate(urls)
    )


def test_variante_por_el_ultimo_tramo_de_la_ruta() -> None:
    html = _variantes(
        ("/product/zapatilla-azul/111111.html", 115),
        ("/product/zapatilla-roja/222222.html", 100),
        ("/product/zapatilla-verde/333333.html", 90),
    )
    pagina = _pagina(html + "<p>100,00 €</p>", "https://tienda.es/es/product/~/222222.html")
    ficha = _extraer(pagina, ("precio",))
    assert ficha.titulo == "Variante 1"
    assert ficha.precio is not None and ficha.precio.importe == 100
    assert not any("JSON-LD" in aviso for aviso in ficha.avisos)


@pytest.mark.parametrize(
    ("variantes", "url"),
    [
        # Dos productos comparten el tramo: no se sabe cuál es, se queda el primero con precio.
        (
            (("/a/222222.html", 115), ("/b/222222.html", 100)),
            "https://tienda.es/c/222222.html",
        ),
        # Tramos genéricos (sin cifras o cortos): la variante va en la query, no identifican nada.
        (
            (("/otra/product.php?v=1", 115), ("/x/ficha/product.php?v=2", 100)),
            "https://tienda.es/product.php?v=2",
        ),
        (
            (("/a/zapatilla-azul", 115), ("/b/zapatilla-roja", 100)),
            "https://tienda.es/zapatilla-roja",
        ),
        ((("/a/1/p1", 115), ("/b/2/p2", 100)), "https://tienda.es/c/p2"),
    ],
)
def test_sin_tramo_unico_e_identificativo_no_cambia_de_producto(
    variantes: tuple[tuple[str, int], ...], url: str
) -> None:
    ficha = _extraer(_pagina(_variantes(*variantes) + "<p>100 € 115 €</p>", url), ("precio",))
    assert ficha.titulo == "Variante 0"
    assert ficha.precio is not None and ficha.precio.importe == 115


def _con_sku(sku: str, precio: int, url: str = "/model/zapatilla/9.html") -> str:
    return _jsonld(
        {
            "@type": "Product",
            "name": f"Sku {sku}",
            "sku": sku,
            "url": url,
            "offers": {"price": precio},
        }
    )


def test_variante_por_el_sku_en_la_ruta_de_la_pagina() -> None:
    html = _con_sku("314217634204", 115) + _con_sku("314218739804", 100)
    pagina = _pagina(html + "<p>100 €</p>", "https://tienda.es/p/zapatilla-314218739804-azul")
    ficha = _extraer(pagina, ("precio",))
    assert ficha.titulo == "Sku 314218739804"
    assert ficha.precio is not None and ficha.precio.importe == 100


def test_variante_por_el_gtin_en_la_ruta_de_la_pagina() -> None:
    html = _jsonld(
        {"@type": "Product", "name": "Uno", "gtin13": "8412345678901", "offers": {"price": 115}}
    ) + _jsonld(
        {"@type": "Product", "name": "Dos", "gtin13": "8498765432109", "offers": {"price": 100}}
    )
    pagina = _pagina(html + "<p>100 €</p>", "https://tienda.es/p/8498765432109")
    ficha = _extraer(pagina, ("precio",))
    assert ficha.titulo == "Dos"


@pytest.mark.parametrize(
    ("skus", "url"),
    [
        # Sku corto: «12» está en cualquier ruta.
        (("99", "12"), "https://tienda.es/p/zapatilla-12-azul"),
        # Pegado a otros alfanuméricos no es el sku, es parte de otro identificador.
        (("314217634204", "218739"), "https://tienda.es/p/A314218739804"),
        # Dos skus en la ruta: no se sabe cuál es.
        (("314217", "218739"), "https://tienda.es/p/314217-218739"),
    ],
)
def test_sku_que_no_identifica_la_variante_no_cambia_de_producto(
    skus: tuple[str, str], url: str
) -> None:
    html = _con_sku(skus[0], 115) + _con_sku(skus[1], 100)
    ficha = _extraer(_pagina(html + "<p>100 € 115 €</p>", url), ("precio",))
    assert ficha.titulo == f"Sku {skus[0]}"
    assert ficha.precio is not None and ficha.precio.importe == 115


def test_el_mpn_en_la_ruta_no_elige_la_variante() -> None:
    # En recambios la OEM va en el slug de varios productos relacionados: no identifica la ficha.
    html = _jsonld(
        {"@type": "Product", "name": "Otro", "mpn": "1K0698151", "offers": {"price": 115}}
    ) + _jsonld({"@type": "Product", "name": "Uno", "mpn": "1K0698451", "offers": {"price": 100}})
    pagina = _pagina(html + "<p>100 € 115 €</p>", "https://tienda.es/pastillas-1K0698451-trw")
    assert _extraer(pagina, ("precio",)).titulo == "Otro"


# --- Variante seleccionada en la página (R2-C11) ------------------------------------------------


@pytest.mark.parametrize(
    ("fichero", "url", "importe"),
    [
        # Ninguna variante casa con la URL (id del maestro HIL5224DA): salía la primera con precio
        # (18,79 €, saco de 2,5 kg) y la página muestra la seleccionada (83,99 €, 18 kg).
        (
            "kiwoko_ficha_variantes_seleccionada.html",
            "https://www.kiwoko.com/perros/especial-cachorro/pienso-para-cachorros/"
            "hill_s-science-plan-pienso-pollo-para-cachorros-de-razas-medianas/HIL5224DA_M.html",
            Decimal("83.99"),
        ),
        # El id del maestro de la URL (HIL9274M) es el sku de otra variante (70,29 €): ganaba esa
        # y la página muestra la seleccionada (HIL604314, 84,49 €).
        (
            "tiendanimal_ficha_variantes_seleccionada.html",
            "https://www.tiendanimal.es/hills-science-plan-pienso-pollo-para-perros-mayores-de-"
            "razas-grandes/HIL9274M_M.html",
            Decimal("84.49"),
        ),
    ],
)
def test_ficha_real_toma_la_variante_seleccionada_en_la_pagina(
    fichero: str, url: str, importe: Decimal
) -> None:
    # Campaña multi-sector (causa R2-C11): Salesforce Commerce Cloud publica un ProductGroup con
    # un Product por peso y marca la variante de la página con un botón `is-selected` cuyo
    # `data-variantid` es su sku.
    html = (REALES / fichero).read_text(encoding="utf-8")
    ficha = _extraer(_pagina(html, url), ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == importe
    assert ficha.campos["precio"].origen is OrigenDato.JSONLD
    assert not any("JSON-LD" in aviso for aviso in ficha.avisos)


_DOS_SKUS = _con_sku("ABC12345", 115, "/v/ABC12345.html") + _con_sku(
    "XYZ67890", 100, "/v/XYZ67890.html"
)
_URL_MAESTRO = "https://tienda.es/p/pienso/ABC12345_M.html"
"""El sku de la ruta (el del maestro) casa con la primera variante, que no es la seleccionada."""


@pytest.mark.parametrize(
    "marcado",
    [
        '<button class="card is-selected" id="variant-XYZ67890">18 kg</button>',
        '<button class="card selected" data-variantid="XYZ67890">18 kg</button>',
        '<div class="swatch swatch--selected" data-sku="xyz67890">18 kg</div>',
        '<button class="card" aria-current="true" data-pid="XYZ67890">18 kg</button>',
        '<button class="card" aria-pressed="true" data-pid="XYZ67890">18 kg</button>',
        '<li role="option" aria-selected="true" data-value="XYZ67890">18 kg</li>',
        '<input type="radio" name="peso" value="XYZ67890" checked>',
        '<select name="peso"><option value="ABC12345">2 kg</option>'
        '<option value="XYZ67890" selected>18 kg</option></select>',
    ],
)
def test_la_variante_seleccionada_gana_al_sku_de_la_ruta(marcado: str) -> None:
    pagina = _pagina(_DOS_SKUS + marcado + "<p>100 € 115 €</p>", _URL_MAESTRO)
    ficha = _extraer(pagina, ("precio",))
    assert ficha.titulo == "Sku XYZ67890"
    assert ficha.precio is not None and ficha.precio.importe == 100


@pytest.mark.parametrize(
    "marcado",
    [
        # Dos marcados que apuntan a variantes distintas: no se sabe cuál es.
        '<button class="is-selected" data-variantid="XYZ67890"></button>'
        '<button class="is-selected" data-variantid="ABC99999"></button>',
        # «active» se usa en pestañas, carruseles y menús: no marca la variante.
        '<button class="card is-active" data-variantid="XYZ67890"></button>',
        '<button class="card active" data-variantid="XYZ67890"></button>',
        # aria-current="page" es la navegación, no una selección.
        '<a aria-current="page" data-pid="XYZ67890" href="/v/XYZ67890.html">18 kg</a>',
        # Marcado sin seleccionar.
        '<button class="card" aria-pressed="false" data-variantid="XYZ67890"></button>',
        # Un nodo que nombra dos variantes no identifica ninguna.
        '<div class="selected" data-a="XYZ67890" data-b="ABC99999"></div>',
        # Pegado a otros caracteres de identificador no es el sku.
        '<button class="is-selected" data-variantid="XYZ67890_B2"></button>',
        '<button class="is-selected" data-variantid="XYZ678901"></button>',
        # El sku en el texto o en un atributo que no es de datos no cuenta.
        '<button class="is-selected" title="XYZ67890">XYZ67890</button>',
    ],
)
def test_marcado_que_no_identifica_una_variante_no_cambia_nada(marcado: str) -> None:
    html = _DOS_SKUS + _con_sku("ABC99999", 90, "/v/ABC99999.html") + marcado
    ficha = _extraer(_pagina(html + "<p>100 € 115 € 90 €</p>", _URL_MAESTRO), ("precio",))
    assert ficha.titulo == "Sku ABC12345"
    assert ficha.precio is not None and ficha.precio.importe == 115


def test_la_variante_seleccionada_con_sufijo_es_la_suya() -> None:
    # «HIL604334_B2» (el pack de dos) es otra variante, no la de sku «HIL604334».
    html = _con_sku("HIL604334", 84, "/v/HIL604334.html") + _con_sku(
        "HIL604334_B2", 168, "/v/HIL604334_B2.html"
    )
    marcado = '<button class="is-selected" id="variant-HIL604334_B2"></button>'
    pagina = _pagina(html + marcado + "<p>84 € 168 €</p>", "https://tienda.es/p/HIL5224DA_M.html")
    assert _extraer(pagina, ("precio",)).titulo == "Sku HIL604334_B2"


def test_la_seleccion_no_quita_la_variante_del_ultimo_tramo_de_la_ruta() -> None:
    # El tramo de la ruta igual al de la url de una variante sigue mandando (footlocker, C7).
    marcado = '<button class="is-selected" data-variantid="ABC12345"></button>'
    pagina = _pagina(
        _DOS_SKUS + marcado + "<p>100 € 115 €</p>", "https://tienda.es/es/~/XYZ67890.html"
    )
    assert _extraer(pagina, ("precio",)).titulo == "Sku XYZ67890"


def test_la_seleccion_de_un_solo_producto_no_cambia_nada() -> None:
    html = _con_sku("ABC12345", 115) + '<button class="is-selected" data-sku="OTRO1234"></button>'
    ficha = _extraer(_pagina(html + "<p>115 €</p>", "https://tienda.es/p/1"), ("precio",))
    assert ficha.titulo == "Sku ABC12345"


# --- Listados con JSON-LD ItemList (R2-C10) ------------------------------------------------------


@pytest.mark.parametrize(
    ("fichero", "url", "primero"),
    [
        (
            "bricodepot_landing_itemlist_taladros.html",
            "https://www.bricodepot.es/v/taladro-percutor-bosch-profesional",
            "Taladro percutor y amoladora",
        ),
        (
            "mediamarkt_marca_itemlist_auriculares.html",
            "https://www.mediamarkt.es/es/brand/jvc/auriculares-bluetooth",
            "Auriculares True Wireless",
        ),
    ],
)
def test_landing_real_con_itemlist_es_un_listado_sin_precio(
    fichero: str, url: str, primero: str
) -> None:
    # Campaña multi-sector (causa R2-C10): la landing SEO de Brico Dépôt y la página de marca de
    # MediaMarkt solo traen un `ItemList` de productos, ninguno con la URL de la página. Salían el
    # título y el precio del primero (365,00 € y 54,99 €) como si fueran los de la ficha.
    html = (REALES / fichero).read_text(encoding="utf-8")
    ia = ExtractorFalso({"precio": "365,00 €", "marca": "Bosch"})
    ficha = _extraer(_pagina(html, url), TODOS, extractor_ia=ia)
    assert ficha.listado == 3
    assert ficha.precio is None
    assert ficha.campos == {}
    assert not ficha.titulo.startswith(primero)
    assert ia.llamadas == []
    assert "es un listado (ItemList con 3 productos), no una ficha" in " ".join(ficha.avisos)


def _lista(*productos: dict[str, object]) -> str:
    elementos = [
        {"@type": "ListItem", "position": n, "item": p} for n, p in enumerate(productos, 1)
    ]
    return _jsonld({"@type": "ItemList", "itemListElement": elementos})


def _listado(numero: int, precio: int, url: str | None = None) -> dict[str, object]:
    return {
        "@type": "Product",
        "name": f"Listado {numero}",
        "url": url or f"/p/{numero + 100}",
        "offers": {"price": precio, "priceCurrency": "EUR"},
    }


def test_en_un_listado_ninguna_via_da_precio() -> None:
    # El precio de una tarjeta lo leerían el selector del perfil o la IA: tampoco vale.
    html = _lista(_listado(1, 9), _listado(2, 12)) + '<p class="precio">9,00 €</p>'
    ia = ExtractorFalso({"precio": "12,00 €"})
    ficha = _extraer(_pagina(html), ("precio",), perfil=_perfil(precio=".precio"), extractor_ia=ia)
    assert (ficha.listado, ficha.precio, ficha.campos, ia.llamadas) == (2, None, {}, [])


def test_el_producto_suelto_gana_a_los_relacionados_de_una_lista() -> None:
    # Una ficha cuyo `Product` no lleva url y que publica además un `ItemList` de relacionados.
    suelto = _jsonld({"@type": "Product", "name": "Este", "offers": {"price": 20}})
    html = _lista(_listado(1, 9), _listado(2, 12)) + suelto + "<p>20 €</p>"
    ficha = _extraer(_pagina(html), ("precio",))
    assert (ficha.listado, ficha.titulo) == (0, "Este")
    assert ficha.precio is not None and ficha.precio.importe == 20


def test_el_producto_de_la_pagina_dentro_de_su_lista_gana() -> None:
    html = _lista(_listado(1, 9), _listado(2, 20, url="/p/1")) + "<p>20 €</p>"
    ficha = _extraer(_pagina(html), ("precio",))
    assert (ficha.listado, ficha.titulo) == (0, "Listado 2")
    assert ficha.precio is not None and ficha.precio.importe == 20


def test_con_microdatos_la_lista_de_relacionados_no_da_el_producto() -> None:
    # El producto va en microdatos y el JSON-LD solo trae relacionados: no es un listado, y el
    # precio es el de los microdatos, no el del primer relacionado.
    micro = _microdatos(
        '<h1 itemprop="name">Este</h1><span itemprop="offers" itemscope '
        'itemtype="https://schema.org/Offer"><meta itemprop="price" content="20">'
        '<meta itemprop="priceCurrency" content="EUR">20 €</span>'
    )
    html = _lista(_listado(1, 9), _listado(2, 12)) + micro
    ficha = _extraer(_pagina(html), ("precio",))
    assert (ficha.listado, ficha.titulo) == (0, "Este")
    assert ficha.precio is not None and ficha.precio.importe == 20
    assert ficha.campos["precio"].origen is OrigenDato.METADATOS


@pytest.mark.parametrize(
    "lista",
    [
        pytest.param(_lista(_listado(1, 9)), id="un-producto"),
        pytest.param(_lista(_listado(1, 9), _listado(2, 9, url="/p/101")), id="misma-url"),
    ],
)
def test_una_lista_de_un_solo_producto_no_es_un_listado(lista: str) -> None:
    ficha = _extraer(_pagina(lista + "<p>9 €</p>"), ("precio",))
    assert ficha.listado == 0
    assert ficha.precio is not None and ficha.precio.importe == 9


# --- R2-CAMPOS: dos selectores del perfil que leen el mismo valor ---------------------------
# Campaña multi-sector, ronda 2: en PcComponentes la IA propuso como marca
# `div.group-h0XKFq span.identifier-M7hbzz:first-of-type`, que lee el mismo «P/N: …» que
# `referencia_oem` (la marca es otro span). Las fichas son recortes de las reales.

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
PCC = "https://www.pccomponentes.com/"
SEL_PCC = {
    "precio": "#pdp-price-current-container",
    "referencia_oem": "#pdp-mpn",
    "marca": "div.group-h0XKFq span.identifier-M7hbzz:first-of-type",
}
FICHAS_PCC = [
    pytest.param(
        "pccomponentes_ficha_marca_igual_referencia_phs.html",
        "memoria-ram-16gb-phs-memory-ddr5-5600mhz-1x16gb-so-dimm-compatible-portatil",
        "P/N: SP634452",
        Decimal("525.31"),
        id="phs",
    ),
    pytest.param(
        "pccomponentes_ficha_marca_igual_referencia_adata.html",
        "memoria-ram-adata-csodimm-16gb-ddr5-6400mhz-cl52-1-4v-para-portatil",
        "P/N: AD5CS640016G-S",
        Decimal("345.82"),
        id="adata",
    ),
]


def _real(nombre: str, ruta: str) -> Pagina:
    return _pagina((REALES / nombre).read_bytes().decode("utf-8"), PCC + ruta)


@pytest.mark.parametrize(("nombre", "ruta", "referencia", "importe"), FICHAS_PCC)
def test_la_marca_que_lee_lo_mismo_que_la_referencia_se_descarta(
    nombre: str, ruta: str, referencia: str, importe: Decimal
) -> None:
    ficha = _extraer(_real(nombre, ruta), POR_DEFECTO, perfil=_perfil(**SEL_PCC))
    assert ficha.precio is not None and ficha.precio.importe == importe
    assert ficha.campos["referencia_oem"].valor == referencia
    assert ficha.campos["referencia_oem"].origen is OrigenDato.PERFIL
    assert "marca" not in ficha.campos
    assert ficha.repetidos == {"marca": "referencia_oem"}
    assert any("«marca»" in a and "«referencia_oem»" in a for a in ficha.avisos), ficha.avisos


def test_se_descarta_el_de_menos_prioridad_aunque_se_pida_antes() -> None:
    nombre, ruta = "pccomponentes_ficha_marca_igual_referencia_phs.html", FICHAS_PCC[0].values[1]
    campos = ("marca", "referencia_oem", "precio")
    ficha = _extraer(_real(nombre, str(ruta)), campos, perfil=_perfil(**SEL_PCC))
    assert ficha.repetidos == {"marca": "referencia_oem"}
    assert set(ficha.campos) == {"referencia_oem", "precio"}


def test_el_campo_descartado_se_le_pide_a_la_ia() -> None:
    nombre, ruta = "pccomponentes_ficha_marca_igual_referencia_phs.html", FICHAS_PCC[0].values[1]
    ia = ExtractorFalso({"marca": "PHS-memory"})
    ficha = _extraer(_real(nombre, str(ruta)), perfil=_perfil(**SEL_PCC), extractor_ia=ia)
    assert "marca" in ia.llamadas[0][1]
    assert ficha.campos["marca"] == ValorCampo(
        "PHS-memory", OrigenDato.IA, True, CONFIANZA[OrigenDato.IA]
    )
    assert ficha.repetidos == {"marca": "referencia_oem"}


def _ficha_datos(referencia: str, marca: str, descripcion: str = "Faro delantero") -> str:
    return (
        "<html><head><title>Faro</title></head><body><h1>Faro</h1>"
        "<span class='precio'>89,90 €</span><table>"
        f"<tr><td class='ref'>{referencia}</td></tr><tr><td class='marca'>{marca}</td></tr>"
        f"</table><p class='desc'>{descripcion}</p></body></html>"
    )


SEL_DATOS = {
    "precio": "span.precio",
    "referencia_oem": "td.ref",
    "marca": "td.marca",
    "descripcion": "p.desc",
}


def test_recambios_con_marca_y_referencia_distintas_no_descarta_nada() -> None:
    ficha = _extraer(
        _pagina(_ficha_datos("5K1941005E", "Valeo")), TODOS, perfil=_perfil(**SEL_DATOS)
    )
    assert ficha.repetidos == {}
    assert ficha.campos["referencia_oem"].valor == "5K1941005E"
    assert ficha.campos["marca"].valor == "Valeo"
    assert ficha.campos["descripcion"].valor == "Faro delantero"


def test_el_mismo_valor_con_otros_espacios_y_mayusculas_se_descarta() -> None:
    html = _ficha_datos("P/N: SP634452", "  p/n:\n  sp634452 ")
    ficha = _extraer(_pagina(html), TODOS, perfil=_perfil(**SEL_DATOS))
    assert ficha.repetidos == {"marca": "referencia_oem"}
    assert "marca" not in ficha.campos


def test_tres_campos_iguales_se_quedan_con_el_de_mas_prioridad() -> None:
    html = _ficha_datos("SP634452", "SP634452", "SP634452")
    ficha = _extraer(_pagina(html), TODOS, perfil=_perfil(**SEL_DATOS))
    assert ficha.repetidos == {"marca": "referencia_oem", "descripcion": "referencia_oem"}
    assert set(ficha.campos) == {"precio", "referencia_oem"}


def test_un_valor_del_json_ld_igual_al_del_perfil_no_se_descarta() -> None:
    # Solo se comparan los selectores del perfil entre sí: el JSON-LD es la tienda declarando.
    producto: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Faro",
        "mpn": "SP634452",
        "offers": {"@type": "Offer", "priceCurrency": "EUR", "price": "89.90"},
    }
    html = _ficha_datos("SP634452", "SP634452").replace("</head>", _jsonld(producto) + "</head>")
    ficha = _extraer(_pagina(html), POR_DEFECTO, perfil=_perfil(**SEL_DATOS))
    assert ficha.campos["referencia_oem"].origen is OrigenDato.JSONLD
    assert ficha.campos["marca"].origen is OrigenDato.PERFIL
    assert ficha.repetidos == {}


# --- Valores del perfil sin forma de su campo (R3-E) --------------------------------------------
# Campaña multi-sector, ronda 3: en Dia la IA propuso como marca `div.manufacturer-info`, que lee
# el bloque entero «Información del responsable Envasado para DIA Retail España, S.A.U. C/ …»; en
# Zalando, como referencia `dd.voFjEy:nth-of-type(1)`, que lee el primer <dd> de los atributos,
# «100% algodón». Los dos valores salían anclados, con origen perfil y confianza 0,9. Las fichas
# son recortes de las reales.

DIA = "https://www.dia.es/aceites-salsas-y-especias/aceites/p/112529"
SEL_DIA = {
    "precio": "p.buy-box__active-price",
    "marca": "div.manufacturer-info",
    "descripcion": "div.instructions-info",
}
ZALANDO = "https://www.zalando.es/"
SEL_ZALANDO = {
    "precio": ".voFjEy.VrucAJ",
    "referencia_oem": "dd.voFjEy:nth-of-type(1)",
    "marca": "[data-testid=product_title-brand-name]",
}
FICHAS_ZALANDO = [
    pytest.param(
        "zalando_ficha_referencia_lee_material_nike.html",
        "nike-sportswear-club-tee-camiseta-basica-ni122o0ce-a12.html",
        "Nike Sportswear",
        Decimal("16.95"),
        id="nike",
    ),
    pytest.param(
        "zalando_ficha_referencia_lee_material_boss.html",
        "boss-classic-3-pack-camiseta-interior-open-miscellaneous-bb182n034-a11.html",
        "BOSS",
        Decimal("37.95"),
        id="boss",
    ),
]


def _real_en(nombre: str, url: str) -> Pagina:
    return _pagina((REALES / nombre).read_bytes().decode("utf-8"), url)


def test_la_marca_de_dia_que_lee_el_bloque_del_responsable_se_descarta() -> None:
    pagina = _real_en("dia_ficha_marca_bloque_responsable.html", DIA)
    ficha = _extraer(pagina, TODOS, perfil=_perfil(**SEL_DIA))
    assert ficha.precio is not None and ficha.precio.importe == Decimal("4.35")
    assert "marca" not in ficha.campos
    assert set(ficha.sin_forma) == {"marca"}
    assert ficha.sin_forma["marca"].startswith("Información del responsable Envasado para DIA")
    assert any("«marca»" in a and "forma" in a for a in ficha.avisos), ficha.avisos


@pytest.mark.parametrize(("nombre", "ruta", "marca", "importe"), FICHAS_ZALANDO)
def test_la_referencia_de_zalando_que_lee_el_material_se_descarta(
    nombre: str, ruta: str, marca: str, importe: Decimal
) -> None:
    ficha = _extraer(_real_en(nombre, ZALANDO + ruta), TODOS, perfil=_perfil(**SEL_ZALANDO))
    assert ficha.precio is not None and ficha.precio.importe == importe
    assert "referencia_oem" not in ficha.campos
    assert ficha.sin_forma == {"referencia_oem": "100% algodón"}
    assert ficha.campos["marca"] == ValorCampo(
        marca, OrigenDato.PERFIL, True, CONFIANZA[OrigenDato.PERFIL]
    )
    assert any("«referencia_oem»" in a and "100% algodón" in a for a in ficha.avisos)


def test_el_campo_sin_forma_se_le_pide_a_la_ia_que_tiene_que_anclarlo() -> None:
    nombre, ruta = "zalando_ficha_referencia_lee_material_nike.html", FICHAS_ZALANDO[0].values[1]
    ia = ExtractorFalso({"referencia_oem": "NI122O0CE-A12"})
    pagina = _real_en(nombre, ZALANDO + str(ruta))
    ficha = _extraer(pagina, POR_DEFECTO, perfil=_perfil(**SEL_ZALANDO), extractor_ia=ia)
    assert ia.llamadas[0][1] == ("referencia_oem", "estado")
    assert ficha.campos["referencia_oem"] == ValorCampo(
        "NI122O0CE-A12", OrigenDato.IA, True, CONFIANZA[OrigenDato.IA]
    )


@pytest.mark.parametrize(
    "referencia",
    [
        "038253014G",
        "8200 660 034",
        "0 986 494 527",
        "1K0 698 151 A / 1K0698151B",
        "A 000 420 32 20",
        "P/N: SP634452",
        "Ref. OEM: 1K0615301AA, 5Q0615301F",
        "NX.J7WEB.039",
    ],
)
def test_las_referencias_de_recambios_del_perfil_se_conservan(referencia: str) -> None:
    ficha = _extraer(_pagina(_ficha_datos(referencia, "Valeo")), TODOS, perfil=_perfil(**SEL_DATOS))
    assert ficha.campos["referencia_oem"].valor == referencia
    assert ficha.campos["referencia_oem"].origen is OrigenDato.PERFIL
    assert ficha.sin_forma == {}


@pytest.mark.parametrize(
    "marca",
    ["Valeo", "DACIA", "C.F. Martin &amp; Co.", "Hill's Science Plan", "Pottermore Publishing"],
)
def test_las_marcas_cortas_del_perfil_se_conservan(marca: str) -> None:
    ficha = _extraer(_pagina(_ficha_datos("5K1941005E", marca)), TODOS, perfil=_perfil(**SEL_DATOS))
    assert ficha.campos["marca"].valor == marca.replace("&amp;", "&")
    assert ficha.sin_forma == {}


@pytest.mark.parametrize(
    ("referencia", "marca", "campo"),
    [
        ("100% algodón", "Nike", "referencia_oem"),
        ("Talla: M", "Nike", "referencia_oem"),
        ("Material exterior", "Nike", "referencia_oem"),
        ("5K1941005E", "Envasado para la cadena por un fabricante de la zona", "marca"),
        ("5K1941005E", "M" * 61, "marca"),
    ],
)
def test_los_valores_del_perfil_sin_forma_de_su_campo_se_descartan(
    referencia: str, marca: str, campo: str
) -> None:
    ficha = _extraer(_pagina(_ficha_datos(referencia, marca)), TODOS, perfil=_perfil(**SEL_DATOS))
    assert campo not in ficha.campos
    assert ficha.sin_forma == {campo: referencia if campo == "referencia_oem" else marca}
    assert ficha.campos["descripcion"].valor == "Faro delantero"


def test_los_valores_del_json_ld_no_se_comprueban() -> None:
    # Es la tienda declarando sus datos: la comprobación es para los selectores de la IA.
    producto: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Faro",
        "mpn": "Sin referencia",
        "brand": {"@type": "Brand", "name": "Envasado para la cadena por un fabricante local"},
        "offers": {"@type": "Offer", "priceCurrency": "EUR", "price": "89.90"},
    }
    html = _ficha_datos("100% algodón", "Valeo").replace("</head>", _jsonld(producto) + "</head>")
    # La marca declarada tiene que verse (R5-H), pero su forma no se comprueba.
    html += "<p>Envasado para la cadena por un fabricante local</p>"
    ficha = _extraer(_pagina(html), POR_DEFECTO, perfil=_perfil(**SEL_DATOS))
    assert ficha.campos["referencia_oem"].origen is OrigenDato.JSONLD
    assert ficha.campos["marca"].origen is OrigenDato.JSONLD
    assert ficha.sin_forma == {}


# --- Precio del JSON-LD que solo se ve tachado (R3-A1) -------------------------------------------
# Campaña multi-sector, ronda 3: en Brico Dépôt el JSON-LD declara 316.54, que en la página solo
# sale como precio anterior (<s>316,54 €</s>); el vigente es 228,96 € (y `product:price:amount`
# 228.96). El JSON-LD se daba por anclado porque su `precio.texto` sale del propio bloque, y el
# precio de la ficha era uno que no se paga. La ficha es un recorte de la real.

BRICODEPOT = (
    "https://www.bricodepot.es/"
    "taladro-percutor-1100w-gsb-24-2-en-caja-l-case-bosch-060119c801-48da9018"
)
JSONLD_316 = _jsonld({"@type": "Product", "name": "Taladro", "offers": {"price": "316.54"}})
OG_228 = (
    '<meta property="og:type" content="product">'
    '<meta property="product:price:amount" content="228.96">'
)


def test_bricodepot_el_precio_del_jsonld_solo_tachado_cede_a_los_metadatos() -> None:
    pagina = _real_en("bricodepot_ficha_jsonld_precio_tachado.html", BRICODEPOT)
    ficha = _extraer(pagina, ("precio", "marca"))
    assert ficha.precio is not None and ficha.precio.importe == Decimal("228.96")
    assert ficha.campos["precio"].origen is OrigenDato.METADATOS
    assert ficha.campos["marca"] == ValorCampo("Bosch", OrigenDato.JSONLD)
    assert any("316,54 €" in a and "tachado" in a for a in ficha.avisos), ficha.avisos


def test_jsonld_solo_tachado_cede_a_los_metadatos() -> None:
    html = JSONLD_316 + OG_228 + "<p><s>316,54 €</s> 228,96 €</p>"
    ficha = _extraer(_pagina(html), ("precio",))
    assert ficha.precio == Precio(Decimal("228.96"), "EUR", "228.96")
    assert ficha.campos["precio"].origen is OrigenDato.METADATOS


def test_jsonld_solo_tachado_cede_al_perfil() -> None:
    html = JSONLD_316 + '<p><del>316,54 €</del> <b class="precio">228,96 €</b></p>'
    ficha = _extraer(_pagina(html), ("precio",), perfil=_perfil(precio=".precio"))
    assert ficha.campos["precio"] == ValorCampo("228,96 €", OrigenDato.PERFIL, confianza=0.9)


def test_jsonld_solo_tachado_y_sin_otra_fuente_se_queda_sin_precio() -> None:
    # Mejor sin precio que con uno que no se paga.
    html = JSONLD_316 + '<p><span class="old-price">316,54 €</span> Agotado</p>'
    ficha = _extraer(_pagina(html), ("precio",))
    assert ficha.precio is None
    assert "precio" not in ficha.campos
    assert any("316,54 €" in a and "tachado" in a for a in ficha.avisos), ficha.avisos


def test_jsonld_tachado_y_tambien_sin_tachar_sigue_ganando() -> None:
    html = JSONLD_316 + OG_228 + "<p><s>316,54 €</s></p><p>Precio: 316,54 €</p>"
    ficha = _extraer(_pagina(html), ("precio",))
    assert ficha.campos["precio"] == ValorCampo("316,54 €", OrigenDato.JSONLD)
    assert not any("tachado" in a for a in ficha.avisos)


def test_jsonld_que_no_sale_en_el_texto_se_sigue_aceptando_con_aviso() -> None:
    # Precio pintado por JS: no sale en ninguna parte del texto, ni tachado ni sin tachar.
    html = JSONLD_316 + "<p><s>400,00 €</s></p>"
    ficha = _extraer(_pagina(html), ("precio",))
    assert ficha.campos["precio"] == ValorCampo("316,54 €", OrigenDato.JSONLD)
    assert any("JSON-LD" in a and "no se ve" in a for a in ficha.avisos)
    assert not any("tachado" in a for a in ficha.avisos)


def test_shopify_sin_oferta_con_el_precio_en_price_item_regular_no_se_queda_sin_precio() -> None:
    # Dawn pone el precio normal de un producto sin oferta en `price-item--regular`, que
    # `dom.tachado` toma por antiguo; Shopify declara también `og:price:amount`.
    html = _jsonld({"@type": "Product", "name": "Camiseta", "offers": {"price": "12.90"}})
    html += '<meta property="og:type" content="product">'
    html += '<meta property="og:price:amount" content="12.90">'
    html += '<span class="price-item price-item--regular">12,90 €</span>'
    ficha = _extraer(_pagina(html), ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == Decimal("12.90")


# --- Céntimos en un elemento aparte (R3-A2) ------------------------------------------------------
# Brico Dépôt pinta los céntimos en un span aparte («14<span>95</span>€»): el texto decía «1495€»
# y el precio del JSON-LD (o de los metadatos) se daba por bueno con el aviso de que no se veía.

BRICODEPOT_FILTRO = "https://www.bricodepot.es/filtro-de-carbono-o-13-2-cm-3663602843092"


def test_bricodepot_el_precio_con_los_centimos_aparte_se_ve_en_la_pagina() -> None:
    pagina = _real_en("bricodepot_ficha_precio_centimos_en_span.html", BRICODEPOT_FILTRO)
    ficha = _extraer(pagina, ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == Decimal("14.95")
    assert ficha.campos["precio"].origen is OrigenDato.JSONLD
    assert not any("no se ve" in a for a in ficha.avisos), ficha.avisos


def test_bricodepot_el_precio_de_los_metadatos_tras_el_tachado_se_ve_en_la_pagina() -> None:
    # La ficha de R3-A1: el JSON-LD (316,54, solo tachado) cede a los metadatos (228,96), que
    # ahora se ven en la página.
    pagina = _real_en("bricodepot_ficha_jsonld_precio_tachado.html", BRICODEPOT)
    ficha = _extraer(pagina, ("precio",))
    assert ficha.precio is not None and ficha.precio.importe == Decimal("228.96")
    assert ficha.campos["precio"].origen is OrigenDato.METADATOS
    assert not any("no se ve" in a for a in ficha.avisos), ficha.avisos


def test_perfil_que_lee_los_centimos_aparte_queda_anclado() -> None:
    html = (
        '<h1>Taladro</h1><p class="precio"><span>228</span><span><span class="top">96</span>'
        '<span class="bottom">€</span></span></p>'
    )
    ficha = _extraer(_pagina(html), ("precio",), perfil=_perfil(precio=".precio"))
    assert ficha.campos["precio"] == ValorCampo("228,96 €", OrigenDato.PERFIL, confianza=0.9)
    assert ficha.precio is not None and ficha.precio.importe == Decimal("228.96")


# --- R4-H: la referencia OEM que es el id interno de la tienda (el número de la URL) --------

DLI = (
    "https://desguacesdli.com/recambios/"
    "faro-izquierdo-mercedes-benz-clase-ml-bm-166-ml-350-bluetec-166-004-402519/"
)
# Solo la primera fila («RefID: 402519»): con todas, R5-A ya elige «Referencia pieza: …».
SEL_DLI = {"referencia_oem": "div#brxe-kdiwou h3:first-of-type"}
TUTETE = (
    "https://www.tutete.com/tienda/es/117_baneras-bebe/217_silletas/"
    "101507_silla-paseo-grande-con-bolsa-teresa.html"
)


def _dli(extractor_ia: ExtractorIA | None = None) -> FichaExtraida:
    pagina = _real_en("desguacesdli_ficha_refid_id_interno.html", DLI)
    return _extraer(pagina, POR_DEFECTO, perfil=_perfil(**SEL_DLI), extractor_ia=extractor_ia)


def test_la_referencia_del_perfil_que_es_el_id_de_la_url_de_dli_se_descarta() -> None:
    ficha = _dli()
    assert ficha.precio is not None and ficha.precio.importe == Decimal("544.50")
    assert "referencia_oem" not in ficha.campos
    assert ficha.sin_forma == {"referencia_oem": "RefID: 402519"}
    assert any("«referencia_oem»" in a and "id" in a for a in ficha.avisos), ficha.avisos


def test_descartado_el_id_de_dli_la_ia_da_la_referencia_de_la_pieza() -> None:
    ia = ExtractorFalso({"referencia_oem": "A1668207259"})
    ficha = _dli(ia)
    assert "referencia_oem" in ia.llamadas[0][1]
    assert ficha.campos["referencia_oem"] == ValorCampo(
        "A1668207259", OrigenDato.IA, True, CONFIANZA[OrigenDato.IA]
    )


def test_el_id_de_la_url_que_devuelve_la_ia_tambien_se_descarta() -> None:
    ficha = _dli(ExtractorFalso({"referencia_oem": "402519"}))
    assert "referencia_oem" not in ficha.campos
    assert any("«referencia_oem»" in a and "id" in a for a in ficha.avisos), ficha.avisos


def test_el_mpn_del_jsonld_de_tutete_que_es_el_id_del_slug_se_descarta() -> None:
    ficha = _extraer(_real_en("tutete_ficha_mpn_id_interno.html", TUTETE), POR_DEFECTO)
    assert ficha.precio is not None and ficha.precio.importe == Decimal("34.9")
    assert ficha.campos["marca"].valor == "Así"
    assert "referencia_oem" not in ficha.campos
    # `sin_forma` es solo lo que lee un selector del perfil: el JSON-LD no lo tiene.
    assert ficha.sin_forma == {}
    assert any("«referencia_oem»" in a and "101507" in a for a in ficha.avisos), ficha.avisos


@pytest.mark.parametrize(
    ("url", "mpn"),
    [
        ("https://t.es/p/21876", "21876"),
        ("https://t.es/recambios/123456-faro-golf.html", "123456"),
        ("https://t.es/recambios/faro-golf-4521/", "4521"),
        ("https://t.es/recambios/faro_golf_4521.html", "45-21"),
    ],
    ids=["tramo", "delante-del-slug", "detras-del-slug", "con-separador"],
)
def test_el_mpn_numerico_corto_en_posicion_de_id_en_la_url_se_descarta(url: str, mpn: str) -> None:
    html = _jsonld({"@type": "Product", "name": "Faro", "mpn": mpn, "productID": mpn})
    ficha = _extraer(_pagina(html, url), ("referencia_oem",))
    assert "referencia_oem" not in ficha.campos


@pytest.mark.parametrize(
    ("url", "mpn"),
    [
        ("https://t.es/recambios/faro-renault-clio-8200660034", "8200660034"),
        ("https://t.es/recambios/123-faro-a1668207259.html", "A1668207259"),
        ("https://t.es/recambios/febi-21876-brazo.html", "21876"),
        ("https://t.es/recambios/1234567-faro.html", "7654321"),
        ("https://t.es/p/1", "0 986 494 104"),
    ],
    ids=["oem-de-10-cifras", "alfanumerica", "en-medio-del-slug", "otro-numero", "bosch"],
)
def test_la_referencia_que_no_es_el_id_de_la_url_se_conserva(url: str, mpn: str) -> None:
    html = _jsonld({"@type": "Product", "name": "Faro", "mpn": mpn, "productID": mpn})
    ficha = _extraer(_pagina(html, url), ("referencia_oem",))
    assert ficha.campos["referencia_oem"] == ValorCampo(mpn, OrigenDato.JSONLD)


def test_el_id_de_la_url_en_los_metadatos_tambien_se_descarta() -> None:
    html = (
        '<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Faro</h1>'
        '<span itemprop="mpn">402519</span></div>'
    )
    ficha = _extraer(_pagina(html, "https://t.es/recambios/faro-402519/"), ("referencia_oem",))
    assert "referencia_oem" not in ficha.campos
