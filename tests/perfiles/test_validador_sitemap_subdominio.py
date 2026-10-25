"""Un perfil `sitemap` cuyo sitemap raíz está en un subdominio de la tienda (causa R5-B).

Xtralife declara en su robots.txt `https://static.xtralife.com/sitemap.xml`. Desde R4-B ese
fichero se descarga (`fuentes.sitemap.url_de_sitemap`), pero el validador solo admitía el
dominio y `www.` y dejaba el perfil inválido. La regla tiene que ser la misma en los dos
sitios: la tienda o un subdominio estricto suyo, nunca otro dominio.
"""

import asyncio
from pathlib import Path

import pytest

from precia.fuentes.sitemap import leer_sitemap, url_de_sitemap
from precia.perfiles.validador import problemas_de_url_sitemap, validar
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import DOMINIO, PRODUCTOS, NavegadorFalso, paginas_sitio, perfil_sitemap

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
FICHAS = [p.url for p in PRODUCTOS]


def _declarado_de_xtralife() -> str:
    robots = (REALES / "xtralife_robots_sitemap_subdominio.txt").read_text(encoding="utf-8")
    return robots.strip().splitlines()[-1].removeprefix("Sitemap: ")


def test_el_sitemap_declarado_por_xtralife_en_static_vale() -> None:
    declarado = _declarado_de_xtralife()
    assert declarado == "https://static.xtralife.com/sitemap.xml"
    # Lo que se descarga (R4-B) tiene que valer también como raíz del perfil.
    assert url_de_sitemap(declarado, "https://www.xtralife.com") is not None
    assert problemas_de_url_sitemap(declarado, "xtralife.com") == []
    assert problemas_de_url_sitemap(declarado, "www.xtralife.com") == []


@pytest.mark.parametrize(
    "url",
    [
        "https://xtralife.com/sitemap.xml",
        "https://www.xtralife.com/sitemap.xml",
        "https://static.xtralife.com/sitemap.xml",
        "http://cdn.static.xtralife.com/sitemap_index.xml",
        "https://static.xtralife.com:443/sitemap.xml",
    ],
)
def test_la_tienda_o_un_subdominio_suyo(url: str) -> None:
    assert problemas_de_url_sitemap(url, "xtralife.com") == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evilxtralife.com/sitemap.xml",
        "https://evil-xtralife.com/sitemap.xml",
        "https://xtralife.com.evil.com/sitemap.xml",
        "https://static.xtralife.com.evil.com/sitemap.xml",
        "https://usuario@static.xtralife.com/sitemap.xml",
        "https://static.xtralife.com:8443/sitemap.xml",
        "ftp://static.xtralife.com/sitemap.xml",
        "https://static.xtralife.com/sitemap.xml#x",
        "https://static.xtralife.com/sitemap.xml?q={consulta}",
        "//static.xtralife.com/sitemap.xml",
        "/sitemap.xml",
        "https://.xtralife.com/sitemap.xml",
    ],
)
def test_otro_dominio_sigue_rechazado(url: str) -> None:
    assert problemas_de_url_sitemap(url, "xtralife.com") != []


def test_los_hijos_del_indice_real_en_static_son_de_la_tienda() -> None:
    indice = leer_sitemap(
        (REALES / "xtralife_sitemap_indice_subdominio.xml").read_text(encoding="utf-8")
    )
    hijos = [e.url for e in indice.entradas]
    assert hijos == [f"https://static.xtralife.com/sitemapproducts-{n}.xml" for n in (1, 4, 5)]
    assert all(problemas_de_url_sitemap(h, "xtralife.com") == [] for h in hijos)


def test_un_perfil_sitemap_con_raiz_en_un_subdominio_es_valido() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None, jsonld=(0, 1, 2)))
    p = perfil_sitemap(url_busqueda=f"https://static.{DOMINIO}/sitemap.xml")
    informe = asyncio.run(
        validar(p, navegador, "faro golf", validador=validar_con_dns_publico, fichas=FICHAS)
    )
    assert informe.valido, informe.problemas
