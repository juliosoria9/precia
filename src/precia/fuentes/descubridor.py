"""Descubrimiento de tiendas con Google (H5): de los resultados a las tiendas nuevas.

Flujo (memoria §5): se piden a Google los resultados orgánicos y los de Shopping de la
consulta; de cada web nueva se abre la página que Google enseña y, si es la ficha de un
producto (`fuentes.clasificador`: datos estructurados y, si hay duda y hay IA, el clasificador),
su dominio es una tienda descubierta. Quien llama la da de alta y la busca como a las demás: la
IA le crea el perfil en esa misma búsqueda (modos Auto e IA).

Límites, para que una búsqueda no se convierta en un rastreo:

- Dos peticiones a Google (orgánico y Shopping). Si la primera sale bloqueada, no se hace la
  segunda y **no se intenta pasar el bloqueo**.
- Como mucho `MAX_PAGINAS` páginas descubiertas abiertas y `MAX_TIENDAS` tiendas nuevas.
- Una web por dominio; fuera las de `LISTA_NEGRA` (no son tiendas o venden entre particulares,
  nota 08) y las tiendas ya dadas de alta.

Todo pasa por el `navegador` (anti-SSRF, cortesía por dominio, caché): aquí no se descarga nada
por otra vía. Las URL vienen de Google, que es contenido externo: las valida el navegador.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from precia.fuentes.clasificador import Veredicto, clasificar, clasificar_con_ia
from precia.fuentes.google import (
    ORGANICO,
    SHOPPING,
    ResultadoWeb,
    leer_resultados,
    motivo_bloqueo,
    url_busqueda,
)
from precia.ia.servicio import SesionIA
from precia.ia.tipos import ErrorIA, PresupuestoAgotado
from precia.navegador.tipos import Bloqueado, ErrorNavegador, Pagina
from precia.nucleo.dominios import normalizar_dominio
from precia.nucleo.modelos import EstadoPaso, Paso

registro = logging.getLogger(__name__)

MAX_PAGINAS = 8
"""Páginas descubiertas que se abren como mucho en una búsqueda."""
MAX_TIENDAS = 3
"""Tiendas nuevas por búsqueda: cada una cuesta después una configuración con IA."""
PASO_FALLO = "descubrir"
"""Nombre del paso cuando Google no da resultados (bloqueo, error): la web lo enseña aparte."""
SIN_RESOLVER = "no se intenta resolver"

LISTA_NEGRA: frozenset[str] = frozenset(
    {
        # Vídeo, redes sociales y enciclopedias: no venden.
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "pinterest.com",
        "pinterest.es",
        "linkedin.com",
        "reddit.com",
        "wikipedia.org",
        "forocoches.com",
        # Comparadores: enlazan a tiendas, no son una.
        "idealo.es",
        "kelkoo.es",
        # Venta entre particulares: sus fichas llevan datos personales del vendedor (nota 08).
        "wallapop.com",
        "milanuncios.com",
        "vinted.es",
    }
)


def en_lista_negra(dominio: str) -> bool:
    """Si `dominio` es uno de `LISTA_NEGRA` o un subdominio suyo."""
    return any(dominio == d or dominio.endswith(f".{d}") for d in LISTA_NEGRA)


class NavegadorWeb(Protocol):
    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina: ...


@dataclass(frozen=True, slots=True)
class TiendaDescubierta:
    dominio: str
    url: str
    """La ficha que enseñó Google (sirve de prueba de que la tienda vende el producto)."""
    titulo: str
    origen: str
    """`google.ORGANICO` o `google.SHOPPING`."""
    por_ia: bool = False
    """Si la decidió el clasificador con IA (los datos estructurados no bastaban)."""


@dataclass(frozen=True, slots=True)
class Descubrimiento:
    tiendas: tuple[TiendaDescubierta, ...]
    pasos: tuple[Paso, ...]
    """Lo que se enseña en el progreso de la búsqueda, bajo «Google»."""


async def descubrir(
    consulta: str,
    navegador: NavegadorWeb,
    *,
    sesion: SesionIA | None = None,
    conocidas: Collection[str] = (),
    max_paginas: int = MAX_PAGINAS,
    max_tiendas: int = MAX_TIENDAS,
) -> Descubrimiento:
    """Tiendas nuevas que venden lo buscado, según Google. Nunca lanza por un fallo de la red,
    de Google o de la IA: lo cuenta en los pasos."""
    pasos: list[Paso] = []
    resultados: list[ResultadoWeb] = []
    for origen, shopping in ((ORGANICO, False), (SHOPPING, True)):
        fallo = ""
        try:
            pagina = await navegador.obtener(url_busqueda(consulta, shopping=shopping), js=True)
        except Bloqueado:
            fallo = f"Google ha bloqueado la consulta con un CAPTCHA o un desafío; {SIN_RESOLVER}"
        except ErrorNavegador as error:
            registro.info("Google: no se pudo descargar la página de resultados: %s", error)
            fallo = "No se pudo consultar Google"
        else:
            if motivo := motivo_bloqueo(pagina):
                fallo = f"{motivo}; {SIN_RESOLVER}"
            else:
                resultados.extend(leer_resultados(pagina.html, origen=origen))
        if not fallo:
            continue
        if not shopping:
            # Sin los resultados orgánicos no se sigue: Shopping saldría bloqueado igual.
            return Descubrimiento((), (Paso(PASO_FALLO, EstadoPaso.ERROR, fallo),))
        pasos.append(Paso("Shopping", EstadoPaso.AVISO, f"Sin resultados de Shopping: {fallo}"))

    conocidas = {d for d in (normalizar_dominio(c) for c in conocidas) if d}
    candidatas = _candidatas(resultados, conocidas, max_paginas)
    organicos = sum(1 for r in resultados if r.origen == ORGANICO)
    if not resultados:
        pasos.append(Paso("resultados", EstadoPaso.AVISO, "Google no ha dado ningún resultado"))
        return Descubrimiento((), tuple(pasos))
    pasos.append(
        Paso(
            "resultados",
            EstadoPaso.OK,
            f"{len(resultados)} resultados ({organicos} orgánicos y "
            f"{len(resultados) - organicos} de Shopping); {len(candidatas)} webs nuevas que mirar",
        )
    )

    tiendas: list[TiendaDescubierta] = []
    vistos: set[str] = set(conocidas)
    sin_abrir = dudosas = no_fichas = por_ia = 0
    ia_disponible = sesion is not None
    for dominio, resultado in candidatas:
        if len(tiendas) >= max_tiendas:
            break
        try:
            pagina = await navegador.obtener(resultado.url)
        except ErrorNavegador:
            sin_abrir += 1
            continue
        # Si redirige a otra web, la tienda es la del final.
        final = normalizar_dominio(urlsplit(pagina.url_final).hostname or "") or dominio
        if final in vistos or en_lista_negra(final):
            continue
        veredicto = clasificar(pagina.html, pagina.url_final)
        decidida_por_ia = False
        if veredicto is Veredicto.DUDA and ia_disponible and sesion is not None:
            try:
                es_ficha = await clasificar_con_ia(sesion, pagina.html)
            except PresupuestoAgotado:
                ia_disponible = False
            except ErrorIA as error:
                registro.info("Clasificador: la IA no pudo decidir sobre %s: %s", final, error)
            else:
                veredicto = Veredicto.FICHA if es_ficha else Veredicto.NO_FICHA
                decidida_por_ia = True
                por_ia += 1
        if veredicto is Veredicto.DUDA:
            dudosas += 1
        elif veredicto is Veredicto.NO_FICHA:
            no_fichas += 1
        else:
            vistos.add(final)
            tiendas.append(
                TiendaDescubierta(
                    final, pagina.url_final, resultado.titulo, resultado.origen, decidida_por_ia
                )
            )
    partes = [f"{len(tiendas)} son fichas de producto"]
    if por_ia:
        partes.append(f"{por_ia} decididas por la IA")
    if no_fichas:
        partes.append(f"{no_fichas} no lo son")
    if dudosas:
        partes.append(f"{dudosas} dudosas sin resolver")
    if sin_abrir:
        partes.append(f"{sin_abrir} no se pudieron abrir")
    estado = EstadoPaso.IA if por_ia else EstadoPaso.OK
    pasos.append(Paso("clasificación", estado, ", ".join(partes)))
    if not tiendas:
        pasos.append(Paso("tiendas nuevas", EstadoPaso.AVISO, "Google no ha traído tiendas nuevas"))
    return Descubrimiento(tuple(tiendas), tuple(pasos))


def _candidatas(
    resultados: list[ResultadoWeb], conocidas: Collection[str], maximo: int
) -> list[tuple[str, ResultadoWeb]]:
    """El primer resultado de cada dominio nuevo que puede ser una tienda, como mucho `maximo`."""
    candidatas: list[tuple[str, ResultadoWeb]] = []
    vistos: set[str] = set()
    for resultado in resultados:
        if len(candidatas) >= maximo:
            break
        dominio = normalizar_dominio(urlsplit(resultado.url).hostname or "")
        if dominio is None or dominio in vistos or dominio in conocidas or en_lista_negra(dominio):
            continue
        vistos.add(dominio)
        candidatas.append((dominio, resultado))
    return candidatas
