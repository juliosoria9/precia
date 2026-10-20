"""Producto estructurado desde OpenGraph (`og:*`, `product:*`) y microdatos de schema.org."""

import time
from decimal import Decimal
from pathlib import Path

import pytest

from precia.extraccion.dom import analizar
from precia.extraccion.metadatos import leer_importe, producto_metadatos
from precia.extraccion.tipos import Precio, ProductoJSONLD

FIXTURES = Path(__file__).parent.parent / "fixtures" / "extraccion"
PRESTASHOP = "https://recambios-ejemplo.es"


def _producto(html: str) -> ProductoJSONLD | None:
    return producto_metadatos(analizar(html))


def _fixture(nombre: str) -> ProductoJSONLD | None:
    return _producto((FIXTURES / nombre).read_text(encoding="utf-8"))


def _og(**propiedades: str) -> str:
    """`product_price_amount="1"` → `<meta property="product:price:amount" content="1">`."""
    metas: list[str] = []
    for clave, valor in propiedades.items():
        propiedad = clave.replace("_", ":")
        for compuesto in ("retailer_item_id", "mfr_part_no", "sale_price"):
            propiedad = propiedad.replace(compuesto.replace("_", ":"), compuesto)
        metas.append(f'<meta property="{propiedad}" content="{valor}">')
    return "".join(metas)


def _microdatos(cuerpo: str, tipo: str = "https://schema.org/Product") -> str:
    return f'<div itemscope itemtype="{tipo}">{cuerpo}</div>'


def test_prestashop_con_microdatos_y_opengraph() -> None:
    producto = _fixture("ficha_prestashop_microdatos.html")
    assert producto == ProductoJSONLD(
        nombre="Bomba de agua SEAT Ibiza 1.4 TDI",
        precio=Precio(Decimal("45.9"), "EUR", "45.9"),
        # La marca es la del producto principal, no «Meyle» del accesorio relacionado.
        marca="SKF",
        mpn="045121011H",
        sku="VKPC 81418",
        estado="Nuevo",
        disponible=True,
        descripcion="Bomba de agua con junta para motores 1.4 TDI (BNM, BNV).",
        imagenes=(
            f"{PRESTASHOP}/4581-large_default/bomba-agua-seat-ibiza-14-tdi.jpg",
            f"{PRESTASHOP}/4581-home_default/bomba-agua-seat-ibiza-14-tdi.jpg",
            f"{PRESTASHOP}/4582-home_default/bomba-agua-seat-ibiza-14-tdi.jpg",
        ),
        url=f"{PRESTASHOP}/refrigeracion/1532-bomba-agua-seat-ibiza-14-tdi.html",
    )


def test_shopify_con_og_price() -> None:
    producto = _fixture("ficha_shopify_og.html")
    base = "http://piezas-ejemplo.es/cdn/shop/files"
    assert producto == ProductoJSONLD(
        nombre="Filtro de aceite MANN-FILTER W 712/75",
        precio=Precio(Decimal("12.90"), "EUR", "12,90"),
        descripcion=(
            "Filtro de aceite roscado para motores VAG 1.9 TDI y 2.0 TDI. Referencia W 712/75."
        ),
        imagenes=(f"{base}/w712-75.jpg?v=1712345678", f"{base}/w712-75-caja.jpg?v=1712345679"),
        url="https://piezas-ejemplo.es/products/filtro-aceite-mann-w712-75",
    )


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<h1>Faro</h1><p>45,90 €</p>",
        # Una portada o un artículo de blog también llevan og:title y og:image.
        _og(og_type="website", og_title="Recambios Ejemplo", og_image="/logo.png"),
        _og(og_type="article", og_title="Cómo cambiar un faro"),
        # `itemprop` sin `itemscope` no es microdato (lo lee el perfil, si acaso).
        '<span itemprop="price" content="1.00">89,90 €</span>',
        _microdatos('<span itemprop="name">Inicio</span>', "http://schema.org/BreadcrumbList"),
        _microdatos('<span itemprop="name">Faro</span>', "https://otro.example/Product"),
    ],
)
def test_sin_producto(html: str) -> None:
    assert _producto(html) is None


