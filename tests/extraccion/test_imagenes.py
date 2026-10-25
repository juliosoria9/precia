"""Validación de la URL de la foto principal: solo https, de la tienda o de una CDN conocida.

La foto se enlaza (la pide el navegador del usuario), nunca se descarga: la URL viene de una web no
confiable y tiene que ser segura de pintar en un `<img src>`.
"""

import pytest

from precia.extraccion.dom import analizar
from precia.extraccion.imagenes import (
    CDN_CONOCIDAS,
    MAX_URL_FOTO,
    anclada_en_html,
    foto_de_tarjeta,
    foto_valida,
    primera_foto,
)
from precia.extraccion.selectores import seleccionar

BASE = "https://www.tienda.es/p/faro-golf"


@pytest.mark.parametrize(
    "valor",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:image/png;base64,iVBORw0KGgo=",
        "blob:https://www.tienda.es/1234",
        "vbscript:msgbox",
        "file:///etc/passwd",
        "http://www.tienda.es/img/faro.jpg",
        "https://user@www.tienda.es/img/faro.jpg",
        "https://user:clave@www.tienda.es/img/faro.jpg",
        "https://www.tienda.es:8443/img/faro.jpg",
        "https://192.168.1.1/faro.jpg",
        "https://[::1]/faro.jpg",
        "https://8.8.8.8/faro.jpg",
        "https://tienda.local/faro.jpg",
        "https://router.lan/faro.jpg",
        "https://otra.example/faro.jpg",
        "https://tienda.es.evil.com/faro.jpg",
        "https://eviltienda.es/faro.jpg",
        "https://cdn..tienda.es/faro.jpg",
        "https://www.tienda.es/img/faro con espacio.jpg",
        'https://www.tienda.es/img/faro".jpg',
        "https://www.tienda.es/img/<faro>.jpg",
        "https://www.tienda.es/img/faro\n.jpg",
        "https://www.tienda.es/img/faro\x00.jpg",
        "https://www.tienda.es/" + "a" * MAX_URL_FOTO + ".jpg",
        "https://www.tienda.es/img/placeholder.gif",
        "https://www.tienda.es/img/logo.svg",
        "https://www.tienda.es/img/faro.svg",
        "https://www.tienda.es/img/no-image.png",
        "https://www.tienda.es/img/spinner-loading.gif",
        "https://www.tienda.es/mu/catalog/img-not-available.png",
        "https://www.tienda.es/img/sin-foto.jpg",
        "https://www.tienda.es/mu/suppliers/manufacturer/jpg/0030001.jpg",
        "https://www.tienda.es/img/brands/valeo.png",
        "https://www.tienda.es/themes/webscraper-logo-header.png",
        "https://[abc/faro.jpg",
        "",
        "   ",
    ],
)
def test_rechaza_urls_peligrosas_o_ajenas(valor: str) -> None:
    assert foto_valida(valor, base=BASE) is None


@pytest.mark.parametrize("valor", [None, 12, ["https://www.tienda.es/a.jpg"]])
def test_rechaza_lo_que_no_es_texto(valor: object) -> None:
    assert foto_valida(valor, base=BASE) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("valor", "esperada"),
    [
        ("/img/faro.jpg", "https://www.tienda.es/img/faro.jpg"),
        ("img/faro.jpg", "https://www.tienda.es/p/img/faro.jpg"),
        ("//www.tienda.es/img/faro.jpg", "https://www.tienda.es/img/faro.jpg"),
        ("https://tienda.es/img/faro.jpg", "https://tienda.es/img/faro.jpg"),
        ("https://cdn.tienda.es/a.webp", "https://cdn.tienda.es/a.webp"),
        ("https://static.img.tienda.es/a.jpg", "https://static.img.tienda.es/a.jpg"),
        ("https://WWW.Tienda.ES/img/Faro.JPG", "https://www.tienda.es/img/Faro.JPG"),
        ("https://www.tienda.es:443/img/faro.jpg", "https://www.tienda.es/img/faro.jpg"),
        ("https://www.tienda.es/img/faro.jpg#zoom", "https://www.tienda.es/img/faro.jpg"),
        (
            "https://www.tienda.es/img/faro.jpg?v=12&w=800",
            "https://www.tienda.es/img/faro.jpg?v=12&w=800",
        ),
        ("https://www.tienda.es/img/faro-ñ.jpg", "https://www.tienda.es/img/faro-%C3%B1.jpg"),
        ("  https://www.tienda.es/img/faro.jpg  ", "https://www.tienda.es/img/faro.jpg"),
        # Las palabras de marcador se miran enteras: «silicona» no es «icon».
        (
            "https://www.tienda.es/img/tubo-silicona.jpg",
            "https://www.tienda.es/img/tubo-silicona.jpg",
        ),
        # La misma marca con otro dominio de primer nivel (oscaro.es → oscaro.media).
        ("https://tienda.media/catalog/faro.jpg", "https://tienda.media/catalog/faro.jpg"),
    ],
)
def test_acepta_y_normaliza(valor: str, esperada: str) -> None:
    assert foto_valida(valor, base=BASE) == esperada


