"""Validador de perfiles: selectores admitidos, búsqueda real, listado y fichas con precio."""

import asyncio
from typing import Any

import pytest

from precia.ia.servicio import SesionIA as SesionReal
from precia.navegador.navegador import Navegador as NavegadorReal
from precia.navegador.tipos import Bloqueado, ErrorDescarga, ProhibidoPorRobots, URLNoPermitida
from precia.perfiles.protocolos import Navegador, SesionIA
from precia.perfiles.tipos import TIPO_API_SHOPIFY, InformeValidacion, Perfil
from precia.perfiles.validador import MAX_FICHAS, resumir, url_de_busqueda, validar
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.plataformas import (
    SHOPIFY_API,
    SHOPIFY_PORTADA,
    WOO_API,
    fixture,
    paginas_shopify,
    paginas_woo,
    perfil_api,
)
from tests.perfiles.sitio import (
    ANUNCIO,
    BUSQUEDA,
    CONSULTA,
    PRODUCTOS,
    TIENDA,
    NavegadorFalso,
    html_ficha,
    html_listado,
    paginas_sitio,
    perfil,
)


def ejecutar(p: Perfil, navegador: NavegadorFalso, consulta: str = CONSULTA) -> InformeValidacion:
    return asyncio.run(validar(p, navegador, consulta, validador=validar_con_dns_publico))


def test_el_navegador_falso_cumple_el_protocolo() -> None:
    navegador: Navegador = NavegadorFalso({})
    assert navegador is not None


def test_perfil_correcto_es_valido() -> None:
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(), navegador)
    assert informe.valido
    assert informe.resultados == len(PRODUCTOS)
    assert (informe.fichas_ok, informe.fichas_total) == (3, 3)
    assert informe.campos_encontrados == {
        "precio": 3,
        "referencia_oem": 3,
        "marca": 3,
        "estado": 3,
        "descripcion": 3,
        "fotos": 3,
    }
    # La búsqueda y solo las tres primeras fichas, sin JavaScript.
    assert navegador.pedidas == [(BUSQUEDA, False)] + [(p.url, False) for p in PRODUCTOS[:3]]
    assert MAX_FICHAS == 3


def test_selector_fuera_del_subconjunto_invalida_sin_descargar_nada() -> None:
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(
        perfil(sel_resultado="li.producto:has(a)", sel_campos={"precio": "//span[@class]"}),
        navegador,
    )
    assert not informe.valido
    assert navegador.pedidas == []
    assert any("sel_resultado" in p for p in informe.problemas)
    assert any("sel_campos[precio]" in p for p in informe.problemas)
    assert (informe.resultados, informe.fichas_ok, informe.fichas_total) == (0, 0, 0)


@pytest.mark.parametrize(
    "cambios",
    [
        {"sel_titulo": ""},
        {"sel_enlace": "a.enlace @"},
        {"sel_precio_listado": "span.precio::before"},
        {"sel_titulo": "h3 + p"},
    ],
)
def test_cualquier_selector_no_admitido_invalida(cambios: dict[str, Any]) -> None:
    informe = ejecutar(perfil(**cambios), NavegadorFalso(paginas_sitio()))
    assert not informe.valido
    assert any(next(iter(cambios)) in p for p in informe.problemas)


def test_clave_de_campo_desconocida_invalida() -> None:
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(sel_campos={"precio": ".precio-ficha", "comando": "td"}), navegador)
    assert not informe.valido
    assert any("comando" in p for p in informe.problemas)
    assert navegador.pedidas == []


@pytest.mark.parametrize(
    ("plantilla", "motivo"),
    [
        (f"{TIENDA}/buscar?q=faro", "{consulta}"),
        ("https://otra-tienda.es/buscar?q={consulta}", "dominio"),
        ("ftp://www.recambios-prueba.es/buscar?q={consulta}", "dominio"),
        ("https://127.0.0.1/buscar?q={consulta}", "dominio"),
        # Con «prueba» el host sería el de la tienda; con otra consulta, otra web.
        ("https://www.recambios-{consulta}.es/buscar", "host"),
        ("https://recambios-{consulta}.es/?q=faro", "host"),
    ],
)
def test_url_de_busqueda_no_valida(plantilla: str, motivo: str) -> None:
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(url_busqueda=plantilla), navegador)
    assert not informe.valido
    assert any(motivo in p for p in informe.problemas)
    assert navegador.pedidas == []


