"""Catálogo local de URL sacado del `sitemap.xml` de una tienda sin buscador (nota 16 §B).

Solo se guardan la URL, las palabras de su slug y el `lastmod`: nunca precios ni descripciones
(art. 133 TRLPI). Las URL y las palabras vienen de la web: no son de fiar.
"""

import time
from datetime import datetime
from typing import Any

import pytest

from precia.almacen import sqlite as modulo
from precia.almacen.sqlite import (
    MAX_CATALOGO,
    Almacen,
    DatoNoValido,
    EntradaCatalogo,
    TiendaNoEncontrada,
)
from precia.fuentes.sitemap import palabras_url
from precia.nucleo.modelos import InfoCatalogo
from tests.almacen.conftest import RelojFalso

DOMINIO = "libros.example"
SITEMAP = "https://libros.example/sitemap.xml"

ENTRADAS = [
    (
        "https://libros.example/p/pastillas-freno-delanteras-golf-vi",
        "p pastillas freno delanteras golf vi",
        None,
    ),
    (
        "https://libros.example/p/pastillas-freno-traseras-golf-vi",
        "p pastillas freno traseras golf vi",
        "2026-10-01",
    ),
    ("https://libros.example/p/disco-freno-golf-vi", "p disco freno golf vi", None),
    ("https://libros.example/p/bateria-agm-70ah", "p bateria agm 70ah", None),
    ("https://www.libros.example/p/kit-pastillas-1k0-698-151", "p kit pastillas 1k0 698 151", None),
]


def _urls(*indices: int) -> list[str]:
    return sorted(ENTRADAS[i][0] for i in indices)


@pytest.fixture
def con_tienda(almacen: Almacen) -> Almacen:
    almacen.alta_tienda(DOMINIO, "Libros")
    return almacen


@pytest.fixture
def con_catalogo(con_tienda: Almacen) -> Almacen:
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=False)
    return con_tienda


# --- Guardar y leer ------------------------------------------------------------------------------


def test_sin_catalogo(con_tienda: Almacen) -> None:
    assert con_tienda.catalogo(DOMINIO) is None
    assert con_tienda.catalogo("nada.example") is None
    assert con_tienda.buscar_catalogo(DOMINIO, "pastillas freno", limite=5) == []


def test_guardar_y_leer_el_catalogo(con_tienda: Almacen, reloj: RelojFalso) -> None:
    info = con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=True)
    assert info == InfoCatalogo(
        dominio=DOMINIO, sitemap=SITEMAP, actualizado=reloj.ahora, total=5, parcial=True
    )
    assert con_tienda.catalogo(DOMINIO) == info


def test_admite_entradas_catalogo_y_tuplas(con_tienda: Almacen) -> None:
    entradas = [EntradaCatalogo(*ENTRADAS[0]), ENTRADAS[1], EntradaCatalogo(*ENTRADAS[2])]
    info = con_tienda.guardar_catalogo(DOMINIO, SITEMAP, iter(entradas), parcial=False)
    assert info.total == 3


def test_guardar_sustituye_todo_el_catalogo(con_catalogo: Almacen, reloj: RelojFalso) -> None:
    reloj.ahora = datetime(2026, 10, 27, 12, 0)
    nuevo = [("https://libros.example/p/filtro-aceite", "p filtro aceite", None)]
    info = con_catalogo.guardar_catalogo(DOMINIO, SITEMAP + ".gz", nuevo, parcial=False)
    assert info.total == 1 and info.actualizado == reloj.ahora
    assert info.sitemap == SITEMAP + ".gz" and info.parcial is False
    assert con_catalogo.buscar_catalogo(DOMINIO, "pastillas freno", limite=5) == []
    assert con_catalogo.buscar_catalogo(DOMINIO, "filtro", limite=5) == [nuevo[0][0]]


def test_las_url_repetidas_cuentan_una_vez(con_tienda: Almacen) -> None:
    info = con_tienda.guardar_catalogo(DOMINIO, SITEMAP, [*ENTRADAS, *ENTRADAS], parcial=False)
    assert info.total == len(ENTRADAS)


