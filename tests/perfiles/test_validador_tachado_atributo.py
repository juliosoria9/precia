"""El validador no tira un perfil bueno por un PVP tachado marcado con `data-test` (R2-B4).

Causa R2-B4 de la campaña multi-sector (ronda 2): en la búsqueda de MediaMarkt el PVP tachado de
cada tarjeta va en `<div data-test="mms-strike-price-type-rrp">`, sin clase ni id que lo delaten.
`dom.tachado` no lo reconocía, el listado leía el tachado (39,90 € y 34,95 €) en vez del vigente
(29,99 € y 30,99 €), el validador daba por mal 2 de 3 fichas («el selector puede estar leyendo el
precio de otro producto») y el perfil HTML, con selectores correctos, no valía. La búsqueda es el
recorte real (`tests/fixtures/reales/mediamarkt_busqueda_filtro_tarjetas.html`); las fichas son
sintéticas y mínimas, con el precio de las reales en el JSON-LD. Sin red.
"""

import asyncio
import json
import re
from pathlib import Path

from precia.perfiles.validador import validar
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import NavegadorFalso, perfil

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
TIENDA = "https://www.mediamarkt.es"
BUSQUEDA = f"{TIENDA}/es/search.html?query=filtro"
VIGENTES = ("29.99", "30.99", "26.99")
"""Precio de las tres fichas reales de la ronda 2 (JSON-LD de `05_…`, `06_…` y `07_…`)."""


def _busqueda() -> str:
    return (REALES / "mediamarkt_busqueda_filtro_tarjetas.html").read_bytes().decode("utf-8")


def _ficha(url: str, n: int, importe: str) -> str:
    producto = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": f"Filtro de agua {n}",
        "offers": {"@type": "Offer", "url": url, "priceCurrency": "EUR", "price": importe},
    }
    return (
        f"<html><head><title>Filtro de agua {n}</title>"
        f'<script type="application/ld+json">{json.dumps(producto)}</script></head>'
        f"<body><h1>Filtro de agua {n}</h1><span class='precio'>{importe.replace('.', ',')} €"
        "</span></body></html>"
    )


def test_perfil_html_de_mediamarkt_con_pvp_tachado_en_data_test_vale() -> None:
    fichas = [TIENDA + r for r in re.findall(r'href="(/es/product/[^"]+)"', _busqueda())]
    assert len(fichas) == len(VIGENTES)
    sitio = {BUSQUEDA: _busqueda()}
    sitio.update({u: _ficha(u, n, VIGENTES[n]) for n, u in enumerate(fichas)})
    p = perfil(
        dominio="mediamarkt.es",
        url_busqueda=f"{TIENDA}/es/search.html?query={{consulta}}",
        sel_resultado="[data-test=mms-product-card]",
        sel_titulo="h3",
        sel_enlace="a[data-test=mms-router-link-product-list-item-link] @href",
        sel_precio_listado="[data-test=mms-price]",
        sel_campos={"precio": "span.precio"},
    )
    informe = asyncio.run(
        validar(p, NavegadorFalso(sitio), "filtro", validador=validar_con_dns_publico)
    )
    assert not any("no coincide" in problema for problema in informe.problemas), informe.problemas
    assert (informe.fichas_ok, informe.fichas_total) == (3, 3)
    assert informe.valido, informe.problemas
