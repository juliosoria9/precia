"""Catálogo local de URL sacado del `sitemap.xml` de una tienda sin buscador (nota 16 §B).

Solo se guardan la URL, las palabras de su slug y el `lastmod`: nunca precios ni descripciones
(art. 133 TRLPI). Las URL y las palabras vienen de la web: no son de fiar.
"""

import time
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from precia.almacen import sqlite as modulo
from precia.almacen.sqlite import (
    MAX_CATALOGO,
    MAX_PENDIENTES_CATALOGO,
    Almacen,
    DatoNoValido,
    EntradaCatalogo,
    TiendaNoEncontrada,
)
from precia.fuentes.sitemap import es_listado, leer_sitemap, palabras_url
from precia.nucleo.modelos import InfoCatalogo
from tests.almacen.conftest import RelojFalso
from tests.cronometro import segundos_de_cpu

REALES = Path(__file__).parent.parent / "fixtures" / "reales"
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

    infos: list[InfoCatalogo] = []

    def guardar() -> None:
        # Un generador nuevo en cada intento; `guardar_catalogo` sustituye el catálogo entero.
        infos.append(con_tienda.guardar_catalogo(DOMINIO, SITEMAP, infinito(), parcial=False))

    cpu = segundos_de_cpu(guardar, 10)
    assert infos[-1].total == MAX_CATALOGO and infos[-1].parcial is True
    assert cpu < 10, f"guardar {MAX_CATALOGO} URL costó {cpu:.2f} s de CPU"


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
    # El límite se aplica después de ordenar: «golf» sale antes en «disco-freno-golf-vi».
    resultado = con_catalogo.buscar_catalogo(DOMINIO, "golf", limite=2)
    assert resultado == [ENTRADAS[2][0], ENTRADAS[0][0]]


# --- Buscar: relevancia (R2-C14a) ----------------------------------------------------------------


def _con_urls(almacen: Almacen, urls: list[str], dominio: str = DOMINIO) -> None:
    entradas = [(u, palabras_url(u), None) for u in urls]
    almacen.guardar_catalogo(dominio, f"https://{dominio}/sitemap.xml", entradas, parcial=False)


def test_la_palabra_completa_va_antes_que_la_subcadena(con_tienda: Almacen) -> None:
    # Ecooparts: 706 faros, pero arriba salían las «centralita-faros-xenon» (audi va antes en el
    # alfabeto); y en Casa del Libro, «faro» casaba con «farola». La subcadena sigue valiendo
    # para encontrar («faros», «farola»), pero cuenta más empezar palabra y más ser la palabra.
    base = "https://libros.example/recambio-automovil-segunda-mano"
    faro = f"{base}/faro-derecho/seat/ibiza-6j5/90652_faro-derecho-seat-ibiza-6j5-14-tdi.html"
    centralita = f"{base}/centralita-faros-xenon/audi/a1/210509_centralita-faros-xenon-audi-a1.html"
    farola = "https://libros.example/papeleria-bolsa-farola-el-principito/697126371/16106532"
    antifaro = "https://libros.example/a/antifaro-bici-led"
    _con_urls(con_tienda, [antifaro, centralita, farola, faro])
    resultado = con_tienda.buscar_catalogo(DOMINIO, "faro", limite=9)
    assert resultado == [faro, centralita, farola, antifaro]


def test_antes_las_que_nombran_la_consulta_al_principio_del_nombre(con_tienda: Almacen) -> None:
    # Recorte real del sitemap de papelería de Casa del Libro (ronda 2 de la campaña): por URL,
    # la primera era la agenda «papeleria-agenda-anual-2027-semana-vista-a6-harry-potter».
    texto = (REALES / "casadellibro_sitemap_papeleria_harry_potter.xml").read_text(encoding="utf-8")
    entradas = [(e.url, palabras_url(e.url), e.lastmod) for e in leer_sitemap(texto).entradas]
    assert len(entradas) == 7
    con_tienda.alta_tienda("casadellibro.com", "Casa del Libro")
    sitemap = "https://www.casadellibro.com/sitemap-cdl-papeleria.xml.gz"
    con_tienda.guardar_catalogo("casadellibro.com", sitemap, entradas, parcial=False)
    resultado = con_tienda.buscar_catalogo("casadellibro.com", "harry potter", limite=9)
    slugs = [url.split("/")[3] for url in resultado]
    assert slugs == [
        "papeleria-harry-potter-cartas-de-conversacion",
        "papeleria-harry-potter-nimbus-capa-roja-mini-figura-goma",
        "papeleria-set-harry-potter-safari---4-plumas",
        "papeleria-hermione-granger-super-dough-harry-potter-do-it-yourself-serie-1",
        "papeleria-agenda-anual-2027-semana-vista-a6-harry-potter",
    ]


