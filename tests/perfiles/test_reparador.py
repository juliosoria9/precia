"""IA · Reparador: propone una versión nueva del perfil roto y solo la devuelve si valida."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.simulado import Guion
from precia.ia.tipos import PeticionIA, RespuestaIA, SalidaNoValida
from precia.navegador.tipos import Bloqueado
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS
from precia.perfiles.reparador import AUTOR, COMPONENTE, reparar
from precia.perfiles.tipos import Perfil, ReparacionFallida
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    INYECCION,
    LISTADO_OK,
    PLANTILLA,
    PRODUCTOS,
    NavegadorFalso,
    html_listado,
    pagina,
    paginas_sitio,
    perfil,
    sesion,
)

AHORA = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
# El perfil de antes del rediseño de la tienda: ya no encuentra nada.
ROTO = perfil(
    version=3,
    sel_resultado="div.resultado",
    sel_titulo="span.titulo",
    sel_enlace="a.ver @href",
    sel_precio_listado="b.pvp",
    sel_campos={"precio": "b.pvp-ficha", "marca": "span.fabricante"},
    autor="manual",
    revisado=True,
    nota="Hecho a mano",
)
ACTUAL = pagina(BUSQUEDA, html_listado())
PROPUESTA: dict[str, Any] = {**LISTADO_OK, "sel_campos": CAMPOS_OK}


def ejecutar(
    guion: list[Guion], navegador: NavegadorFalso | None = None, base: Perfil = ROTO
) -> tuple[Perfil, list[PeticionIA], NavegadorFalso]:
    navegador = navegador or NavegadorFalso(paginas_sitio())
    sesion_ia, proveedor = sesion({COMPONENTE: guion})
    nuevo = asyncio.run(
        reparar(
            base,
            ACTUAL,
            navegador,
            sesion_ia,
            CONSULTA,
            validador=validar_con_dns_publico,
            reloj=lambda: AHORA,
        )
    )
    return nuevo, proveedor.peticiones, navegador


def fallo(
    guion: list[Guion], navegador: NavegadorFalso | None = None
) -> tuple[str, NavegadorFalso]:
    navegador = navegador or NavegadorFalso(paginas_sitio())
    sesion_ia, _ = sesion({COMPONENTE: guion})
    with pytest.raises(ReparacionFallida) as error:
        asyncio.run(
            reparar(ROTO, ACTUAL, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
        )
    return str(error.value), navegador


def test_repara_y_sube_la_version() -> None:
    nuevo, peticiones, navegador = ejecutar([PROPUESTA])
    assert nuevo == Perfil(
        dominio=DOMINIO,
        version=4,
        url_busqueda=PLANTILLA,
        sel_resultado=LISTADO_OK["sel_resultado"],
        sel_titulo=LISTADO_OK["sel_titulo"],
        sel_enlace=LISTADO_OK["sel_enlace"],
        sel_precio_listado=LISTADO_OK["sel_precio_listado"],
        sel_campos=CAMPOS_OK,
        necesita_js=False,
        autor=AUTOR,
        revisado=False,
        creado=AHORA,
        nota=nuevo.nota,
    )
    assert AUTOR == "reparador IA"
    assert nuevo.nota.startswith("Válido")
    assert len(peticiones) == 1
    # Validado de verdad: búsqueda y fichas.
    assert navegador.urls == [BUSQUEDA] + [p.url for p in PRODUCTOS[:3]]


def test_peticion_separa_instrucciones_de_datos_web() -> None:
    _, (peticion,), _ = ejecutar([PROPUESTA])
    assert peticion.componente == COMPONENTE
    assert INYECCION not in peticion.instrucciones
    assert PRODUCTOS[0].titulo not in peticion.instrucciones
    assert "recambios-prueba" not in peticion.instrucciones
    assert ":nth-of-type" in peticion.instrucciones
    # El perfil viejo y la página actual van en los datos.
    assert "div.resultado" in peticion.datos
    assert "span.fabricante" in peticion.datos
    assert '<li class="producto">' in peticion.datos
    assert INYECCION in peticion.datos


def test_esquema_estricto() -> None:
    _, (peticion,), _ = ejecutar([PROPUESTA])
    esquema = peticion.esquema
    assert esquema["additionalProperties"] is False
    assert esquema["required"] == [
        "sel_resultado",
        "sel_titulo",
        "sel_enlace",
        "sel_precio_listado",
        "sel_campos",
    ]
    campos = esquema["properties"]["sel_campos"]
    assert campos["additionalProperties"] is False
    assert list(campos["properties"]) == [c.clave for c in CAMPOS]
    assert campos["required"] == [c.clave for c in CAMPOS]
    # La IA no puede proponer otra URL de búsqueda: no está en el esquema.
    assert "url_busqueda" not in esquema["properties"]


def test_conserva_la_configuracion_que_no_es_de_selectores() -> None:
    sitio = paginas_sitio()
    base = replace(ROTO, necesita_js=True)
    nuevo, _, navegador = ejecutar([PROPUESTA], NavegadorFalso(sitio, paginas_js=sitio), base)
    assert nuevo.necesita_js
    assert nuevo.url_busqueda == ROTO.url_busqueda
    assert all(js for _, js in navegador.pedidas)


def test_los_nulos_no_entran() -> None:
    propuesta = {
        **PROPUESTA,
        "sel_campos": {**dict.fromkeys(CLAVES_CAMPOS), "precio": ".precio-ficha"},
    }
    nuevo, _, _ = ejecutar([propuesta])
    assert nuevo.sel_campos == {"precio": ".precio-ficha"}


@pytest.mark.parametrize(
    "malo",
    [
        {**PROPUESTA, "sel_resultado": "li.producto:nth-child(odd)"},
        {**PROPUESTA, "sel_enlace": "a.enlace @href @src"},
        {**PROPUESTA, "sel_titulo": "//h3"},
    ],
)
def test_selectores_no_admitidos_sin_descargar_nada(malo: dict[str, Any]) -> None:
    motivo, navegador = fallo([malo])
    assert "no es un selector admitido" in motivo
    assert navegador.pedidas == []


def test_resultados_que_enlazan_a_otro_dominio() -> None:
    motivo, navegador = fallo([{**PROPUESTA, "sel_resultado": "div.patrocinado"}])
    assert "1 elemento" in motivo
    # Se comprueba primero en la página recibida: ni siquiera se repite la búsqueda.
    assert navegador.pedidas == []


def test_si_no_valida_no_se_devuelve() -> None:
    sin_precio = {**PROPUESTA, "sel_campos": {**dict.fromkeys(CLAVES_CAMPOS), "marca": "td.marca"}}
    motivo, _ = fallo([sin_precio], NavegadorFalso(paginas_sitio(jsonld=())))
    assert "precio" in motivo
    assert "No válido" in motivo


def test_clave_de_campo_inventada_la_rechaza_el_esquema() -> None:
    inventada = {**PROPUESTA, "sel_campos": {**CAMPOS_OK, "comando": "body"}}
    nuevo, peticiones, _ = ejecutar([inventada, PROPUESTA])
    assert set(nuevo.sel_campos) <= CLAVES_CAMPOS
    assert len(peticiones) == 2
    with pytest.raises(SalidaNoValida):
        ejecutar([inventada, inventada])


class SesionSinEsquema:
    """No valida la salida: el reparador tampoco se fía."""

    def __init__(self, datos: dict[str, Any]) -> None:
        self.datos = datos

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA:
        return RespuestaIA(self.datos, 1, 1, Decimal(0), 1, "falso", "falso")


def test_sin_esquema_no_entran_claves_inventadas_ni_urls() -> None:
    datos = {
        **PROPUESTA,
        "sel_campos": {**CAMPOS_OK, "comando": "body", "marca": 7},
        "url_busqueda": "https://otra-tienda.es/?q={consulta}",
        "revisado": True,
    }
    nuevo = asyncio.run(
        reparar(
            ROTO,
            ACTUAL,
            NavegadorFalso(paginas_sitio()),
            SesionSinEsquema(datos),
            CONSULTA,
            validador=validar_con_dns_publico,
        )
    )
    assert set(nuevo.sel_campos) == set(CAMPOS_OK) - {"marca"}
    assert nuevo.url_busqueda == PLANTILLA
    assert not nuevo.revisado


def test_sin_esquema_con_tipos_raros_falla() -> None:
    datos = {**PROPUESTA, "sel_resultado": ["li"], "sel_campos": "precio"}
    with pytest.raises(ReparacionFallida):
        asyncio.run(
            reparar(
                ROTO,
                ACTUAL,
                NavegadorFalso(paginas_sitio()),
                SesionSinEsquema(datos),
                CONSULTA,
                validador=validar_con_dns_publico,
            )
        )


def test_bloqueo_al_validar_se_propaga() -> None:
    navegador = NavegadorFalso(paginas_sitio(), errores={BUSQUEDA: Bloqueado(BUSQUEDA, "DataDome")})
    with pytest.raises(Bloqueado):
        ejecutar([PROPUESTA], navegador)


def test_precio_de_otra_parte_de_la_ficha_no_repara() -> None:
    engano = {**PROPUESTA, "sel_campos": {**CAMPOS_OK, "precio": "aside.relacionados span"}}
    motivo, _ = fallo([engano])
    assert "No válido" in motivo
    assert "12,00 €" in motivo
