"""🤖 Configurador: descubre la búsqueda, pide los selectores a la IA y valida antes de devolver."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from precia.ia.simulado import Guion
from precia.ia.tipos import PeticionIA, PresupuestoAgotado, RespuestaIA, SalidaNoValida
from precia.navegador.tipos import Bloqueado
from precia.nucleo.modelos import CAMPOS, CLAVES_CAMPOS, Campo
from precia.perfiles.configurador import AUTOR, COMPONENTE, configurar
from precia.perfiles.descubrimiento import (
    ESQUEMA_PLANTILLAS,
    MAX_FORMULARIOS,
    PATRONES,
    parece_listado,
    plantillas_de_formularios,
)
from precia.perfiles.tipos import ConfiguracionFallida, Perfil
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    ANUNCIO,
    BUSQUEDA,
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    INYECCION,
    LISTADO_OK,
    PLANTILLA,
    PORTADA,
    PRODUCTOS,
    TIENDA,
    NavegadorFalso,
    html_listado,
    html_portada,
    html_vacio,
    pagina,
    paginas_sitio,
    sesion,
)

AHORA = datetime(2026, 10, 19, 18, 30, tzinfo=UTC)
NULOS = dict.fromkeys(CLAVES_CAMPOS)


def ejecutar(
    navegador: NavegadorFalso,
    guion: list[Guion],
    *,
    dominio: str = DOMINIO,
    tope: int = 20,
    **opciones: Any,
) -> tuple[Perfil, list[Any]]:
    sesion_ia, proveedor = sesion({COMPONENTE: guion}, tope=tope)
    perfil = asyncio.run(
        configurar(
            dominio,
            navegador,
            sesion_ia,
            CONSULTA,
            validador=validar_con_dns_publico,
            reloj=lambda: AHORA,
            **opciones,
        )
    )
    return perfil, proveedor.peticiones


def fallo(navegador: NavegadorFalso, guion: list[Guion], **opciones: Any) -> tuple[str, list[Any]]:
    sesion_ia, proveedor = sesion({COMPONENTE: guion})
    with pytest.raises(ConfiguracionFallida) as error:
        asyncio.run(
            configurar(
                DOMINIO,
                navegador,
                sesion_ia,
                CONSULTA,
                validador=validar_con_dns_publico,
                **opciones,
            )
        )
    return str(error.value), proveedor.peticiones


def redirigida() -> NavegadorFalso:
    """La tienda contesta en `www.` aunque se pida el dominio desnudo."""
    return NavegadorFalso(paginas_sitio(), redirecciones={f"https://{DOMINIO}/": PORTADA})


# --- Descubrimiento de la búsqueda -----------------------------------------------------------


def test_formulario_de_busqueda_de_la_portada() -> None:
    portada = pagina(PORTADA, html_portada())
    assert plantillas_de_formularios(portada, DOMINIO) == [PLANTILLA]


@pytest.mark.parametrize(
    "formulario",
    [
        # Por POST, a otro dominio, sin campo de búsqueda o con `javascript:`.
        '<form action="/buscar" method="post"><input type="search" name="q"></form>',
        '<form action="https://otra-tienda.es/buscar"><input type="search" name="q"></form>',
        '<form action="/buscar"><input type="text" name="email"></form>',
        '<form action="javascript:alert(1)"><input type="search" name="q"></form>',
        '<form action="//otra-tienda.es/buscar"><input name="q"></form>',
    ],
)
def test_formularios_que_no_son_la_busqueda(formulario: str) -> None:
    portada = pagina(PORTADA, f"<html><body>{formulario}</body></html>")
    assert plantillas_de_formularios(portada, DOMINIO) == []


def test_formulario_con_campos_ocultos_en_orden() -> None:
    html = (
        '<form action="/busqueda" method="GET">'
        '<input type="hidden" name="controller" value="search">'
        '<input type="text" name="s" value="">'
        '<input type="hidden" name="order" value="product.position.desc">'
        '<input type="submit" name="enviar" value="Buscar"></form>'
    )
    portada = pagina(PORTADA, html)
    assert plantillas_de_formularios(portada, DOMINIO) == [
        f"{TIENDA}/busqueda?controller=search&s={{consulta}}&order=product.position.desc"
    ]


def test_formulario_sin_accion_busca_en_la_misma_pagina() -> None:
    portada = pagina(f"{TIENDA}/tienda?x=1", '<form><input type="search" name="buscar"></form>')
    assert plantillas_de_formularios(portada, DOMINIO) == [f"{TIENDA}/tienda?buscar={{consulta}}"]


def test_parece_listado() -> None:
    portada = pagina(PORTADA, html_portada())
    listado = pagina(BUSQUEDA, html_listado())
    assert parece_listado(listado, DOMINIO, CONSULTA, portada=portada)
    # La portada tiene productos con precio, pero no es una página de resultados.
    assert not parece_listado(
        pagina(f"{TIENDA}/?s=x", html_portada()), DOMINIO, CONSULTA, portada=portada
    )
    assert not parece_listado(pagina(BUSQUEDA, html_vacio()), DOMINIO, CONSULTA, portada=portada)
    assert not parece_listado(
        pagina(BUSQUEDA, html_listado(), estado=404), DOMINIO, CONSULTA, portada=portada
    )
    # Los resultados no tienen nada que ver con lo buscado.
    assert not parece_listado(listado, DOMINIO, "amortiguador", portada=portada)
    # Un listado de otra tienda (todos los enlaces fuera del dominio) no vale.
    assert not parece_listado(listado, "otra-tienda.es", CONSULTA, portada=None)


def test_configura_con_el_formulario_de_la_portada() -> None:
    navegador = redirigida()
    perfil, peticiones = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK])
    assert perfil == Perfil(
        dominio=DOMINIO,
        version=1,
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
        nota=perfil.nota,
    )
    assert AUTOR == "🤖 configurador"
    assert perfil.nota.startswith("Válido: 4 resultados, 3/3 fichas con precio anclado.")
    assert len(peticiones) == 2
    # Ni los formularios trampa ni los patrones: la búsqueda del formulario funcionó.
    assert navegador.urls[:3] == [f"https://{DOMINIO}/", BUSQUEDA, PRODUCTOS[0].url]
    assert all("newsletter" not in u and "buscador-externo" not in u for u in navegador.urls)


def test_patrones_habituales_en_orden_si_no_hay_formulario() -> None:
    woo = f"{TIENDA}/?s=faro+golf&post_type=product"
    sitio = paginas_sitio(formulario=False, busqueda=woo)
    # PrestaShop no está: «/busqueda» da 404. «/buscar» devuelve la portada (ignora la consulta).
    sitio[f"{TIENDA}/buscar?q=faro+golf"] = html_portada(formulario=False)
    navegador = NavegadorFalso(sitio)
    perfil, _ = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.url_busqueda == f"{TIENDA}/?s={{consulta}}&post_type=product"
    assert perfil.dominio == DOMINIO
    # Sin formulario ni enlaces de búsqueda se mira la portada con Chromium (aquí no hay) antes
    # de los patrones (nota 15 §D).
    assert navegador.pedidas[:6] == [
        (PORTADA, False),
        (PORTADA, True),
        (f"{TIENDA}/busqueda?controller=search&s=faro+golf", False),
        (f"{TIENDA}/buscar?q=faro+golf", False),
        (f"{TIENDA}/search?q=faro+golf", False),
        (woo, False),
    ]
    assert PATRONES[0] == "/busqueda?controller=search&s={consulta}"
    assert PATRONES[-1] == "/buscar/{consulta}"


def test_patron_en_la_ruta() -> None:
    ruta = f"{TIENDA}/buscar/faro%20golf"
    navegador = NavegadorFalso(paginas_sitio(formulario=False, busqueda=ruta))
    perfil, _ = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.url_busqueda == f"{TIENDA}/buscar/{{consulta}}"


def test_sin_pagina_de_busqueda_no_se_llama_a_la_ia() -> None:
    navegador = NavegadorFalso(paginas_sitio(formulario=False, busqueda=None))
    motivo, peticiones = fallo(navegador, [LISTADO_OK, CAMPOS_OK])
    assert "búsqueda" in motivo
    assert peticiones == []


def test_necesita_js_si_solo_chromium_trae_resultados() -> None:
    sitio = paginas_sitio()
    con_js = dict(sitio)
    sitio[BUSQUEDA] = html_vacio()
    navegador = NavegadorFalso(sitio, paginas_js=con_js)
    perfil, _ = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    assert perfil.necesita_js
    assert (BUSQUEDA, False) in navegador.pedidas
    assert (BUSQUEDA, True) in navegador.pedidas
    # La validación ya usa Chromium para la búsqueda y las fichas.
    assert navegador.pedidas[-1] == (PRODUCTOS[2].url, True)


def test_sin_chromium_una_tienda_con_js_no_se_configura() -> None:
    sitio = paginas_sitio()
    sitio[BUSQUEDA] = html_vacio()
    motivo, peticiones = fallo(NavegadorFalso(sitio), [LISTADO_OK, CAMPOS_OK])
    assert peticiones == []
    assert DOMINIO in motivo


def test_bloqueo_se_propaga() -> None:
    navegador = redirigida()
    navegador.errores = {BUSQUEDA: Bloqueado(BUSQUEDA, "hCaptcha")}
    sesion_ia, proveedor = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK]})
    with pytest.raises(Bloqueado):
        asyncio.run(
            configurar(DOMINIO, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
        )
    assert proveedor.peticiones == []


def test_dominio_no_valido() -> None:
    sesion_ia, _ = sesion({})
    with pytest.raises(ConfiguracionFallida):
        asyncio.run(
            configurar(
                "", NavegadorFalso({}), sesion_ia, CONSULTA, validador=validar_con_dns_publico
            )
        )


# --- Peticiones a la IA ----------------------------------------------------------------------


def test_peticiones_separan_instrucciones_de_datos_web() -> None:
    _, peticiones = ejecutar(redirigida(), [LISTADO_OK, CAMPOS_OK])
    listado, ficha = peticiones
    assert {listado.componente, ficha.componente} == {COMPONENTE}
    for peticion in peticiones:
        # Nada de la web en las instrucciones: ni la inyección, ni títulos, ni la consulta.
        assert INYECCION not in peticion.instrucciones
        assert PRODUCTOS[0].titulo not in peticion.instrucciones
        assert "recambios-prueba" not in peticion.instrucciones
        assert ":nth-of-type" in peticion.instrucciones
        assert "@href" in peticion.instrucciones
    # El listado va resumido (HTML simplificado) en los datos, con la inyección como dato más.
    assert '<li class="producto">' in listado.datos
    assert INYECCION in listado.datos
    assert PRODUCTOS[0].referencia in ficha.datos


def test_esquemas_estrictos() -> None:
    _, (listado, ficha) = ejecutar(redirigida(), [LISTADO_OK, CAMPOS_OK])
    assert listado.esquema["additionalProperties"] is False
    assert listado.esquema["required"] == [
        "sel_resultado",
        "sel_titulo",
        "sel_enlace",
        "sel_precio_listado",
    ]
    assert ficha.esquema["additionalProperties"] is False
    assert list(ficha.esquema["properties"]) == [c.clave for c in CAMPOS]
    assert ficha.esquema["required"] == [c.clave for c in CAMPOS]
    for propiedad in ficha.esquema["properties"].values():
        tipos = [opcion["type"] for opcion in propiedad["anyOf"]]
        assert tipos == ["string", "null"]


def test_con_json_ld_completo_solo_pide_lo_que_falta() -> None:
    # La primera ficha trae JSON-LD con todos los campos: solo se pide el precio, que decide la
    # validación y no tienen por qué traer las demás fichas.
    navegador = NavegadorFalso(paginas_sitio(jsonld=(0,)))
    perfil, (_, ficha) = ejecutar(
        navegador, [LISTADO_OK, {"precio": ".precio-ficha"}], dominio=f"www.{DOMINIO}"
    )
    assert list(ficha.esquema["properties"]) == ["precio"]
    assert perfil.sel_campos == {"precio": ".precio-ficha"}


def test_campos_pedidos() -> None:
    campos = (Campo("precio", "Precio"), Campo("marca", "Marca"))
    respuesta = {"precio": ".precio-ficha", "marca": "td.marca"}
    perfil, (_, ficha) = ejecutar(redirigida(), [LISTADO_OK, respuesta], campos=campos)
    assert list(ficha.esquema["properties"]) == ["precio", "marca"]
    assert perfil.sel_campos == respuesta


def test_campo_desconocido_en_los_pedidos() -> None:
    with pytest.raises(ValueError, match="comando"):
        ejecutar(redirigida(), [], campos=(Campo("comando", "Comando"),))


def test_los_nulos_no_entran_en_el_perfil() -> None:
    respuesta = {**NULOS, "precio": ".precio-ficha", "marca": "  td.marca  "}
    perfil, _ = ejecutar(redirigida(), [LISTADO_OK, respuesta])
    assert perfil.sel_campos == {"precio": ".precio-ficha", "marca": "td.marca"}


# --- Respuestas maliciosas o erróneas y reintento ---------------------------------------------


def test_selectores_fuera_del_subconjunto_y_reintento() -> None:
    malo = {**LISTADO_OK, "sel_resultado": "li.producto:has(a.enlace)"}
    navegador = redirigida()
    perfil, peticiones = ejecutar(navegador, [malo, LISTADO_OK, CAMPOS_OK])
    assert perfil.sel_resultado == LISTADO_OK["sel_resultado"]
    assert len(peticiones) == 3
    reintento = peticiones[1]
    # El problema viaja en los datos (contiene el selector de la IA), no en las instrucciones.
    assert "sel_resultado" in reintento.datos
    assert ":has" in reintento.datos
    assert "li.producto:has(a.enlace)" not in reintento.instrucciones
    assert "reintento" in reintento.instrucciones.lower()
    # Con el selector rechazado no se llegó a pedir ninguna ficha.
    assert navegador.urls[:3] == [f"https://{DOMINIO}/", BUSQUEDA, PRODUCTOS[0].url]


def test_selector_de_campo_no_admitido_se_descarta_y_se_reintenta() -> None:
    xpath = {**CAMPOS_OK, "precio": "//span[@class='precio-ficha']"}
    perfil, peticiones = ejecutar(redirigida(), [LISTADO_OK, xpath, LISTADO_OK, CAMPOS_OK])
    assert perfil.sel_campos == CAMPOS_OK
    assert len(peticiones) == 4
    assert "sel_campos[precio]" in peticiones[2].datos
    assert "sel_campos[precio]" in peticiones[3].datos


def test_resultados_que_enlazan_a_otro_dominio_se_rechazan() -> None:
    anuncio = {**LISTADO_OK, "sel_resultado": "div.patrocinado"}
    navegador = redirigida()
    perfil, peticiones = ejecutar(navegador, [anuncio, LISTADO_OK, CAMPOS_OK])
    assert perfil.sel_resultado == LISTADO_OK["sel_resultado"]
    assert "sel_resultado encuentra 1 elemento" in peticiones[1].datos
    assert ANUNCIO not in navegador.urls
    assert all("otra-tienda" not in u for u in navegador.urls)


def test_claves_de_campo_inventadas_las_rechaza_el_esquema() -> None:
    inventado = {**CAMPOS_OK, "comando": "body"}
    perfil, peticiones = ejecutar(redirigida(), [LISTADO_OK, inventado, CAMPOS_OK])
    # La sesión de IA reintenta la salida no válida: la clave inventada nunca llega al perfil.
    assert perfil.sel_campos == CAMPOS_OK
    assert set(perfil.sel_campos) <= CLAVES_CAMPOS
    assert len(peticiones) == 3


def test_salida_no_valida_dos_veces_se_propaga() -> None:
    inventado = {**CAMPOS_OK, "comando": "body"}
    with pytest.raises(SalidaNoValida):
        ejecutar(redirigida(), [LISTADO_OK, inventado, inventado])


class SesionSinEsquema:
    """Una sesión que no valida la salida contra el esquema: el configurador no se fía igual."""

    def __init__(self, respuestas: list[dict[str, Any]]) -> None:
        self.respuestas = respuestas

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA:
        return RespuestaIA(self.respuestas.pop(0), 1, 1, Decimal(0), 1, "falso", "falso")


@pytest.mark.parametrize(
    "respuesta_campos",
    [
        {**CAMPOS_OK, "comando": "body", "__proto__": "x"},
        {**CAMPOS_OK, "marca": 42, "estado": ["td"]},
    ],
)
def test_sin_esquema_tampoco_entran_claves_ni_tipos_raros(
    respuesta_campos: Mapping[str, Any],
) -> None:
    sesion_ia = SesionSinEsquema([dict(LISTADO_OK), dict(respuesta_campos)])
    perfil = asyncio.run(
        configurar(DOMINIO, redirigida(), sesion_ia, CONSULTA, validador=validar_con_dns_publico)
    )
    assert set(perfil.sel_campos) <= CLAVES_CAMPOS
    assert all(isinstance(v, str) for v in perfil.sel_campos.values())
    assert perfil.sel_campos["precio"] == CAMPOS_OK["precio"]


def test_si_el_reintento_tampoco_valida_falla() -> None:
    sin_precio = {**NULOS, "marca": "td.marca"}
    motivo, peticiones = fallo(redirigida(), [LISTADO_OK, sin_precio, LISTADO_OK, sin_precio])
    assert len(peticiones) == 4
    assert "precio" in motivo
    assert DOMINIO in motivo


def test_presupuesto_agotado_se_propaga() -> None:
    malo = {**LISTADO_OK, "sel_resultado": "li:not(.x)"}
    with pytest.raises(PresupuestoAgotado):
        ejecutar(redirigida(), [malo, LISTADO_OK, CAMPOS_OK], tope=1)


def test_el_precio_se_pide_siempre() -> None:
    respuesta = {"precio": ".precio-ficha", "marca": "td.marca"}
    perfil, (_, ficha) = ejecutar(redirigida(), [LISTADO_OK, respuesta], campos=CAMPOS[2:3])
    assert list(ficha.esquema["properties"]) == ["precio", "marca"]
    assert perfil.sel_campos == respuesta


def test_portada_que_redirige_a_otro_dominio() -> None:
    sitio = paginas_sitio(busqueda=f"{TIENDA}/buscar?q=faro+golf")
    sitio["https://otra-tienda.es/"] = html_portada()
    navegador = NavegadorFalso(sitio, redirecciones={PORTADA: "https://otra-tienda.es/"})
    perfil, _ = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK], dominio=f"www.{DOMINIO}")
    # Su formulario no cuenta y los patrones se prueban en la tienda, no en el otro dominio.
    assert perfil.url_busqueda == f"{TIENDA}/buscar?q={{consulta}}"
    assert all("otra-tienda" not in url for url in navegador.urls)


def test_el_reintento_ve_lo_que_propuso_la_ia() -> None:
    malo = {**LISTADO_OK, "sel_titulo": "h3 ~ span"}
    _, peticiones = ejecutar(redirigida(), [malo, LISTADO_OK, CAMPOS_OK])
    reintento = peticiones[1].datos
    assert reintento.index("SELECTORES DEL INTENTO ANTERIOR") < reintento.index("PÁGINA")
    assert '"h3 ~ span"' in reintento
    assert "PROBLEMAS DETECTADOS" in reintento
    assert "SELECTORES DEL INTENTO ANTERIOR" not in peticiones[0].datos


# --- Revisión adversarial ---------------------------------------------------------------------


def test_los_formularios_de_la_portada_tienen_tope() -> None:
    # Una portada con decenas de «buscadores» no puede multiplicar las descargas (cada plantilla
    # que responde se prueba por HTTP y otra vez con Chromium; las que dan 404, solo por HTTP).
    formularios = "".join(
        f'<form action="/buscar{n}"><input type="search" name="q"></form>' for n in range(40)
    )
    portada = pagina(PORTADA, f"<html><body>{formularios}</body></html>")
    plantillas = plantillas_de_formularios(portada, DOMINIO)
    assert plantillas == [f"{TIENDA}/buscar{n}?q={{consulta}}" for n in range(MAX_FORMULARIOS)]
    # Los 40 responden (sin ser un listado): el peor caso, dos descargas por plantilla.
    sin_resultados = "<html><body>Sin resultados</body></html>"
    paginas = {f"{TIENDA}/buscar{n}?q=faro+golf": sin_resultados for n in range(40)}
    navegador = NavegadorFalso(
        {PORTADA: portada.html, **paginas},
        paginas_js={},
        redirecciones={f"https://{DOMINIO}/": PORTADA},
    )
    motivo, peticiones = fallo(navegador, [])
    assert "búsqueda" in motivo
    # Como último recurso se pide una propuesta a la IA (aquí no responde): una sola petición.
    assert [p.esquema for p in peticiones] == [ESQUEMA_PLANTILLAS]
    # La portada, los formularios por HTTP y con Chromium y los patrones (404) solo por HTTP.
    assert len(navegador.pedidas) == 1 + 2 * MAX_FORMULARIOS + len(PATRONES)


@pytest.mark.parametrize(
    "dominio",
    [
        "otra-tienda.es#recambios-prueba.es",
        "otra-tienda.es?recambios-prueba.es",
        "recambios-prueba.es@otra-tienda.es",
        "recambios prueba.es",
        "recambios-prueba.es\\@otra-tienda.es",
        "-recambios-prueba.es",
        "localhost",
    ],
)
def test_dominio_que_no_es_un_nombre_de_host(dominio: str) -> None:
    navegador = redirigida()
    sesion_ia, proveedor = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK]})
    with pytest.raises(ConfiguracionFallida, match="no es válido"):
        asyncio.run(
            configurar(dominio, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
        )
    assert navegador.pedidas == []
    assert proveedor.peticiones == []


def test_dominio_con_mayusculas_y_punto_final_vale() -> None:
    perfil, _ = ejecutar(redirigida(), [LISTADO_OK, CAMPOS_OK], dominio=" Recambios-Prueba.ES. ")
    assert perfil.dominio == DOMINIO


def test_ficha_que_redirige_fuera_no_se_envia_a_la_ia() -> None:
    sitio = paginas_sitio(jsonld=())
    secreto = "CONTENIDO DE OTRA TIENDA"
    sitio[ANUNCIO] = f"<html><body><h1>{secreto}</h1><b>1,00 €</b></body></html>"
    navegador = NavegadorFalso(
        sitio, redirecciones={f"https://{DOMINIO}/": PORTADA, PRODUCTOS[0].url: ANUNCIO}
    )
    perfil, (_, ficha) = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK])
    assert secreto not in ficha.datos
    assert PRODUCTOS[1].referencia in ficha.datos
    # Valida con las otras dos fichas (2 de 3).
    assert perfil.nota.startswith("Válido: 4 resultados, 2/3 fichas")


def test_busqueda_que_redirige_fuera_no_parece_un_listado() -> None:
    fuera = "https://otra-tienda.es/buscar?q=faro+golf"
    html = html_listado().replace('href="/p/', f'href="{TIENDA}/p/')
    assert parece_listado(pagina(BUSQUEDA, html), DOMINIO, CONSULTA, portada=None)
    redirigida_fuera = pagina(BUSQUEDA, html, url_final=fuera)
    assert not parece_listado(redirigida_fuera, DOMINIO, CONSULTA, portada=None)


def test_precio_de_otra_parte_de_la_ficha_se_rechaza_y_se_reintenta() -> None:
    # Inducida por la página, la IA propone el precio de «productos relacionados»: está anclado
    # (se ve en la página) pero no es el del producto. La validación lo detecta y se reintenta.
    engano = {**CAMPOS_OK, "precio": "aside.relacionados span"}
    perfil, peticiones = ejecutar(redirigida(), [LISTADO_OK, engano, LISTADO_OK, CAMPOS_OK])
    assert perfil.sel_campos == CAMPOS_OK
    assert "12,00 €" in peticiones[3].datos
    motivo, _ = fallo(redirigida(), [LISTADO_OK, engano, LISTADO_OK, engano])
    assert "12,00 €" in motivo


# --- Retroalimentación y resumen de la página (prueba de generalidad, desguacesdli.com) -------


def test_el_reintento_dice_que_selectores_no_aparecen_en_la_pagina() -> None:
    # Las clases típicas de WooCommerce que la IA propuso sin haberlas visto en la página.
    inventado = {
        **LISTADO_OK,
        "sel_titulo": "h2.woocommerce-loop-product__title",
        "sel_enlace": "a.woocommerce-LoopProduct-link @href",
    }
    perfil, peticiones = ejecutar(redirigida(), [inventado, LISTADO_OK, CAMPOS_OK])
    assert perfil.sel_titulo == LISTADO_OK["sel_titulo"]
    reintento = peticiones[1].datos
    assert (
        "sel_titulo ('h2.woocommerce-loop-product__title') no aparece en ninguna parte de la "
        "página: usa clases e id que estén en el HTML"
    ) in reintento
    assert "sel_enlace ('a.woocommerce-LoopProduct-link @href') no aparece" in reintento
    # sel_resultado sí encaja: no se señala.
    assert "sel_resultado ('#resultados > li.producto') no aparece" not in reintento
    assert "sel_resultado encuentra 4 elementos" in reintento


def test_las_instrucciones_explican_el_marcador_de_elementos_omitidos() -> None:
    _, (listado, ficha) = ejecutar(redirigida(), [LISTADO_OK, CAMPOS_OK])
    for peticion in (listado, ficha):
        assert "elementos similares omitidos" in peticion.instrucciones


def test_en_la_ficha_los_relacionados_no_quitan_sitio_al_producto() -> None:
    # Una ficha grande con muchos productos relacionados al final: en el resumen de la ficha no
    # se reserva sitio para ellos (eso es para el listado), así que el producto principal entra.
    relleno = "".join(f'<div class="nota-{i}">Nota {i} de la tienda</div>' for i in range(450))
    relacionados = (
        '<ul class="relacionados">'
        + "".join(
            f'<li class="rel"><a href="/p/rel-{i}"><img src="/f/rel-{i}.jpg" alt="Relacionado {i}">'
            f'</a><a class="nombre-rel" href="/p/rel-{i}">Producto relacionado número {i}</a>'
            f'<span class="precio">{i},00 €</span><p class="extracto">{"texto " * 30}</p></li>'
            for i in range(40)
        )
        + "</ul>"
    )
    sitio = paginas_sitio()
    ficha = sitio[PRODUCTOS[0].url]
    sitio[PRODUCTOS[0].url] = ficha.replace(
        '<h1 class="titulo-producto">', relleno + '<h1 class="titulo-producto">'
    ).replace("</body>", relacionados + "</body>")
    navegador = NavegadorFalso(sitio, redirecciones={f"https://{DOMINIO}/": PORTADA})
    _, (_, peticion_ficha) = ejecutar(navegador, [LISTADO_OK, CAMPOS_OK])
    assert PRODUCTOS[0].referencia in peticion_ficha.datos
    assert "elementos similares omitidos" in peticion_ficha.datos
