"""Navegador: validar → robots → caché → turno → descargar → detectar bloqueo → guardar."""

import asyncio
from pathlib import Path

import pytest

from precia.navegador.bloqueo import ESTADOS_SIN_RESCATE
from precia.navegador.cache import CachePaginas
from precia.navegador.con_proxy import DescargadorConProxy, UsoProxy
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.navegador import Navegador
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import (
    Bloqueado,
    ConexionCortada,
    ErrorDescarga,
    Pagina,
    URLNoPermitida,
)
from tests.navegador.falsos import (
    DescargadorFalso,
    Reloj,
    RelojFecha,
    pagina,
    validar_con_dns_publico,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
TIENDA = "https://www.recambios-ejemplo.example"
ROBOTS = f"{TIENDA}/robots.txt"
BUSQUEDA = f"{TIENDA}/es/busqueda?s=1K0698151"
FICHA = f"{TIENDA}/frenos/pastillas-brembo-p85075.html"


def fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


class Montaje:
    """Un navegador completo con todo lo de fuera falseado."""

    def __init__(
        self,
        paginas: dict[str, Pagina | Exception],
        *,
        robots: str = "User-agent: *\nDisallow: /es/carrito\n",
        chromium: bool = False,
        paginas_js: dict[str, Pagina | Exception] | None = None,
        cache: Path | None = None,
    ) -> None:
        """Con `paginas_js`, el Chromium falso contesta eso (y existe aunque no se pida)."""
        self.reloj = Reloj()
        self.robots_http = DescargadorFalso({ROBOTS: pagina(ROBOTS, robots, tipo="text/plain")})
        self.http = DescargadorFalso(paginas)
        con_chromium = chromium or paginas_js is not None
        self.chromium = (
            DescargadorFalso(paginas if paginas_js is None else paginas_js)
            if con_chromium
            else None
        )
        self.cache = CachePaginas(cache, 3600, reloj=RelojFecha()) if cache else None
        self.navegador = Navegador(
            http=self.http,
            chromium=self.chromium,
            robots=ServicioRobots(self.robots_http, UA, reloj=self.reloj),
            limitador=LimitadorDominio(1.0, reloj=self.reloj, dormir=self.reloj.dormir),
            cache=self.cache,
            validador=validar_con_dns_publico,
        )

    def obtener(self, url: str, **opciones: bool) -> Pagina:
        return asyncio.run(self.navegador.obtener(url, **opciones))


def test_obtiene_una_pagina() -> None:
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA, fixture("listado_desguace.html"))})
    resultado = m.obtener(BUSQUEDA)
    assert "PASTILLAS FRENO" in resultado.html
    assert m.robots_http.pedidas == [ROBOTS]
    assert m.http.pedidas == [BUSQUEDA]


def test_normaliza_la_url_antes_de_todo() -> None:
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA)})
    m.obtener("HTTPS://WWW.Recambios-Ejemplo.example:443/es/busqueda?s=1K0698151#resultados")
    assert m.http.pedidas == [BUSQUEDA]


def test_url_no_permitida_no_toca_la_red() -> None:
    m = Montaje({})
    with pytest.raises(URLNoPermitida):
        m.obtener("http://169.254.169.254/latest/meta-data/")
    assert m.robots_http.pedidas == [] and m.http.pedidas == []


def test_robots_prohibe_se_visita_igual_y_queda_anotado() -> None:
    # ADR-017 (enmienda del 2026-10-25): el robots.txt nunca impide visitar; solo se avisa.
    carrito = f"{TIENDA}/es/carrito"
    m = Montaje({carrito: pagina(carrito)})
    m.obtener(carrito)
    assert m.http.pedidas == [carrito]
    assert m.navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_respeta_el_crawl_delay() -> None:
    m = Montaje(
        {BUSQUEDA: pagina(BUSQUEDA), FICHA: pagina(FICHA)},
        robots="User-agent: precia\nCrawl-delay: 4\nDisallow: /es/carrito\n",
    )

    async def dos() -> None:
        await m.navegador.obtener(BUSQUEDA)
        await m.navegador.obtener(FICHA)

    asyncio.run(dos())
    # El robots.txt también es una petición a la tienda (ADR-017): la búsqueda espera tras él.
    assert m.reloj.esperas == [pytest.approx(4.0), pytest.approx(4.0)]


