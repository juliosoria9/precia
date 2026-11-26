"""Comprobación de las alertas de precio (H7).

Para cada producto con alguna alerta activa se **repite la búsqueda** en las tiendas donde ya
tenía ofertas, en modo clásico (con los perfiles ya creados y sin IA: no gasta presupuesto), y
se decide con los precios de **esa** búsqueda, no con los guardados: un precio viejo de una
tienda que hoy no responde no dispara nada.

Reglas:

- Se avisa cuando el mejor precio queda **por debajo** del objetivo (estrictamente).
- El aviso se envía **una sola vez**: la alerta queda `avisada` hasta que el precio vuelve a
  estar en el objetivo o por encima; entonces se rearma.
- Si el envío falla, la alerta no queda avisada y se reintenta en la comprobación siguiente.
- El mejor precio sale de `GrupoProducto.mejor_oferta_de`: nunca cuenta un precio no anclado
  en la página (podría venir de una inyección) ni, por defecto, una oferta que no sea exacta.

Este módulo no conoce el almacén ni la web: la búsqueda, los canales y el apunte se inyectan.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from precia.nucleo.modelos import (
    Alerta,
    ConsultaBusqueda,
    GrupoProducto,
    InformeAlertas,
    Modo,
    Oferta,
    ResultadoBusqueda,
)
from precia.seguimiento.avisos import Aviso, Canal, ErrorAviso

registro = logging.getLogger(__name__)

SIN_OFERTAS_GUARDADAS = "El producto ya no tiene ofertas guardadas: búscalo otra vez."
BUSQUEDA_FALLIDA = "No se pudo repetir la búsqueda (detalle en el registro)."
SIN_OFERTAS_HOY = "La última comprobación no encontró ofertas de este producto."
CANAL_DESCONOCIDO = "El canal de aviso no está configurado."


class Accion(StrEnum):
    AVISAR = "avisar"
    REARMAR = "rearmar"
    NADA = "nada"


def decidir(alerta: Alerta, mejor: Oferta | None) -> Accion:
    """Qué hacer con la alerta ante la mejor oferta de la comprobación (`None`: no hay)."""
    if not alerta.activa or mejor is None:
        return Accion.NADA
    if mejor.precio < alerta.umbral:
        return Accion.NADA if alerta.avisada else Accion.AVISAR
    return Accion.REARMAR if alerta.avisada else Accion.NADA


def consulta_de(grupo: GrupoProducto) -> ConsultaBusqueda:
    """La búsqueda que vuelve a mirar el producto: por su referencia (o su nombre), solo en las
    tiendas donde ya tiene ofertas y sin IA."""
    return ConsultaBusqueda(
        texto=grupo.referencia_oem or grupo.nombre,
        tiendas=tuple(sorted({o.tienda for o in grupo.ofertas})),
        modo=Modo.CLASICO,
    )


class Anotar(Protocol):
    def __call__(
        self, id_alerta: int, *, comprobada: datetime, avisada: bool | None = None, error: str = ""
    ) -> None: ...


async def comprobar(
    alertas: Iterable[Alerta],
    *,
    producto: Callable[[str], GrupoProducto | None],
    buscar: Callable[[ConsultaBusqueda], Awaitable[ResultadoBusqueda]],
    canales: Mapping[str, Canal],
    motivos: Mapping[str, str],
    anotar: Anotar,
    reloj: Callable[[], datetime],
) -> InformeAlertas:
    """Comprueba las alertas activas, envía los avisos que toquen y apunta cada resultado.

    `producto` da el producto guardado (para saber qué buscar) y `buscar` repite la búsqueda;
    `motivos` dice por qué falta cada canal que no está en `canales`. Un fallo en un producto
    se apunta en sus alertas y se sigue con los demás. Una cancelación no se traga.
    """
    por_producto: dict[str, list[Alerta]] = {}
    for alerta in alertas:
        if alerta.activa:
            por_producto.setdefault(alerta.producto_id, []).append(alerta)
    comprobadas = avisos = 0
    errores: list[str] = []
    for id_producto, suyas in por_producto.items():
        comprobadas += len(suyas)
        guardado = producto(id_producto)
        if guardado is None:
            for alerta in suyas:
                anotar(alerta.id, comprobada=reloj(), error=SIN_OFERTAS_GUARDADAS)
            continue
        try:
            resultado = await buscar(consulta_de(guardado))
        except Exception:
            # El detalle (que puede citar una URL o un fichero) va al registro, no a la web.
            registro.exception("Alertas: no se pudo repetir la búsqueda de %s", id_producto)
            for alerta in suyas:
                anotar(alerta.id, comprobada=reloj(), error=BUSQUEDA_FALLIDA)
                errores.append(f"{alerta.producto_nombre}: {BUSQUEDA_FALLIDA}")
            continue
        fresco = next((g for g in resultado.grupos if g.id == id_producto), None)
        for alerta in suyas:
            mejor = fresco.mejor_oferta_de(alerta.coincidencias) if fresco else None
            ahora = reloj()
            if mejor is None:
                anotar(alerta.id, comprobada=ahora, error=SIN_OFERTAS_HOY)
                continue
            accion = decidir(alerta, mejor)
            if accion is Accion.REARMAR:
                anotar(alerta.id, comprobada=ahora, avisada=False)
            elif accion is Accion.NADA:
                anotar(alerta.id, comprobada=ahora)
            else:
                error = await _avisar(alerta, mejor, canales, motivos)
                if error:
                    anotar(alerta.id, comprobada=ahora, error=error)
                    errores.append(f"{alerta.producto_nombre}: {error}")
                else:
                    anotar(alerta.id, comprobada=ahora, avisada=True)
                    avisos += 1
    return InformeAlertas(
        fecha=reloj(), comprobadas=comprobadas, avisos=avisos, errores=tuple(errores)
    )


async def _avisar(
    alerta: Alerta, mejor: Oferta, canales: Mapping[str, Canal], motivos: Mapping[str, str]
) -> str:
    """Envía el aviso por el canal de la alerta. Devuelve "" o el motivo del fallo."""
    canal = canales.get(alerta.canal)
    if canal is None:
        return motivos.get(alerta.canal) or CANAL_DESCONOCIDO
    aviso = Aviso(
        producto=alerta.producto_nombre,
        umbral=alerta.umbral,
        precio=mejor.precio,
        tienda=mejor.tienda,
        url=mejor.url,
    )
    try:
        await canal.enviar(aviso)
    except ErrorAviso as error:
        # Sus mensajes son propios y no llevan secretos (`seguimiento.avisos`).
        return str(error)
    return ""