def test_opengraph_completo() -> None:
    html = _og(
        og_type="product.item",
        og_title="Faro Golf VI",
        product_brand="Valeo",
        product_condition="used",
        product_availability="in stock",
        product_retailer_item_id="F-77123",
        product_mfr_part_no="5K1941005E",
        product_price_amount="100.00",
        product_sale_price_amount="89.90",
        product_price_currency="eur",
    )
    assert _producto(html) == ProductoJSONLD(
        nombre="Faro Golf VI",
        # El precio rebajado es el que paga el cliente.
        precio=Precio(Decimal("89.90"), "EUR", "89.90"),
        marca="Valeo",
        mpn="5K1941005E",
        sku="F-77123",
        estado="Usado",
        disponible=True,
    )


def test_opengraph_con_name_en_vez_de_property() -> None:
    html = '<meta name="og:type" content="product"><meta name="og:title" content="Faro">'
    html += '<meta name="product:price:amount" content="10">'
    producto = _producto(html)
    assert producto is not None
    assert producto.nombre == "Faro"
    assert producto.precio == Precio(Decimal("10"), "EUR", "10")


@pytest.mark.parametrize(
    ("valor", "disponible"),
    [
        ("instock", True),
        ("in stock", True),
        ("preorder", True),
        ("available for order", True),
        ("oos", False),
        ("out of stock", False),
        ("discontinued", False),
        ("https://schema.org/OutOfStock", False),
        ("quizás", None),
    ],
)
def test_disponibilidad_opengraph(valor: str, disponible: bool | None) -> None:
    producto = _producto(_og(og_type="product", og_title="X", product_availability=valor))
    assert producto is not None and producto.disponible is disponible


@pytest.mark.parametrize(
    ("valor", "estado"),
    [("new", "Nuevo"), ("used", "Usado"), ("refurbished", "Reacondicionado"), ("roto", None)],
)
def test_estado_opengraph(valor: str, estado: str | None) -> None:
    producto = _producto(_og(og_type="product", og_title="X", product_condition=valor))
    assert producto is not None and producto.estado == estado


@pytest.mark.parametrize(
    ("contenido", "importe", "moneda"),
    [
        ("45.9", "45.9", "EUR"),
        ("12,90", "12.90", "EUR"),
        ("1.234,56", "1234.56", "EUR"),
        ("1,234.56", "1234.56", "EUR"),
        ("12,90 €", "12.90", "EUR"),
        ("$15.00", "15.00", "USD"),
    ],
)
def test_formatos_de_precio_opengraph(contenido: str, importe: str, moneda: str) -> None:
    producto = _producto(_og(og_type="product", og_price_amount=contenido))
    assert producto is not None and producto.precio is not None
    assert (producto.precio.importe, producto.precio.moneda) == (Decimal(importe), moneda)


@pytest.mark.parametrize(
    "contenido",
    ["-5", "-5,00", "0", "0.00", "NaN", "nan", "Infinity", "1e309", "1E5", "99999999999999", "abc"],
)
def test_precios_absurdos_se_descartan(contenido: str) -> None:
    producto = _producto(_og(og_type="product", og_title="X", product_price_amount=contenido))
    assert producto is not None and producto.precio is None
    html = _microdatos(f'<span itemprop="name">X</span><span itemprop="price">{contenido}</span>')
    producto = _producto(html)
    assert producto is not None and producto.precio is None


