"""Caché de páginas en disco: un JSON por URL, con caducidad y tolerante a ficheros dañados."""

import hashlib
import json
from pathlib import Path

import pytest

from precia.navegador.cache import CachePaginas
from tests.navegador.falsos import INSTANTE, RelojFecha, pagina

URL = "https://tienda.example/pastillas-freno-1k0698151"


def cache(directorio: Path, ttl: float = 3600) -> tuple[CachePaginas, RelojFecha]:
    reloj = RelojFecha()
    return CachePaginas(directorio, ttl, reloj=reloj), reloj


def test_guarda_y_recupera(tmp_path: Path) -> None:
    c, _ = cache(tmp_path / "cache")
    original = pagina(URL, "<html><body>Pastillas «Brembo» 38,90 €</body></html>")
    c.guardar(original)
    recuperada = c.obtener(URL)
    assert recuperada is not None
    assert recuperada.desde_cache
    assert recuperada.html == original.html
    assert recuperada.url_final == original.url_final
    assert recuperada.estado == 200
    assert recuperada.tipo_contenido == "text/html"
    assert recuperada.obtenida == INSTANTE


def test_nombre_de_fichero_es_el_sha256_de_la_url(tmp_path: Path) -> None:
    c, _ = cache(tmp_path)
    c.guardar(pagina(URL))
    nombre = hashlib.sha256(URL.encode()).hexdigest() + ".json"
    assert [p.name for p in tmp_path.iterdir()] == [nombre]
    datos = json.loads((tmp_path / nombre).read_text(encoding="utf-8"))
    assert datos["url"] == URL


def test_url_desconocida(tmp_path: Path) -> None:
    c, _ = cache(tmp_path)
    assert c.obtener(URL) is None


def test_caduca(tmp_path: Path) -> None:
    c, reloj = cache(tmp_path, ttl=60)
    c.guardar(pagina(URL))
    reloj.avanzar(59)
    assert c.obtener(URL) is not None
    reloj.avanzar(2)
    assert c.obtener(URL) is None


def test_sobrescribe(tmp_path: Path) -> None:
    c, _ = cache(tmp_path)
    c.guardar(pagina(URL, "vieja"))
    c.guardar(pagina(URL, "nueva"))
    recuperada = c.obtener(URL)
    assert recuperada is not None and recuperada.html == "nueva"


@pytest.mark.parametrize(
    "contenido",
    [
        "{no es json",
        "[]",
        '{"url": "https://tienda.example/pastillas-freno-1k0698151"}',
        json.dumps(
            {
                "version": 1,
                "url": URL,
                "url_final": URL,
                "estado": "200",
                "html": "",
                "tipo_contenido": "text/html",
                "obtenida": INSTANTE.isoformat(),
            }
        ),
        json.dumps(
            {
                "version": 1,
                "url": URL,
                "url_final": URL,
                "estado": 200,
                "html": "",
                "tipo_contenido": "text/html",
                "obtenida": "ayer",
            }
        ),
    ],
)
def test_fichero_danado_se_ignora(tmp_path: Path, contenido: str) -> None:
    c, _ = cache(tmp_path)
    (tmp_path / (hashlib.sha256(URL.encode()).hexdigest() + ".json")).write_text(
        contenido, encoding="utf-8"
    )
    assert c.obtener(URL) is None


def test_fichero_de_otra_url_se_ignora(tmp_path: Path) -> None:
    # Si alguien copia a mano el fichero de otra URL, no se sirve como si fuera esta.
    c, _ = cache(tmp_path)
    otra = "https://tienda.example/otra"
    c.guardar(pagina(otra))
    nombre_otra = hashlib.sha256(otra.encode()).hexdigest() + ".json"
    nombre = hashlib.sha256(URL.encode()).hexdigest() + ".json"
    (tmp_path / nombre_otra).rename(tmp_path / nombre)
    assert c.obtener(URL) is None


def test_fichero_enorme_se_ignora(tmp_path: Path) -> None:
    c, _ = cache(tmp_path)
    c.guardar(pagina(URL, "x" * 100))
    c_pequena = CachePaginas(tmp_path, 3600, reloj=RelojFecha(), max_bytes=50)
    assert c_pequena.obtener(URL) is None


def test_no_deja_temporales(tmp_path: Path) -> None:
    c, _ = cache(tmp_path)
    for i in range(3):
        c.guardar(pagina(f"{URL}?p={i}"))
    assert all(p.suffix == ".json" for p in tmp_path.iterdir())


def test_version_con_javascript_aparte(tmp_path: Path) -> None:
    # La misma URL renderizada con Chromium no es el mismo HTML que la descarga directa.
    c, _ = cache(tmp_path)
    c.guardar(pagina(URL, "sin js"))
    assert c.obtener(URL, js=True) is None
    c.guardar(pagina(URL, "con js"), js=True)
    con_js = c.obtener(URL, js=True)
    sin_js = c.obtener(URL)
    assert con_js is not None and con_js.html == "con js"
    assert sin_js is not None and sin_js.html == "sin js"
