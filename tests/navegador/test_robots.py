"""robots.txt según el RFC 9309: grupos, comodines, coincidencia más larga y códigos de estado."""

import asyncio
from pathlib import Path

import pytest

from precia.navegador.robots import (
    MAXIMO_BYTES,
    MAXIMO_RETARDO_S,
    ServicioRobots,
    analizar_robots,
    token_producto,
)
from precia.navegador.tipos import ErrorDescarga, Pagina, URLNoPermitida
from tests.navegador.falsos import DescargadorFalso, Reloj, pagina

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
TIENDA = "https://www.recambios-ejemplo.example"
ROBOTS = f"{TIENDA}/robots.txt"


def prestashop() -> str:
    return (FIXTURES / "robots_prestashop.txt").read_text(encoding="utf-8")


def test_token_de_producto() -> None:
    assert token_producto(UA) == "precia"
    assert token_producto("Mozilla/5.0 (compatible; PrecIA/0.1)") == "mozilla"


@pytest.mark.parametrize(
    ("ruta", "permitida"),
    [
        ("/es/busqueda?s=pastillas", True),
        ("/es/carrito", False),
        ("/es/carrito?add=1", False),
        # El grupo de PrecIA sustituye al de «*»: lo que solo prohíbe «*» no nos afecta.
        ("/es/mi-cuenta", True),
        ("/catalogo?order=price", True),
    ],
)
def test_usa_el_grupo_de_nuestro_user_agent(ruta: str, permitida: bool) -> None:
    reglas = analizar_robots(prestashop(), "precia")
    assert reglas.permite(ruta) is permitida
    assert reglas.retardo == 5.0


@pytest.mark.parametrize(
    ("ruta", "permitida"),
    [
        ("/", True),
        ("/3-pastillas-de-freno", True),
        ("/es/busqueda", False),
        # «Allow: /es/busqueda?s=» es más largo que «Disallow: /es/busqueda»: gana el más largo.
        ("/es/busqueda?s=pastillas", True),
        ("/catalogo?order=price.asc", False),
        ("/catalogo?q=Marca-Bosch&order=price", False),
        ("/index.php?controller=search&s=freno", False),
        ("/themes/classic/modules/ps_searchbar/ps_searchbar.css", True),
        ("/themes/classic/modules/ps_searchbar/logo.png", False),
        ("/docs/ficha-tecnica.pdf", False),
        ("/docs/ficha-tecnica.pdf?v=2", True),
        ("/robots.txt", True),
    ],
)
def test_grupo_comodin_con_comodines_y_anclas(ruta: str, permitida: bool) -> None:
    reglas = analizar_robots(prestashop(), "otrobot")
    assert reglas.permite(ruta) is permitida
    assert reglas.retardo is None


def test_varios_user_agent_comparten_grupo() -> None:
    reglas = analizar_robots(prestashop(), "semrushbot")
    assert not reglas.permite("/")
    assert reglas.permite("/robots.txt")


def test_los_grupos_del_mismo_agente_se_fusionan() -> None:
    texto = (
        "User-agent: precia\nDisallow: /a\n\n"
        "User-agent: otro\nDisallow: /\n\n"
        "User-Agent: PRECIA\nDisallow: /b\n"
    )
    reglas = analizar_robots(texto, "precia")
    assert not reglas.permite("/a") and not reglas.permite("/b")
    assert reglas.permite("/c")


def test_el_agente_se_compara_entero_no_como_subcadena() -> None:
    # robotparser de la stdlib aceptaría «Pre» como grupo de «PrecIA»: aquí no.
    texto = "User-agent: Pre\nDisallow: /\n\nUser-agent: *\nDisallow: /privado\n"
    reglas = analizar_robots(texto, "precia")
    assert reglas.permite("/")
    assert not reglas.permite("/privado")


def test_agente_con_version_en_la_linea() -> None:
    reglas = analizar_robots("User-agent: PrecIA/2.0\nDisallow: /x\n", "precia")
    assert not reglas.permite("/x")


