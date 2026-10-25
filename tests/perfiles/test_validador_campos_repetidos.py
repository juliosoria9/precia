"""Dos selectores de campo que leen el mismo valor no dan un perfil «bueno» (R2-CAMPOS).

Causa R2-CAMPOS de la campaña multi-sector (ronda 2): en el perfil sitemap de PcComponentes la
IA propuso como marca `div.group-h0XKFq span.identifier-M7hbzz:first-of-type`, que lee el mismo
span que `referencia_oem` («P/N: SP634452»; la marca es otro span). El validador solo contaba si
cada campo encontraba algo («marca 3/3») y el perfil se guardaba con esa marca falsa.

Ahora el validador apunta el campo de menos prioridad (orden de `nucleo.modelos.CAMPOS`) en
`campos_repetidos`, con un problema que lo explica, sin que el perfil deje de ser válido; y el
configurador y el reparador lo quitan de `sel_campos` antes de guardar el perfil. Las fichas de
PcComponentes son recortes de las reales (`tests/fixtures/reales/`); la tienda de recambios es
la falsa de `tests/perfiles/sitio.py`. Sin red.
"""

import asyncio
from pathlib import Path
from typing import Any

from precia.ia.simulado import Guion
from precia.perfiles.configurador import COMPONENTE as CONFIGURADOR
from precia.perfiles.configurador import configurar
from precia.perfiles.reparador import COMPONENTE as REPARADOR
from precia.perfiles.reparador import reparar
from precia.perfiles.tipos import TIPO_HTML, TIPO_SITEMAP, Perfil
from precia.perfiles.validador import resumir, sin_campos_repetidos, validar
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    INSTANTE,
    LISTADO_OK,
    PRODUCTOS,
    TIENDA,
    CatalogoFalso,
    NavegadorFalso,
    html_listado,
    pagina,
    paginas_sitio,
    perfil,
    perfil_sitemap,
    sesion,
    xml_indice,
    xml_urlset,
)

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
PCC = "https://www.pccomponentes.com"
PHS = f"{PCC}/memoria-ram-16gb-phs-memory-ddr5-5600mhz-1x16gb-so-dimm-compatible-portatil"
ADATA = f"{PCC}/memoria-ram-adata-csodimm-16gb-ddr5-6400mhz-cl52-1-4v-para-portatil"
SEL_PCC = {
    "precio": "#pdp-price-current-container",
    "referencia_oem": "#pdp-mpn",
    "marca": "div.group-h0XKFq span.identifier-M7hbzz:first-of-type",
}
"""Los selectores que propuso la IA en la ronda 2 (sin descripción ni fotos)."""
MARCA_REPETIDA = {**CAMPOS_OK, "marca": "td.ref"}
"""En la tienda de recambios: la marca lee la celda de la referencia."""
PROBLEMA_RECAMBIOS = "sel_campos[marca] lee lo mismo que sel_campos[referencia_oem]"
SIN_MARCA = {clave: selector for clave, selector in CAMPOS_OK.items() if clave != "marca"}


def _real(nombre: str) -> str:
    return (REALES / nombre).read_bytes().decode("utf-8")


def _pccomponentes() -> NavegadorFalso:
    return NavegadorFalso(
        {
            PHS: _real("pccomponentes_ficha_marca_igual_referencia_phs.html"),
            ADATA: _real("pccomponentes_ficha_marca_igual_referencia_adata.html"),
        }
    )


def _perfil_pcc() -> Perfil:
    return perfil_sitemap(
        dominio="pccomponentes.com", url_busqueda=f"{PCC}/sitemap.xml", sel_campos=SEL_PCC
    )


def _validar(
    p: Perfil, navegador: NavegadorFalso, consulta: str = "memoria ram", **opciones: Any
) -> Any:
    return asyncio.run(
        validar(p, navegador, consulta, validador=validar_con_dns_publico, **opciones)
    )


# --- Validador ----------------------------------------------------------------------------------


def test_la_marca_de_pccomponentes_que_lee_la_referencia_se_apunta_y_no_cuenta() -> None:
    informe = _validar(_perfil_pcc(), _pccomponentes(), fichas=[PHS, ADATA])
    assert informe.valido, informe.problemas
    assert (informe.fichas_ok, informe.fichas_total) == (2, 2)
    assert informe.campos_repetidos == ("marca",)
    assert informe.campos_encontrados == {"precio": 2, "referencia_oem": 2}
    assert (
        "sel_campos[marca] lee lo mismo que sel_campos[referencia_oem] en las 2 fichas "
        "(«P/N: SP634452»): se quita."
    ) in informe.problemas
    # No se confunde con un selector que no encuentra nada.
    assert not any("no encuentra nada" in problema for problema in informe.problemas)
    assert "marca 2/2" not in resumir(informe)