@pytest.mark.parametrize(
    ("nombre", "estado"),
    [
        ("cloudflare_just_a_moment.html", 403),
        ("recaptcha_trafico_inusual.html", 200),
        ("datadome.html", 403),
    ],
)
def test_bloqueo_lanza_y_no_reintenta(tmp_path: Path, nombre: str, estado: int) -> None:
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA, fixture(nombre), estado=estado)}, cache=tmp_path)
    with pytest.raises(Bloqueado) as error:
        m.obtener(BUSQUEDA)
    assert error.value.url == BUSQUEDA
    assert error.value.motivo
    assert m.http.pedidas == [BUSQUEDA]
    assert list(tmp_path.iterdir()) == []  # el desafío no se guarda en caché


def test_usa_la_cache(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA, fixture("ficha_con_recaptcha.html"))}, cache=tmp_path)
    primera = m.obtener(FICHA)
    segunda = m.obtener(FICHA)
    assert not primera.desde_cache
    assert segunda.desde_cache and segunda.html == primera.html
    assert m.http.pedidas == [FICHA]


def test_sin_cache_si_se_pide(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA)}, cache=tmp_path)
    m.obtener(FICHA)
    m.obtener(FICHA, usar_cache=False)
    assert m.http.pedidas == [FICHA, FICHA]


def test_la_cache_tambien_anota_el_robots_ignorado(tmp_path: Path) -> None:
    carrito = f"{TIENDA}/es/carrito"
    m = Montaje({}, cache=tmp_path)
    assert m.cache is not None
    m.cache.guardar(pagina(carrito))
    assert m.obtener(carrito).desde_cache
    assert m.navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_errores_http_no_se_guardan(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA, "<h1>No encontrada</h1>", estado=404)}, cache=tmp_path)
    assert m.obtener(FICHA).estado == 404
    assert m.obtener(FICHA).estado == 404
    assert m.http.pedidas == [FICHA, FICHA]


def test_js_usa_chromium(tmp_path: Path) -> None:
    m = Montaje({FICHA: pagina(FICHA, "renderizada")}, chromium=True, cache=tmp_path)
    m.obtener(FICHA, js=True)
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]
    assert m.http.pedidas == []
    # La versión con JavaScript no se sirve a quien pide la descarga directa.
    m.obtener(FICHA)
    assert m.http.pedidas == [FICHA]


def test_js_sin_chromium() -> None:
    m = Montaje({FICHA: pagina(FICHA)})
    with pytest.raises(ErrorDescarga):
        m.obtener(FICHA, js=True)


def test_redireccion_final_prohibida_por_robots_se_usa_y_queda_anotada() -> None:
    carrito = f"{TIENDA}/es/carrito"
    m = Montaje({BUSQUEDA: pagina(BUSQUEDA, url_final=carrito)})
    assert m.obtener(BUSQUEDA).url_final == carrito
    assert m.navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_errores_del_descargador_se_propagan() -> None:
    m = Montaje({FICHA: ErrorDescarga("La página es demasiado grande.")})
    with pytest.raises(ErrorDescarga, match="grande"):
        m.obtener(FICHA)


def test_cerrar_cierra_los_descargadores() -> None:
    m = Montaje({}, chromium=True)
    asyncio.run(m.navegador.cerrar())
    assert m.http.cerrado
    assert m.chromium is not None and m.chromium.cerrado


# --- Rescate con Chromium tras un bloqueo por HTTP (ADR-023) ------------------------------------

RENDERIZADA = "<html><body><h1>Pastillas</h1><p>38,90 €</p></body></html>"
SECO = "<html><body>Forbidden</body></html>"


def desafio() -> Pagina:
    return pagina(FICHA, fixture("cloudflare_just_a_moment.html"), estado=403)


def test_bloqueo_por_http_se_reintenta_una_vez_con_chromium() -> None:
    m = Montaje({FICHA: desafio()}, paginas_js={FICHA: pagina(FICHA, RENDERIZADA)})
    resultado = m.obtener(FICHA)
    assert resultado.html == RENDERIZADA
    assert m.http.pedidas == [FICHA]
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]


def test_un_403_seco_tambien_se_reintenta_con_chromium() -> None:
    m = Montaje(
        {FICHA: pagina(FICHA, SECO, estado=403)}, paginas_js={FICHA: pagina(FICHA, RENDERIZADA)}
    )
    assert m.obtener(FICHA).html == RENDERIZADA