def test_url_que_no_pasa_la_validacion_anti_ssrf() -> None:
    # Mismo dominio, pero con un puerto que `validar_url` no admite.
    plantilla = "https://www.recambios-prueba.es:8443/buscar?q={consulta}"
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(url_busqueda=plantilla), navegador)
    assert not informe.valido
    assert navegador.pedidas == []


def test_la_consulta_se_codifica() -> None:
    assert url_de_busqueda(f"{TIENDA}/buscar?q={{consulta}}", "faro golf & más") == (
        f"{TIENDA}/buscar?q=faro+golf+%26+m%C3%A1s"
    )
    # En la ruta, el espacio es %20 y la barra también se escapa.
    assert url_de_busqueda(f"{TIENDA}/buscar/{{consulta}}", "faro golf 1/2") == (
        f"{TIENDA}/buscar/faro%20golf%201%2F2"
    )


def test_sin_resultados_no_es_valido() -> None:
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(sel_resultado="div.no-existe"), navegador)
    assert not informe.valido
    assert informe.resultados == 0
    assert informe.fichas_total == 0
    assert any("resultado" in p for p in informe.problemas)
    assert navegador.pedidas == [(BUSQUEDA, False)]


def test_resultados_de_otro_dominio_no_cuentan() -> None:
    # El bloque patrocinado enlaza a otra tienda: ni cuenta ni se visita.
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(sel_resultado="div.patrocinado"), navegador)
    assert not informe.valido
    assert informe.resultados == 0
    assert all("otra-tienda" not in url for url in navegador.urls)
    assert any("1 elemento" in p for p in informe.problemas)


def test_busqueda_con_error_http_no_es_valida() -> None:
    navegador = NavegadorFalso(paginas_sitio(busqueda=None))
    informe = ejecutar(perfil(), navegador)
    assert not informe.valido
    assert any("404" in p for p in informe.problemas)


def test_dos_de_tres_fichas_con_precio_bastan() -> None:
    informe = ejecutar(perfil(), NavegadorFalso(paginas_sitio(jsonld=(), sin_precio=(2,))))
    assert informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (2, 3)
    assert informe.campos_encontrados["precio"] == 2


def test_una_de_tres_fichas_con_precio_no_basta() -> None:
    informe = ejecutar(perfil(), NavegadorFalso(paginas_sitio(jsonld=(), sin_precio=(1, 2))))
    assert not informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (1, 3)
    assert any("precio" in p for p in informe.problemas)


def test_el_json_ld_cuenta_aunque_falle_el_selector() -> None:
    p = perfil(sel_campos={"precio": "span.no-existe"})
    informe = ejecutar(p, NavegadorFalso(paginas_sitio(jsonld=(0, 1, 2))))
    assert informe.valido
    assert informe.fichas_ok == 3


def test_con_menos_de_tres_resultados_tienen_que_valer_todas() -> None:
    sitio = paginas_sitio(productos=PRODUCTOS[:2], jsonld=(), sin_precio=(1,))
    informe = ejecutar(perfil(), NavegadorFalso(sitio))
    assert not informe.valido
    assert (informe.resultados, informe.fichas_ok, informe.fichas_total) == (2, 1, 2)
    sitio = paginas_sitio(productos=PRODUCTOS[:2], jsonld=())
    informe = ejecutar(perfil(), NavegadorFalso(sitio))
    assert informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (2, 2)