def test_microdatos_precio_en_el_texto_y_marca_en_meta() -> None:
    html = _microdatos(
        '<h1 itemprop="name">Faro</h1><meta itemprop="brand" content="Hella">'
        '<div itemprop="offers" itemscope itemtype="http://schema.org/Offer">'
        '<span itemprop="price">1.234,56 €</span>'
        '<meta itemprop="availability" content="http://schema.org/OutOfStock">'
        '<meta itemprop="itemCondition" content="UsedCondition"></div>'
        '<meta itemprop="gtin13" content="4047023301250">',
        "http://schema.org/Product",
    )
    producto = _producto(html)
    assert producto == ProductoJSONLD(
        nombre="Faro",
        precio=Precio(Decimal("1234.56"), "EUR", "1.234,56 €"),
        marca="Hella",
        gtin="4047023301250",
        estado="Usado",
        disponible=False,
    )


def test_microdatos_marca_como_organizacion_y_precio_directo() -> None:
    html = _microdatos(
        '<span itemprop="name">Faro</span>'
        '<div itemprop="brand" itemscope itemtype="https://schema.org/Organization">'
        '<span itemprop="name">Valeo</span><span itemprop="url">https://valeo.example</span></div>'
        '<span itemprop="price" content="20">20 €</span>'
        '<meta itemprop="priceCurrency" content="GBP">'
    )
    producto = _producto(html)
    assert producto is not None
    assert producto.marca == "Valeo"
    # La URL de la marca no es la del producto.
    assert producto.url is None
    assert producto.precio == Precio(Decimal("20"), "GBP", "20")


def test_solo_una_oferta_sin_producto() -> None:
    html = _microdatos(
        '<span itemprop="price" content="33.50">33,50 €</span>'
        '<meta itemprop="priceCurrency" content="EUR">',
        "https://schema.org/Offer",
    )
    assert _producto(html) == ProductoJSONLD(precio=Precio(Decimal("33.50"), "EUR", "33.50"))


def test_varios_tipos_y_varias_propiedades_en_un_atributo() -> None:
    html = _microdatos(
        '<span itemprop="name alternateName">Faro</span>'
        '<img itemprop="image thumbnailUrl" src="/f1.jpg"><img itemprop="image" src="/f1.jpg">',
        "https://schema.org/IndividualProduct https://schema.org/Product",
    )
    producto = _producto(html)
    assert producto is not None
    assert producto.nombre == "Faro"
    assert producto.imagenes == ("/f1.jpg",)


def test_el_primer_producto_con_datos_es_el_principal() -> None:
    html = _microdatos("<p>Vacío</p>") + _microdatos(
        '<span itemprop="name">Este</span><span itemprop="price" content="5">5 €</span>'
    )
    producto = _producto(html)
    assert producto is not None and producto.nombre == "Este"


def test_microdatos_tienen_prioridad_y_opengraph_completa() -> None:
    html = _og(og_type="product", og_title="Título OG", product_brand="Marca OG")
    html += _og(og_description="Descripción OG", og_image="https://t.es/og.jpg")
    html += _microdatos('<span itemprop="name">Nombre microdatos</span>')
    producto = _producto(html)
    assert producto == ProductoJSONLD(
        nombre="Nombre microdatos",
        marca="Marca OG",
        descripcion="Descripción OG",
        imagenes=("https://t.es/og.jpg",),
    )


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "vbscript:x", ""]
)
def test_urls_peligrosas_se_ignoran(url: str) -> None:
    html = _og(og_type="product", og_title="X", og_url=url, og_image=url)
    html += _microdatos(f'<a itemprop="url" href="{url}">x</a><img itemprop="image" src="{url}">')
    producto = _producto(html)
    assert producto is not None
    assert producto.url is None
    assert producto.imagenes == ()


def test_valores_largos_se_recortan_y_se_limpian() -> None:
    html = _og(og_type="product", og_title="a" * 5_000, og_description="b " * 10_000)
    html += _og(product_retailer_item_id="c" * 500, product_brand="  Bosch\n\t &amp; Co ")
    producto = _producto(html)
    assert producto is not None
    assert producto.nombre is not None and len(producto.nombre) <= 300
    assert producto.descripcion is not None and len(producto.descripcion) <= 5_000
    assert producto.sku is not None and len(producto.sku) <= 100
    assert producto.marca == "Bosch & Co"