def test_un_catalogo_vacio_se_guarda(con_tienda: Almacen) -> None:
    info = con_tienda.guardar_catalogo(DOMINIO, SITEMAP, [], parcial=False)
    assert info.total == 0


def test_catalogo_de_tienda_inexistente(almacen: Almacen) -> None:
    with pytest.raises(TiendaNoEncontrada):
        almacen.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=False)


def test_los_catalogos_de_tiendas_distintas_no_se_mezclan(con_catalogo: Almacen) -> None:
    con_catalogo.alta_tienda("otra.example", "Otra")
    otra = [("https://otra.example/pastillas-freno", "pastillas freno", None)]
    con_catalogo.guardar_catalogo("otra.example", "https://otra.example/s.xml", otra, parcial=False)
    assert con_catalogo.buscar_catalogo("otra.example", "pastillas freno", limite=9) == [otra[0][0]]
    assert len(con_catalogo.buscar_catalogo(DOMINIO, "pastillas freno", limite=9)) == 2
    info = con_catalogo.catalogo(DOMINIO)
    assert info is not None and info.total == 5


def test_tope_de_url_del_catalogo(con_tienda: Almacen, monkeypatch: pytest.MonkeyPatch) -> None:
    # Al llegar al tope se guarda lo que cabe y el catálogo queda marcado como parcial.
    assert MAX_CATALOGO == 100_000
    monkeypatch.setattr(modulo, "MAX_CATALOGO", 3)
    info = con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=False)
    assert info.total == 3 and info.parcial is True
    # Exactamente en el tope no se ha perdido nada: no es parcial.
    info = con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS[:3], parcial=False)
    assert info.total == 3 and info.parcial is False


def test_el_tope_no_consume_un_generador_infinito(con_tienda: Almacen) -> None:
    def infinito() -> Any:
        n = 0
        while True:
            n += 1
            yield (f"https://libros.example/p/{n}", f"p {n}", None)

    inicio = time.perf_counter()
    info = con_tienda.guardar_catalogo(DOMINIO, SITEMAP, infinito(), parcial=False)
    duracion = time.perf_counter() - inicio
    assert info.total == MAX_CATALOGO and info.parcial is True
    assert duracion < 10, f"guardar {MAX_CATALOGO} URL tardó {duracion:.2f} s"


@pytest.mark.parametrize(
    "entrada",
    [
        ("https://otra.example/p/x", "p x", None),
        ("https://libros.example.otra.example/p/x", "p x", None),
        ("https://otra.example/?libros.example", "x", None),
        ("http://usuario@libros.example/p/x", "p x", None),
        ("https://libros.example:8443/p/x", "p x", None),
        ("ftp://libros.example/p/x", "p x", None),
        ("javascript:alert(1)//libros.example", "x", None),
        ("/p/x", "p x", None),
        ("", "", None),
        ("https://libros.example/" + "x" * 2048, "x", None),
        ("https://libros.example/p/x", "x" * 301, None),
        ("https://libros.example/p/x", "p x", "x" * 65),
        ("https://libros.example/p/\ud800", "p", None),
        ("https://[libros.example/p/x", "p x", None),
        ("https://libros.example]/p/x", "p x", None),
        ("https://libros.example/p/x", 3, None),
        ("https://libros.example/p/x", "p x"),
        "https://libros.example/p/x",
        None,
    ],
)
def test_entradas_no_validas_no_cambian_nada(con_catalogo: Almacen, entrada: Any) -> None:
    # Se valida todo antes de tocar nada: el catálogo anterior queda entero.
    with pytest.raises(DatoNoValido):
        con_catalogo.guardar_catalogo(DOMINIO, SITEMAP, [ENTRADAS[0], entrada], parcial=False)
    info = con_catalogo.catalogo(DOMINIO)
    assert info is not None and info.total == 5
    assert len(con_catalogo.buscar_catalogo(DOMINIO, "golf", limite=9)) == 3