def test_sin_grupo_aplicable_se_permite_todo() -> None:
    reglas = analizar_robots("User-agent: Googlebot\nDisallow: /\n", "precia")
    assert reglas.permite("/cualquier/cosa")


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "User-agent: *\nDisallow:\n",
        "<!doctype html><html><body>Página no encontrada</body></html>",
        "Disallow: /\n",  # regla fuera de cualquier grupo
        "User-agent: *\nDisallow /sin-dos-puntos\n",
    ],
)
def test_ficheros_vacios_o_raros_permiten(texto: str) -> None:
    assert analizar_robots(texto, "precia").permite("/sin-dos-puntos")


def test_empate_gana_allow() -> None:
    reglas = analizar_robots("User-agent: *\nDisallow: /pagina\nAllow: /pagina\n", "precia")
    assert reglas.permite("/pagina")


def test_formato_tolerante() -> None:
    texto = (
        "﻿USER-AGENT : *   # todos\r\nDISALLOW:/privado # comentario\r\nallow: /privado/publico\r"
    )
    reglas = analizar_robots(texto, "precia")
    assert not reglas.permite("/privado/x")
    assert reglas.permite("/privado/publico/y")


@pytest.mark.parametrize(
    ("regla", "ruta"),
    [
        ("/pi%C3%A8ce", "/pi%C3%A8ce/1"),
        ("/pièce", "/pi%C3%A8ce/1"),
        ("/pi%c3%a8ce", "/pi%C3%A8ce/1"),
        ("/%7Eusuario", "/~usuario/"),
        ("/~usuario", "/%7Eusuario/"),
    ],
)
def test_codificacion_porcentual_equivalente(regla: str, ruta: str) -> None:
    reglas = analizar_robots(f"User-agent: *\nDisallow: {regla}\n", "precia")
    assert not reglas.permite(ruta)


def test_barra_codificada_no_equivale_a_barra() -> None:
    reglas = analizar_robots("User-agent: *\nDisallow: /a/b\n", "precia")
    assert reglas.permite("/a%2Fb")


def test_comodines_sin_retroceso_exponencial() -> None:
    reglas = analizar_robots("User-agent: *\nDisallow: /" + "*a" * 200 + "b$\n", "precia")
    assert reglas.permite("/" + "a" * 2000)


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("2", 2.0), ("0.5", 0.5), ("-3", None), ("nan", None), ("inf", None), ("abc", None)],
)
def test_crawl_delay(valor: str, esperado: float | None) -> None:
    reglas = analizar_robots(f"User-agent: *\nCrawl-delay: {valor}\n", "precia")
    assert reglas.retardo == esperado


def test_crawl_delay_desorbitado_se_acota() -> None:
    reglas = analizar_robots("User-agent: *\nCrawl-delay: 86400\n", "precia")
    assert reglas.retardo == MAXIMO_RETARDO_S