def test_muchas_imagenes_se_limitan() -> None:
    html = _og(og_type="product") + "".join(
        f'<meta property="og:image" content="https://t.es/{i}.jpg">' for i in range(500)
    )
    producto = _producto(html)
    assert producto is not None and len(producto.imagenes) == 50


def test_anidamiento_hostil_no_rompe_ni_tarda() -> None:
    # Más allá de 512 niveles `dom` cuelga los elementos del último admitido (y su texto, del
    # padre), así que el valor va en un atributo.
    profundo = "<div>" * 10_000 + '<meta itemprop="name" content="Fondo">' + "</div>" * 10_000
    inicio = time.perf_counter()
    producto = _producto(_microdatos(profundo))
    assert time.perf_counter() - inicio < 5
    assert producto is not None and producto.nombre == "Fondo"


def test_miles_de_ambitos_no_tardan() -> None:
    ambitos = _microdatos('<span itemprop="name">X</span>', "https://schema.org/Thing") * 20_000
    vacios = _microdatos("<i>sin datos</i>") * 20_000
    inicio = time.perf_counter()
    assert _producto(ambitos + vacios) is None
    assert time.perf_counter() - inicio < 5


# --- Revisión: productos que no son el de la ficha ------------------------------------------

DESGUACE = "https://desguaces-ejemplo.es"
FICHA_FARO = f"{DESGUACE}/faros/2210-faro-delantero-izquierdo-golf-vi.html"
_OFERTA = "https://schema.org/Offer"


def _oferta(precio: str) -> str:
    return (
        f'<div itemprop="offers" itemscope itemtype="{_OFERTA}">'
        f'<span itemprop="price" content="{precio}">{precio} €</span></div>'
    )


@pytest.mark.parametrize("url", [None, FICHA_FARO, f"{FICHA_FARO}?search_query=faro#/2-lado"])
def test_los_relacionados_no_son_el_producto_de_la_ficha(url: str | None) -> None:
    # La ficha principal no tiene microdatos y los del carrusel «Productos relacionados» sí:
    # tomarlos daría el precio de una bombilla (4,90 €) a un faro de 120 €. El canonical y el
    # og:url dicen qué página es, y las miniaturas apuntan a otra.
    html = (FIXTURES / "ficha_relacionados_microdatos.html").read_text(encoding="utf-8")
    assert producto_metadatos(analizar(html), url=url) is None


def test_el_accesorio_anidado_no_sustituye_al_principal_sin_datos() -> None:
    accesorio = (
        '<div itemprop="isAccessoryOrSparePartFor" itemscope itemtype="https://schema.org/Product">'
        f'<span itemprop="name">Bombilla H7</span>{_oferta("4.90")}</div>'
    )
    html = _microdatos(f'<h1>Faro</h1><meta itemprop="sku" content="F1">{accesorio}')
    producto = _producto(html)
    assert producto is None or (producto.nombre != "Bombilla H7" and producto.precio is None)


def test_la_oferta_de_un_relacionado_no_es_el_precio_de_la_ficha() -> None:
    html = _microdatos('<meta itemprop="sku" content="F1">')
    html += _microdatos(f'<a itemprop="url" href="/p/bombilla">x</a>{_oferta("4.90")}')
    relacionado = _microdatos('<meta itemprop="url" content="/p/termostato">' + _oferta("9.90"))
    html += f'<section class="relacionados"><article>{relacionado}</article></section>'
    producto = producto_metadatos(analizar(html), url="https://t.es/p/faro")
    assert producto is None or producto.precio is None


def test_la_oferta_suelta_sigue_valiendo() -> None:
    html = f'<div itemscope itemtype="{_OFERTA}"><span itemprop="price">45,90 €</span></div>'
    producto = producto_metadatos(analizar(html), url="https://t.es/p/faro")
    assert producto is not None and producto.precio == Precio(Decimal("45.90"), "EUR", "45,90 €")