def test_ficha_que_no_se_descarga_cuenta_como_fallo() -> None:
    error = ErrorDescarga("Tiempo de espera agotado")
    navegador = NavegadorFalso(paginas_sitio(jsonld=()), errores={PRODUCTOS[0].url: error})
    informe = ejecutar(perfil(), navegador)
    assert informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (2, 3)
    assert any("Tiempo de espera agotado" in p for p in informe.problemas)


def test_un_campo_que_no_aparece_en_ninguna_ficha_se_avisa() -> None:
    p = perfil(sel_campos={"precio": ".precio-ficha", "marca": "span.marca-inexistente"})
    informe = ejecutar(p, NavegadorFalso(paginas_sitio(jsonld=())))
    assert informe.valido
    assert "marca" not in informe.campos_encontrados
    assert any("sel_campos[marca]" in p for p in informe.problemas)


@pytest.mark.parametrize(
    "error", [Bloqueado(BUSQUEDA, "reCAPTCHA"), ProhibidoPorRobots("robots.txt")]
)
def test_bloqueo_y_robots_se_propagan(error: Exception) -> None:
    navegador = NavegadorFalso(paginas_sitio(), errores={BUSQUEDA: error})
    with pytest.raises(type(error)):
        ejecutar(perfil(), navegador)


def test_bloqueo_en_una_ficha_se_propaga() -> None:
    error = Bloqueado(PRODUCTOS[1].url, "Turnstile")
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[1].url: error})
    with pytest.raises(Bloqueado):
        ejecutar(perfil(), navegador)
    # Tras el bloqueo no se sigue pidiendo nada a la tienda.
    assert navegador.urls[-1] == PRODUCTOS[1].url


def test_perfil_con_javascript_usa_chromium() -> None:
    sitio = paginas_sitio()
    navegador = NavegadorFalso(sitio, paginas_js=sitio)
    informe = ejecutar(perfil(necesita_js=True), navegador)
    assert informe.valido
    assert all(js for _, js in navegador.pedidas)


def test_resumen() -> None:
    informe = InformeValidacion(
        valido=True,
        resultados=4,
        fichas_ok=2,
        fichas_total=3,
        campos_encontrados={"precio": 2, "marca": 1},
        problemas=("Ficha 3: sin precio anclado.",),
    )
    texto = resumir(informe)
    assert texto.startswith("Válido")
    assert "4 resultados" in texto
    assert "2/3 fichas con precio" in texto
    assert "marca 1/3" in texto
    assert "Ficha 3: sin precio anclado." in texto
    largo = InformeValidacion(False, 0, 0, 0, problemas=("x" * 500,) * 20)
    assert resumir(largo).startswith("No válido")
    assert len(resumir(largo)) <= 2000


def test_el_motivo_principal_no_se_pierde_entre_avisos() -> None:
    # Muchos avisos de extracción (selectores que leen texto oculto...) no tapan el veredicto.
    campos = {c: "span.no-existe" for c in ("referencia_oem", "marca", "estado", "descripcion")}
    sitio = paginas_sitio(jsonld=(), sin_precio=(0, 1, 2))
    informe = ejecutar(perfil(sel_campos={**campos, "precio": "td.ref"}), NavegadorFalso(sitio))
    assert not informe.valido
    assert any(p.startswith("Solo 0 de 3 fichas") for p in informe.problemas)


def _como_protocolos(navegador: NavegadorReal, sesion_ia: SesionReal) -> tuple[Navegador, SesionIA]:
    """mypy comprueba aquí que las clases reales cumplen los protocolos de `perfiles`."""
    return navegador, sesion_ia


def test_las_clases_reales_cumplen_los_protocolos() -> None:
    assert callable(_como_protocolos)


# --- Revisión adversarial: perfiles que «validan» con datos que no son de la tienda ----------