def test_cdn_conocidas() -> None:
    assert "cdn.shopify.com" in CDN_CONOCIDAS
    url = "//cdn.shopify.com/s/files/1/a.jpg?v=1"
    assert foto_valida(url, base=BASE) == "https://cdn.shopify.com/s/files/1/a.jpg?v=1"
    # Una subcadena o un subdominio de una CDN conocida no valen: la lista es de hosts exactos.
    assert foto_valida("https://evil.cdn.shopify.com.example/a.jpg", base=BASE) is None


def test_la_misma_marca_solo_con_dominios_de_primer_nivel_habituales() -> None:
    base = "https://www.oscaro.es/x"
    assert foto_valida("https://oscaro.media/a.jpg", base=base) == "https://oscaro.media/a.jpg"
    assert foto_valida("https://img.oscaro.com/a.jpg", base=base) == "https://img.oscaro.com/a.jpg"
    # Dominios que cualquiera registra por poco o nada: un píxel de rastreo con la marca.
    assert foto_valida("https://oscaro.tk/p.gif", base=base) is None
    assert foto_valida("https://track.oscaro.xyz/p.jpg", base=base) is None


def test_photon_solo_con_la_foto_de_la_propia_tienda() -> None:
    base = "https://www.tienda.es/p/1"
    buena = "https://i0.wp.com/www.tienda.es/wp-content/uploads/faro.jpg?w=800"
    assert foto_valida(buena, base=base) == buena
    sin_www = "https://i1.wp.com/tienda.es/wp-content/uploads/faro.jpg"
    assert foto_valida(sin_www, base=base) == sin_www
    # Photon sirve cualquier URL de origen: otra web no vale.
    assert foto_valida("https://i0.wp.com/evil.com/pixel.jpg", base=base) is None
    assert foto_valida("https://i2.wp.com/tienda.es.evil.com/a.jpg", base=base) is None
    assert foto_valida("https://i0.wp.com/", base=base) is None


def test_la_base_tiene_que_ser_https_o_http_valida() -> None:
    assert foto_valida("/img/faro.jpg", base="") is None
    assert foto_valida("/img/faro.jpg", base="javascript:alert(1)") is None
    # Una ficha servida por http: la foto relativa queda en http, y no se acepta.
    assert foto_valida("/img/faro.jpg", base="http://www.tienda.es/p/1") is None
    assert foto_valida("https://www.tienda.es/a.jpg", base="http://www.tienda.es/p/1") == (
        "https://www.tienda.es/a.jpg"
    )


def test_primera_foto_valida() -> None:
    candidatas = [None, "javascript:x", "https://otra.example/a.jpg", "/img/b.jpg", "/img/c.jpg"]
    assert primera_foto(candidatas, base=BASE) == "https://www.tienda.es/img/b.jpg"
    assert primera_foto([], base=BASE) is None


def _tarjeta(html: str) -> object:
    (nodo,) = seleccionar(analizar(html), "div.tarjeta")
    return nodo


def test_foto_de_tarjeta_con_lazy_load() -> None:
    nodo = _tarjeta(
        '<div class="tarjeta"><a href="/p/1"><img src="/img/placeholder.gif"'
        ' data-src="/img/faro.jpg" alt="Faro"></a><h3>Faro</h3></div>'
    )
    assert foto_de_tarjeta(nodo, base=BASE) == "https://www.tienda.es/img/faro.jpg"  # type: ignore[arg-type]


def test_foto_de_tarjeta_srcset_primer_candidato() -> None:
    nodo = _tarjeta('<div class="tarjeta"><img srcset="/img/a.jpg 1x, /img/b.jpg 2x"></div>')
    assert foto_de_tarjeta(nodo, base=BASE) == "https://www.tienda.es/img/a.jpg"  # type: ignore[arg-type]


def test_foto_de_tarjeta_picture_source() -> None:
    nodo = _tarjeta(
        '<div class="tarjeta"><picture><source srcset="/img/a.webp 400w">'
        '<img src="/img/a.jpg"></picture></div>'
    )
    assert foto_de_tarjeta(nodo, base=BASE) == "https://www.tienda.es/img/a.webp"  # type: ignore[arg-type]


