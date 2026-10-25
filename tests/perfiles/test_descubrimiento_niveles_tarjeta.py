"""Causa R4-G de la campaña multi-sector: hasta dónde se sube desde el enlace para buscar el precio.

`_enlaces_con_precio` sube por los ancestros de cada enlace buscando un precio en su «tarjeta», y
para en un bloque grande (`MAX_TEXTO_CONTENEDOR`), en uno que enlaza a otras páginas o al llegar a
`NIVELES_CONTENEDOR`. En la búsqueda de El Corte Inglés por Chromium (`/search-nwx/?s=filtro`) el
título va en `div.product_preview-ppal > div.title > div.product_preview-info > h2 > a` y el precio
en el hermano `div.product_preview-buy`: hay que llegar al quinto contenedor contando el propio
enlace, y con 4 la búsqueda real se descartaba (0 enlaces con precio).

El límite de niveles sí hace de guarda: con 6, en una ficha de Coolmod el enlace de marca
(`/fabricante/coolmod/`) llega al bloque del precio del producto principal sin cruzar ningún otro
enlace ni pasar de 600 caracteres, y sale como un producto más. Por eso 5 y no 6.
"""

from pathlib import Path

from precia.extraccion.dom import analizar
from precia.navegador.tipos import Pagina
from precia.perfiles.descubrimiento import _enlaces_con_precio, parece_listado
from tests.perfiles.sitio import pagina

REALES = Path(__file__).parent.parent / "fixtures" / "reales"

ECI = "elcorteingles.es"
ECI_TIENDA = f"https://www.{ECI}"
ECI_BUSQUEDA = f"{ECI_TIENDA}/search-nwx/?s=filtro"
ECI_HTML = (REALES / "elcorteingles_busqueda_tarjeta_cinco_niveles.html").read_text(
    encoding="utf-8"
)
ECI_BRITA = (
    f"{ECI_TIENDA}/hogar/A46834528-4006387121884-pr-pack-6-filtros-de-agua-maxtra-pro-pure"
    "-performance-brita-blanco/"
)
ECI_PHILIPS = (
    f"{ECI_TIENDA}/hogar/A40304301-4897099308423-pr-pack-filtro-micro-x-clean-5-1-philips"
    "-water-solutions-blanco/"
)

COOLMOD = "coolmod.com"
COOLMOD_FICHA = f"https://www.{COOLMOD}/cable-y-molex-grande/"
COOLMOD_HTML = (REALES / "coolmod_ficha_marca_seis_niveles.html").read_text(encoding="utf-8")

TIENDA = "https://www.tienda-prueba.es"
DOMINIO = "tienda-prueba.es"


def productos(p: Pagina, dominio: str) -> dict[str, str]:
    return _enlaces_con_precio(analizar(p.html), p, dominio)


def tarjeta_profunda(ruta: str, nombre: str, precio: str) -> str:
    """Una tarjeta con la forma de la de El Corte Inglés: el precio, a 5 niveles del enlace."""
    return (
        '<li class="item"><div class="ppal"><div class="title"><div class="info">'
        f'<h2><a href="{ruta}">{nombre}</a></h2></div></div>'
        f'<div class="buy"><span class="price">{precio}</span></div></div></li>'
    )


# --- La búsqueda real de El Corte Inglés ---------------------------------------------------------


def test_el_corte_ingles_cada_tarjeta_da_su_enlace_con_su_precio() -> None:
    encontrados = productos(pagina(ECI_BUSQUEDA, ECI_HTML), ECI)
    assert set(encontrados) == {ECI_BRITA, ECI_PHILIPS}
    assert "29,36 €" in encontrados[ECI_BRITA]
    assert "Brita" in encontrados[ECI_BRITA]
    assert "17,46 €" in encontrados[ECI_PHILIPS]
    assert "29,36" not in encontrados[ECI_PHILIPS]


def test_el_corte_ingles_la_tarjeta_con_variantes_sigue_sin_precio() -> None:
    # La tercera tarjeta (perfumería) enlaza a cinco variantes de color: `_enlaza_a_otras` la
    # corta antes del precio, igual que antes. Subir niveles no salta esa guarda.
    encontrados = productos(pagina(ECI_BUSQUEDA, ECI_HTML), ECI)
    assert not any("/perfumeria/" in url for url in encontrados)


def test_el_corte_ingles_la_busqueda_real_pasa_el_modo_estricto() -> None:
    resultados = pagina(ECI_BUSQUEDA, ECI_HTML)
    assert parece_listado(resultados, ECI, "filtro", portada=None, estricto=True)


# --- Tarjetas hermanas con la misma profundidad --------------------------------------------------


def test_tarjetas_hermanas_profundas_cada_precio_con_su_enlace() -> None:
    rejilla = tarjeta_profunda("/p/filtro-aceite", "Filtro de aceite", "12,50 €") + (
        tarjeta_profunda("/p/filtro-aire", "Filtro de aire", "30,00 €")
    )
    html = f"<html><head><title>Filtro</title></head><body><ul>{rejilla}</ul></body></html>"
    resultados = pagina(f"{TIENDA}/buscar?q=filtro", html)
    encontrados = productos(resultados, DOMINIO)
    assert encontrados == {
        f"{TIENDA}/p/filtro-aceite": "Filtro de aceite 12,50 €",
        f"{TIENDA}/p/filtro-aire": "Filtro de aire 30,00 €",
    }
    assert parece_listado(resultados, DOMINIO, "filtro", portada=None, estricto=True)


def test_el_precio_a_seis_niveles_del_enlace_no_cuenta() -> None:
    # Un nivel más que la tarjeta de El Corte Inglés: queda fuera del límite.
    tarjeta = (
        '<li><div class="a"><div class="ppal"><div class="title"><div class="info">'
        '<h2><a href="/p/filtro-aceite">Filtro de aceite</a></h2></div></div></div>'
        '<div class="buy"><span>12,50 €</span></div></div></li>'
    )
    html = f"<html><body><ul>{tarjeta}</ul></body></html>"
    assert productos(pagina(f"{TIENDA}/buscar?q=filtro", html), DOMINIO) == {}


# --- Guarda: el enlace de marca de una ficha (Coolmod) -------------------------------------------


def test_coolmod_el_enlace_de_marca_de_la_ficha_no_se_lleva_el_precio() -> None:
    # Con 6 niveles, `/fabricante/coolmod/` salía con «Marca: COOLMOD Y Molex Grande - Cable …
    # 1 95 €» (350 caracteres, ningún otro enlace en medio). Este test fija que el límite es 5.
    ficha = pagina(COOLMOD_FICHA, COOLMOD_HTML)
    assert productos(ficha, COOLMOD) == {}
    assert not parece_listado(ficha, COOLMOD, "cable", portada=None)
