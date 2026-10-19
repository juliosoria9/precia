"""Agrupador: convierte las fichas de todas las tiendas en grupos del mismo producto.

Orden de criterios (memoria §5):

1. Si la consulta contiene una referencia OEM, solo entran las ofertas con esa referencia
   exacta (normalizada); el resto se descarta con su motivo.
2. Si no, las ofertas que comparten referencia forman un grupo cada una.
3. Lo que queda se agrupa por similitud de títulos (difflib) o, en modo IA, en un único grupo
   con las ofertas que la IA considera la pieza buscada.

En modo IA la IA clasifica antes cada oferta (accesorio, variante, kit...); en modo IA o Auto
aplica también el filtro en lenguaje natural. En modo clásico nunca se llama a la IA.
"""

import hashlib
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal

from precia.emparejado.referencias import es_referencia, normalizar_referencia, referencias_en
from precia.emparejado.semantico import clasificar, motivo_descarte
from precia.emparejado.texto import (
    UMBRAL_POR_DEFECTO,
    agrupar_por_texto,
    limpiar_titulo,
    normalizar_titulo,
)
from precia.emparejado.tipos import OfertaCandidata, SesionIA
from precia.nucleo.modelos import ConsultaBusqueda, Descartado, GrupoProducto, Modo, Oferta

CRITERIO_OEM = "Referencia OEM exacta"
CRITERIO_TEXTO = "Similitud de texto (difflib)"
CRITERIO_IA = "🤖 Emparejado por IA"
MONEDA = "EUR"
SIN_NOMBRE = "Producto sin nombre"


async def agrupar(
    consulta: ConsultaBusqueda,
    ofertas: Sequence[OfertaCandidata],
    *,
    sesion_ia: SesionIA | None = None,
    umbral: float = UMBRAL_POR_DEFECTO,
) -> tuple[list[GrupoProducto], list[Descartado]]:
    """Agrupa las ofertas y devuelve los grupos y las ofertas descartadas con su motivo.

    La IA solo se usa si hay `sesion_ia` y el modo lo pide. Sus errores (`ErrorIA`) se
    propagan: el orquestador puede repetir el emparejado sin sesión, en modo clásico.
    """
    descartados: list[Descartado] = []
    validas: list[tuple[Oferta, set[str]]] = []
    for candidata in ofertas:
        oferta, motivo = _oferta(candidata)
        if oferta is None:
            descartados.append(Descartado(candidata.tienda, candidata.ficha.titulo, motivo))
        else:
            validas.append((oferta, _referencias_de(oferta)))

    buscadas = referencias_en(consulta.texto)
    if buscadas:
        validas = _con_referencia(validas, buscadas, descartados)

    semantico = sesion_ia is not None and consulta.modo is Modo.IA
    filtro = consulta.filtro.strip() if consulta.modo is not Modo.CLASICO else ""
    if sesion_ia is not None and (semantico or filtro):
        clasificaciones = await clasificar(
            sesion_ia, consulta.texto, [o for o, _ in validas], piezas=semantico, filtro=filtro
        )
        quedan: list[tuple[Oferta, set[str]]] = []
        for (oferta, refs), clasificacion in zip(validas, clasificaciones, strict=True):
            descarte = motivo_descarte(clasificacion, filtro)
            if descarte is None:
                quedan.append((oferta, refs))
            else:
                descartados.append(Descartado(oferta.tienda, oferta.titulo, descarte))
        validas = quedan

    grupos, sueltas = _agrupar_por_referencia(validas, buscadas)
    if semantico:
        if sueltas:
            grupos.append(_grupo_texto(sueltas, CRITERIO_IA))
    else:
        indices = agrupar_por_texto([o.titulo for o in sueltas], umbral=umbral)
        grupos.extend(_grupo_texto([sueltas[i] for i in g], CRITERIO_TEXTO) for g in indices)
    return _ids_unicos(grupos), descartados


def _oferta(candidata: OfertaCandidata) -> tuple[Oferta | None, str]:
    precio = candidata.ficha.precio
    if precio is None:
        return None, "Sin precio"
    # Comparar euros con dólares daría un «mejor precio» falso.
    # `priceCurrency` llega tal cual de la tienda: «eur» o « EUR » también son euros.
    if precio.moneda.strip().upper() != MONEDA:
        return None, f"Precio en otra moneda ({limpiar_titulo(precio.moneda, 10)})"
    if not precio.importe.is_finite() or precio.importe <= Decimal(0):
        return None, "Precio no válido"
    oferta = Oferta(
        tienda=candidata.tienda,
        url=candidata.url,
        titulo=candidata.ficha.titulo,
        precio=precio.importe,
        campos=candidata.ficha.campos,
        disponible=candidata.disponible,
    )
    return oferta, ""