def test_el_reintento_respeta_la_cortesia() -> None:
    m = Montaje({FICHA: desafio()}, paginas_js={FICHA: pagina(FICHA, RENDERIZADA)})
    m.obtener(FICHA)
    # Tres peticiones a la misma tienda (robots.txt, HTTP y Chromium): cada turno tras el primero
    # espera el intervalo (1 s).
    assert m.reloj.esperas == [pytest.approx(1.0), pytest.approx(1.0)]


def test_si_chromium_tambien_esta_bloqueado_se_para() -> None:
    m = Montaje({FICHA: desafio()}, paginas_js={FICHA: desafio()})
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]
    assert m.http.pedidas == [FICHA]


def test_un_429_normal_no_se_repite_con_chromium() -> None:
    m = Montaje(
        {FICHA: pagina(FICHA, "<p>Too many requests</p>", estado=429)},
        paginas_js={FICHA: pagina(FICHA, RENDERIZADA)},
    )
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    assert m.chromium is not None and m.chromium.pedidas == []


@pytest.mark.parametrize("estado", [401, 451])
def test_401_y_451_no_se_repiten_con_chromium(estado: int) -> None:
    # 401: pide credenciales (nunca se inicia sesión). 451: motivo legal o geográfico.
    m = Montaje(
        {FICHA: pagina(FICHA, SECO, estado=estado)},
        paginas_js={FICHA: pagina(FICHA, RENDERIZADA)},
    )
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    assert m.chromium is not None and m.chromium.pedidas == []


def test_estados_sin_rescate() -> None:
    assert frozenset({401, 451}) == ESTADOS_SIN_RESCATE


def _reset_en_la_causa() -> ErrorDescarga:
    error = ErrorDescarga("Error de red.")
    error.__cause__ = ConnectionResetError(10054, "reset")
    return error


@pytest.mark.parametrize(
    "corte",
    [ConexionCortada("La tienda cortó la conexión."), _reset_en_la_causa()],
    ids=["conexion-cortada", "reset-en-la-causa"],
)
def test_conexion_cortada_por_http_se_reintenta_con_chromium(corte: ErrorDescarga) -> None:
    m = Montaje({FICHA: corte}, paginas_js={FICHA: pagina(FICHA, RENDERIZADA)})
    assert m.obtener(FICHA).html == RENDERIZADA
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]


def test_si_chromium_falla_se_mantiene_el_bloqueo() -> None:
    fallo = ErrorDescarga("No se pudo arrancar Chromium: Executable doesn't exist")
    m = Montaje({FICHA: desafio()}, paginas_js={FICHA: fallo})
    with pytest.raises(Bloqueado) as error:
        m.obtener(FICHA)
    assert error.value.__cause__ is fallo


def test_si_chromium_falla_tras_un_corte_se_mantiene_el_corte() -> None:
    corte = ConexionCortada("La tienda cortó la conexión.")
    m = Montaje({FICHA: corte}, paginas_js={FICHA: ErrorDescarga("No se pudo arrancar Chromium")})
    with pytest.raises(ConexionCortada) as error:
        m.obtener(FICHA)
    assert error.value is corte


class ErrorAjeno(Exception):
    """Un error que no es de `navegador.tipos` (p. ej. `playwright.Error` sin traducir)."""


@pytest.mark.parametrize("corte", [False, True], ids=["bloqueo", "corte"])
def test_si_chromium_falla_con_un_error_ajeno_se_relanza_el_original(corte: bool) -> None:
    # El rescate es un extra: si Chromium falla con algo inesperado, quien llama ve lo mismo
    # que sin Chromium (antes salía el error ajeno y la tienda acababa como INESPERADO).
    original = ConexionCortada("La tienda cortó la conexión.") if corte else desafio()
    fallo = ErrorAjeno("Target page, context or browser has been closed")
    m = Montaje({FICHA: original}, paginas_js={FICHA: fallo})
    with pytest.raises(ConexionCortada if corte else Bloqueado) as error:
        m.obtener(FICHA)
    if corte:
        assert error.value is original
    else:
        assert error.value.__cause__ is fallo
    # La tienda no queda marcada para Chromium.
    assert m.navegador._por_chromium == set()


def test_url_no_permitida_de_chromium_se_propaga() -> None:
    m = Montaje({FICHA: desafio()}, paginas_js={FICHA: URLNoPermitida("Salto a la red interna.")})
    with pytest.raises(URLNoPermitida):
        m.obtener(FICHA)


def test_otro_error_de_descarga_no_se_repite() -> None:
    m = Montaje(
        {FICHA: ErrorDescarga("La página es demasiado grande.")},
        paginas_js={FICHA: pagina(FICHA, RENDERIZADA)},
    )
    with pytest.raises(ErrorDescarga, match="grande"):
        m.obtener(FICHA)
    assert m.chromium is not None and m.chromium.pedidas == []


