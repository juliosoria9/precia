"""Catálogo de una tienda cuyos ficheros sitemap están en un subdominio suyo (causa R5-B).

Xtralife sirve su índice y sus hijos en `static.xtralife.com`, mientras que las fichas están en
`www.xtralife.com`. El almacén aceptaba solo el dominio y `www.` también para el sitemap y los
hijos pendientes: `guardar_catalogo` y `ampliar_catalogo` lanzaban `DatoNoValido`. Los ficheros
sitemap admiten la tienda o un subdominio estricto suyo (como `fuentes.sitemap.url_de_sitemap`);
las URL de las fichas siguen exigiendo el host de la tienda.
"""

from pathlib import Path
from typing import Any

import pytest

from precia.almacen.sqlite import Almacen, DatoNoValido
from precia.fuentes.sitemap import leer_sitemap

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
DOMINIO = "xtralife.com"
SITEMAP = "https://static.xtralife.com/sitemap.xml"
FICHA = (
    "https://www.xtralife.com/producto/cristal-templado-con-filtro-de-luz-azul-switch-2/107107",
    "producto cristal templado con filtro de luz azul switch 2",
    None,
)


@pytest.fixture
def con_tienda(almacen: Almacen) -> Almacen:
    almacen.alta_tienda(DOMINIO, "Xtralife")
    return almacen


def _hijos_reales() -> list[str]:
    texto = (REALES / "xtralife_sitemap_indice_subdominio.xml").read_text(encoding="utf-8")
    return [e.url for e in leer_sitemap(texto).entradas]


def test_guardar_y_ampliar_con_sitemap_e_hijos_en_static(con_tienda: Almacen) -> None:
    leido, *pendientes = _hijos_reales()
    info = con_tienda.guardar_catalogo(
        DOMINIO, SITEMAP, [FICHA], parcial=True, pendientes=[leido, *pendientes]
    )
    assert info.sitemap == SITEMAP and info.total == 1
    assert con_tienda.hijos_pendientes(DOMINIO) == [leido, *pendientes]
    # Ida y vuelta: el hijo pendiente se lee en una búsqueda y se amplía el catálogo (R2-C14b).
    otra = ("https://www.xtralife.com/producto/mando-pro-switch-2/107200", "producto mando", None)
    nuevo = "https://static.xtralife.com/sitemapproducts-6.xml"
    info = con_tienda.ampliar_catalogo(DOMINIO, [leido], [otra], parcial=True, pendientes=[nuevo])
    assert info.total == 2
    assert con_tienda.hijos_pendientes(DOMINIO) == [*pendientes, nuevo]


def test_la_tienda_con_www_y_sin_el_sigue_valiendo(con_tienda: Almacen) -> None:
    for sitemap in ("https://xtralife.com/sitemap.xml", "https://www.xtralife.com/s.xml"):
        con_tienda.guardar_catalogo(DOMINIO, sitemap, [FICHA], parcial=False, pendientes=[sitemap])
        assert con_tienda.hijos_pendientes(DOMINIO) == [sitemap]


MALAS = [
    "https://evilxtralife.com/sitemap.xml",
    "https://xtralife.com.evil.com/sitemap.xml",
    "https://static.xtralife.com.evil.com/sitemap.xml",
    "https://usuario@static.xtralife.com/sitemap.xml",
    "https://static.xtralife.com:8443/sitemap.xml",
    "https://.xtralife.com/sitemap.xml",
    "ftp://static.xtralife.com/sitemap.xml",
    "https://static.xtralife.com/sitemap.xml\n",
    "https://static.xtralife.com\\@evil.com/sitemap.xml",
    "/sitemap.xml",
    42,
]


@pytest.mark.parametrize("malo", MALAS)
def test_el_sitemap_de_otro_dominio_sigue_rechazado(con_tienda: Almacen, malo: Any) -> None:
    with pytest.raises(DatoNoValido):
        con_tienda.guardar_catalogo(DOMINIO, malo, [FICHA], parcial=False)
    with pytest.raises(DatoNoValido):
        con_tienda.guardar_catalogo(DOMINIO, SITEMAP, [FICHA], parcial=True, pendientes=[malo])
    assert con_tienda.catalogo(DOMINIO) is None


@pytest.mark.parametrize("malo", MALAS)
def test_ampliar_con_un_hijo_de_otro_dominio_sigue_rechazado(
    con_tienda: Almacen, malo: Any
) -> None:
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, [FICHA], parcial=True)
    with pytest.raises(DatoNoValido):
        con_tienda.ampliar_catalogo(DOMINIO, [malo], [], parcial=True)
    with pytest.raises(DatoNoValido):
        con_tienda.ampliar_catalogo(DOMINIO, [], [], parcial=True, pendientes=[malo])


def test_una_ficha_en_el_subdominio_sigue_siendo_dato_no_valido(con_tienda: Almacen) -> None:
    # Solo los ficheros sitemap pueden estar en un subdominio: las fichas, no.
    ficha = ("https://static.xtralife.com/producto/mando/1", "producto mando", None)
    with pytest.raises(DatoNoValido):
        con_tienda.guardar_catalogo(DOMINIO, SITEMAP, [ficha], parcial=False)
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, [FICHA], parcial=False)
    with pytest.raises(DatoNoValido):
        con_tienda.ampliar_catalogo(DOMINIO, [], [ficha], parcial=False)
    info = con_tienda.catalogo(DOMINIO)
    assert info is not None and info.total == 1
