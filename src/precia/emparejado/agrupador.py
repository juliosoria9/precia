"""Agrupador: convierte las fichas de todas las tiendas en grupos del mismo producto.

Orden de criterios (memoria §5):

1. Si la consulta contiene una referencia OEM, solo entran las ofertas con esa referencia
   (normalizada), por este orden de preferencia y etiquetadas aparte (B5, decisión de Julio):
   exacta; revisión, la buscada con un sufijo de 1-2 letras («1K0941005E» al buscar 1K0941005);
   y equivalente, una ficha con otra referencia principal que cita la buscada entre sus
   equivalencias (`FichaExtraida.referencias_citadas`). Todas van al grupo de la buscada, que
   solo lleva `CRITERIO_OEM` si todas son exactas. El resto se descarta con su motivo.
2. Si no, las ofertas que comparten referencia forman un grupo cada una.
3. Lo que queda se agrupa por similitud de títulos (difflib) o, en modo IA, en un único grupo
   con las ofertas que la IA considera la pieza buscada.

Revisión de los resultados (nota 05): en modo IA, y en Auto si se pide (`revisar`, ajuste
`revisar_resultados`), la IA clasifica antes cada oferta (pieza o accesorio de lo buscado,
variante, kit...) en una sola llamada; en modo IA o Auto aplica también el filtro en lenguaje
natural. En modo clásico nunca se llama a la IA. En Auto, si la consulta lleva una referencia
OEM, la IA no clasifica piezas (las ofertas ya han entrado por la referencia; solo el filtro).
Si la IA no ha revisado las ofertas (clásico, sin IA o porque ha fallado), la regla del núcleo
nominal (`nucleo_nominal.pieza_de`) descarta las que parecen una pieza de lo buscado
(«cerradura de la puerta…»), salvo en una consulta por referencia: lo que tiene la referencia
buscada nunca lo descarta la regla. Si la IA descarta por su clase casi todo y la regla ve al
menos dos que sí son lo buscado, su revisión no se aplica (puede ser una prompt injection), no
se guarda en la caché y se usa la regla; los descartes del filtro no cuentan para eso. Lo
descartado nunca se borra: va a `Descartado` con su origen («ia» o «regla») y su motivo.
"""

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from precia.emparejado.nucleo_nominal import encabeza, pieza_de
from precia.emparejado.referencias import (
    es_referencia,
    es_revision,
    normalizar_referencia,
    referencias_de_consulta,
    referencias_en,
)
from precia.emparejado.semantico import ClaseOferta, Clasificacion, motivo_descarte
from precia.emparejado.semantico import revisar as revisar_con_ia
from precia.emparejado.texto import (
    UMBRAL_POR_DEFECTO,
    agrupar_por_texto,
    limpiar_titulo,
    normalizar_titulo,
)
from precia.emparejado.tipos import CacheRevisiones, OfertaCandidata, SesionIA
from precia.nucleo.modelos import (
    Coincidencia,
    ConsultaBusqueda,
    Descartado,
    GrupoProducto,
    Modo,
    Oferta,
)

CRITERIO_OEM = "Referencia OEM exacta"
CRITERIO_OEM_AMPLIADO = "Referencia OEM (con equivalentes o revisiones)"
CRITERIO_TEXTO = "Similitud de texto (difflib)"
CRITERIO_IA = "Emparejado por IA"
MONEDA = "EUR"
SIN_NOMBRE = "Producto sin nombre"
ORIGEN_IA = "ia"
ORIGEN_REGLA = "regla"
UMBRAL_ANOMALIA = 0.8
"""Si la IA descarta más de esta fracción (o todas)..."""
MIN_BUENAS_REGLA = 2
"""...y la regla ve al menos estas con el núcleo en cabeza, la revisión no se aplica."""


@dataclass(slots=True)
class InformeRevision:
    """Lo que ha hecho la revisión de los resultados, para el progreso de la búsqueda."""

    revisadas: int = 0
    """Ofertas que ha revisado la IA (0 si no ha intervenido)."""
    descartadas_ia: int = 0
    descartadas_regla: int = 0
    desde_cache: int = 0
    anomala: bool = False
    """La IA descartaba demasiado: no se ha aplicado su revisión, sino la regla."""
    por_motivo: Counter[str] = field(default_factory=Counter)
    """Descartes de la IA por etiqueta («Pieza o accesorio de lo buscado», «Kit»...)."""