def test_sin_chromium_se_lanza_bloqueado() -> None:
    m = Montaje({FICHA: desafio()})
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)


def test_js_bloqueado_no_reintenta() -> None:
    m = Montaje({FICHA: desafio()}, chromium=True)
    with pytest.raises(Bloqueado):
        m.obtener(FICHA, js=True)
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]
    assert m.http.pedidas == []


def test_bloqueo_en_robots_no_va_por_chromium() -> None:
    # El robots.txt nunca se rescata con Chromium; como se ignora (ADR-017), un desafío en él no
    # corta la visita: la página se pide por HTTP como siempre.
    m = Montaje({FICHA: pagina(FICHA)}, paginas_js={FICHA: pagina(FICHA, RENDERIZADA)})
    m.robots_http.respuestas[ROBOTS] = pagina(
        ROBOTS, fixture("cloudflare_just_a_moment.html"), estado=403
    )
    assert not m.obtener(FICHA).por_chromium
    assert m.chromium is not None and m.chromium.pedidas == []
    assert m.robots_http.pedidas == [ROBOTS] and m.http.pedidas == [FICHA]


def test_tienda_rescatada_va_directa_a_chromium() -> None:
    otra = f"{TIENDA}/frenos/otra.html"
    m = Montaje(
        {FICHA: desafio(), otra: pagina(otra)},
        paginas_js={FICHA: pagina(FICHA, RENDERIZADA), otra: pagina(otra, RENDERIZADA)},
    )
    m.obtener(FICHA)
    # Otra página de la misma tienda: ya no se intenta por HTTP.
    m.obtener(otra)
    assert m.http.pedidas == [FICHA]
    assert m.chromium is not None and m.chromium.pedidas == [FICHA, otra]


def test_lo_rescatado_se_guarda_en_cache(tmp_path: Path) -> None:
    m = Montaje({FICHA: desafio()}, paginas_js={FICHA: pagina(FICHA, RENDERIZADA)}, cache=tmp_path)
    m.obtener(FICHA)
    segunda = m.obtener(FICHA)
    assert segunda.desde_cache and segunda.html == RENDERIZADA
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]


def test_con_proxy_chromium_sale_por_el_proxy_si_la_tienda_esta_marcada() -> None:
    # HTTP directo bloqueado → HTTP por el proxy bloqueado (la tienda queda marcada) → un único
    # intento con Chromium, que ya sale por el proxy. Como mucho 3 peticiones, sin bucles.
    uso = UsoProxy()
    http_directo, http_proxy = (
        DescargadorFalso({FICHA: desafio()}),
        DescargadorFalso({FICHA: desafio()}),
    )
    chromium_directo = DescargadorFalso({FICHA: pagina(FICHA, RENDERIZADA)})
    chromium_proxy = DescargadorFalso({FICHA: pagina(FICHA, RENDERIZADA)})
    reloj = Reloj()
    navegador = Navegador(
        http=DescargadorConProxy(http_directo, http_proxy, uso=uso, dormir=reloj.dormir),
        chromium=DescargadorConProxy(
            chromium_directo, chromium_proxy, uso=uso, dormir=reloj.dormir
        ),
        robots=ServicioRobots(
            DescargadorFalso({ROBOTS: pagina(ROBOTS, "User-agent: *\n", tipo="text/plain")}),
            UA,
            reloj=reloj,
        ),
        limitador=LimitadorDominio(1.0, reloj=reloj, dormir=reloj.dormir),
        validador=validar_con_dns_publico,
    )
    resultado = asyncio.run(navegador.obtener(FICHA))
    assert resultado.html == RENDERIZADA and resultado.via_proxy
    assert http_directo.pedidas == [FICHA] and http_proxy.pedidas == [FICHA]
    assert chromium_directo.pedidas == [] and chromium_proxy.pedidas == [FICHA]


# --- Lo que ya vino de Chromium no se vuelve a pedir con Chromium --------------------------------

OTRA = f"{TIENDA}/frenos/otra.html"
GRANDE_403 = "<html><body>" + "<p>Acceso restringido.</p>" * 2000 + "</body></html>"