def test_foto_de_tarjeta_salta_insignias_y_ajenas() -> None:
    nodo = _tarjeta(
        '<div class="tarjeta"><img src="/img/logo-envio.png">'
        '<img src="https://pixel.example/p.gif"><img src="/img/faro.jpg"></div>'
    )
    assert foto_de_tarjeta(nodo, base=BASE) == "https://www.tienda.es/img/faro.jpg"  # type: ignore[arg-type]


def test_foto_de_tarjeta_sin_imagen() -> None:
    nodo = _tarjeta('<div class="tarjeta"><h3>Faro</h3></div>')
    assert foto_de_tarjeta(nodo, base=BASE) is None  # type: ignore[arg-type]


def test_foto_de_tarjeta_el_propio_img() -> None:
    (nodo,) = seleccionar(analizar('<img class="f" data-src="/img/a.jpg">'), "img.f")
    assert foto_de_tarjeta(nodo, base=BASE) == "https://www.tienda.es/img/a.jpg"


@pytest.mark.parametrize(
    ("crudo", "html"),
    [
        ("https://www.tienda.es/a.jpg", '<img src="https://www.tienda.es/a.jpg">'),
        ("/a.jpg?v=1&w=2", '<img src="/a.jpg?v=1&amp;w=2">'),
        ("https://www.tienda.es/a.jpg", '{"image":"https:\\/\\/www.tienda.es\\/a.jpg"}'),
    ],
)
def test_anclada_en_html(crudo: str, html: str) -> None:
    assert anclada_en_html(crudo, html)


def test_no_anclada_en_html() -> None:
    assert not anclada_en_html("https://www.tienda.es/a.jpg", "<p>Faro</p>")
    assert not anclada_en_html("", "<p>Faro</p>")


@pytest.mark.parametrize(
    "fichero",
    [
        # Caso real (DLI, ronda 8): el marcador de WooCommerce de la tarjeta del listado.
        "wp-content/uploads/2020/02/imagen_no_disponible.jpg",
        "img/no-disponible.jpg",
        "img/image-not-found.jpg",
        "img/not-found-image.png",
        "img/default-product.jpg",
        "img/product-default.png",
        "img/imagen-producto-no-disponible.webp",
        "img/foto_no_disponible_300x300.jpg",
        "img/no_image_available.png",
        "img/image-placeholder-product.jpg",
        "img/productplaceholder.png",
        "img/woocommerce-placeholder-600x600.png",
        "img/noimagen.jpg",
        "img/imagenotfound.jpg",
        "img/sin_imagen.jpg",
        "img/imagen-sin.jpg",
    ],
)
def test_marcadores_por_palabras_en_cualquier_orden(fichero: str) -> None:
    assert foto_valida(f"https://www.tienda.es/{fichero}", base=BASE) is None


@pytest.mark.parametrize(
    "fichero",
    [
        # Un nombre de producto largo con «sin» o «no» no es un marcador.
        "img/gasolina-sin-plomo-bidon-5-litros-foto-frontal.jpg",
        "img/tubo-no-retorno-gasoil.jpg",
        "img/faro-default.jpg",
        "img/disponible-faro-golf.jpg",
    ],
)
def test_no_confunde_productos_con_marcadores(fichero: str) -> None:
    assert foto_valida(f"https://www.tienda.es/{fichero}", base=BASE) is not None


def test_foto_de_tarjeta_que_es_marcador_no_se_usa() -> None:
    nodo = _tarjeta(
        '<div class="tarjeta"><a href="/p/1"><img'
        ' src="https://www.tienda.es/wp-content/uploads/2020/02/imagen_no_disponible.jpg">'
        "</a><h3>Puerta</h3></div>"
    )
    assert foto_de_tarjeta(nodo, base=BASE) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "fichero",
    [
        # Revisión: fotos de producto de nombre corto con una negación y una palabra de imagen.
        # Un marcador solo tiene palabras de marcador; «marco», «cristal» o «fondo» no lo son.
        "img/marco-fotos-sin-cristal-20x30.jpg",
        "img/foto-sin-marco.jpg",
        "img/no-photo-frame.jpg",
        "img/imagen-sin-fondo.jpg",
        "img/camiseta-algodon-sin-imagen-estampada-azul-talla-xl.jpg",
        "img/funda-movil-not-available-in-stores.jpg",
    ],
)
def test_no_confunde_fotos_de_producto_con_una_negacion_y_una_imagen(fichero: str) -> None:
    assert foto_valida(f"https://www.tienda.es/{fichero}", base=BASE) is not None


@pytest.mark.parametrize(
    "fichero",
    [
        "img/not-available.png",
        "img/no-image-available-300x300-v2.png",
        "img/the-image-is-not-available.jpg",
    ],
)
def test_marcadores_solo_con_palabras_de_marcador(fichero: str) -> None:
    assert foto_valida(f"https://www.tienda.es/{fichero}", base=BASE) is None