def test_precio_de_la_ficha_distinto_del_listado_no_cuenta() -> None:
    # El selector lee el precio de «productos relacionados» (12,00 € en todas las fichas): está
    # anclado, pero no es el del producto. El listado dice otra cosa y eso lo delata.
    p = perfil(sel_campos={"precio": "aside.relacionados span"})
    informe = ejecutar(p, NavegadorFalso(paginas_sitio()))
    assert not informe.valido
    # Ni siquiera la ficha 2, cuyo precio sale del JSON-LD y coincide con el listado: allí el
    # selector también lee 12,00 €.
    assert (informe.fichas_ok, informe.fichas_total) == (0, 3)
    assert any("12,00 €" in p and "89,90 €" in p for p in informe.problemas)


def test_sin_precio_en_el_listado_no_se_compara() -> None:
    informe = ejecutar(perfil(sel_precio_listado=None), NavegadorFalso(paginas_sitio(jsonld=())))
    assert informe.valido
    assert informe.fichas_ok == 3


def test_ficha_que_redirige_a_otro_dominio_no_cuenta() -> None:
    # Enlaces del propio dominio que redirigen fuera (afiliados, patrocinados): lo que hay al
    # otro lado no es de la tienda aunque tenga un precio anclado.
    sitio = paginas_sitio(jsonld=())
    sitio[ANUNCIO] = html_ficha(PRODUCTOS[0])
    redirecciones = {PRODUCTOS[0].url: ANUNCIO, PRODUCTOS[1].url: ANUNCIO}
    informe = ejecutar(perfil(), NavegadorFalso(sitio, redirecciones=redirecciones))
    assert not informe.valido
    assert (informe.fichas_ok, informe.fichas_total) == (1, 3)
    assert any("otro dominio" in p for p in informe.problemas)


def test_busqueda_que_redirige_a_otro_dominio_no_vale() -> None:
    fuera = "https://otra-tienda.es/buscar?q=faro+golf"
    sitio = paginas_sitio()
    # La página de fuera enlaza con URL absolutas a las fichas de la tienda.
    sitio[fuera] = html_listado().replace('href="/p/', f'href="{TIENDA}/p/')
    navegador = NavegadorFalso(sitio, redirecciones={BUSQUEDA: fuera})
    informe = ejecutar(perfil(), navegador)
    assert not informe.valido
    assert any("otro dominio" in p for p in informe.problemas)
    assert navegador.urls == [BUSQUEDA]


def test_consulta_en_el_fragmento_no_llega_a_la_tienda() -> None:
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(url_busqueda=f"{TIENDA}/buscar#q={{consulta}}"), navegador)
    assert not informe.valido
    assert navegador.pedidas == []


def test_el_selector_de_precio_se_comprueba_aunque_haya_json_ld() -> None:
    # Con JSON-LD en las tres fichas el selector no se usa al validar, pero sí en las fichas de
    # la tienda que no lo traen: si lee otro precio que el del JSON-LD, el perfil no vale.
    p = perfil(sel_campos={"precio": "aside.relacionados span"}, sel_precio_listado=None)
    informe = ejecutar(p, NavegadorFalso(paginas_sitio(jsonld=(0, 1, 2))))
    assert not informe.valido
    assert informe.fichas_ok == 0
    assert any("sel_campos[precio]" in p and "12,00 €" in p for p in informe.problemas)
    # El selector bueno sí pasa.
    informe = ejecutar(
        perfil(sel_precio_listado=None), NavegadorFalso(paginas_sitio(jsonld=(0, 1, 2)))
    )
    assert informe.valido


def test_cada_ficha_pasa_por_el_validador_antes_de_descargarse() -> None:
    async def sin_la_primera(url: str) -> str:
        if url == PRODUCTOS[0].url:
            raise URLNoPermitida("El servidor resuelve a una dirección IP no pública.")
        return await validar_con_dns_publico(url)

    navegador = NavegadorFalso(paginas_sitio())
    informe = asyncio.run(validar(perfil(), navegador, CONSULTA, validador=sin_la_primera))
    assert PRODUCTOS[0].url not in navegador.urls
    assert (informe.fichas_ok, informe.fichas_total) == (2, 3)
    assert any("no pública" in p for p in informe.problemas)


# --- Perfiles `api_*` (nota 15 §D) --------------------------------------------------------------


