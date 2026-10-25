"""Dominio de una tienda escrito por el usuario: se valida y se normaliza antes de guardarlo."""

import asyncio

import pytest

from precia.navegador.tipos import URLNoPermitida
from precia.nucleo.dominios import dominio_de_tienda, normalizar_dominio
from precia.nucleo.fuente_datos import DominioNoValido, ErrorFuente


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("tienda.es", "tienda.es"),
        ("  Recambios-Prueba.ES. ", "recambios-prueba.es"),
        ("www.tienda.es", "tienda.es"),
        ("WWW.Tienda.es", "tienda.es"),
        ("españa.es", "xn--espaa-rta.es"),
        ("tienda.co.uk", "tienda.co.uk"),
    ],
)
def test_normaliza(texto: str, esperado: str) -> None:
    assert normalizar_dominio(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "   ",
        "localhost",
        "www.es",
        "tienda.es/ruta",
        "https://tienda.es",
        "tienda.es:8080",
        "otra.es#tienda.es",
        "otra.es?tienda.es",
        "usuario@tienda.es",
        "tienda.es\\otra.es",
        "a b.es",
        "127.0.0.1",
        "2130706433",
        "0x7f.1",
        "-tienda.es",
        "tienda-.es",
        "x" * 64 + ".es",
        ("a" * 60 + ".") * 5 + "es",
        "tienda..es",
        "tienda\n.es",
        "tienda.es\x00.otra.es",
    ],
)
def test_rechaza_lo_que_no_es_un_nombre_de_host(texto: str) -> None:
    assert normalizar_dominio(texto) is None


class ValidadorEspia:
    def __init__(self, error: URLNoPermitida | None = None) -> None:
        self.error = error
        self.urls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return url


def test_dominio_de_tienda_valida_con_el_validador_anti_ssrf() -> None:
    validador = ValidadorEspia()
    assert asyncio.run(dominio_de_tienda("WWW.Tienda.es", validador=validador)) == "tienda.es"
    # Se valida el host tal como lo escribe el usuario: hay tiendas que solo responden en www.
    assert validador.urls == ["https://www.tienda.es/"]


def test_dominio_que_no_pasa_el_validador_da_un_error_claro() -> None:
    validador = ValidadorEspia(
        URLNoPermitida("El servidor resuelve a una dirección IP no pública.")
    )
    with pytest.raises(DominioNoValido, match="no pública") as error:
        asyncio.run(dominio_de_tienda("interna.es", validador=validador))
    assert isinstance(error.value, ErrorFuente)


@pytest.mark.parametrize("texto", ["localhost", "169.254.169.254", "https://tienda.es/x", ""])
def test_texto_no_valido_no_llega_al_validador(texto: str) -> None:
    validador = ValidadorEspia()
    with pytest.raises(DominioNoValido, match="dominio"):
        asyncio.run(dominio_de_tienda(texto, validador=validador))
    assert validador.urls == []