def test_el_nombre_es_el_tramo_con_mas_palabras(con_tienda: Almacen) -> None:
    # La posición se mide en el nombre del producto: ni en el último tramo (en Casa del Libro es
    # un id) ni en las categorías del principio de la ruta.
    en_categoria = "https://libros.example/harry-potter/coleccion/agir-et-penser-comme-harry-potter"
    en_el_nombre = "https://libros.example/libros/harry-potter-y-la-piedra/9788478884452/123"
    _con_urls(con_tienda, [en_categoria, en_el_nombre])
    resultado = con_tienda.buscar_catalogo(DOMINIO, "harry potter", limite=9)
    assert resultado == [en_el_nombre, en_categoria]


# Dos fichas reales del catálogo de MediaMarkt que guardó la ronda 2 (de su sitemap de productos),
# en el orden esperado: «auriculares» abre el nombre de la primera y es la 6.ª palabra en la de JVC.
FICHAS_MEDIAMARKT = [
    "https://www.mediamarkt.es/es/product/_auriculares-126-23-sandberg-supraaurales-bluetooth-"
    "negro-97294293.html",
    "https://www.mediamarkt.es/es/product/_-jvc-ha-a3t-bu-negro-auriculares-de-boton-bluetooth-"
    "jvc-intraurales-106699129.html",
]


def test_las_paginas_de_listado_van_detras(con_tienda: Almacen) -> None:
    # Recorte real del sitemap de marcas de MediaMarkt: por URL, «/es/brand/…» iba antes que
    # «/es/product/…». Quién es una página de listado lo decide quien llama (`es_listado`).
    texto = (REALES / "mediamarkt_sitemap_marcas_auriculares.xml").read_text(encoding="utf-8")
    marcas = [e.url for e in leer_sitemap(texto).entradas]
    assert len(marcas) == 2
    con_tienda.alta_tienda("mediamarkt.es", "MediaMarkt")
    _con_urls(con_tienda, [*marcas, *FICHAS_MEDIAMARKT], "mediamarkt.es")
    consulta = "auriculares bluetooth"
    resultado = con_tienda.buscar_catalogo(
        "mediamarkt.es", consulta, limite=9, es_listado=es_listado
    )
    assert resultado[:2] == FICHAS_MEDIAMARKT and sorted(resultado[2:]) == sorted(marcas)
    # Sin `es_listado`, ninguna se aparta: en las de marca el nombre («auriculares-bluetooth»)
    # empieza por la consulta.
    resultado = con_tienda.buscar_catalogo("mediamarkt.es", consulta, limite=9)
    assert sorted(resultado[:2]) == sorted(marcas)
    uno = con_tienda.buscar_catalogo("mediamarkt.es", consulta, limite=1, es_listado=es_listado)
    assert uno == FICHAS_MEDIAMARKT[:1]


# --- Buscar: sin ninguna con todas las palabras (R5-I) -------------------------------------------


# Los kits de aromas de Decántalo (enlazados desde las fichas 32 y 33 de la ronda 5). No están en
# el trozo guardado de su sitemap, pero sí en el catálogo real que se probó en la campaña.
KITS_DECANTALO = [
    f"https://www.decantalo.com/es/es/le-nez-du-vin-los-vinos-tintos-12-aromas-en-{idioma}.html"
    for idioma in ("aleman", "espanol", "frances", "ingles")
]


def test_sin_ninguna_con_todas_entra_tambien_la_palabra_rara(con_tienda: Almacen) -> None:
    # Campaña real (ronda 5): en Decántalo ninguna URL lleva «vino», «tinto» y «rioja». Los kits
    # casan dos palabras genéricas y los riojas solo una, la rara: quedaban todos fuera.
    texto = (REALES / "decantalo_sitemap_productos_riojas_y_tintos.xml").read_text(encoding="utf-8")
    urls = [e.url for e in leer_sitemap(texto).entradas]
    assert len(urls) == 10
    riojas = [u for u in urls if "rioja" in u]
    assert len(riojas) == 4
    con_tienda.alta_tienda("decantalo.com", "Decántalo")
    _con_urls(con_tienda, [*urls, *KITS_DECANTALO], "decantalo.com")
    resultado = con_tienda.buscar_catalogo("decantalo.com", "vino tinto rioja", limite=5)
    assert len(resultado) == 5
    assert resultado[0] in riojas
    assert sum(u in riojas for u in resultado) >= 2
    assert any(u in KITS_DECANTALO for u in resultado)
    # Ni las de «vino» solo ni las de «tinto» solo: lo que casan ya lo casan los kits.
    assert set(resultado) <= {*riojas, *KITS_DECANTALO}