def test_solo_se_leen_los_primeros_500_kib() -> None:
    relleno = "# " + "x" * 100 + "\n"
    texto = "User-agent: *\nDisallow: /antes\n" + relleno * (MAXIMO_BYTES // len(relleno) + 1)
    texto += "Disallow: /despues\n"
    reglas = analizar_robots(texto, "precia")
    assert not reglas.permite("/antes")
    assert reglas.permite("/despues")


def test_linea_cortada_por_el_limite_se_descarta() -> None:
    # Si el corte deja «Allow: /» de lo que era «Allow: /solo-esto», permitiría de más.
    cabecera = "User-agent: *\nDisallow: /\n"
    corte = MAXIMO_BYTES - len(cabecera) - len("\nAllow: /")
    texto = cabecera + "#" * corte + "\nAllow: /solo-esto\n"
    reglas = analizar_robots(texto, "precia")
    assert not reglas.permite("/otra")


# --- Servicio: descarga, códigos de estado y caché ---------------------------------------------


def servicio(
    respuesta: Pagina | Exception, reloj: Reloj | None = None
) -> tuple[ServicioRobots, DescargadorFalso]:
    descargador = DescargadorFalso({ROBOTS: respuesta})
    return ServicioRobots(descargador, UA, reloj=reloj or Reloj()), descargador


def test_servicio_aplica_el_robots_descargado() -> None:
    robots, descargador = servicio(pagina(ROBOTS, prestashop(), tipo="text/plain"))
    assert asyncio.run(robots.permitido(f"{TIENDA}/es/busqueda?s=pastillas"))
    assert not asyncio.run(robots.permitido(f"{TIENDA}/es/carrito"))
    assert descargador.pedidas == [ROBOTS]
    assert robots.retardo("www.recambios-ejemplo.example") == 5.0
    assert robots.retardo("otra.example") is None


@pytest.mark.parametrize(
    ("estado", "permitido"),
    [(404, True), (401, True), (403, True), (410, True), (429, False), (500, False), (503, False)],
)
def test_codigos_de_estado(estado: int, permitido: bool) -> None:
    robots, _ = servicio(pagina(ROBOTS, "User-agent: *\nDisallow: /\n", estado=estado))
    assert asyncio.run(robots.permitido(f"{TIENDA}/ficha")) is permitido


@pytest.mark.parametrize("error", [ErrorDescarga("tiempo agotado"), URLNoPermitida("privada")])
def test_errores_de_red_prohiben_todo(error: Exception) -> None:
    robots, _ = servicio(error)
    assert not asyncio.run(robots.permitido(f"{TIENDA}/ficha"))


def test_cache_por_origen_con_caducidad() -> None:
    reloj = Reloj()
    robots, descargador = servicio(pagina(ROBOTS, "User-agent: *\nDisallow: /x\n"), reloj)
    asyncio.run(robots.permitido(f"{TIENDA}/a"))
    asyncio.run(robots.permitido(f"{TIENDA}/b"))
    assert len(descargador.pedidas) == 1
    reloj.ahora += 24 * 3600 + 1
    asyncio.run(robots.permitido(f"{TIENDA}/c"))
    assert len(descargador.pedidas) == 2


def test_los_errores_se_recuerdan_menos_tiempo() -> None:
    reloj = Reloj()
    robots, descargador = servicio(pagina(ROBOTS, "", estado=503), reloj)
    asyncio.run(robots.permitido(f"{TIENDA}/a"))
    reloj.ahora += 11 * 60
    descargador.respuestas[ROBOTS] = pagina(ROBOTS, "User-agent: *\nAllow: /\n")
    assert asyncio.run(robots.permitido(f"{TIENDA}/a"))
    assert len(descargador.pedidas) == 2


def test_peticiones_simultaneas_descargan_una_vez() -> None:
    robots, descargador = servicio(pagina(ROBOTS, "User-agent: *\nDisallow: /x\n"))

    async def varias() -> list[bool]:
        urls = [f"{TIENDA}/{i}" for i in range(5)]
        return list(await asyncio.gather(*(robots.permitido(u) for u in urls)))

    assert asyncio.run(varias()) == [True] * 5
    assert descargador.pedidas == [ROBOTS]


def test_cada_origen_tiene_su_robots() -> None:
    otro = "https://otra-tienda.example/robots.txt"
    descargador = DescargadorFalso(
        {ROBOTS: pagina(ROBOTS, "User-agent: *\nDisallow: /\n"), otro: pagina(otro, "")}
    )
    robots = ServicioRobots(descargador, UA, reloj=Reloj())
    assert not asyncio.run(robots.permitido(f"{TIENDA}/a"))
    assert asyncio.run(robots.permitido("https://otra-tienda.example/a"))


def test_el_propio_robots_siempre_se_permite() -> None:
    robots, descargador = servicio(pagina(ROBOTS, "User-agent: *\nDisallow: /\n"))
    assert asyncio.run(robots.permitido(ROBOTS))
    assert descargador.pedidas == [ROBOTS]
