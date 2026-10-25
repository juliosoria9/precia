"""amazon.es, encontrado por Julio al probar la app: «No se encuentra la página de búsqueda».

Tras el desafío anti-bot de AWS WAF, Chromium (por el proxy) capturó la portada a medias: solo el
`<head>`, con el estado 202 del desafío (`amazon_portada_202_solo_cabecera_chromium.html`). De
ella no sale ninguna candidata. De las rutas habituales, `/?s=filtro&post_type=product` devolvió
la portada completa (`amazon_portada_completa_buscador_chromium.html`), con su buscador, pero:

1. Su campo de búsqueda no se reconocía: es `name="field-keywords"`, `type="text"`, con
   `role="searchbox"` dentro de un `<form role="search">`; ni `type=search` ni un nombre típico.
2. El buscador de una página de la tienda descargada después de la portada no se aprovechaba:
   solo se miraba si era un listado.

Nada de esto depende del dominio: los tests de abajo usan también formularios sintéticos.
"""

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlencode

import pytest

from precia.navegador.tipos import Pagina
from precia.perfiles.descubrimiento import (
    COMPONENTE,
    ORIGEN_FORMULARIO,
    Busqueda,
    abrir_portada,
    descubrir,
    plantillas_de_formularios,
    plantillas_sin_accion,
)
from precia.perfiles.tipos import ConfiguracionFallida
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import NavegadorFalso, pagina, sesion

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
A_MEDIAS = (REALES / "amazon_portada_202_solo_cabecera_chromium.html").read_text(encoding="utf-8")
COMPLETA = (REALES / "amazon_portada_completa_buscador_chromium.html").read_text(encoding="utf-8")

DOMINIO = "amazon.es"
TIENDA = f"https://www.{DOMINIO}"
PORTADA = f"{TIENDA}/"
CONSULTA = "filtro"
WOO = f"{TIENDA}/?s=filtro&post_type=product"
OCULTO = urlencode([("__mk_es_ES", "ÅMÅŽÕÑ")])
PLANTILLA = f"{TIENDA}/s/ref=nb_sb_noss?{OCULTO}&field-keywords={{consulta}}"
BUSQUEDA = f"{TIENDA}/s/ref=nb_sb_noss?{OCULTO}&field-keywords=filtro"
RESULTADOS = f"{TIENDA}/s?k=filtro"

DOMINIO_X = "tienda-prueba.es"
TIENDA_X = f"https://www.{DOMINIO_X}"


def listado() -> str:
    """Resultados sintéticos: no hay HTML de resultados de amazon.es guardado."""
    tarjetas = "".join(
        f'<div class="tarjeta"><h2><a href="/filtro-agua-{n}/dp/B0{n}">Filtro de agua {n}</a>'
        f'</h2><span class="precio">{n},99 €</span></div>'
        for n in range(1, 5)
    )
    return f"<html><head><title>Amazon.es : filtro</title></head><body>{tarjetas}</body></html>"