def test_los_grupos_se_turnan_empezando_por_la_palabra_mas_rara(con_tienda: Almacen) -> None:
    genericas = [f"https://libros.example/p/kit-vino-tinto-{n}" for n in (1, 2, 3)]
    raras = [f"https://libros.example/p/rioja-crianza-{n}" for n in (1, 2)]
    _con_urls(con_tienda, [*genericas, *raras])
    resultado = con_tienda.buscar_catalogo(DOMINIO, "vino tinto rioja", limite=4)
    assert resultado == [raras[0], genericas[0], raras[1], genericas[1]]


def test_un_grupo_contenido_en_otro_no_entra(con_tienda: Almacen) -> None:
    # {tinto} está dentro de {vino, tinto}: no aporta nada que no casen ya las de vino tinto.
    vino_tinto = [f"https://libros.example/p/kit-vino-tinto-{n}" for n in (1, 2)]
    tinto = "https://libros.example/p/godelia-tinto"
    rioja = "https://libros.example/p/rioja-crianza"
    _con_urls(con_tienda, [tinto, *vino_tinto, rioja])
    resultado = con_tienda.buscar_catalogo(DOMINIO, "vino tinto rioja", limite=9)
    assert resultado == [rioja, *vino_tinto]


# --- Buscar: una palabra que no casa con nada se prueba por su raíz (R5-J) ------------------------


def test_la_palabra_sin_ninguna_url_se_prueba_por_su_raiz_hawkers(con_tienda: Almacen) -> None:
    # Campaña real (ronda 5): en Hawkers las polarizadas llevan «polarized» en el slug. Como
    # «polarizadas» no casaba con ninguna URL, se caía al respaldo de «gafas» y «sol» y salían
    # gafas sin polarizar.
    texto = (REALES / "hawkers_sitemap_gafas_polarized.xml").read_text(encoding="utf-8")
    urls = [e.url for e in leer_sitemap(texto).entradas]
    assert len(urls) == 5
    polarizadas = sorted(u for u in urls if "polarized" in u)
    assert len(polarizadas) == 2
    con_tienda.alta_tienda("hawkersco.com", "Hawkers")
    _con_urls(con_tienda, urls, "hawkersco.com")
    resultado = con_tienda.buscar_catalogo("hawkersco.com", "gafas de sol polarizadas", limite=5)
    assert sorted(resultado) == polarizadas


def test_consulta_en_ingles_y_catalogo_en_espanol(con_tienda: Almacen) -> None:
    polarizadas = "https://libros.example/p/gafas-polarizadas-negras"
    otras = "https://libros.example/p/gafas-graduadas-negras"
    _con_urls(con_tienda, [otras, polarizadas])
    assert con_tienda.buscar_catalogo(DOMINIO, "gafas polarized", limite=9) == [polarizadas]


def test_la_palabra_que_casa_no_se_recorta(con_tienda: Almacen) -> None:
    # «polarizadas» casa con una URL: no se cambia por «polariz», que casaría también con la otra.
    polarizadas = "https://libros.example/p/gafas-polarizadas-negras"
    polarized = "https://libros.example/p/gafas-polarized-black"
    _con_urls(con_tienda, [polarized, polarizadas])
    assert con_tienda.buscar_catalogo(DOMINIO, "gafas polarizadas", limite=9) == [polarizadas]


def test_recambios_en_plural_casan_con_el_slug_en_singular(con_tienda: Almacen) -> None:
    piloto = "https://libros.example/p/piloto-trasero-izquierdo-golf-vi"
    faro = "https://libros.example/p/faro-delantero-golf-vi"
    _con_urls(con_tienda, [faro, piloto])
    assert con_tienda.buscar_catalogo(DOMINIO, "pilotos traseros golf", limite=9) == [piloto]


