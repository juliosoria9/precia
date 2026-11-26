"""Módulo propio de Google (H5): URL de búsqueda, detección de bloqueo y lectura de resultados.

Sin red. Las páginas de bloqueo son reales (`tests/fixtures/reales/google/`, capturadas en S06);
la de resultados es **sintética**: Google bloqueó a PrecIA y no se pudo guardar una real.
"""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from precia.fuentes.google import (
    MAX_RESULTADOS,
    ORGANICO,
    SHOPPING,
    ResultadoWeb,
    leer_resultados,
    motivo_bloqueo,
    url_busqueda,
)
from precia.navegador.tipos import Pagina

FIXTURES = Path(__file__).parent.parent / "fixtures"
REALES = FIXTURES / "reales" / "google"
SERP = (FIXTURES / "fuentes" / "google_resultados_sintetico.html").read_text(encoding="utf-8")
CONSULTA = "pastillas de freno 1K0698151"


def pagina(html: str, url_final: str | None = None, estado: int = 200) -> Pagina:
    url = url_busqueda(CONSULTA)
    return Pagina(
        url, url_final or url, estado, html, "text/html", datetime(2026, 11, 24, tzinfo=UTC)
    )


# --- URL --------------------------------------------------------------------------------------


def test_url_de_la_busqueda_organica() -> None:
    partes = urlsplit(url_busqueda(CONSULTA))
    assert (partes.scheme, partes.netloc, partes.path) == ("https", "www.google.com", "/search")
    parametros = parse_qs(partes.query)
    assert parametros["q"] == [CONSULTA]
    assert parametros["hl"] == ["es"] and parametros["gl"] == ["es"]
    assert "udm" not in parametros


def test_url_de_shopping() -> None:
    parametros = parse_qs(urlsplit(url_busqueda(CONSULTA, shopping=True)).query)
    assert parametros["udm"] == ["28"]
    assert parametros["q"] == [CONSULTA]


def test_la_consulta_va_codificada_y_no_puede_anadir_parametros() -> None:
    url = url_busqueda("faro&num=100#x ñ")
    parametros = parse_qs(urlsplit(url).query)
    assert parametros["q"] == ["faro&num=100#x ñ"]
    assert urlsplit(url).fragment == ""


@pytest.mark.parametrize("consulta", ["", "   ", "x" * 501])
def test_una_consulta_vacia_o_enorme_no_vale(consulta: str) -> None:
    with pytest.raises(ValueError):
        url_busqueda(consulta)


# --- Bloqueo ----------------------------------------------------------------------------------


def test_la_pagina_real_de_captcha_es_un_bloqueo() -> None:
    html = (REALES / "sorry_captcha.html").read_text(encoding="utf-8")
    motivo = motivo_bloqueo(pagina(html, "https://www.google.com/sorry/index?continue=x"))
    assert motivo is not None and "CAPTCHA" in motivo


def test_el_captcha_se_reconoce_tambien_sin_la_url_de_sorry() -> None:
    html = (REALES / "sorry_captcha.html").read_text(encoding="utf-8")
    assert motivo_bloqueo(pagina(html)) is not None


def test_la_pagina_real_que_exige_javascript_es_un_bloqueo() -> None:
    html = (REALES / "habilita_javascript.html").read_text(encoding="utf-8")
    motivo = motivo_bloqueo(pagina(html))
    assert motivo is not None and "JavaScript" in motivo
    assert leer_resultados(html, origen=ORGANICO) == []


def test_la_pagina_de_consentimiento_es_un_bloqueo() -> None:
    motivo = motivo_bloqueo(pagina("<html></html>", "https://consent.google.com/m?continue=x"))
    assert motivo is not None and "cookies" in motivo


@pytest.mark.parametrize("estado", [403, 429, 503])
def test_un_estado_de_error_es_un_bloqueo(estado: int) -> None:
    assert motivo_bloqueo(pagina("<html></html>", estado=estado)) is not None


def test_una_pagina_con_resultados_no_es_un_bloqueo() -> None:
    assert motivo_bloqueo(pagina(SERP)) is None


def test_una_pagina_normal_sin_resultados_no_es_un_bloqueo() -> None:
    html = "<html><body><p>No se han encontrado resultados para tu búsqueda.</p></body></html>"
    assert motivo_bloqueo(pagina(html)) is None


# --- Resultados -------------------------------------------------------------------------------


def test_lee_los_resultados_organicos_en_orden_y_sin_repetir() -> None:
    assert leer_resultados(SERP, origen=ORGANICO) == [
        ResultadoWeb(
            "https://www.recambios-uno.example/pastillas-freno-1k0698151.html",
            "Pastillas de freno 1K0698151 | Recambios Uno",
            ORGANICO,
        ),
        ResultadoWeb(
            "https://tienda-dos.example/p/1k0698151?ref=g",
            "Juego de pastillas 1K0 698 151 - Tienda Dos",
            ORGANICO,
        ),
        ResultadoWeb(
            "https://www.youtube.com/watch?v=abc123",
            "Cómo cambiar las pastillas de freno",
            ORGANICO,
        ),
        ResultadoWeb(
            "https://foro.example/hilo/123", "¿Qué pastillas montar? - Foro del motor", ORGANICO
        ),
        ResultadoWeb(
            "https://desguace-tres.example/pieza/98123", "Pastillas 1K0698151 usadas", ORGANICO
        ),
    ]


def test_no_salen_anuncios_ni_enlaces_de_google_ni_hostiles() -> None:
    urls = " ".join(r.url for r in leer_resultados(SERP, origen=ORGANICO))
    for prohibido in ("anuncio", "google", "javascript:", "clave@", "192.168", "sin-titulo"):
        assert prohibido not in urls


def test_el_origen_se_apunta_en_cada_resultado() -> None:
    assert {r.origen for r in leer_resultados(SERP, origen=SHOPPING)} == {SHOPPING}


def test_como_mucho_max_resultados() -> None:
    filas = "".join(
        f'<a href="https://tienda{n}.example/p"><h3>Producto {n}</h3></a>' for n in range(200)
    )
    assert len(leer_resultados(f"<html><body>{filas}</body></html>", origen=ORGANICO)) == (
        MAX_RESULTADOS
    )


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<html>",
        "<a href='https://t.example/'><h3>",
        "\x00\x01<a><h3>x</h3></a>",
        "<h3>solo</h3>",
    ],
)
def test_html_roto_o_vacio_no_falla(html: str) -> None:
    resultados = leer_resultados(html, origen=ORGANICO)
    assert all(r.url.startswith("https://") for r in resultados)


def test_el_titulo_se_recorta_y_va_en_una_linea() -> None:
    html = f'<a href="https://t.example/p"><h3>Faro\n\n  delantero {"x" * 900}</h3></a>'
    [resultado] = leer_resultados(html, origen=ORGANICO)
    assert len(resultado.titulo) <= 300 and "\n" not in resultado.titulo
    assert resultado.titulo.startswith("Faro delantero")
