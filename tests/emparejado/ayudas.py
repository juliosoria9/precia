"""Constructores de datos de prueba y una sesión de IA falsa (sin red ni API real)."""

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from precia.emparejado.tipos import OfertaCandidata
from precia.extraccion.tipos import FichaExtraida, Precio
from precia.ia.tipos import ErrorIA, PeticionIA, RespuestaIA
from precia.nucleo.modelos import OrigenDato, ValorCampo


def ficha(
    titulo: str,
    precio: str | None,
    *,
    referencia: str | None = None,
    referencia_anclada: bool = True,
    estado: str | None = None,
    marca: str | None = None,
    moneda: str = "EUR",
    url: str = "https://tienda.example/p/1",
    citadas: frozenset[str] = frozenset(),
) -> FichaExtraida:
    campos: dict[str, ValorCampo] = {}
    if precio is not None:
        campos["precio"] = ValorCampo(f"{precio.replace('.', ',')} €", OrigenDato.JSONLD)
    if referencia is not None:
        campos["referencia_oem"] = ValorCampo(
            referencia,
            OrigenDato.PERFIL if referencia_anclada else OrigenDato.IA,
            anclado=referencia_anclada,
            confianza=1.0 if referencia_anclada else 0.4,
        )
    if estado is not None:
        campos["estado"] = ValorCampo(estado, OrigenDato.PERFIL)
    if marca is not None:
        campos["marca"] = ValorCampo(marca, OrigenDato.JSONLD)
    importe = None if precio is None else Precio(Decimal(precio), moneda, f"{precio} €")
    return FichaExtraida(
        url=url, titulo=titulo, precio=importe, campos=campos, referencias_citadas=citadas
    )


def candidata(
    tienda: str,
    titulo: str,
    precio: str | None,
    *,
    disponible: bool = True,
    **kwargs: Any,
) -> OfertaCandidata:
    url = f"https://{tienda}/producto/{hashlib.sha1(titulo.encode()).hexdigest()[:8]}"
    return OfertaCandidata(tienda, ficha(titulo, precio, url=url, **kwargs), url, disponible)


Guion = dict[str, Any] | Callable[[PeticionIA], dict[str, Any]] | ErrorIA


class SesionFalsa:
    """Devuelve respuestas guionizadas y guarda las peticiones recibidas."""

    def __init__(self, guion: Guion) -> None:
        self.guion = guion
        self.peticiones: list[PeticionIA] = []

    async def pedir(self, peticion: PeticionIA) -> RespuestaIA:
        self.peticiones.append(peticion)
        if isinstance(self.guion, ErrorIA):
            raise self.guion
        datos = self.guion(peticion) if callable(self.guion) else self.guion
        return RespuestaIA(datos, 900, 120, Decimal("0.00015"), 640, "simulado", "falso")


def ofertas_de(peticion: PeticionIA) -> list[dict[str, Any]]:
    """Las ofertas que el emparejado envió a la IA, tal como van en los datos."""
    lista: list[dict[str, Any]] = json.loads(peticion.datos)["ofertas"]
    return lista


def clasificar_por_palabras(reglas: dict[str, tuple[str, str]]) -> Callable[..., dict[str, Any]]:
    """Guion que clasifica cada oferta según la primera palabra clave de su título."""

    def responder(peticion: PeticionIA) -> dict[str, Any]:
        salida = []
        for oferta in ofertas_de(peticion):
            clase, motivo = "misma_pieza", "Es la pieza buscada"
            for palabra, (c, m) in reglas.items():
                if palabra in oferta["titulo"].lower():
                    clase, motivo = c, m
                    break
            salida.append({"indice": oferta["indice"], "clase": clase, "motivo": motivo})
        return {"clasificaciones": salida}

    return responder