def test_perfil_api_woocommerce_valida_con_la_api_sin_abrir_fichas() -> None:
    navegador = NavegadorFalso(paginas_woo())
    informe = ejecutar(perfil_api(), navegador)
    assert informe.valido
    # Tres productos en la respuesta, dos con precio (el tercero es «consultar precio»).
    assert (informe.resultados, informe.fichas_ok, informe.fichas_total) == (3, 2, 3)
    assert informe.campos_encontrados["precio"] == 2
    # Una sola petición, a la API y sin JavaScript: ni la portada ni las fichas.
    assert navegador.pedidas == [(WOO_API, False)]


def test_perfil_api_shopify_lee_la_moneda_de_la_portada() -> None:
    navegador = NavegadorFalso(paginas_shopify())
    informe = ejecutar(perfil_api(TIPO_API_SHOPIFY), navegador)
    assert informe.valido and informe.fichas_ok == 2
    assert navegador.urls == [SHOPIFY_API, SHOPIFY_PORTADA]


def test_perfil_api_shopify_sin_moneda_conocida_no_valida() -> None:
    # Sin `Shopify.currency` en la portada no se sabe en qué moneda están los precios: mejor
    # sin perfil API que un precio en libras leído como euros con confianza 1.
    paginas = paginas_shopify()
    paginas[SHOPIFY_PORTADA] = "<html><body><h1>Recambios Rápidos</h1></body></html>"
    informe = ejecutar(perfil_api(TIPO_API_SHOPIFY), NavegadorFalso(paginas))
    assert not informe.valido
    assert any("moneda" in p for p in informe.problemas)


@pytest.mark.parametrize("respuesta", ["[]", "{}", "no es json", '{"code": "rest_no_route"}'])
def test_perfil_api_sin_productos_no_valida(respuesta: str) -> None:
    paginas = paginas_woo()
    paginas[WOO_API] = respuesta
    informe = ejecutar(perfil_api(), NavegadorFalso(paginas))
    assert not informe.valido
    assert any("API" in p and "productos" in p for p in informe.problemas)


def test_perfil_api_que_responde_con_error_no_valida() -> None:
    informe = ejecutar(perfil_api(), NavegadorFalso({}))
    assert not informe.valido
    assert any("404" in p for p in informe.problemas)


def test_perfil_api_que_redirige_fuera_no_valida() -> None:
    fuera = "https://otra-tienda.es/wp-json/wc/store/v1/products?search=faro+golf"
    paginas = {fuera: fixture("woocommerce_store_api.json")}
    navegador = NavegadorFalso(paginas, redirecciones={WOO_API: fuera})
    informe = ejecutar(perfil_api(), navegador)
    assert not informe.valido
    assert any("otro dominio" in p for p in informe.problemas)


def test_perfil_api_de_otro_dominio_no_descarga_nada() -> None:
    navegador = NavegadorFalso(paginas_woo())
    otra = "https://otra-tienda.es/wp-json/wc/store/v1/products?search={consulta}"
    informe = ejecutar(perfil_api(url_busqueda=otra), navegador)
    assert not informe.valido
    assert navegador.pedidas == []


def test_selectores_vacios_solo_valen_en_perfiles_api() -> None:
    navegador = NavegadorFalso(paginas_sitio())
    informe = ejecutar(perfil(sel_resultado="", sel_titulo=""), navegador)
    assert not informe.valido
    assert navegador.pedidas == []


def test_tipo_de_perfil_desconocido_no_valida() -> None:
    navegador = NavegadorFalso(paginas_woo())
    informe = ejecutar(perfil_api(tipo="api_magento"), navegador)
    assert not informe.valido
    assert any("tipo" in p for p in informe.problemas)
    assert navegador.pedidas == []


def test_bloqueo_en_la_api_se_propaga() -> None:
    navegador = NavegadorFalso(paginas_woo(), errores={WOO_API: Bloqueado(WOO_API, "DataDome")})
    with pytest.raises(Bloqueado):
        ejecutar(perfil_api(), navegador)
