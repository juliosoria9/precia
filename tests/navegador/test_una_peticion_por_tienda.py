"""ADR-017 con varias búsquedas a la vez (B6): una sola petición en vuelo por tienda.

Encontrado en la revisión de la tanda 3 de velocidad: el robots.txt se pedía fuera del turno del
limitador (tras un cambio de ajustes, el navegador nuevo lo pedía con una ficha del viejo aún en
vuelo) y «www.tienda.es» y «tienda.es» tenían filas distintas (un sitemap sin «www.» coincidía con
una ficha con «www.»).
"""

import asyncio

from precia.navegador.cortesia import LimitadorDominio, pausa_de_cortesia
from precia.navegador.navegador import Navegador
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import Pagina
from tests.navegador.falsos import pagina, validar_con_dns_publico

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"


class Contador:
    """Descargador que tarda un poco y cuenta las peticiones en vuelo a la vez."""

    def __init__(self) -> None:
        self.en_vuelo = 0
        self.maximo = 0
        self.pedidas: list[str] = []

    async def descargar(self, url: str) -> Pagina:
        self.pedidas.append(url)
        self.en_vuelo += 1
        self.maximo = max(self.maximo, self.en_vuelo)
        try:
            await asyncio.sleep(0.02)
        finally:
            self.en_vuelo -= 1
        if url.endswith("/robots.txt"):
            return pagina(url, "User-agent: *\nAllow: /\n", tipo="text/plain")
        return pagina(url)

    async def cerrar(self) -> None:
        pass


def _navegador(http: Contador, limitador: LimitadorDominio) -> Navegador:
    return Navegador(
        http=http,
        robots=ServicioRobots(http, UA),
        limitador=limitador,
        validador=validar_con_dns_publico,
    )


def test_el_robots_txt_espera_al_turno_de_su_dominio() -> None:
    http = Contador()
    limitador = LimitadorDominio(0.0)
    servicio = ServicioRobots(http, UA)
    servicio.usar_turnos(limitador)

    async def escenario() -> None:
        async with limitador.turno("www.tienda.example"):
            tarea = asyncio.create_task(servicio.permitido("https://www.tienda.example/p/1"))
            await asyncio.sleep(0.05)
            assert http.pedidas == []  # Hay otra petición en vuelo a la tienda.
        assert await tarea is True

    asyncio.run(escenario())
    assert http.pedidas == ["https://www.tienda.example/robots.txt"]


def test_tras_cambiar_los_ajustes_el_robots_txt_no_coincide_con_una_ficha_del_viejo() -> None:
    http = Contador()

    async def escenario() -> None:
        viejo_limitador = LimitadorDominio(0.0)
        viejo = _navegador(http, viejo_limitador)
        await viejo.obtener("https://www.tienda.example/a")
        nuevo_limitador = LimitadorDominio(0.0)
        nuevo_limitador.heredar(viejo_limitador)
        nuevo = _navegador(http, nuevo_limitador)  # Su robots.txt está aún sin leer.
        http.maximo = 0
        await asyncio.gather(
            viejo.obtener("https://www.tienda.example/b"),
            nuevo.obtener("https://www.tienda.example/c"),
        )

    asyncio.run(escenario())
    assert http.maximo == 1


def test_con_y_sin_www_comparten_la_fila_de_la_tienda() -> None:
    http = Contador()

    async def escenario() -> None:
        navegador = _navegador(http, LimitadorDominio(0.0))
        await navegador.obtener("https://www.tienda.example/x")
        await navegador.obtener("https://tienda.example/x")
        http.maximo = 0
        await asyncio.gather(
            navegador.obtener("https://www.tienda.example/y"),
            navegador.obtener("https://tienda.example/sitemap.xml"),
        )

    asyncio.run(escenario())
    assert http.maximo == 1


def test_la_pausa_de_cortesia_reconoce_la_tienda_con_o_sin_www() -> None:
    limitador = LimitadorDominio(0.0)

    async def escenario() -> tuple[bool, bool]:
        async with limitador.turno("www.tienda.example"):
            sin_www = await pausa_de_cortesia("tienda.example")
        async with limitador.turno("tienda.example"):
            con_www = await pausa_de_cortesia("WWW.tienda.example")
        return sin_www, con_www

    assert asyncio.run(escenario()) == (True, True)


def test_otro_subdominio_tiene_su_propia_fila() -> None:
    # Solo se quita «www.», como en `dominio_de`: un subdominio de imágenes o un CDN suele ser
    # otro servidor, y la tienda se identifica igual en el resto del programa.
    limitador = LimitadorDominio(0.0)

    async def escenario() -> bool:
        async with limitador.turno("tienda.example"):
            tarea = asyncio.create_task(limitador.esperar_turno("static.tienda.example"))
            await asyncio.sleep(0.01)
            return tarea.done()

    assert asyncio.run(escenario()) is True