@pytest.mark.parametrize(
    ("palabra", "raiz"),
    [
        ("polarizadas", "polariz"),
        ("polarized", "polariz"),
        ("pilotos", "pilot"),
        ("tintos", "tinto"),  # «tint» se queda corta: se quita solo la «s».
        ("camisetas", "camiset"),
        ("faros", None),  # Menos de 6 letras: no se recorta.
        ("running", None),  # «runn» se queda corta.
        ("rioja", None),
        ("sneaker", None),  # Sin terminación conocida.
    ],
)
def test_raiz_de_una_palabra(palabra: str, raiz: str | None) -> None:
    assert modulo._raiz_catalogo(palabra) == raiz


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
    assert con_catalogo.buscar_catalogo(DOMINIO, consulta, limite=9) == _urls(0, 1, 4)
    cpu = segundos_de_cpu(lambda: con_catalogo.buscar_catalogo(DOMINIO, consulta, limite=9), 0.5)
    assert cpu < 0.5, f"la consulta hostil costó {cpu:.3f} s de CPU"


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
    # Segundos de CPU, el mejor de hasta 3 intentos (bug 6); guardar sustituye el catálogo.
    guardar = segundos_de_cpu(
        lambda: con_tienda.guardar_catalogo(DOMINIO, SITEMAP, entradas, parcial=False), 10
    )
    tiempos = []
    for consulta in ("pastillas golf", "amortiguador megane delantero", "ref099999", "nada"):
        assert len(con_tienda.buscar_catalogo(DOMINIO, consulta, limite=10)) <= 10
        buscar = partial(con_tienda.buscar_catalogo, DOMINIO, consulta, limite=10)
        tiempos.append(segundos_de_cpu(buscar, 0.5))
    # El peor caso: una palabra que está en todas las URL y hay que puntuarlas todas (R2-C14a).
    inicio = time.process_time()
    todas = con_tienda.buscar_catalogo(DOMINIO, "ref", limite=10, es_listado=es_listado)
    peor = time.process_time() - inicio
    print(f"guardar 100 000 URL: {guardar:.2f} s; buscar: {[f'{t:.3f}' for t in tiempos]} s")
    print(f"buscar una palabra de todas las URL: {peor:.3f} s")
    assert con_tienda.buscar_catalogo(DOMINIO, "ref099999", limite=10) == [entradas[99_999][0]]
    assert len(todas) == 10
    assert guardar < 10
    assert max(tiempos) < 0.5, f"buscar en {MAX_CATALOGO} URL costó {max(tiempos):.3f} s de CPU"
    assert peor < 3, f"puntuar las {MAX_CATALOGO} URL tardó {peor:.3f} s"


def test_patron_like_escapa_los_comodines() -> None:
    # Las palabras normalizadas ya no traen `%` ni `_`, pero el patrón no se fía de eso.
    assert modulo._patron_like(r"a%b_c\d") == r"%a\%b\_c\\d%"


# --- Hijos del índice sin leer (R2-C14b) ---------------------------------------------------------

HIJO = "https://libros.example/sitemaps/sitemap-{}.xml"


def test_guardar_y_leer_los_hijos_pendientes(con_tienda: Almacen) -> None:
    # Los hijos de un índice que no cupieron en el tope de ficheros, en el orden en que se leerían.
    hijos = [HIJO.format("faros"), HIJO.format("frenos"), HIJO.format("faros")]
    assert con_tienda.hijos_pendientes(DOMINIO) == []
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=True, pendientes=hijos)
    assert con_tienda.hijos_pendientes(DOMINIO) == hijos[:2]
    # Rehacer el catálogo rehace también la lista (el refresco de 7 días).
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=False)
    assert con_tienda.hijos_pendientes(DOMINIO) == []
    assert con_tienda.hijos_pendientes("otra.example") == []


@pytest.mark.parametrize(
    "hijo",
    ["https://otra.example/sitemap-faros.xml", "javascript:alert(1)", "/sitemap-faros.xml", 42],
)
def test_hijos_pendientes_no_validos_no_cambian_nada(con_catalogo: Almacen, hijo: Any) -> None:
    with pytest.raises(DatoNoValido):
        con_catalogo.guardar_catalogo(
            DOMINIO, SITEMAP, ENTRADAS[:1], parcial=True, pendientes=[HIJO.format("a"), hijo]
        )
    info = con_catalogo.catalogo(DOMINIO)
    assert info is not None and info.total == 5
    assert con_catalogo.hijos_pendientes(DOMINIO) == []


def test_tope_de_hijos_pendientes(con_tienda: Almacen, monkeypatch: pytest.MonkeyPatch) -> None:
    assert MAX_PENDIENTES_CATALOGO == 5_000
    monkeypatch.setattr(modulo, "MAX_PENDIENTES_CATALOGO", 2)
    hijos = [HIJO.format(n) for n in range(5)]
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=True, pendientes=hijos)
    assert con_tienda.hijos_pendientes(DOMINIO) == hijos[:2]