@pytest.mark.parametrize(
    "sitemap",
    [
        "",
        "https://otra.example/sitemap.xml",
        "https://libros.example/" + "x" * 2048,
        "https://[libros.example/sitemap.xml",
    ],
)
def test_el_sitemap_tiene_que_ser_de_la_tienda(con_tienda: Almacen, sitemap: str) -> None:
    with pytest.raises(DatoNoValido):
        con_tienda.guardar_catalogo(DOMINIO, sitemap, ENTRADAS, parcial=False)
    assert con_tienda.catalogo(DOMINIO) is None


def test_inyeccion_sql_en_url_y_palabras_se_guarda_como_texto(con_tienda: Almacen) -> None:
    mala = "https://libros.example/p/x');DROP%20TABLE%20catalogo;--"
    entradas = [(mala, "x'); DELETE FROM catalogo; -- freno", None)]
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, entradas, parcial=False)
    assert con_tienda.buscar_catalogo(DOMINIO, "freno", limite=5) == [mala]


# --- Buscar --------------------------------------------------------------------------------------


def test_busca_las_url_con_todas_las_palabras(con_catalogo: Almacen) -> None:
    assert con_catalogo.buscar_catalogo(DOMINIO, "pastillas freno golf", limite=9) == _urls(0, 1)
    assert con_catalogo.buscar_catalogo(DOMINIO, "Pastillas DELANTERAS", limite=9) == _urls(0)


def test_normaliza_la_consulta_como_las_palabras_del_slug(con_catalogo: Almacen) -> None:
    # Minúsculas, sin acentos y separando por signos, como `fuentes.sitemap.palabras_url`.
    assert con_catalogo.buscar_catalogo(DOMINIO, "Batería AGM-70Ah", limite=9) == _urls(3)


def test_ignora_palabras_cortas_y_vacias(con_catalogo: Almacen) -> None:
    resultado = con_catalogo.buscar_catalogo(DOMINIO, "disco de freno para el Golf VI", limite=9)
    assert resultado == _urls(2)


def test_sin_ninguna_con_todas_las_que_tienen_mas(con_catalogo: Almacen) -> None:
    # «ceramicas» no sale en ninguna URL: se devuelven las que tienen más palabras (3 de 4).
    resultado = con_catalogo.buscar_catalogo(
        DOMINIO, "pastillas freno delanteras ceramicas", limite=9
    )
    assert resultado == _urls(0)
    resultado = con_catalogo.buscar_catalogo(DOMINIO, "disco ventilado", limite=9)
    assert resultado == _urls(2)


def test_ninguna_palabra_en_comun(con_catalogo: Almacen) -> None:
    assert con_catalogo.buscar_catalogo(DOMINIO, "amortiguador", limite=9) == []


def test_busca_por_referencia_oem(con_catalogo: Almacen) -> None:
    # La referencia sale troceada en el slug («1k0-698-151»): se compara sin espacios.
    for consulta in ("1K0 698 151", "1K0-698-151", "1k0698151", "pastillas 1K0698151 bosch"):
        assert con_catalogo.buscar_catalogo(DOMINIO, consulta, limite=9) == _urls(4), consulta


def test_con_las_palabras_de_palabras_url_encuentra_el_modelo(con_tienda: Almacen) -> None:
    # De extremo a extremo con `fuentes.sitemap.palabras_url`: la consulta y el catálogo tienen
    # que normalizar igual también los números del modelo y de las medidas.
    urls = [
        f"https://libros.example/pastillas-freno-peugeot-{modelo}.html"
        for modelo in ("208", "2008", "3008", "308", "408", "508")
    ] + [
        "https://libros.example/neumatico-michelin-195-65-r15.html",
        "https://libros.example/neumatico-michelin-205-55-r16.html",
    ]
    entradas = [(u, palabras_url(u), None) for u in urls]
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, entradas, parcial=False)
    assert con_tienda.buscar_catalogo(DOMINIO, "pastillas Peugeot 308", limite=5) == [urls[3]]
    assert con_tienda.buscar_catalogo(DOMINIO, "neumático 205/55 R16", limite=5) == [urls[7]]


