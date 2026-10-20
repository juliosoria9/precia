"""URL de entrada que pega el usuario cuando PrecIA no encuentra el buscador (nota 16 §A).

La URL es un dato del usuario, pero acaba en una descarga: no puede apuntar a otro servidor ni
llevar marcadores que cambien el host en cada búsqueda. El anti-SSRF (`validar_url`) se aplica
además al descargar; aquí se comprueba lo que no depende del DNS.
"""

import time

import pytest

from precia.perfiles.entrada import MAX_ENTRADA, EntradaNoValida, normalizar_entrada

DOMINIO = "tienda.es"


def _error(texto: str, dominio: str = DOMINIO) -> str:
    with pytest.raises(EntradaNoValida) as error:
        normalizar_entrada(dominio, texto)
    return str(error.value)


# --- Vacío ----------------------------------------------------------------------------------


@pytest.mark.parametrize("texto", ["", "   ", "\t\n"])
def test_vacia_es_none(texto: str) -> None:
    assert normalizar_entrada(DOMINIO, texto) is None


def test_es_un_value_error() -> None:
    assert issubclass(EntradaNoValida, ValueError)


# --- Lo que vale ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperada"),
    [
        (
            "https://tienda.es/buscar?q={consulta}",
            "https://tienda.es/buscar?q={consulta}",
        ),
        (
            "  https://www.tienda.es/buscar?q={consulta}&orden=precio  ",
            "https://www.tienda.es/buscar?q={consulta}&orden=precio",
        ),
        ("http://tienda.es/b/{consulta}", "http://tienda.es/b/{consulta}"),
        (
            "https://tienda.es/categoria/frenos?page={pagina}",
            "https://tienda.es/categoria/frenos?page={pagina}",
        ),
        (
            "https://tienda.es/catalogue/page-{pagina}.html",
            "https://tienda.es/catalogue/page-{pagina}.html",
        ),
        ("https://tienda.es/categoria/frenos/", "https://tienda.es/categoria/frenos/"),
        ("https://tienda.es", "https://tienda.es/"),
        # Esquema y host en minúsculas; el resto se queda tal cual (la ruta distingue).
        ("HTTPS://WWW.Tienda.ES/Frenos?Q={consulta}", "https://www.tienda.es/Frenos?Q={consulta}"),
        # El puerto por defecto se admite y se quita.
        ("https://tienda.es:443/buscar?q={consulta}", "https://tienda.es/buscar?q={consulta}"),
        ("http://tienda.es:80/listado", "http://tienda.es/listado"),
        # Una barra codificada en la ruta no cambia el host.
        ("https://tienda.es/a%2Fb?q={consulta}", "https://tienda.es/a%2Fb?q={consulta}"),
    ],
)
def test_entradas_validas(texto: str, esperada: str) -> None:
    assert normalizar_entrada(DOMINIO, texto) == esperada


def test_el_dominio_de_la_tienda_se_normaliza() -> None:
    assert (
        normalizar_entrada(" WWW.Tienda.ES ", "https://tienda.es/listado")
        == "https://tienda.es/listado"
    )


def test_tienda_con_dominio_idn_en_punycode() -> None:
    url = "https://xn--espaa-rta.es/buscar?q={consulta}"
    assert normalizar_entrada("españa.es", url) == url


def test_resultado_cabe_en_el_maximo() -> None:
    url = "https://tienda.es/" + "a" * (MAX_ENTRADA - len("https://tienda.es/"))
    assert len(url) == MAX_ENTRADA
    assert normalizar_entrada(DOMINIO, url) == url


# --- Esquema --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "javascript:alert(1)//tienda.es/{consulta}",
        "javascript://tienda.es/%0aalert(1)",
        "data:text/html,<b>tienda.es</b>",
        "data:text/html;base64,PHNjcmlwdD4=",
        "ftp://tienda.es/listado",
        "file:///etc/passwd",
        "//tienda.es/buscar?q={consulta}",
        "tienda.es/buscar?q={consulta}",
        "/buscar?q={consulta}",
        "https:/tienda.es/buscar",
        "https:tienda.es/buscar",
    ],
)
def test_solo_http_y_https_absolutas(texto: str) -> None:
    assert _error(texto)


