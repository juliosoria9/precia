"""ADR-017: el robots.txt se puede ignorar, pero un anti-bot en el propio robots.txt nunca."""

import asyncio
from pathlib import Path

import pytest

from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.navegador import Navegador
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import Bloqueado, Pagina, ProhibidoPorRobots
from tests.navegador.falsos import DescargadorFalso, Reloj, pagina, validar_con_dns_publico

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
UA = "PrecIA/0.1 (proyecto universitario; uso personal)"
TIENDA = "https://www.recambios-ejemplo.example"
ROBOTS = f"{TIENDA}/robots.txt"
BUSQUEDA = f"{TIENDA}/buscar?q=1K0698151"
PROHIBE_BUSQUEDA = "User-agent: *\nDisallow: /buscar\nCrawl-delay: 7\n"


def montar(robots: Pagina, *, respetar_robots: bool) -> tuple[Navegador, DescargadorFalso, Reloj]:
    reloj = Reloj()
    http = DescargadorFalso({BUSQUEDA: pagina(BUSQUEDA)})
    navegador = Navegador(
        http=http,
        robots=ServicioRobots(DescargadorFalso({ROBOTS: robots}), UA, reloj=reloj),
        limitador=LimitadorDominio(1.0, reloj=reloj, dormir=reloj.dormir),
        validador=validar_con_dns_publico,
        respetar_robots=respetar_robots,
    )
    return navegador, http, reloj


def robots_txt(texto: str) -> Pagina:
    return pagina(ROBOTS, texto, tipo="text/plain")


def test_por_defecto_se_respeta_el_robots() -> None:
    navegador, http, _ = montar(robots_txt(PROHIBE_BUSQUEDA), respetar_robots=True)
    with pytest.raises(ProhibidoPorRobots):
        asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == []


def test_si_se_ignora_se_descarga_y_queda_anotado() -> None:
    navegador, http, _ = montar(robots_txt(PROHIBE_BUSQUEDA), respetar_robots=False)
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA]
    assert navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_si_el_robots_permite_no_se_anota_como_ignorado() -> None:
    navegador, _, _ = montar(robots_txt("User-agent: *\nAllow: /\n"), respetar_robots=False)
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert not navegador.robots_ignorado("www.recambios-ejemplo.example")


def test_aunque_se_ignore_se_respeta_el_crawl_delay() -> None:
    navegador, http, reloj = montar(robots_txt(PROHIBE_BUSQUEDA), respetar_robots=False)

    async def dos() -> None:
        await navegador.obtener(BUSQUEDA, usar_cache=False)
        await navegador.obtener(BUSQUEDA, usar_cache=False)

    asyncio.run(dos())
    assert len(http.pedidas) == 2
    assert sum(reloj.esperas) >= 7


@pytest.mark.parametrize("respetar", [True, False])
def test_un_desafio_anti_bot_en_el_robots_es_un_bloqueo(respetar: bool) -> None:
    # Caso real (oscaro.es, 2026-10-19): el propio robots.txt responde 403 con el desafío de
    # Cloudflare. El RFC 9309 trataría un 4xx como «permitido todo»; aquí es un muro anti-bot.
    desafio = (FIXTURES / "cloudflare_just_a_moment.html").read_text(encoding="utf-8")
    navegador, http, _ = montar(pagina(ROBOTS, desafio, estado=403), respetar_robots=respetar)
    with pytest.raises(Bloqueado):
        asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == []


def test_un_404_en_el_robots_sigue_siendo_permitido() -> None:
    navegador, http, _ = montar(pagina(ROBOTS, "No encontrado", estado=404), respetar_robots=True)
    asyncio.run(navegador.obtener(BUSQUEDA))
    assert http.pedidas == [BUSQUEDA]
