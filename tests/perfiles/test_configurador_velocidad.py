"""Velocidad del configurador (plan de velocidad de S03, tanda 2).

- A6: las fichas que va a abrir la validación se descargan mientras responde la IA de campos.
"""

import asyncio
import contextlib
from pathlib import Path

import pytest

from precia.ia.tipos import PeticionIA, RespuestaIA
from precia.navegador.tipos import Bloqueado, Pagina
from precia.perfiles.configurador import COMPONENTE, ESQUEMA_LISTADO, configurar
from precia.perfiles.validador import MAX_FICHAS
from tests.navegador.falsos import validar_con_dns_publico
from tests.perfiles.sitio import (
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    LISTADO_OK,
    PORTADA,
    PRODUCTOS,
    TIENDA,
    NavegadorFalso,
    paginas_sitio,
    sesion,
)


class NavegadorQueEspera(NavegadorFalso):
    """Cede el turno en cada descarga, como el navegador real (red, cortesía)."""

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        await asyncio.sleep(0)
        return await super().obtener(url, js=js, grande=grande)


def _configurar_anotando(navegador: NavegadorFalso) -> list[str]:
    """Configura la tienda y devuelve las URL pedidas cuando la IA contesta los campos."""
    sesion_ia, proveedor = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK]})
    completar = proveedor.completar
    vistas: list[str] = []

    async def anotar(peticion: PeticionIA) -> RespuestaIA:
        if peticion.esquema != ESQUEMA_LISTADO:
            for _ in range(50):  # La IA tarda: el bucle sigue con lo demás.
                await asyncio.sleep(0)
            vistas.extend(navegador.urls)
        return await completar(peticion)

    proveedor.completar = anotar  # type: ignore[method-assign]
    asyncio.run(
        configurar(DOMINIO, navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico)
    )
    return vistas


def test_las_fichas_de_la_validacion_se_descargan_mientras_responde_la_ia() -> None:
    navegador = NavegadorQueEspera(paginas_sitio(), redirecciones={f"https://{DOMINIO}/": PORTADA})
    vistas = _configurar_anotando(navegador)
    fichas = [p.url for p in PRODUCTOS]
    assert MAX_FICHAS == 3
    # Ya estaban pedidas las fichas 2 y 3 (la 1 la ve la IA); la 4 no la abre la validación.
    assert all(url in vistas for url in fichas[:MAX_FICHAS])
    assert fichas[MAX_FICHAS] not in vistas


def test_un_bloqueo_al_adelantar_las_fichas_lo_decide_la_validacion() -> None:
    # Adelantar no cambia lo que pasa: el bloqueo sale igual, de la validación.
    segunda = PRODUCTOS[1].url
    navegador = NavegadorQueEspera(
        paginas_sitio(),
        redirecciones={f"https://{DOMINIO}/": PORTADA},
        errores={segunda: Bloqueado(segunda, "Desafío de Cloudflare")},
    )
    with pytest.raises(Bloqueado):
        _configurar_anotando(navegador)
    assert navegador.urls.count(segunda) == 2  # Al adelantar y, otra vez, al validar.


def test_con_el_navegador_real_la_validacion_no_vuelve_a_descargar_las_fichas(
    tmp_path: Path,
) -> None:
    # Lo adelantado tiene que estar en la caché de páginas cuando valida: si no, A6 solo
    # añadiría descargas.
    from precia.navegador.cache import CachePaginas
    from precia.navegador.cortesia import LimitadorDominio
    from precia.navegador.navegador import Navegador
    from precia.navegador.robots import ServicioRobots
    from tests.navegador.falsos import INSTANTE, DescargadorFalso, Reloj
    from tests.navegador.falsos import pagina as pagina_navegador

    robots = f"{TIENDA}/robots.txt"
    reloj = Reloj()
    sitio = {url: pagina_navegador(url, html) for url, html in paginas_sitio().items()}
    http = DescargadorFalso(sitio)
    navegador = Navegador(
        http=http,
        chromium=None,
        robots=ServicioRobots(
            DescargadorFalso(
                {robots: pagina_navegador(robots, "User-agent: *\n", tipo="text/plain")}
            ),
            "PrecIA-test",
            reloj=reloj,
        ),
        limitador=LimitadorDominio(0.0, reloj=reloj, dormir=reloj.dormir),
        cache=CachePaginas(
            tmp_path, 3600.0, reloj=lambda: INSTANTE
        ),  # Las páginas son de entonces.
        validador=validar_con_dns_publico,
    )
    sesion_ia, _ = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK]})
    asyncio.run(
        configurar(
            f"www.{DOMINIO}", navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico
        )
    )
    for producto in PRODUCTOS[:MAX_FICHAS]:
        assert http.pedidas.count(producto.url) == 1


def test_una_ficha_que_no_es_2xx_tampoco_se_descarga_dos_veces(tmp_path: Path) -> None:
    # Revisión de A6: la caché de páginas solo guarda los 2xx. Una ficha con un 404 (caso real:
    # decathlon.es, ronda 1) se pedía al adelantar y otra vez al validar.
    from precia.navegador.cache import CachePaginas
    from precia.navegador.cortesia import LimitadorDominio
    from precia.navegador.navegador import Navegador
    from precia.navegador.robots import ServicioRobots
    from precia.perfiles.tipos import ConfiguracionFallida
    from tests.navegador.falsos import INSTANTE, DescargadorFalso, Reloj
    from tests.navegador.falsos import pagina as pagina_navegador

    robots = f"{TIENDA}/robots.txt"
    rota = PRODUCTOS[2].url
    reloj = Reloj()
    sitio = {url: pagina_navegador(url, html) for url, html in paginas_sitio().items()}
    sitio[rota] = pagina_navegador(rota, "<html><body>No existe</body></html>", estado=404)
    http = DescargadorFalso(sitio)
    navegador = Navegador(
        http=http,
        chromium=None,
        robots=ServicioRobots(
            DescargadorFalso(
                {robots: pagina_navegador(robots, "User-agent: *\n", tipo="text/plain")}
            ),
            "PrecIA-test",
            reloj=reloj,
        ),
        limitador=LimitadorDominio(0.0, reloj=reloj, dormir=reloj.dormir),
        cache=CachePaginas(tmp_path, 3600.0, reloj=lambda: INSTANTE),
        validador=validar_con_dns_publico,
    )
    sesion_ia, _ = sesion({COMPONENTE: [LISTADO_OK, CAMPOS_OK] * 2})
    # Lo que se mira es cuántas veces se pide la ficha, no si la tienda vale.
    with contextlib.suppress(ConfiguracionFallida):
        asyncio.run(
            configurar(
                f"www.{DOMINIO}", navegador, sesion_ia, CONSULTA, validador=validar_con_dns_publico
            )
        )
    assert http.pedidas.count(rota) == 1
