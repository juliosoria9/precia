"""La oferta lleva la foto de su ficha; la IA del emparejado no la toca."""

import asyncio
from dataclasses import replace

from precia.emparejado.agrupador import agrupar
from precia.nucleo.modelos import ConsultaBusqueda, Modo
from tests.emparejado.ayudas import candidata

FOTO = "https://recambiosnorte.example/img/pastillas.jpg"


def test_la_oferta_lleva_la_foto_de_la_ficha() -> None:
    con_foto = candidata("recambiosnorte.example", "Pastillas 1K0698151", "38.90")
    con_foto = replace(con_foto, ficha=replace(con_foto.ficha, foto=FOTO))
    sin_foto = candidata("motorpiezas.example", "Brembo pastillas 1K0698151", "35.20")
    consulta = ConsultaBusqueda("1K0698151", modo=Modo.CLASICO)
    grupos, _ = asyncio.run(agrupar(consulta, [con_foto, sin_foto], sesion_ia=None))
    (grupo,) = grupos
    fotos = {o.tienda: o.foto for o in grupo.ofertas}
    assert fotos == {"recambiosnorte.example": FOTO, "motorpiezas.example": None}
    # La más barata no tiene foto: el grupo usa la de la otra.
    assert grupo.foto == FOTO