def _referencias_de(oferta: Oferta) -> set[str]:
    refs = referencias_en(oferta.titulo)
    campo = oferta.campos.get("referencia_oem")
    # Un valor no anclado (no aparece en la página) no sirve para emparejar: puede ser una
    # invención de la IA o una prompt injection para colarse en el grupo de otra pieza.
    if campo is not None and campo.anclado:
        refs |= referencias_en(campo.valor)
        entera = normalizar_referencia(campo.valor)
        if es_referencia(entera):
            refs.add(entera)
    return refs


def _con_referencia(
    validas: list[tuple[Oferta, set[str]]], buscadas: set[str], descartados: list[Descartado]
) -> list[tuple[Oferta, set[str]]]:
    quedan = []
    for oferta, refs in validas:
        if refs & buscadas:
            quedan.append((oferta, refs & buscadas))
        else:
            motivo = "Referencia OEM distinta" if refs else "Sin referencia OEM"
            descartados.append(Descartado(oferta.tienda, oferta.titulo, motivo))
    return quedan


def _agrupar_por_referencia(
    validas: list[tuple[Oferta, set[str]]], buscadas: set[str]
) -> tuple[list[GrupoProducto], list[Oferta]]:
    """Grupos por referencia y las ofertas que no entran en ninguno."""
    # Cada oferta va al grupo de su referencia más compartida. Sin consulta por referencia,
    # una referencia que solo tiene una oferta no forma grupo: puede ser un falso positivo
    # (un código interno de la tienda) y es mejor que esa oferta se empareje por el título.
    veces = Counter(ref for _, refs in validas for ref in refs)
    por_referencia: dict[str, list[Oferta]] = {}
    sueltas: list[Oferta] = []
    for oferta, refs in validas:
        candidatas = [r for r in refs if r in buscadas or veces[r] >= 2]
        if candidatas:
            elegida = max(sorted(candidatas), key=lambda r: veces[r])
            por_referencia.setdefault(elegida, []).append(oferta)
        else:
            sueltas.append(oferta)
    grupos = [
        GrupoProducto(
            id=f"oem-{ref.lower()}",
            nombre=_nombre(ofertas),
            referencia_oem=ref,
            criterio=CRITERIO_OEM,
            ofertas=_por_precio(ofertas),
        )
        for ref, ofertas in por_referencia.items()
    ]
    return grupos, sueltas


def _grupo_texto(ofertas: list[Oferta], criterio: str) -> GrupoProducto:
    nombre = _nombre(ofertas)
    resumen = hashlib.sha1(normalizar_titulo(nombre).encode()).hexdigest()[:10]
    return GrupoProducto(
        id=f"txt-{resumen}",
        nombre=nombre,
        referencia_oem=None,
        criterio=criterio,
        ofertas=_por_precio(ofertas),
    )


def _nombre(ofertas: list[Oferta]) -> str:
    """El título más corto; a igualdad, el de la oferta más barata (y después, alfabético).

    Los títulos que quedan vacíos al limpiarlos no cuentan: si no, el más corto siempre sería
    el de la ficha sin título.
    """
    titulos = [(t, o.precio) for o in ofertas if (t := limpiar_titulo(o.titulo))]
    if not titulos:
        return SIN_NOMBRE
    return min(titulos, key=lambda t: (len(t[0]), t[1], t[0]))[0]


def _por_precio(ofertas: list[Oferta]) -> tuple[Oferta, ...]:
    return tuple(sorted(ofertas, key=lambda o: o.precio))


def _ids_unicos(grupos: list[GrupoProducto]) -> list[GrupoProducto]:
    vistos: Counter[str] = Counter()
    resultado = []
    for grupo in grupos:
        vistos[grupo.id] += 1
        if vistos[grupo.id] > 1:
            grupo = GrupoProducto(
                f"{grupo.id}-{vistos[grupo.id]}",
                grupo.nombre,
                grupo.referencia_oem,
                grupo.criterio,
                grupo.ofertas,
            )
        resultado.append(grupo)
    return resultado