def test_el_mensaje_pide_la_url_completa() -> None:
    assert "https://" in _error("tienda.es/buscar?q={consulta}")


# --- Host -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "https://otra.es/buscar?q={consulta}",
        "https://tienda.es.atacante.com/buscar?q={consulta}",
        "https://atacante-tienda.es/buscar?q={consulta}",
        "https://tiendaes/buscar",
        "https://m.tienda.es/buscar?q={consulta}",
        "https://www.www.tienda.es/listado",
        "https://tienda.es./listado",
        "https://tienda.es%2Fbuscar.atacante.com/listado",
        "https://atacante.com%2F.tienda.es/listado",
        "https://tienda.es%2F@atacante.com/listado",
        "https://127.0.0.1/listado",
        "https://[::1]/listado",
        "https://169.254.169.254/latest/meta-data",
        "https:///tienda.es/listado",
        # Corchetes de IPv6 sin cerrar: `urlsplit` lanza un ValueError que no es EntradaNoValida.
        "https://[tienda.es/listado",
        "https://tienda.es]/listado",
        "https://[::1/listado?p={pagina}",
    ],
)
def test_otro_host_o_subdominio_trampa(texto: str) -> None:
    assert DOMINIO in _error(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "https://usuario@tienda.es/listado",
        "https://usuario:clave@tienda.es/listado",
        "https://tienda.es@atacante.com/listado",
        "https://tienda.es:@atacante.com/listado",
        "https://@tienda.es/listado",
    ],
)
def test_sin_usuario(texto: str) -> None:
    assert _error(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "https://tienda.es:8080/listado",
        "https://tienda.es:80/listado",
        "http://tienda.es:443/listado",
        "https://tienda.es:0/listado",
        "https://tienda.es:99999/listado",
        "https://tienda.es:abc/listado",
        "https://tienda.es:-1/listado",
    ],
)
def test_sin_puerto_raro(texto: str) -> None:
    assert "puerto" in _error(texto)


def test_idn_homografo_no_es_ascii() -> None:
    # La «a» cirílica: se ve igual, pero es otro dominio.
    assert "ASCII" in _error("https://tiend\u0430.es/buscar?q={consulta}")


def test_idn_homografo_en_punycode_es_otro_host() -> None:
    falso = "tiend\u0430.es".encode("idna").decode("ascii")
    assert falso.startswith("xn--")
    assert DOMINIO in _error(f"https://{falso}/buscar?q={{consulta}}")


# --- Caracteres -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "https://tienda.es/buscar?q={consulta}&x=a b",
        "https://tienda.es/buscar\t?q={consulta}",
        "https://tienda.es/buscar\n?q={consulta}",
        "https://tien\nda.es/buscar",
        "https://tienda.es/buscar\x00?q={consulta}",
        "https://tienda.es/buscar\x7f?q={consulta}",
        'https://tienda.es/buscar?q={consulta}"onmouseover="x',
        "https://tienda.es/buscar?q={consulta}'",
        "https://tienda.es/buscar?q={consulta}`",
        "https://tienda.es/<script>",
        "https://tienda.es/a>b",
        "https:\\\\tienda.es\\buscar?q={consulta}",
        "https://tienda.es\\@atacante.com/listado",
        "https://atacante.com\\.tienda.es/listado",
    ],
)
def test_caracteres_prohibidos(texto: str) -> None:
    assert _error(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "https://tienda.es/categoría?q={consulta}",
        "https://tienda.es/buscar?q={consulta}\u200b",
        "https://tienda.es/buscar?q={consulta}\u202e",
        "https://tienda.es\uff0fbuscar",
        "https://tienda.es/buscar?q=\uff5bconsulta\uff5d",
    ],
)
def test_no_ascii(texto: str) -> None:
    assert "ASCII" in _error(texto)