def componente_revision(consulta: ConsultaBusqueda, revisar: bool) -> str | None:
    """El componente de IA con el que `agrupar` revisará esta consulta, o `None` si no llama.

    «revision» si en Auto clasifica las piezas; «emparejado» en modo IA o si en Auto solo
    aplica el filtro. Sirve para reservar esa llamada en el tope de la búsqueda.
    """
    if consulta.modo is Modo.IA:
        return "emparejado"
    if consulta.modo is not Modo.AUTO:
        return None
    if revisar and not referencias_de_consulta(consulta.texto):
        return "revision"
    return "emparejado" if consulta.filtro.strip() else None


async def agrupar(
    consulta: ConsultaBusqueda,
    ofertas: Sequence[OfertaCandidata],
    *,
    sesion_ia: SesionIA | None = None,
    umbral: float = UMBRAL_POR_DEFECTO,
    revisar: bool = False,
    regla: bool = True,
    cache: CacheRevisiones | None = None,
    modelo: str = "",
    informe: InformeRevision | None = None,
) -> tuple[list[GrupoProducto], list[Descartado]]:
    """Agrupa las ofertas y devuelve los grupos y las ofertas descartadas con su motivo.

    La IA solo se usa si hay `sesion_ia` y el modo lo pide (`revisar` en Auto). Sus errores
    (`ErrorIA`) se propagan: el orquestador puede repetir el emparejado sin sesión, y entonces
    descarta la regla del núcleo. `regla=False` (el resultado provisional) no descarta nada por
    la regla. `cache` y `modelo` son la caché por oferta de la revisión. `informe`, si se da,
    se rellena con lo que ha hecho la revisión.
    """
    informe = informe if informe is not None else InformeRevision()
    descartados: list[Descartado] = []
    validas: list[tuple[Oferta, set[str]]] = []
    citadas: list[frozenset[str]] = []
    for candidata in ofertas:
        oferta, motivo = _oferta(candidata)
        if oferta is None:
            # Sin precio comparable: no se enseña (podría estar en dólares o ser negativo).
            descartados.append(
                Descartado(candidata.tienda, candidata.ficha.titulo, motivo, url=candidata.url)
            )
        else:
            validas.append((oferta, _referencias_de(oferta)))
            citadas.append(candidata.ficha.referencias_citadas)

    buscadas = referencias_de_consulta(consulta.texto)
    if buscadas:
        validas = _con_referencia(validas, citadas, buscadas, descartados)

    semantico = sesion_ia is not None and consulta.modo is Modo.IA
    componente = componente_revision(consulta, revisar) if sesion_ia is not None else None
    piezas = semantico or componente == "revision"
    filtro = consulta.filtro.strip() if consulta.modo is not Modo.CLASICO else ""
    revisado = False
    if sesion_ia is not None and componente is not None and validas:
        revision = await revisar_con_ia(
            sesion_ia,
            consulta.texto,
            [o for o, _ in validas],
            piezas=piezas,
            filtro=filtro,
            componente=componente,
            cache=cache,
            modelo=modelo,
        )
        informe.desde_cache = revision.desde_cache
        validas, revisado = _aplicar_revision(
            consulta.texto, validas, revision.clasificaciones, filtro, piezas, descartados, informe
        )
        if cache is not None and not informe.anomala:
            revision.guardar(cache)
    # Lo que ha entrado por la referencia buscada nunca lo descarta la regla (B5).
    if regla and not revisado and not buscadas:
        validas = _aplicar_regla(consulta.texto, validas, descartados, informe)

    grupos, sueltas = _agrupar_por_referencia(validas, buscadas)
    if semantico:
        if sueltas:
            grupos.append(_grupo_texto(sueltas, CRITERIO_IA))
    else:
        indices = agrupar_por_texto([o.titulo for o in sueltas], umbral=umbral)
        grupos.extend(_grupo_texto([sueltas[i] for i in g], CRITERIO_TEXTO) for g in indices)
    return _ids_unicos(grupos), descartados