def test_pagina_dice_si_vino_de_chromium() -> None:
    m = Montaje(
        {FICHA: desafio(), BUSQUEDA: pagina(BUSQUEDA)},
        paginas_js={FICHA: pagina(FICHA, RENDERIZADA), OTRA: pagina(OTRA, RENDERIZADA)},
    )
    assert not m.obtener(BUSQUEDA).por_chromium  # por HTTP, antes del rescate
    assert m.obtener(FICHA).por_chromium  # rescatada
    assert m.obtener(OTRA).por_chromium  # tienda marcada: va directa a Chromium
    assert m.obtener(OTRA, js=True).por_chromium


def test_tienda_rescatada_no_pide_dos_veces_con_chromium_la_misma_url(tmp_path: Path) -> None:
    # El descubrimiento prueba cada candidata con js=False y luego con js=True. En una tienda
    # rescatada las dos van por Chromium: la segunda sale de la caché.
    m = Montaje(
        {FICHA: desafio()},
        paginas_js={FICHA: pagina(FICHA, RENDERIZADA), OTRA: pagina(OTRA, RENDERIZADA)},
        cache=tmp_path,
    )
    m.obtener(FICHA)
    m.obtener(OTRA)
    segunda = m.obtener(OTRA, js=True)
    assert segunda.desde_cache and segunda.por_chromium
    assert m.obtener(FICHA, js=True).desde_cache
    assert m.chromium is not None and m.chromium.pedidas == [FICHA, OTRA]
    assert m.http.pedidas == [FICHA]


def test_rescate_que_da_un_403_no_marca_la_tienda() -> None:
    # Un 403 grande con Chromium (sin señales de bloqueo) no es un rescate que haya funcionado:
    # la siguiente página de la tienda se sigue pidiendo por HTTP.
    m = Montaje(
        {FICHA: desafio(), OTRA: pagina(OTRA)},
        paginas_js={FICHA: pagina(FICHA, GRANDE_403, estado=403)},
    )
    assert m.obtener(FICHA).estado == 403
    assert m.obtener(OTRA).html == pagina(OTRA).html
    assert m.http.pedidas == [FICHA, OTRA]
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]


def test_un_429_de_kasada_si_se_rescata_con_chromium() -> None:
    # Solo el 429 normal (`MOTIVO_LIMITE`, la tienda pide ir más despacio) se queda sin rescate.
    # Kasada usa el 429 para su desafío: es un muro anti-bot y se prueba una vez con Chromium.
    kasada = pagina(FICHA, fixture("kasada_429.html"), estado=429)
    m = Montaje({FICHA: kasada}, paginas_js={FICHA: pagina(FICHA, RENDERIZADA)})
    assert m.obtener(FICHA).html == RENDERIZADA
    assert m.chromium is not None and m.chromium.pedidas == [FICHA]


# --- Una página a medias no se guarda en la caché ------------------------------------------------

A_MEDIAS = (
    Path(__file__).parent.parent
    / "fixtures"
    / "reales"
    / "amazon_portada_202_solo_cabecera_chromium.html"
).read_text(encoding="utf-8")


def test_un_2xx_distinto_de_200_sin_cuerpo_no_se_guarda_en_cache(tmp_path: Path) -> None:
    # Encontrado por Julio al probar la app con amazon.es: Chromium capturó la portada a medias
    # tras el desafío anti-bot (solo el `<head>`, con el 202 del desafío). Guardada, «Reintentar»
    # volvía a usarla durante las 6 h de la caché.
    m = Montaje(
        {FICHA: desafio()},
        paginas_js={FICHA: pagina(FICHA, A_MEDIAS, estado=202)},
        cache=tmp_path,
    )
    assert m.obtener(FICHA).html == A_MEDIAS
    assert list(tmp_path.iterdir()) == []
    m.obtener(FICHA)
    assert m.chromium is not None and m.chromium.pedidas == [FICHA, FICHA]


@pytest.mark.parametrize(
    ("estado", "html", "tipo"),
    [
        (202, RENDERIZADA, "text/html"),  # Un 202 con su `<body>` es una página completa.
        (200, "<html><head><title>Ficha</title></head></html>", "text/html"),
        (202, "<?xml version='1.0'?><OpenSearchDescription/>", "application/xml"),
    ],
    ids=["202-con-cuerpo", "200-sin-body", "202-xml"],
)
def test_lo_demas_se_sigue_guardando_en_cache(
    tmp_path: Path, estado: int, html: str, tipo: str
) -> None:
    m = Montaje({FICHA: pagina(FICHA, html, estado=estado, tipo=tipo)}, cache=tmp_path)
    m.obtener(FICHA)
    assert m.obtener(FICHA).desde_cache
