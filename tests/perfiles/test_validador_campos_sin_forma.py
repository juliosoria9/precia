"""Un selector de campo que lee algo sin forma de ese campo no da un perfil «bueno» (R3-E).

Causa R3-E de la campaña multi-sector (ronda 3): en Dia la IA propuso como marca
`div.manufacturer-info`, que lee el bloque «Información del responsable Envasado para DIA Retail
España, S.A.U. C/ …», y en Zalando, como referencia `dd.voFjEy:nth-of-type(1)`, que lee el
primer <dd> de los atributos, «100% algodón». El validador solo contaba si cada campo leía algo
(«referencia_oem 3/3») y los perfiles se guardaban con esos datos falsos.

Ahora `extraer_ficha` descarta el valor del perfil sin forma de su campo, el validador apunta el
campo en `campos_sin_forma` con un problema que lo cita (sin restar validez) si en ninguna ficha
leyó un valor con forma, y el configurador y el reparador lo quitan de `sel_campos` antes de
guardar el perfil. Las fichas de Dia y Zalando son recortes de las reales (`tests/fixtures/
reales/`); la tienda de recambios es la falsa de `tests/perfiles/sitio.py`. Sin red.
"""

import asyncio
from pathlib import Path
from typing import Any

from precia.ia.simulado import Guion
from precia.perfiles.configurador import COMPONENTE as CONFIGURADOR
from precia.perfiles.configurador import configurar
from precia.perfiles.reparador import COMPONENTE as REPARADOR
from precia.perfiles.reparador import reparar
from precia.perfiles.tipos import TIPO_HTML, Perfil
from precia.perfiles.validador import resumir, sin_campos_repetidos, validar
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    INSTANTE,
    LISTADO_OK,
    TIENDA,
    NavegadorFalso,
    html_listado,
    pagina,
    paginas_sitio,
    perfil,
    perfil_sitemap,
    sesion,
)

REALES = Path(__file__).resolve().parent.parent / "fixtures" / "reales"
ZALANDO = "https://www.zalando.es"
NIKE = f"{ZALANDO}/nike-sportswear-club-tee-camiseta-basica-ni122o0ce-a12.html"
BOSS = f"{ZALANDO}/boss-classic-3-pack-camiseta-interior-open-miscellaneous-bb182n034-a11.html"
SEL_ZALANDO = {
    "precio": ".voFjEy.VrucAJ",
    "referencia_oem": "dd.voFjEy:nth-of-type(1)",
    "marca": "[data-testid=product_title-brand-name]",
}
"""Los selectores que propuso la IA en la ronda 3."""
DIA = "https://www.dia.es/aceites-salsas-y-especias/aceites/p/112529"
SEL_DIA = {
    "precio": "p.buy-box__active-price",
    "marca": "div.manufacturer-info",
    "descripcion": "div.instructions-info",
}
REFERENCIA_SIN_FORMA = {**CAMPOS_OK, "referencia_oem": "td.marca"}
"""En la tienda de recambios: la referencia lee la celda de la marca («Valeo», «Hella»)."""
PROBLEMA_RECAMBIOS = "sel_campos[referencia_oem] lee «Valeo»"
SIN_REFERENCIA = {c: s for c, s in CAMPOS_OK.items() if c != "referencia_oem"}


def _real(nombre: str) -> str:
    return (REALES / nombre).read_bytes().decode("utf-8")


def _zalando() -> NavegadorFalso:
    return NavegadorFalso(
        {
            NIKE: _real("zalando_ficha_referencia_lee_material_nike.html"),
            BOSS: _real("zalando_ficha_referencia_lee_material_boss.html"),
        }
    )


def _perfil_zalando() -> Perfil:
    return perfil_sitemap(
        dominio="zalando.es", url_busqueda=f"{ZALANDO}/sitemap.xml", sel_campos=SEL_ZALANDO
    )


def _validar(
    p: Perfil, navegador: NavegadorFalso, consulta: str = "camiseta", **opciones: Any
) -> Any:
    return asyncio.run(
        validar(p, navegador, consulta, validador=validar_con_dns_publico, **opciones)
    )


# --- Validador ----------------------------------------------------------------------------------


def test_la_referencia_de_zalando_que_lee_el_material_se_apunta_y_no_cuenta() -> None:
    informe = _validar(_perfil_zalando(), _zalando(), fichas=[NIKE, BOSS])
    assert informe.valido, informe.problemas
    assert (informe.fichas_ok, informe.fichas_total) == (2, 2)
    assert informe.campos_sin_forma == ("referencia_oem",)
    assert informe.campos_repetidos == ()
    assert informe.campos_encontrados == {"precio": 2, "marca": 2}
    assert (
        "sel_campos[referencia_oem] lee «100% algodón» en las 2 fichas, que no tiene forma de "
        "referencia: se quita."
    ) in informe.problemas
    # No se confunde con un selector que no encuentra nada.
    assert not any("no encuentra nada" in problema for problema in informe.problemas)
    assert "referencia_oem" not in resumir(informe).partition("Problemas:")[0]