def test_sin_campos_repetidos_quita_la_clave_del_perfil() -> None:
    informe = _validar(_perfil_pcc(), _pccomponentes(), fichas=[PHS, ADATA])
    limpio = sin_campos_repetidos(_perfil_pcc(), informe)
    assert limpio.sel_campos == {"precio": SEL_PCC["precio"], "referencia_oem": "#pdp-mpn"}
    assert limpio.url_busqueda == _perfil_pcc().url_busqueda


def test_recambios_con_selectores_distintos_no_se_toca_nada() -> None:
    informe = _validar(perfil(), NavegadorFalso(paginas_sitio()), CONSULTA)
    assert informe.valido, informe.problemas
    assert informe.campos_repetidos == ()
    assert informe.campos_encontrados["marca"] == 3
    assert not any("lee lo mismo" in problema for problema in informe.problemas)
    assert sin_campos_repetidos(perfil(), informe) == perfil()


def _ficha_datos(referencia: str, marca: str, importe: str) -> str:
    return (
        f"<html><head><title>Faro {referencia}</title></head><body><h1>Faro {referencia}</h1>"
        f"<span class='precio'>{importe} €</span><table><tr><td class='ref'>{referencia}</td>"
        f"</tr><tr><td class='marca'>{marca}</td></tr></table></body></html>"
    )


def test_si_en_alguna_ficha_lee_un_valor_propio_el_selector_se_queda() -> None:
    # En una ficha la marca repite la referencia (allí la ficha la descarta), en otra no: el
    # selector puede ser bueno y la página rara; no se quita del perfil.
    fichas = [f"{TIENDA}/p/uno", f"{TIENDA}/p/dos"]
    navegador = NavegadorFalso(
        {
            fichas[0]: _ficha_datos("X12345", "X12345", "10,00"),
            fichas[1]: _ficha_datos("Y67890", "Valeo", "12,00"),
        }
    )
    sel = {"precio": "span.precio", "referencia_oem": "td.ref", "marca": "td.marca"}
    informe = _validar(perfil_sitemap(sel_campos=sel), navegador, fichas=fichas)
    assert informe.valido, informe.problemas
    assert informe.campos_repetidos == ()
    assert informe.campos_encontrados["marca"] == 1


# --- Configurador y reparador -------------------------------------------------------------------


def _configurar(navegador: NavegadorFalso, guion: list[Guion], **opciones: Any) -> Perfil:
    sesion_ia, _ = sesion({CONFIGURADOR: guion})
    return asyncio.run(
        configurar(
            f"www.{DOMINIO}",
            navegador,
            sesion_ia,
            CONSULTA,
            validador=validar_con_dns_publico,
            reloj=lambda: INSTANTE,
            **opciones,
        )
    )


def test_el_configurador_guarda_el_perfil_html_sin_la_marca_repetida() -> None:
    nuevo = _configurar(NavegadorFalso(paginas_sitio()), [LISTADO_OK, MARCA_REPETIDA])
    assert nuevo.tipo == TIPO_HTML
    assert nuevo.sel_campos == SIN_MARCA
    assert PROBLEMA_RECAMBIOS in nuevo.nota


def test_el_configurador_guarda_el_perfil_sitemap_sin_la_marca_repetida() -> None:
    # Sin buscador ni JSON-LD: la IA propone los selectores de la ficha del sitemap.
    indice = f"{TIENDA}/sitemaps/indice.xml"
    productos = f"{TIENDA}/sitemaps/product-sitemap.xml.gz"
    sitio = paginas_sitio(formulario=False, busqueda=None, jsonld=())
    sitio[indice] = xml_indice([productos])
    sitio[productos] = xml_urlset([p.url for p in PRODUCTOS])
    navegador = NavegadorFalso(sitio, declarados=(indice,))
    nuevo = _configurar(navegador, [{"plantillas": []}, MARCA_REPETIDA], catalogo=CatalogoFalso())
    assert nuevo.tipo == TIPO_SITEMAP
    assert nuevo.sel_campos == SIN_MARCA
    assert PROBLEMA_RECAMBIOS in nuevo.nota


def test_el_reparador_devuelve_el_perfil_sin_la_marca_repetida() -> None:
    roto = perfil(sel_resultado="div.resultado", sel_titulo="span.t", sel_enlace="a.v @href")
    sesion_ia, _ = sesion({REPARADOR: [{**LISTADO_OK, "sel_campos": MARCA_REPETIDA}]})
    nuevo = asyncio.run(
        reparar(
            roto,
            pagina(BUSQUEDA, html_listado()),
            NavegadorFalso(paginas_sitio()),
            sesion_ia,
            CONSULTA,
            validador=validar_con_dns_publico,
            reloj=lambda: INSTANTE,
        )
    )
    assert nuevo.sel_campos == SIN_MARCA
    assert PROBLEMA_RECAMBIOS in nuevo.nota
