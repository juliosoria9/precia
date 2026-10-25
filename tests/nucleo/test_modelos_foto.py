"""Foto de la oferta y del grupo: la de la mejor oferta exacta o, si no tiene, la más barata."""

from decimal import Decimal

from precia.nucleo.modelos import Coincidencia, GrupoProducto, Oferta, OrigenDato, ValorCampo


def oferta(
    precio: str,
    foto: str | None,
    *,
    coincidencia: Coincidencia = Coincidencia.EXACTA,
    anclado: bool = True,
) -> Oferta:
    return Oferta(
        "tienda.example",
        f"https://tienda.example/p/{precio}",
        "Pastillas",
        Decimal(precio),
        {"precio": ValorCampo(f"{precio} €", OrigenDato.JSONLD, anclado)},
        coincidencia=coincidencia,
        foto=foto,
    )


def grupo(*ofertas: Oferta) -> GrupoProducto:
    return GrupoProducto("oem-1k0698151", "Pastillas", "1K0698151", "Referencia OEM", ofertas)


def test_oferta_sin_foto_por_defecto() -> None:
    assert Oferta("t.example", "https://t.example/p", "P", Decimal(1), {}).foto is None


def test_grupo_usa_la_foto_de_la_mejor_exacta() -> None:
    g = grupo(
        oferta("18.00", "https://a.example/equivalente.jpg", coincidencia=Coincidencia.EQUIVALENTE),
        oferta("30.00", "https://a.example/exacta.jpg"),
        oferta("25.00", "https://a.example/exacta-cara-no.jpg", coincidencia=Coincidencia.REVISION),
    )
    assert g.foto == "https://a.example/exacta.jpg"


def test_grupo_sin_foto_en_la_mejor_exacta_usa_la_mas_barata_con_foto() -> None:
    g = grupo(
        oferta("30.00", None),
        oferta("40.00", "https://a.example/cara.jpg"),
        oferta("35.00", "https://a.example/barata.jpg", coincidencia=Coincidencia.EQUIVALENTE),
    )
    assert g.foto == "https://a.example/barata.jpg"


def test_grupo_no_usa_la_foto_de_un_precio_no_anclado() -> None:
    g = grupo(oferta("1.00", "https://a.example/inyeccion.jpg", anclado=False))
    assert g.foto is None


def test_grupo_sin_fotos() -> None:
    assert grupo(oferta("30.00", None)).foto is None