def test_el_de_la_url_de_la_pagina_gana_aunque_no_sea_el_primero() -> None:
    visto = _microdatos(
        '<a itemprop="url" href="https://t.es/p/visto">x</a>'
        '<span itemprop="name">Visto antes</span>' + _oferta("3")
    )
    principal = _microdatos(
        '<meta itemprop="url" content="https://www.t.es/p/faro/"><span itemprop="name">Faro</span>'
        + _oferta("120")
    )
    producto = producto_metadatos(analizar(visto + principal), url="https://t.es/p/faro")
    assert producto is not None and producto.nombre == "Faro"


def test_url_del_ambito_distinta_pero_es_la_canonica() -> None:
    # PrestaShop servido como `index.php?id_product=…`: los microdatos traen la URL amigable, que
    # es la canónica; no es otro producto.
    canonica = f"{PRESTASHOP}/refrigeracion/1532-bomba-agua-seat-ibiza-14-tdi.html"
    html = f'<link rel="canonical" href="{canonica}">' + _microdatos(
        f'<meta itemprop="url" content="{canonica}"><span itemprop="name">Bomba</span>'
        + _oferta("45.9")
    )
    url = f"{PRESTASHOP}/index.php?id_product=1532&controller=product"
    producto = producto_metadatos(analizar(html), url=url)
    assert producto is not None and producto.nombre == "Bomba"


@pytest.mark.parametrize("contenido", ["1.500", "12.900", "999.000"])
def test_importe_de_maquina_con_tres_decimales_es_ambiguo(contenido: str) -> None:
    # Shopify con el formato «amount_no_decimals_with_comma_separator» escribe 1500 € como
    # «1.500»: leerlo como 1,50 € pondría la pieza mil veces más barata. No se adivina.
    assert leer_importe(contenido) is None
    producto = _producto(_og(og_type="product", og_title="X", og_price_amount=contenido))
    assert producto is not None and producto.precio is None


def test_importes_de_maquina_que_no_son_ambiguos() -> None:
    assert leer_importe("1500") == Precio(Decimal("1500"), "EUR", "1500")
    assert leer_importe("1.5") == Precio(Decimal("1.5"), "EUR", "1.5")
    assert leer_importe("1500.000") == Precio(Decimal("1500.000"), "EUR", "1500.000")
    assert leer_importe(Decimal("1.095")) == Precio(Decimal("1.095"), "EUR", "1.095")


def test_nombre_y_marca_en_un_enlace_son_su_texto_y_no_la_url() -> None:
    # Caso real (webscraper.io): `<a href="/…/product/31" itemprop="name">Packard 255 G2</a>`.
    # Por la regla de los microdatos el valor de un `<a>` es su `href`, pero `name` y `brand`
    # son texto: una ruta no es el nombre de nada.
    html = _microdatos(
        '<a href="/p/31" itemprop="name">Faro Golf</a>'
        '<a href="/marca/hella" itemprop="brand">Hella</a>'
        '<div itemprop="brand" itemscope itemtype="https://schema.org/Brand">'
        '<a href="/marca/valeo" itemprop="name">Valeo</a></div>'
    )
    producto = _producto(html)
    assert producto is not None
    assert (producto.nombre, producto.marca) == ("Faro Golf", "Hella")
    html = _microdatos(
        '<img src="/faro.jpg" itemprop="name"><span itemprop="name">Faro Golf</span>'
        '<div itemprop="brand" itemscope itemtype="https://schema.org/Brand">'
        '<a href="/marca/valeo" itemprop="name">Valeo</a></div>'
    )
    producto = _producto(html)
    assert producto is not None
    # Una imagen no tiene texto: no da nombre, y se pasa a la siguiente propiedad.
    assert (producto.nombre, producto.marca) == ("Faro Golf", "Valeo")
    # Con `content`, manda `content` como en cualquier otro elemento.
    html = _microdatos('<a href="/p/31" itemprop="name" content="Faro Golf VI">Faro</a>')
    producto = _producto(html)
    assert producto is not None
    assert producto.nombre == "Faro Golf VI"