def test_respeta_el_limite(con_catalogo: Almacen) -> None:
    assert con_catalogo.buscar_catalogo(DOMINIO, "golf", limite=2) == _urls(0, 1, 2)[:2]


@pytest.mark.parametrize("limite", [0, -1, 101, 2**63, 1.5, True, "3"])
def test_limite_no_valido(con_catalogo: Almacen, limite: Any) -> None:
    with pytest.raises(DatoNoValido):
        con_catalogo.buscar_catalogo(DOMINIO, "golf", limite=limite)


@pytest.mark.parametrize("consulta", ["", "a b", "%", "___", "%%%", "*", "' OR '1'='1", "--"])
def test_consultas_sin_palabras_no_devuelven_nada(con_catalogo: Almacen, consulta: str) -> None:
    # Ni los comodines de LIKE ni una inyección convierten la búsqueda en «todo el catálogo».
    assert con_catalogo.buscar_catalogo(DOMINIO, consulta, limite=9) == []


def test_los_comodines_de_like_no_son_comodines(con_tienda: Almacen) -> None:
    entradas = [
        ("https://libros.example/a", "pastillas freno", None),
        ("https://libros.example/b", "past_llas fr%no", None),
    ]
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, entradas, parcial=False)
    assert con_tienda.buscar_catalogo(DOMINIO, "pastillas", limite=9) == [
        "https://libros.example/a"
    ]
    assert con_tienda.buscar_catalogo(DOMINIO, "freno' OR 1=1 --", limite=9) == [
        "https://libros.example/a"
    ]


def test_consulta_hostil_enorme(con_catalogo: Almacen) -> None:
    consulta = "pastillas " + "abc%_\\ " * 50_000
    inicio = time.perf_counter()
    resultado = con_catalogo.buscar_catalogo(DOMINIO, consulta, limite=9)
    duracion = time.perf_counter() - inicio
    assert resultado == _urls(0, 1, 4)
    assert duracion < 0.5, f"la consulta hostil tardó {duracion:.3f} s"


def test_buscar_en_100000_url_tarda_poco(con_tienda: Almacen) -> None:
    piezas = ("pastillas", "disco", "filtro", "bujia", "amortiguador", "correa", "bomba", "faro")
    modelos = ("golf", "ibiza", "leon", "astra", "focus", "clio", "corsa", "megane", "polo")
    entradas = [
        (
            f"https://libros.example/p/{piezas[i % 8]}-{modelos[i % 9]}-{i}",
            f"p {piezas[i % 8]} {modelos[i % 9]} ref{i:06d}",
            None,
        )
        for i in range(MAX_CATALOGO)
    ]
    inicio = time.perf_counter()
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, entradas, parcial=False)
    guardar = time.perf_counter() - inicio
    tiempos = []
    for consulta in ("pastillas golf", "amortiguador megane delantero", "ref099999", "nada"):
        inicio = time.perf_counter()
        resultado = con_tienda.buscar_catalogo(DOMINIO, consulta, limite=10)
        tiempos.append(time.perf_counter() - inicio)
        assert len(resultado) <= 10
    print(f"guardar 100 000 URL: {guardar:.2f} s; buscar: {[f'{t:.3f}' for t in tiempos]} s")
    assert con_tienda.buscar_catalogo(DOMINIO, "ref099999", limite=10) == [entradas[99_999][0]]
    assert guardar < 10
    assert max(tiempos) < 0.5, f"buscar en {MAX_CATALOGO} URL tardó {max(tiempos):.3f} s"


def test_patron_like_escapa_los_comodines() -> None:
    # Las palabras normalizadas ya no traen `%` ni `_`, pero el patrón no se fía de eso.
    assert modulo._patron_like(r"a%b_c\d") == r"%a\%b\_c\\d%"