def test_fragmento() -> None:
    assert "#" in _error("https://tienda.es/buscar?q={consulta}#resultados")
    assert "#" in _error("https://tienda.es/#/buscar/{consulta}")


def test_demasiado_larga() -> None:
    url = "https://tienda.es/" + "a" * MAX_ENTRADA
    assert str(MAX_ENTRADA) in _error(url)


# --- Marcadores -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "https://{consulta}.tienda.es/",
        "https://tienda.es.{consulta}/",
        "https://tienda-{consulta}.es/",
        "{consulta}://tienda.es/",
        "https://{consulta}@tienda.es/",
        "https://tienda.es:{consulta}/",
    ],
)
def test_consulta_en_el_host(texto: str) -> None:
    assert _error(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "https://{pagina}.tienda.es/",
        "https://tienda.es:{pagina}/",
    ],
)
def test_pagina_en_el_host(texto: str) -> None:
    assert _error(texto)


def test_consulta_dos_veces() -> None:
    assert "{consulta}" in _error("https://tienda.es/b/{consulta}?q={consulta}")


def test_pagina_dos_veces() -> None:
    assert "{pagina}" in _error("https://tienda.es/c/{pagina}?p={pagina}")


def test_pagina_junto_a_consulta() -> None:
    mensaje = _error("https://tienda.es/buscar?q={consulta}&page={pagina}")
    assert "{pagina}" in mensaje and "{consulta}" in mensaje


@pytest.mark.parametrize(
    "texto",
    [
        "https://tienda.es/buscar?q={query}",
        "https://tienda.es/buscar?q={searchTerms}",
        "https://tienda.es/buscar?q={CONSULTA}",
        "https://tienda.es/buscar?q={ consulta }",
        "https://tienda.es/buscar?q={consulta",
        "https://tienda.es/buscar?q=consulta}",
        "https://tienda.es/buscar?q={{consulta}}",
        "https://tienda.es/buscar?q={consulta}&x={}",
        "https://tienda.es/c/{pagina}{",
        "https://tienda.es/c/}{pagina}",
        "https://tienda.es/buscar?q={consulta}{pagina}",
    ],
)
def test_llaves_desconocidas(texto: str) -> None:
    assert _error(texto)


# --- Hostiles cronometrados -----------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "https://tienda.es/" + "{consulta}" * 1_000_000,
        "https://tienda.es/" + "{" * 5_000_000,
        "https://tienda.es/" + "%2F" * 2_000_000,
        "https://" + "a." * 2_000_000 + "tienda.es/",
        "https://" + "@" * 2_000_000 + "tienda.es/",
        "https://tienda.es/" + "\u0430" * 2_000_000,
        "https://tienda.es/?" + "a{consulta}" * 45,
    ],
    ids=["consultas", "llaves", "barras", "subdominios", "arrobas", "cirilico", "corta"],
)
def test_entradas_gigantes_no_tardan(texto: str) -> None:
    inicio = time.perf_counter()
    with pytest.raises(EntradaNoValida):
        normalizar_entrada(DOMINIO, texto)
    assert time.perf_counter() - inicio < 1


def test_espacios_alrededor_gigantes_no_tardan() -> None:
    inicio = time.perf_counter()
    texto = " " * 5_000_000 + "https://tienda.es/listado" + "\n" * 5_000_000
    assert normalizar_entrada(DOMINIO, texto) == "https://tienda.es/listado"
    assert time.perf_counter() - inicio < 1


def test_el_mensaje_no_repite_la_entrada() -> None:
    # El mensaje se enseña en la web: no cita la URL (que puede ser enorme o maliciosa).
    carga = "https://otra.es/<script>alert(1)</script>"
    assert "<script>" not in _error(carga)
