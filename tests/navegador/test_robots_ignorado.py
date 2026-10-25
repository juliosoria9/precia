"""ADR-017 (enmienda del 2026-10-25): el robots.txt siempre se ignora; no se puede respetar.

Se sigue leyendo para avisar de que lo prohíbe, para su Crawl-delay y para sus sitemaps. Un desafío
anti-bot en el propio robots.txt no prohíbe nada: se sigue con la página, que detecta su propio
bloqueo y hace el único rescate con Chromium de ADR-023 (causa R3-B de la campaña, notino.es).
"""

import asyncio
import inspect
from pathlib import Path

import pytest

from precia.navegador import tipos
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.navegador import Navegador
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import Bloqueado, ErrorDescarga, Pagina
from tests.navegador.falsos import DescargadorFalso, Reloj, pagina, validar_con_dns_publico

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
REALES = Path(__file__).parent.parent / "fixtures" / "reales"
UA = "PrecIA/0.1 (proyecto universitario; uso personal)"
TIENDA = "https://www.recambios-ejemplo.example"
ROBOTS = f"{TIENDA}/robots.txt"
BUSQUEDA = f"{TIENDA}/buscar?q=1K0698151"
PROHIBE_BUSQUEDA = "User-agent: *\nDisallow: /buscar\nCrawl-delay: 7\n"


def montar(robots: Pagina | Exception) -> tuple[Navegador, DescargadorFalso, Reloj]:
    reloj = Reloj()
    http = DescargadorFalso({BUSQUEDA: pagina(BUSQUEDA)})
    navegador = Navegador(
        http=http,
        robots=ServicioRobots(DescargadorFalso({ROBOTS: robots}), UA, reloj=reloj),
        limitador=LimitadorDominio(1.0, reloj=reloj, dormir=reloj.dormir),
        validador=validar_con_dns_publico,
    )
    return navegador, http, reloj


def robots_txt(texto: str) -> Pagina:
    return pagina(ROBOTS, texto, tipo="text/plain")


def test_ya_no_hay_opcion_de_respetar_el_robots() -> None:
    # Julio, 2026-10-25: «quita lo de si lo prohibe robots.txt es inutil».
    assert "respetar_robots" not in inspect.signature(Navegador).parameters
    assert not hasattr(tipos, "ProhibidoPorRobots")


def test_disallow_de_todo_se_descarga_igual_y_queda_anotado() -> None:
    navegador, http, _ = montar(robots_txt("User-agent: *\nDisallow: /\n"))
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA]
    assert navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_si_lo_prohibe_se_descarga_y_queda_anotado() -> None:
    navegador, http, _ = montar(robots_txt(PROHIBE_BUSQUEDA))
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA]
    assert navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_si_el_robots_permite_no_se_anota_como_ignorado() -> None:
    navegador, _, _ = montar(robots_txt("User-agent: *\nAllow: /\n"))
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert not navegador.robots_ignorado("www.recambios-ejemplo.example")


@pytest.mark.parametrize(
    "robots",
    [
        pagina(ROBOTS, "User-agent: *\nDisallow: /\n", estado=500),
        pagina(ROBOTS, "User-agent: *\nDisallow: /\n", estado=503),
        pagina(ROBOTS, "User-agent: *\nDisallow: /\n", estado=429),
        ErrorDescarga("tiempo agotado"),
    ],
    ids=["500", "503", "429", "error-de-red"],
)
def test_un_robots_ilegible_no_se_anota_como_ignorado(robots: Pagina | Exception) -> None:
    # Un 5xx, un 429 o un error de red no dicen qué prohíbe la tienda: no se ha leído ninguna
    # regla, así que el aviso «lo prohíbe; se ignora» sería falso. La página se pide igual.
    navegador, http, _ = montar(robots)
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA]
    assert not navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_aunque_se_ignore_se_respeta_el_crawl_delay() -> None:
    navegador, http, reloj = montar(robots_txt(PROHIBE_BUSQUEDA))

    async def dos() -> None:
        await navegador.obtener(BUSQUEDA, usar_cache=False)
        await navegador.obtener(BUSQUEDA, usar_cache=False)

    asyncio.run(dos())
    assert len(http.pedidas) == 2
    assert sum(reloj.esperas) >= 7


def test_un_desafio_anti_bot_en_el_robots_no_corta_la_visita() -> None:
    # Caso real (oscaro.es, 2026-10-19): el propio robots.txt responde 403 con el desafío de
    # Cloudflare. No se ha leído nada que prohíba: se pide la página igual.
    desafio = (FIXTURES / "cloudflare_just_a_moment.html").read_text(encoding="utf-8")
    navegador, http, _ = montar(pagina(ROBOTS, desafio, estado=403))
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA]