@dataclass
class NavegadorRescatado(NavegadorFalso):
    """Como el navegador real con la tienda rescatada: todo llega de Chromium, con un 202."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        resultado = await super().obtener(url, js=js, grande=grande)
        estado = 202 if resultado.estado == 200 else resultado.estado
        return replace(resultado, estado=estado, por_chromium=True)


def descubrir_en(navegador: NavegadorFalso, host: str, dominio: str) -> Busqueda:
    async def probar() -> Busqueda:
        portada = await abrir_portada(host, dominio, navegador, validador=validar_con_dns_publico)
        return await descubrir(
            portada, host, dominio, navegador, CONSULTA, validador=validar_con_dns_publico
        )

    return asyncio.run(probar())


# --- 1. El campo de búsqueda por su rol ---------------------------------------------------------


def test_el_buscador_de_la_portada_completa_da_su_plantilla() -> None:
    assert plantillas_de_formularios(pagina(WOO, COMPLETA), DOMINIO) == [PLANTILLA]


@pytest.mark.parametrize(
    ("formulario", "campo"),
    [
        # Un campo de texto con `role="searchbox"`, con cualquier nombre.
        (
            '<form action="/b"><input type="text" name="palabras" role="searchbox"></form>',
            "palabras",
        ),
        # El primer campo de texto de un `<form role="search">`.
        (
            '<form action="/b" role="search"><input type="hidden" name="x" value="1">'
            '<input name="palabras"><input type="submit" name="ir" value="Ir"></form>',
            "palabras",
        ),
        # En un `<form role="search">`, el campo con un nombre típico va antes que el primero.
        (
            '<form action="/b" role="search"><input type="text" name="palabras">'
            '<input type="text" name="q"></form>',
            "q",
        ),
    ],
    ids=["searchbox", "form-search", "nombre-tipico-primero"],
)
def test_campo_de_busqueda_por_su_rol(formulario: str, campo: str) -> None:
    html = f"<html><body>{formulario}</body></html>"
    plantillas = plantillas_de_formularios(pagina(f"{TIENDA_X}/", html), DOMINIO_X)
    assert len(plantillas) == 1
    assert plantillas[0].startswith(f"{TIENDA_X}/b?")
    assert plantillas[0].endswith(f"{campo}={{consulta}}")


@pytest.mark.parametrize(
    "formulario",
    [
        # Por POST o con contraseña (un login) no es la búsqueda, aunque diga `role="search"`.
        '<form action="/b" method="post" role="search"><input name="palabras"></form>',
        '<form action="/b" role="search"><input name="usuario">'
        '<input type="password" name="clave"></form>',
        # Un campo oculto, de correo o un botón no son el campo de búsqueda.
        '<form action="/b" role="search"><input type="hidden" name="h" value="1">'
        '<input type="email" name="correo"><input type="submit" name="ir"></form>',
        # Sin rol ni nombre típico, un campo de texto no es la búsqueda (un boletín, un cupón).
        '<form action="/b"><input type="text" name="cupon"></form>',
    ],
    ids=["post", "contrasena", "sin-texto", "sin-rol"],
)
def test_el_rol_no_convierte_en_busqueda_otros_formularios(formulario: str) -> None:
    html = f"<html><body>{formulario}</body></html>"
    assert plantillas_de_formularios(pagina(f"{TIENDA_X}/", html), DOMINIO_X) == []


def test_formulario_sin_accion_con_searchbox_prueba_las_rutas_de_busqueda() -> None:
    html = '<html><body><form><input name="palabras" role="searchbox"></form></body></html>'
    sin_accion = plantillas_sin_accion(pagina(f"{TIENDA_X}/", html), DOMINIO_X)
    assert sin_accion
    assert all(p.endswith("?palabras={consulta}") for p in sin_accion)


# --- 2. El buscador de una página de la tienda descargada después -------------------------------


def test_amazon_se_configura_con_el_buscador_de_la_portada_completa() -> None:
    # Como en el servidor de Julio: `https://amazon.es/` acaba en la portada a medias, de las
    # rutas habituales solo `/?s=…&post_type=product` responde (con la portada completa) y la
    # búsqueda del formulario redirige a `/s?k=`.
    navegador = NavegadorRescatado(
        {PORTADA: A_MEDIAS, WOO: COMPLETA, RESULTADOS: listado()},
        paginas_js={},
        redirecciones={f"https://{DOMINIO}/": PORTADA, BUSQUEDA: RESULTADOS},
    )
    busqueda = descubrir_en(navegador, DOMINIO, DOMINIO)
    assert busqueda.plantilla == PLANTILLA
    assert busqueda.origen == ORIGEN_FORMULARIO
    # Sale del buscador de la página de `WOO`, así que solo puede ir detrás de ella (el orden
    # respecto a las demás candidatas lo prueba `test_..._van_detras_de_todas_las_candidatas`).
    assert navegador.urls.index(BUSQUEDA) > navegador.urls.index(WOO)
    assert navegador.urls.count(BUSQUEDA) == 1


def test_sin_buscador_en_ninguna_pagina_sigue_fallando_igual() -> None:
    navegador = NavegadorRescatado(
        {PORTADA: A_MEDIAS, WOO: A_MEDIAS},
        paginas_js={},
        redirecciones={f"https://{DOMINIO}/": PORTADA},
    )
    with pytest.raises(ConfiguracionFallida):
        descubrir_en(navegador, DOMINIO, DOMINIO)


def test_el_buscador_de_otras_paginas_se_aprovecha_una_sola_vez() -> None:
    # Cada página de la tienda trae un buscador distinto (y ninguno da resultados): solo se
    # prueban los de la primera, para que una tienda no multiplique las descargas.
    def con_buscador(ruta: str) -> str:
        formulario = f'<form action="/{ruta}" role="search"><input name="t"></form>'
        return f"<html><body>{formulario}</body></html>"

    navegador = NavegadorFalso(
        {
            f"{TIENDA_X}/": "<html><head></head></html>",
            f"{TIENDA_X}/buscar?q=filtro": con_buscador("uno"),
            f"{TIENDA_X}/search?q=filtro": con_buscador("dos"),
        }
    )
    with pytest.raises(ConfiguracionFallida):
        descubrir_en(navegador, f"www.{DOMINIO_X}", DOMINIO_X)
    assert f"{TIENDA_X}/uno?t=filtro" in navegador.urls
    assert f"{TIENDA_X}/dos?t=filtro" not in navegador.urls


def test_sin_resultados_la_ia_ve_el_buscador_y_no_la_portada_a_medias() -> None:
    # Si la plantilla del formulario no da resultados, la IA recibe el resumen de la página con
    # el buscador: el de la portada a medias no tiene ninguna pista y no se le preguntaba.
    navegador = NavegadorRescatado(
        {PORTADA: A_MEDIAS, WOO: COMPLETA},
        paginas_js={},
        redirecciones={f"https://{DOMINIO}/": PORTADA},
    )
    sesion_ia, proveedor = sesion({COMPONENTE: [{"plantillas": []}]})

    async def probar() -> None:
        portada = await abrir_portada(
            DOMINIO, DOMINIO, navegador, validador=validar_con_dns_publico
        )
        await descubrir(
            portada,
            DOMINIO,
            DOMINIO,
            navegador,
            CONSULTA,
            sesion=sesion_ia,
            validador=validar_con_dns_publico,
        )

    with pytest.raises(ConfiguracionFallida):
        asyncio.run(probar())
    assert len(proveedor.peticiones) == 1
    assert "field-keywords" in str(proveedor.peticiones[0].datos)


def test_las_plantillas_de_otra_pagina_van_detras_de_todas_las_candidatas() -> None:
    # Una ruta habitual temprana devuelve una página con un buscador, y la última da los
    # resultados: la tienda se configura con la ruta habitual, como antes del arreglo, sin probar
    # el buscador de esa página (iría al final de la lista, no detrás de la página que lo trae).
    buscador = '<form action="/uno" role="search"><input name="t"></form>'
    navegador = NavegadorFalso(
        {
            f"{TIENDA_X}/": "<html><head></head></html>",
            f"{TIENDA_X}/buscar?q=filtro": f"<html><body>{buscador}</body></html>",
            f"{TIENDA_X}/buscar/filtro": listado(),
        }
    )
    busqueda = descubrir_en(navegador, f"www.{DOMINIO_X}", DOMINIO_X)
    assert busqueda.plantilla == f"{TIENDA_X}/buscar/{{consulta}}"
    assert f"{TIENDA_X}/uno?t=filtro" not in navegador.urls


# --- 3. El rol no impide renderizar la portada --------------------------------------------------


def test_un_buscador_marcado_solo_por_su_rol_no_impide_renderizar_la_portada() -> None:
    # Encontrado por un revisor: la portada por HTTP tiene un `<form role="search">` sin `action`
    # y con un nombre de campo poco típico (el JavaScript intercepta el envío); la renderizada,
    # el formulario de verdad. Antes del criterio por rol esa portada no daba candidatas y se
    # renderizaba; con él, la plantilla por rol bastaba para no mirar nunca la renderizada.
    http = '<html><body><form role="search"><input name="palabras"></form></body></html>'
    renderizada = (
        '<html><body><form action="/resultados"><input type="search" name="kw"></form>'
        "</body></html>"
    )
    navegador = NavegadorFalso(
        {f"{TIENDA_X}/": http, f"{TIENDA_X}/resultados?kw=filtro": listado()},
        paginas_js={f"{TIENDA_X}/": renderizada},
    )
    busqueda = descubrir_en(navegador, f"www.{DOMINIO_X}", DOMINIO_X)
    assert busqueda.plantilla == f"{TIENDA_X}/resultados?kw={{consulta}}"
    assert navegador.pedidas == [
        (f"{TIENDA_X}/", False),
        (f"{TIENDA_X}/", True),
        (f"{TIENDA_X}/resultados?kw=filtro", False),
    ]


def test_el_buscador_por_su_rol_se_prueba_si_la_renderizada_no_da_nada() -> None:
    # Si la portada renderizada no trae nada mejor, la plantilla por rol de la portada por HTTP
    # se sigue probando (es la de amazon.es cuando la portada llega completa).
    http = (
        '<html><body><form action="/b" role="search"><input name="palabras"></form></body></html>'
    )
    navegador = NavegadorFalso(
        {f"{TIENDA_X}/": http, f"{TIENDA_X}/b?palabras=filtro": listado()},
        paginas_js={f"{TIENDA_X}/": http},
    )
    busqueda = descubrir_en(navegador, f"www.{DOMINIO_X}", DOMINIO_X)
    assert busqueda.plantilla == f"{TIENDA_X}/b?palabras={{consulta}}"