def test_ampliar_el_catalogo_con_los_hijos_leidos(con_tienda: Almacen, reloj: RelojFalso) -> None:
    faros, frenos, indice, nuevo = (HIJO.format(n) for n in ("faros", "frenos", "x", "nuevo"))
    con_tienda.guardar_catalogo(
        DOMINIO, SITEMAP, ENTRADAS, parcial=True, pendientes=[faros, frenos, indice]
    )
    creado = reloj.ahora
    reloj.ahora = datetime(2026, 10, 21, 9, 0)
    faro = ("https://libros.example/p/faro-golf-vi", "p faro golf vi", "2026-10-02")
    info = con_tienda.ampliar_catalogo(
        DOMINIO,
        [faros, indice],
        [faro, ENTRADAS[0], faro],
        pendientes=[nuevo, frenos],
        parcial=False,
    )
    # Las URL se añaden (sin repetir las que ya había), los hijos leídos salen de la lista y los
    # nuevos (de un índice dentro del índice) van al final. La fecha sigue siendo la del catálogo:
    # el refresco de 7 días lo rehace entero.
    assert info == InfoCatalogo(DOMINIO, SITEMAP, creado, 6, True)
    assert con_tienda.catalogo(DOMINIO) == info
    assert con_tienda.buscar_catalogo(DOMINIO, "faro golf", limite=5) == [faro[0]]
    assert con_tienda.hijos_pendientes(DOMINIO) == [frenos, nuevo]


def test_ampliar_respeta_el_tope_de_url(
    con_tienda: Almacen, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(modulo, "MAX_CATALOGO", 6)
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=False)
    nuevas = [(f"https://libros.example/p/faro-{n}", f"p faro {n}", None) for n in range(3)]
    info = con_tienda.ampliar_catalogo(DOMINIO, [], nuevas, parcial=False)
    assert info.total == 6 and info.parcial is True


def test_ampliar_no_valida_no_cambia_nada(con_catalogo: Almacen) -> None:
    hijo = HIJO.format("faros")
    malas = [
        ("https://libros.example/p/faro", "p faro", None),
        ("https://otra.example/p", "", None),
    ]
    with pytest.raises(DatoNoValido):
        con_catalogo.ampliar_catalogo(DOMINIO, [hijo], malas, parcial=False)
    with pytest.raises(DatoNoValido):
        con_catalogo.ampliar_catalogo(DOMINIO, ["https://otra.example/s.xml"], [], parcial=False)
    info = con_catalogo.catalogo(DOMINIO)
    assert info is not None and info.total == 5


def test_ampliar_sin_catalogo(con_tienda: Almacen) -> None:
    with pytest.raises(DatoNoValido):
        con_tienda.ampliar_catalogo(DOMINIO, [], ENTRADAS, parcial=False)
    assert con_tienda.catalogo(DOMINIO) is None


# --- Prefijo de idioma (R5-C) -------------------------------------------------------------------


def test_el_prefijo_de_idioma_se_guarda_con_el_catalogo(con_tienda: Almacen) -> None:
    info = con_tienda.guardar_catalogo(
        DOMINIO, SITEMAP, ENTRADAS, parcial=False, idioma=("es", "es")
    )
    assert info.idioma == ("es", "es")
    leido = con_tienda.catalogo(DOMINIO)
    assert leido is not None and leido.idioma == ("es", "es")
    ampliado = con_tienda.ampliar_catalogo(DOMINIO, [], [], parcial=False)
    assert ampliado.idioma == ("es", "es")


def test_sin_prefijo_de_idioma_queda_vacio(con_tienda: Almacen) -> None:
    con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=False)
    leido = con_tienda.catalogo(DOMINIO)
    assert leido is not None and leido.idioma == ()


@pytest.mark.parametrize(
    "idioma", [("es/es",), ("",), ("espanol",), ("es", "es", "es"), ("ES",), ("e s",)]
)
def test_prefijo_de_idioma_no_valido_no_cambia_nada(con_tienda: Almacen, idioma: Any) -> None:
    with pytest.raises(DatoNoValido):
        con_tienda.guardar_catalogo(DOMINIO, SITEMAP, ENTRADAS, parcial=False, idioma=idioma)
    assert con_tienda.catalogo(DOMINIO) is None