def _aplicar_revision(
    consulta: str,
    validas: list[tuple[Oferta, set[str]]],
    clasificaciones: list[Clasificacion],
    filtro: str,
    piezas: bool,
    descartados: list[Descartado],
    informe: InformeRevision,
) -> tuple[list[tuple[Oferta, set[str]]], bool]:
    """Las ofertas que quedan tras la revisión con IA y si esta ha revisado las piezas.

    Si la revisión es anómala (descarta por su clase más del `UMBRAL_ANOMALIA` y la regla ve al
    menos `MIN_BUENAS_REGLA` con el núcleo en cabeza entre ellas), no se aplica nada de lo que ha
    dicho y la revisión cuenta como no hecha: entra la regla. Los descartes del filtro en
    lenguaje natural no cuentan: un filtro que deja fuera casi todo es legítimo.
    """
    motivos = [motivo_descarte(c, filtro) for c in clasificaciones]
    fuera = [i for i, m in enumerate(motivos) if m is not None]
    if piezas:
        informe.revisadas = len(validas)
        informe.descartadas_ia = len(fuera)
        por_clase = [
            i for i, c in enumerate(clasificaciones) if c.clase is not ClaseOferta.MISMA_PIEZA
        ]
        buenas_regla = sum(encabeza(validas[i][0].titulo, consulta) for i in por_clase)
        if (
            por_clase
            and len(por_clase) >= UMBRAL_ANOMALIA * len(validas)
            and buenas_regla >= MIN_BUENAS_REGLA
        ):
            informe.anomala = True
            return validas, False
    quedan: list[tuple[Oferta, set[str]]] = []
    for (oferta, refs), motivo in zip(validas, motivos, strict=True):
        if motivo is None:
            quedan.append((oferta, refs))
        else:
            descartados.append(_descartado(oferta, motivo, ORIGEN_IA))
            informe.por_motivo[motivo.split(":")[0].removeprefix("IA · ")] += 1
    return quedan, piezas


def _aplicar_regla(
    consulta: str,
    validas: list[tuple[Oferta, set[str]]],
    descartados: list[Descartado],
    informe: InformeRevision,
) -> list[tuple[Oferta, set[str]]]:
    """Las ofertas que no parecen una pieza o un kit de lo buscado (regla del núcleo, sin IA).

    Solo si alguna oferta es de verdad lo buscado (el núcleo en cabeza): si no hay ninguna, la
    consulta nombra una categoría («freno golf» y todo son «pastillas de freno...») y la regla
    no descarta nada.
    """
    if not any(encabeza(oferta.titulo, consulta) for oferta, _ in validas):
        return validas
    quedan: list[tuple[Oferta, set[str]]] = []
    for oferta, refs in validas:
        motivo = pieza_de(oferta.titulo, consulta)
        if motivo is None:
            quedan.append((oferta, refs))
        else:
            descartados.append(_descartado(oferta, motivo, ORIGEN_REGLA))
            informe.descartadas_regla += 1
    return quedan


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
        obtenida=candidata.ficha.obtenida,
        foto=candidata.ficha.foto,
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
    validas: list[tuple[Oferta, set[str]]],
    citadas: list[frozenset[str]],
    buscadas: set[str],
    descartados: list[Descartado],
) -> list[tuple[Oferta, set[str]]]:
    """Las ofertas con la referencia buscada (exacta > revisión > equivalente), etiquetadas.

    Cada una sale con las buscadas que la emparejan como referencias, para que
    `_agrupar_por_referencia` la meta en el grupo de la buscada y no en el de su propia
    referencia (la revisión «1K0941005E» o la «301635» del fabricante).
    """
    quedan = []
    for (oferta, refs), cita in zip(validas, citadas, strict=True):
        if exactas := refs & buscadas:
            quedan.append((oferta, exactas))
        elif revisadas := {b for b in buscadas for r in refs if es_revision(r, b)}:
            quedan.append((replace(oferta, coincidencia=Coincidencia.REVISION), revisadas))
        elif equivalentes := cita & buscadas:
            oferta = replace(oferta, coincidencia=Coincidencia.EQUIVALENTE)
            quedan.append((oferta, set(equivalentes)))
        else:
            motivo = "Referencia OEM distinta" if refs else "Sin referencia OEM"
            descartados.append(_descartado(oferta, motivo))
    return quedan


def _descartado(oferta: Oferta, motivo: str, origen: str = "") -> Descartado:
    return Descartado(
        oferta.tienda, oferta.titulo, motivo, url=oferta.url, precio=oferta.precio, origen=origen
    )


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
            criterio=_criterio_oem(ofertas),
            ofertas=_por_precio(ofertas),
        )
        for ref, ofertas in por_referencia.items()
    ]
    return grupos, sueltas


def _criterio_oem(ofertas: list[Oferta]) -> str:
    if all(o.coincidencia is Coincidencia.EXACTA for o in ofertas):
        return CRITERIO_OEM
    return CRITERIO_OEM_AMPLIADO


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
