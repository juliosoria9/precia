"""Descubrimiento de tiendas con Google (H5): de los resultados a las tiendas nuevas. Sin red."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from precia.almacen.sqlite import Almacen
from precia.fuentes.clasificador import COMPONENTE
from precia.fuentes.descubridor import (
    LISTA_NEGRA,
    MAX_PAGINAS,
    Descubrimiento,
    descubrir,
    en_lista_negra,
)
from precia.fuentes.google import ORGANICO, SHOPPING, url_busqueda
from precia.ia.servicio import ServicioIA, SesionIA
from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import ErrorIA
from precia.navegador.tipos import Bloqueado, ErrorDescarga, Pagina
from precia.nucleo.modelos import EstadoPaso

FIXTURES = Path(__file__).parent.parent / "fixtures"
AHORA = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
CONSULTA = "pastillas de freno 1K0698151"
SERP = (FIXTURES / "fuentes" / "google_resultados_sintetico.html").read_text(encoding="utf-8")
CAPTCHA = (FIXTURES / "reales" / "google" / "sorry_captcha.html").read_text(encoding="utf-8")
UNO = "https://www.recambios-uno.example/pastillas-freno-1k0698151.html"
DOS = "https://tienda-dos.example/p/1k0698151?ref=g"
FORO = "https://foro.example/hilo/123"
TRES = "https://desguace-tres.example/pieza/98123"

FICHA = (
    '<html><head><script type="application/ld+json">{"@type": "Product", "name": "Pastillas",'
    ' "offers": {"@type": "Offer", "price": "38.90", "priceCurrency": "EUR"}}</script></head>'
    "<body><h1>Pastillas de freno 1K0698151</h1></body></html>"
)
LISTADO = (
    '<html><head><script type="application/ld+json">{"@type": "ItemList", "itemListElement": ['
    '{"@type": "ListItem", "item": {"@type": "Product", "name": "A", "url": "https://x.example/a",'
    ' "offers": {"@type": "Offer", "price": "1", "priceCurrency": "EUR"}}},'
    '{"@type": "ListItem", "item": {"@type": "Product", "name": "B", "url": "https://x.example/b",'
    ' "offers": {"@type": "Offer", "price": "2", "priceCurrency": "EUR"}}}]}</script></head>'
    "<body></body></html>"
)
DUDOSA = "<html><body><h1>Pastillas de freno</h1><p>Consulta el precio</p></body></html>"
VACIA = "<html><body><p>Sin resultados</p></body></html>"


def pagina(url: str, html: str, *, final: str | None = None, estado: int = 200) -> Pagina:
    return Pagina(url, final or url, estado, html, "text/html", AHORA)


@dataclass
class NavegadorFalso:
    paginas: dict[str, str | Exception | Pagina]
    pedidas: list[tuple[str, bool]] = field(default_factory=list)

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        self.pedidas.append((url, js))
        respuesta = self.paginas.get(url)
        if respuesta is None:
            raise ErrorDescarga(f"sin respuesta para {url}")
        if isinstance(respuesta, Exception):
            raise respuesta
        if isinstance(respuesta, Pagina):
            return respuesta
        return pagina(url, respuesta)

    def urls(self) -> list[str]:
        return [url for url, _ in self.pedidas]


def google(
    organico: str | Exception = SERP, shopping: str | Exception = VACIA
) -> dict[str, str | Exception | Pagina]:
    return {
        url_busqueda(CONSULTA): organico,
        url_busqueda(CONSULTA, shopping=True): shopping,
    }


def sesion(*respuestas: Guion, tope: int = 5) -> tuple[SesionIA, ProveedorSimulado]:
    proveedor = ProveedorSimulado({COMPONENTE: respuestas})
    servicio = ServicioIA(
        proveedor,
        Almacen(":memory:", reloj=lambda: AHORA),
        presupuesto_mes_usd=Decimal(10),
        tope_llamadas=tope,
        reloj=lambda: AHORA,
    )
    return servicio.nueva_busqueda(), proveedor


def ejecutar(navegador: NavegadorFalso, **opciones: object) -> Descubrimiento:
    return asyncio.run(descubrir(CONSULTA, navegador, **opciones))  # type: ignore[arg-type]


def detalles(resultado: Descubrimiento) -> str:
    return " | ".join(f"{p.nombre}: {p.detalle}" for p in resultado.pasos)


# --- Camino normal ----------------------------------------------------------------------------


def test_descubre_las_tiendas_cuyas_paginas_son_fichas() -> None:
    navegador = NavegadorFalso({**google(), UNO: FICHA, DOS: FICHA, FORO: DUDOSA, TRES: LISTADO})
    resultado = ejecutar(navegador)

    assert [(t.dominio, t.url, t.origen, t.por_ia) for t in resultado.tiendas] == [
        ("recambios-uno.example", UNO, ORGANICO, False),
        ("tienda-dos.example", DOS, ORGANICO, False),
    ]
    # Google se pide con JavaScript; las páginas descubiertas, como cualquier ficha.
    assert navegador.pedidas[:2] == [
        (url_busqueda(CONSULTA), True),
        (url_busqueda(CONSULTA, shopping=True), True),
    ]
    assert all(js is False for _, js in navegador.pedidas[2:])
    assert all(p.estado is not EstadoPaso.ERROR for p in resultado.pasos)
    assert "2" in detalles(resultado)


def test_no_se_abren_los_dominios_de_la_lista_negra() -> None:
    navegador = NavegadorFalso({**google(), UNO: FICHA, DOS: FICHA})
    ejecutar(navegador)
    assert not any("youtube" in url for url in navegador.urls())


def test_las_tiendas_ya_dadas_de_alta_no_se_vuelven_a_mirar() -> None:
    navegador = NavegadorFalso({**google(), UNO: FICHA, DOS: FICHA})
    resultado = ejecutar(navegador, conocidas={"recambios-uno.example"})
    assert [t.dominio for t in resultado.tiendas] == ["tienda-dos.example"]
    assert UNO not in navegador.urls()


def test_los_resultados_de_shopping_se_suman_sin_repetir_dominio() -> None:
    shopping = (
        '<html><body><a href="https://tienda-dos.example/otra"><h3>Repetida</h3></a>'
        '<a href="https://solo-shopping.example/p/1"><h3>Pastillas en oferta</h3></a></body></html>'
    )
    navegador = NavegadorFalso(
        {**google(shopping=shopping), "https://solo-shopping.example/p/1": FICHA}
    )
    resultado = ejecutar(navegador)
    assert [(t.dominio, t.origen) for t in resultado.tiendas] == [
        ("solo-shopping.example", SHOPPING)
    ]
    assert "https://tienda-dos.example/otra" not in navegador.urls()


def test_como_mucho_max_tiendas_y_deja_de_abrir_paginas() -> None:
    navegador = NavegadorFalso({**google(), UNO: FICHA, DOS: FICHA, FORO: FICHA, TRES: FICHA})
    resultado = ejecutar(navegador, max_tiendas=1)
    assert [t.dominio for t in resultado.tiendas] == ["recambios-uno.example"]
    assert DOS not in navegador.urls()


def test_como_mucho_max_paginas_abiertas() -> None:
    filas = "".join(
        f'<a href="https://tienda{n}.example/p"><h3>Producto {n}</h3></a>' for n in range(25)
    )
    navegador = NavegadorFalso(google(f"<html><body>{filas}</body></html>"))
    ejecutar(navegador)
    assert len(navegador.pedidas) == 2 + MAX_PAGINAS


def test_una_pagina_que_no_se_puede_abrir_no_para_el_descubrimiento() -> None:
    navegador = NavegadorFalso(
        {
            **google(),
            UNO: ErrorDescarga("tiempo agotado"),
            DOS: Bloqueado(DOS, "CAPTCHA"),
            TRES: FICHA,
        }
    )
    resultado = ejecutar(navegador)
    assert [t.dominio for t in resultado.tiendas] == ["desguace-tres.example"]
    assert "no se pudieron abrir" in detalles(resultado)


def test_si_la_pagina_redirige_a_otra_web_cuenta_el_dominio_final() -> None:
    redirigida = pagina(UNO, FICHA, final="https://www.youtube.com/watch?v=1")
    otra = pagina(DOS, FICHA, final="https://www.tienda-final.example/p/9")
    navegador = NavegadorFalso({**google(), UNO: redirigida, DOS: otra})
    resultado = ejecutar(navegador)
    assert [t.dominio for t in resultado.tiendas] == ["tienda-final.example"]


# --- Bloqueo de Google ------------------------------------------------------------------------


def test_si_google_devuelve_el_captcha_no_se_descubre_nada_y_se_dice() -> None:
    bloqueada = pagina(url_busqueda(CONSULTA), CAPTCHA, final="https://www.google.com/sorry/index")
    navegador = NavegadorFalso({url_busqueda(CONSULTA): bloqueada})
    resultado = ejecutar(navegador)
    assert resultado.tiendas == ()
    [paso] = resultado.pasos
    assert paso.nombre == "descubrir" and paso.estado is EstadoPaso.ERROR
    assert "CAPTCHA" in paso.detalle
    # Con la primera consulta bloqueada no se hace la de Shopping.
    assert navegador.urls() == [url_busqueda(CONSULTA)]


@pytest.mark.parametrize(
    "error", [Bloqueado("https://www.google.com/search", "reCAPTCHA"), ErrorDescarga("sin red")]
)
def test_si_el_navegador_no_consigue_la_pagina_de_google(error: Exception) -> None:
    navegador = NavegadorFalso(google(organico=error))
    resultado = ejecutar(navegador)
    assert resultado.tiendas == ()
    assert resultado.pasos[0].nombre == "descubrir"
    assert resultado.pasos[0].estado is EstadoPaso.ERROR
    assert len(navegador.pedidas) == 1


def test_si_solo_falla_shopping_valen_los_resultados_organicos() -> None:
    navegador = NavegadorFalso(
        {**google(shopping=Bloqueado("https://www.google.com/search", "reCAPTCHA")), UNO: FICHA}
    )
    resultado = ejecutar(navegador)
    assert [t.dominio for t in resultado.tiendas] == ["recambios-uno.example"]
    assert not any(p.nombre == "descubrir" for p in resultado.pasos)
    assert "Shopping" in detalles(resultado)


def test_sin_resultados_lo_dice() -> None:
    resultado = ejecutar(NavegadorFalso(google(organico=VACIA)))
    assert resultado.tiendas == ()
    assert "ningún resultado" in detalles(resultado)


# --- IA ---------------------------------------------------------------------------------------


def test_sin_ia_las_dudosas_no_se_dan_de_alta() -> None:
    navegador = NavegadorFalso({**google(), UNO: DUDOSA, DOS: DUDOSA})
    resultado = ejecutar(navegador)
    assert resultado.tiendas == ()
    assert "dudosas" in detalles(resultado)


def test_con_ia_las_dudosas_las_decide_el_clasificador() -> None:
    s, proveedor = sesion({"es_ficha": True}, {"es_ficha": False})
    navegador = NavegadorFalso({**google(), UNO: DUDOSA, DOS: FICHA, FORO: DUDOSA})
    resultado = ejecutar(navegador, sesion=s)
    assert [(t.dominio, t.por_ia) for t in resultado.tiendas] == [
        ("recambios-uno.example", True),
        ("tienda-dos.example", False),
    ]
    assert len(proveedor.peticiones) == 2
    assert any(p.estado is EstadoPaso.IA for p in resultado.pasos)


def test_la_ia_no_se_usa_si_los_datos_estructurados_bastan() -> None:
    s, proveedor = sesion()
    ejecutar(NavegadorFalso({**google(), UNO: FICHA, TRES: LISTADO}), sesion=s)
    assert proveedor.peticiones == []


def test_un_fallo_de_la_ia_deja_la_pagina_como_dudosa_y_sigue() -> None:
    s, _ = sesion(ErrorIA("el proveedor no responde"), {"es_ficha": True})
    navegador = NavegadorFalso({**google(), UNO: DUDOSA, DOS: DUDOSA})
    resultado = ejecutar(navegador, sesion=s)
    assert [t.dominio for t in resultado.tiendas] == ["tienda-dos.example"]


def test_con_el_tope_de_llamadas_agotado_no_se_insiste() -> None:
    s, proveedor = sesion({"es_ficha": True}, {"es_ficha": True}, tope=1)
    navegador = NavegadorFalso({**google(), UNO: DUDOSA, DOS: DUDOSA, FORO: DUDOSA})
    resultado = ejecutar(navegador, sesion=s)
    assert [t.dominio for t in resultado.tiendas] == ["recambios-uno.example"]
    assert len(proveedor.peticiones) == 1


# --- Lista negra ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dominio", ["youtube.com", "m.youtube.com", "es.wikipedia.org", "wallapop.com", "facebook.com"]
)
def test_dominios_que_no_son_tiendas(dominio: str) -> None:
    assert en_lista_negra(dominio)


@pytest.mark.parametrize(
    "dominio", ["oscaro.es", "amazon.es", "noyoutube.com", "youtube.com.tienda.es"]
)
def test_dominios_que_si_pueden_ser_tiendas(dominio: str) -> None:
    assert not en_lista_negra(dominio)


def test_la_lista_negra_son_dominios_normalizados() -> None:
    assert all(d == d.lower() and not d.startswith("www.") and "." in d for d in LISTA_NEGRA)
