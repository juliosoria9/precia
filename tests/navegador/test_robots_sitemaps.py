"""Líneas `Sitemap:` del robots.txt (nota 16, sección B): fuera de grupo y en cualquier sitio."""

import asyncio
import time

import pytest

from precia.navegador.robots import MAX_SITEMAPS_ROBOTS, ServicioRobots, analizar_robots
from precia.navegador.tipos import ErrorDescarga, Pagina
from tests.navegador.falsos import DescargadorFalso, Reloj, pagina

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
TIENDA = "https://www.recambios-ejemplo.example"
ROBOTS = f"{TIENDA}/robots.txt"


def test_sitemaps_en_cualquier_sitio_del_fichero() -> None:
    texto = (
        f"Sitemap: {TIENDA}/sitemap_index.xml\n"
        "User-agent: *\n"
        "Disallow: /carrito\n"
        f"sitemap:   {TIENDA}/product-sitemap.xml   \n"
        "\n"
        "User-agent: otrobot\n"
        "Disallow: /\n"
        f"SITEMAP: {TIENDA}/1_index_sitemap.xml # comentario\n"
    )
    reglas = analizar_robots(texto, "precia")
    assert reglas.sitemaps == (
        f"{TIENDA}/sitemap_index.xml",
        f"{TIENDA}/product-sitemap.xml",
        f"{TIENDA}/1_index_sitemap.xml",
    )
    # Las líneas `Sitemap:` no cambian los grupos: siguen valiendo las reglas de «*».
    assert not reglas.permite("/carrito")
    assert reglas.permite("/ficha")


def test_sitemap_no_parte_un_grupo() -> None:
    texto = "User-agent: precia\nSitemap: https://a.example/s.xml\nDisallow: /privado\n"
    reglas = analizar_robots(texto, "precia")
    assert not reglas.permite("/privado")
    assert reglas.sitemaps == ("https://a.example/s.xml",)


@pytest.mark.parametrize(
    "valor",
    [
        "/sitemap.xml",
        "sitemap.xml",
        "ftp://www.recambios-ejemplo.example/sitemap.xml",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
        "https:///sin-host.xml",
        "https://[::1/roto.xml",
        "https://www.recambios-ejemplo.example/con espacio.xml",
        "https://www.recambios-ejemplo.example/\x00nulo.xml",
        "",
    ],
)
def test_solo_url_absolutas_http(valor: str) -> None:
    reglas = analizar_robots(f"Sitemap: {valor}\n", "precia")
    assert reglas.sitemaps == ()


def test_http_y_https_valen() -> None:
    texto = "Sitemap: http://a.example/s.xml\nSitemap: HTTPS://a.example/t.xml.gz\n"
    assert analizar_robots(texto, "precia").sitemaps == (
        "http://a.example/s.xml",
        "HTTPS://a.example/t.xml.gz",
    )


def test_sin_duplicados_y_como_mucho_veinte() -> None:
    lineas = [f"Sitemap: {TIENDA}/s{i}.xml" for i in range(100)]
    texto = "\n".join([lineas[0], *lineas]) + "\n"
    sitemaps = analizar_robots(texto, "precia").sitemaps
    assert MAX_SITEMAPS_ROBOTS == 20
    assert sitemaps == tuple(f"{TIENDA}/s{i}.xml" for i in range(20))


def test_url_demasiado_larga_se_descarta() -> None:
    larga = f"{TIENDA}/" + "a" * 5000 + ".xml"
    texto = f"Sitemap: {larga}\nSitemap: {TIENDA}/bueno.xml\n"
    assert analizar_robots(texto, "precia").sitemaps == (f"{TIENDA}/bueno.xml",)


def test_robots_hostil_con_muchisimos_sitemaps_es_rapido() -> None:
    texto = "".join(f"Sitemap: {TIENDA}/{'x' * 40}{i}.xml\n" for i in range(8000))
    inicio = time.perf_counter()
    reglas = analizar_robots(texto, "precia")
    assert time.perf_counter() - inicio < 1.0
    assert len(reglas.sitemaps) == MAX_SITEMAPS_ROBOTS


# --- ServicioRobots.sitemaps --------------------------------------------------------------------


def servicio(robots: Pagina | Exception) -> tuple[ServicioRobots, DescargadorFalso]:
    descargador = DescargadorFalso({ROBOTS: robots})
    return ServicioRobots(descargador, UA, reloj=Reloj()), descargador


def test_servicio_lee_el_robots_si_hace_falta() -> None:
    robots = pagina(
        ROBOTS, f"User-agent: *\nDisallow:\nSitemap: {TIENDA}/s.xml\n", tipo="text/plain"
    )
    robots_servicio, descargador = servicio(robots)
    assert asyncio.run(robots_servicio.sitemaps(f"{TIENDA}/cualquier/ruta?x=1")) == (
        f"{TIENDA}/s.xml",
    )
    assert descargador.pedidas == [ROBOTS]


def test_servicio_reutiliza_el_robots_ya_leido() -> None:
    robots = pagina(ROBOTS, f"Sitemap: {TIENDA}/s.xml\n", tipo="text/plain")
    robots_servicio, descargador = servicio(robots)

    async def las_dos() -> tuple[bool, tuple[str, ...]]:
        permitido = await robots_servicio.permitido(f"{TIENDA}/ficha")
        return permitido, await robots_servicio.sitemaps(TIENDA + "/")

    assert asyncio.run(las_dos()) == (True, (f"{TIENDA}/s.xml",))
    assert descargador.pedidas == [ROBOTS]


@pytest.mark.parametrize(
    "robots",
    [
        pagina(ROBOTS, "No encontrado", estado=404),
        pagina(ROBOTS, "Error", estado=503),
        ErrorDescarga("sin red"),
        # Desafío de Cloudflare en el robots.txt: no hay sitemaps que leer (y el navegador lo trata
        # como `Bloqueado` al pedir cualquier cosa de la tienda).
        pagina(
            ROBOTS,
            "<html><head><title>Just a moment...</title></head><body>cloudflare</body></html>",
            estado=403,
        ),
    ],
)
def test_servicio_sin_robots_no_hay_sitemaps(robots: Pagina | Exception) -> None:
    robots_servicio, _ = servicio(robots)
    assert asyncio.run(robots_servicio.sitemaps(TIENDA)) == ()