# --- R3-B: desafío en el robots.txt (ADR-017 y ADR-023) ----------------------------------------


def desafio_notino(url: str = ROBOTS) -> Pagina:
    """El robots.txt real de notino.es (campaña, ronda 3): un 403 con el desafío de Cloudflare."""
    html = (REALES / "notino_robots_desafio_cloudflare.html").read_text(encoding="utf-8")
    return pagina(url, html, estado=403)


def montar_con_chromium(
    paginas: dict[str, Pagina | Exception],
    paginas_js: dict[str, Pagina | Exception],
    robots: dict[str, Pagina | Exception] | None = None,
) -> tuple[Navegador, DescargadorFalso, DescargadorFalso]:
    reloj = Reloj()
    http, chromium = DescargadorFalso(paginas), DescargadorFalso(paginas_js)
    navegador = Navegador(
        http=http,
        chromium=chromium,
        robots=ServicioRobots(
            DescargadorFalso(robots or {ROBOTS: desafio_notino()}), UA, reloj=reloj
        ),
        limitador=LimitadorDominio(1.0, reloj=reloj, dormir=reloj.dormir),
        validador=validar_con_dns_publico,
    )
    return navegador, http, chromium


def test_un_desafio_en_el_robots_ignorado_no_corta_la_visita() -> None:
    # Caso real (notino.es, campaña ronda 3): la única petición era el robots.txt. Se trata como
    # si no lo hubiera (ADR-017) y se descarga la página.
    navegador, http, _ = montar(desafio_notino())
    resultado = asyncio.run(navegador.obtener(BUSQUEDA))
    assert resultado.estado == 200
    assert http.pedidas == [BUSQUEDA]


def test_un_robots_que_no_se_pudo_leer_no_se_anota_como_ignorado() -> None:
    # El desafío no prohíbe nada: no se ha leído. Enseñar «lo prohíbe; se ignora» sería falso.
    navegador, _, _ = montar(desafio_notino())
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert not navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_desafio_en_el_robots_y_en_la_pagina_se_rescata_con_chromium() -> None:
    navegador, http, chromium = montar_con_chromium(
        {BUSQUEDA: desafio_notino(BUSQUEDA)},
        {BUSQUEDA: pagina(BUSQUEDA, "<h1>Resultados</h1><p>38,90 €</p>")},
    )
    resultado = asyncio.run(navegador.obtener(BUSQUEDA))
    assert resultado.por_chromium and "38,90" in resultado.html
    assert http.pedidas == [BUSQUEDA] and chromium.pedidas == [BUSQUEDA]


def test_si_chromium_tambien_se_bloquea_es_bloqueado_con_un_solo_intento() -> None:
    navegador, http, chromium = montar_con_chromium(
        {BUSQUEDA: desafio_notino(BUSQUEDA)}, {BUSQUEDA: desafio_notino(BUSQUEDA)}
    )
    with pytest.raises(Bloqueado):
        asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA] and chromium.pedidas == [BUSQUEDA]


def test_redireccion_a_otro_host_con_el_robots_en_desafio() -> None:
    # Como notino.es → www.notino.es: la comprobación tras la redirección tampoco corta.
    sin_www = "https://recambios-ejemplo.example/buscar?q=1K0698151"
    navegador, http, _ = montar_con_chromium(
        {sin_www: pagina(sin_www, url_final=BUSQUEDA)},
        {},
        robots={
            "https://recambios-ejemplo.example/robots.txt": desafio_notino(),
            ROBOTS: desafio_notino(),
        },
    )
    resultado = asyncio.run(navegador.obtener(sin_www))
    assert resultado.url_final == BUSQUEDA and http.pedidas == [sin_www]


def test_el_desafio_del_robots_se_registra_una_vez(caplog: pytest.LogCaptureFixture) -> None:
    navegador, http, _ = montar(desafio_notino())

    async def dos() -> None:
        await navegador.obtener(BUSQUEDA, usar_cache=False)
        await navegador.obtener(BUSQUEDA, usar_cache=False)

    with caplog.at_level("WARNING", logger="precia.navegador.navegador"):
        asyncio.run(dos())
    avisos = [r for r in caplog.records if "robots.txt" in r.getMessage()]
    assert len(avisos) == 1 and "desafío anti-bot" in avisos[0].getMessage()
    assert len(http.pedidas) == 2


def test_un_404_en_el_robots_sigue_siendo_permitido() -> None:
    navegador, http, _ = montar(pagina(ROBOTS, "No encontrado", estado=404))
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA]
