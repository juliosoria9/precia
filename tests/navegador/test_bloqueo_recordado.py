"""A8: una tienda que sigue bloqueada tras el proxy y el rescate con Chromium se recuerda.

Mientras dura (`BLOQUEO_RECORDADO_S`), sus páginas salen `Bloqueado` al instante, sin pedir nada a
la tienda: son menos peticiones, no más (ADR-017). «Reintentar» lo olvida antes de tiempo.
"""

import asyncio
from pathlib import Path

import pytest

from precia.navegador.cache import CachePaginas
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.navegador import BLOQUEO_RECORDADO_S, Navegador
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import Bloqueado, Pagina
from tests.navegador.falsos import (
    DescargadorFalso,
    DescargadorGrandeFalso,
    Reloj,
    RelojFecha,
    pagina,
    validar_con_dns_publico,
)
from tests.navegador.test_navegador import FICHA, ROBOTS, SECO, TIENDA, UA, desafio

OTRA = f"{TIENDA}/frenos/otra.html"
AJENA = "https://www.otra-tienda.example/p/1"
SITEMAP = f"{TIENDA}/sitemap.xml"


class Montaje:
    def __init__(
        self,
        paginas: dict[str, Pagina | Exception],
        paginas_js: dict[str, Pagina | Exception] | None,
        *,
        cache: Path | None = None,
    ) -> None:
        self.reloj = Reloj()
        robots = {
            ROBOTS: pagina(ROBOTS, "User-agent: *\nAllow: /", tipo="text/plain"),
            "https://www.otra-tienda.example/robots.txt": pagina(
                "https://www.otra-tienda.example/robots.txt", "", tipo="text/plain"
            ),
        }
        self.http = DescargadorGrandeFalso(paginas)
        self.chromium = DescargadorFalso(paginas_js) if paginas_js is not None else None
        self.navegador = Navegador(
            http=self.http,
            chromium=self.chromium,
            robots=ServicioRobots(DescargadorFalso(robots), UA, reloj=self.reloj),
            limitador=LimitadorDominio(1.0, reloj=self.reloj, dormir=self.reloj.dormir),
            cache=CachePaginas(cache, 3600, reloj=RelojFecha()) if cache else None,
            validador=validar_con_dns_publico,
            reloj=self.reloj,
        )

    def obtener(self, url: str, **opciones: bool) -> Pagina:
        return asyncio.run(self.navegador.obtener(url, **opciones))

    def pedidas(self) -> list[str]:
        return self.http.pedidas + (self.chromium.pedidas if self.chromium else [])


def _bloqueada() -> Montaje:
    m = Montaje(
        {FICHA: desafio(), OTRA: pagina(OTRA), AJENA: pagina(AJENA), SITEMAP: pagina(SITEMAP)},
        {FICHA: desafio(), OTRA: pagina(OTRA)},
    )
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    return m


def test_tras_el_rescate_bloqueado_la_tienda_no_se_vuelve_a_pedir() -> None:
    m = _bloqueada()
    antes = m.pedidas()
    with pytest.raises(Bloqueado) as error:
        m.obtener(OTRA)
    assert m.pedidas() == antes
    assert "Reintentar" in error.value.motivo
    # Tampoco sus sitemaps.
    with pytest.raises(Bloqueado):
        m.obtener(SITEMAP, grande=True)
    assert m.pedidas() == antes


def test_las_demas_tiendas_siguen() -> None:
    m = _bloqueada()
    assert m.obtener(AJENA).estado == 200


def test_caduca() -> None:
    m = _bloqueada()
    m.reloj.ahora += BLOQUEO_RECORDADO_S + 1
    assert m.obtener(OTRA).estado == 200


def test_olvidar_el_bloqueo_vuelve_a_probar() -> None:
    m = _bloqueada()
    m.navegador.olvidar_bloqueo("recambios-ejemplo.example")
    assert m.obtener(OTRA).estado == 200


def test_olvidar_acepta_el_www() -> None:
    m = _bloqueada()
    m.navegador.olvidar_bloqueo("WWW.recambios-ejemplo.example")
    assert m.obtener(OTRA).estado == 200


def test_la_cache_se_sigue_sirviendo(tmp_path: Path) -> None:
    m = Montaje({OTRA: pagina(OTRA), FICHA: desafio()}, {FICHA: desafio()}, cache=tmp_path / "c.db")
    m.obtener(OTRA)
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    assert m.obtener(OTRA).estado == 200
    assert m.http.pedidas == [OTRA, FICHA]


def test_sin_chromium_no_se_recuerda() -> None:
    # Sin el rescate no se ha probado todo: la siguiente página lo vuelve a intentar.
    m = Montaje({FICHA: desafio(), OTRA: pagina(OTRA)}, None)
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    assert m.obtener(OTRA).estado == 200


def test_un_429_normal_no_se_recuerda() -> None:
    m = Montaje(
        {FICHA: pagina(FICHA, "<p>Too many requests</p>", estado=429), OTRA: pagina(OTRA)},
        {FICHA: pagina(FICHA)},
    )
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    assert m.obtener(OTRA).estado == 200


def test_un_rechazo_seco_no_se_recuerda() -> None:
    # Un 403 pequeño sin anti-bot puede ser solo esa ruta (una que nadie declaró, R4-D).
    seco = pagina(FICHA, SECO, estado=403)
    m = Montaje({FICHA: seco, OTRA: pagina(OTRA)}, {FICHA: seco})
    with pytest.raises(Bloqueado):
        m.obtener(FICHA)
    assert m.obtener(OTRA).estado == 200


def test_bloqueo_con_chromium_directo_tambien_se_recuerda() -> None:
    # Una tienda que ya va por Chromium (js) no tiene más rescate.
    m = Montaje({OTRA: pagina(OTRA)}, {FICHA: desafio(), OTRA: pagina(OTRA)})
    with pytest.raises(Bloqueado):
        m.obtener(FICHA, js=True)
    with pytest.raises(Bloqueado):
        m.obtener(OTRA)
    assert m.http.pedidas == []