def test_la_marca_de_dia_que_lee_el_bloque_del_responsable_se_apunta() -> None:
    navegador = NavegadorFalso({DIA: _real("dia_ficha_marca_bloque_responsable.html")})
    p = perfil_sitemap(
        dominio="dia.es", url_busqueda="https://www.dia.es/sitemap.xml", sel_campos=SEL_DIA
    )
    informe = _validar(p, navegador, "aceite de oliva", fichas=[DIA])
    assert informe.valido, informe.problemas
    assert informe.campos_sin_forma == ("marca",)
    assert any(
        problema.startswith("sel_campos[marca] lee «Información del responsable Envasado")
        and problema.endswith("en la ficha, que no tiene forma de marca: se quita.")
        for problema in informe.problemas
    ), informe.problemas


def test_sin_campos_repetidos_quita_tambien_los_campos_sin_forma() -> None:
    informe = _validar(_perfil_zalando(), _zalando(), fichas=[NIKE, BOSS])
    limpio = sin_campos_repetidos(_perfil_zalando(), informe)
    assert limpio.sel_campos == {
        "precio": SEL_ZALANDO["precio"],
        "marca": SEL_ZALANDO["marca"],
    }


def test_recambios_con_selectores_buenos_no_se_toca_nada() -> None:
    informe = _validar(perfil(), NavegadorFalso(paginas_sitio()), CONSULTA)
    assert informe.valido, informe.problemas
    assert informe.campos_sin_forma == ()
    assert informe.campos_encontrados["referencia_oem"] == 3
    assert informe.campos_encontrados["marca"] == 3
    assert not any("forma de" in problema for problema in informe.problemas)


def _ficha_datos(referencia: str, importe: str) -> str:
    return (
        f"<html><head><title>Faro</title></head><body><h1>Faro</h1>"
        f"<span class='precio'>{importe} €</span><table><tr><td class='ref'>{referencia}</td>"
        "</tr></table></body></html>"
    )


def test_si_en_alguna_ficha_lee_un_valor_con_forma_el_selector_se_queda() -> None:
    # En una ficha la celda no trae la referencia (allí se descarta), en otra sí: el selector
    # puede ser bueno y la página rara; no se quita del perfil.
    fichas = [f"{TIENDA}/p/uno", f"{TIENDA}/p/dos"]
    navegador = NavegadorFalso(
        {
            fichas[0]: _ficha_datos("Consultar", "10,00"),
            fichas[1]: _ficha_datos("1K0 698 151 A", "12,00"),
        }
    )
    sel = {"precio": "span.precio", "referencia_oem": "td.ref"}
    informe = _validar(perfil_sitemap(sel_campos=sel), navegador, fichas=fichas)
    assert informe.valido, informe.problemas
    assert informe.campos_sin_forma == ()
    assert informe.campos_encontrados["referencia_oem"] == 1


# --- Configurador y reparador -------------------------------------------------------------------


def _configurar(navegador: NavegadorFalso, guion: list[Guion]) -> Perfil:
    sesion_ia, _ = sesion({CONFIGURADOR: guion})
    return asyncio.run(
        configurar(
            f"www.{DOMINIO}",
            navegador,
            sesion_ia,
            CONSULTA,
            validador=validar_con_dns_publico,
            reloj=lambda: INSTANTE,
        )
    )


def test_el_configurador_guarda_el_perfil_sin_la_referencia_sin_forma() -> None:
    nuevo = _configurar(NavegadorFalso(paginas_sitio()), [LISTADO_OK, REFERENCIA_SIN_FORMA])
    assert nuevo.tipo == TIPO_HTML
    assert nuevo.sel_campos == SIN_REFERENCIA
    assert PROBLEMA_RECAMBIOS in nuevo.nota


def test_el_reparador_devuelve_el_perfil_sin_la_referencia_sin_forma() -> None:
    roto = perfil(sel_resultado="div.resultado", sel_titulo="span.t", sel_enlace="a.v @href")
    sesion_ia, _ = sesion({REPARADOR: [{**LISTADO_OK, "sel_campos": REFERENCIA_SIN_FORMA}]})
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
    assert nuevo.sel_campos == SIN_REFERENCIA
    assert PROBLEMA_RECAMBIOS in nuevo.nota
