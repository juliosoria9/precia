"""Orquestador de una búsqueda (memoria §5, nota 14): tiendas → perfiles → fichas → emparejado.

Por cada tienda elegida, en paralelo (la cortesía por dominio la pone el navegador):

1. Perfil vigente del almacén. Si no hay, en modo Auto o IA lo crea el 🤖 configurador; se
   guarda pendiente de revisión (`revisado=False`) y se usa de forma provisional.
2. Búsqueda con la URL del perfil y extracción del listado. Si los selectores no dan nada pero
   la página trae los resultados como datos (`extraccion.embebidos.listado_estructurado`:
   JSON-LD `ItemList`, `__NEXT_DATA__`...), se usan los que nombran la consulta (los
   «destacados» que trae cualquier página no son resultados) con un aviso y el perfil no se da
   por roto mientras así salgan ofertas con precio (nota 15 §D). Si no, y la página tiene productos
   con precio (o un selector no es válido), el perfil está roto: en clásico se marca como roto;
   en Auto o IA el 🤖 reparador propone una versión nueva.
3. Fichas de los `max_fichas` primeros resultados: JSON-LD → metadatos → selectores → 🤖
   extractor de respaldo (solo si se permite la IA) y anclaje de todo valor.

Un perfil `api_*` (WooCommerce, Shopify) busca en la API pública de la tienda, sin JavaScript, y
las ofertas salen directamente de su respuesta (`fuentes.apis.ofertas_api`): no se abre ninguna
ficha ni hay selectores que reparar. Una respuesta sin productos es «sin resultados».

Después, el emparejado de todas las ofertas (como mucho `max_ofertas`) y el histórico.

Modos (ADR-002): **clásico** nunca llama a la IA; **auto** solo cuando falla la vía clásica
(sin perfil, perfil roto, campos que faltan) o para aplicar el filtro; **IA**, además, empareja
siempre con IA. Sin servicio de IA (no hay clave) Auto e IA funcionan como el clásico y lo dicen.

Cada error se convierte en un paso legible del progreso de su tienda y nunca para las demás:
el robots.txt que prohíbe → omitida; un bloqueo (CAPTCHA, anti-bot) → error, se para y se
cuenta, nunca se evade; un error de la IA → aviso y se sigue sin IA.

La IA no ejecuta nada: los selectores que propone los valida `perfiles` con el lenguaje
restringido de `extraccion.selectores`, y toda URL que se visita pasa por `validar_url`.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit

from precia.almacen.sqlite import Almacen, ErrorAlmacen
from precia.emparejado.agrupador import agrupar
from precia.emparejado.tipos import OfertaCandidata
from precia.extraccion.anclaje import normalizar
from precia.extraccion.embebidos import listado_estructurado
from precia.extraccion.ficha import extraer_ficha
from precia.extraccion.listado import extraer_listado
from precia.extraccion.tipos import ExtractorIA, ResultadoListado, SelectorNoValido
from precia.fuentes.apis import AVISO_AGOTADO
from precia.ia.extractor import ExtractorRespaldoIA
from precia.ia.servicio import ServicioIA, SesionIA
from precia.ia.tipos import ErrorIA
from precia.navegador.tipos import (
    Bloqueado,
    ErrorDescarga,
    Pagina,
    ProhibidoPorRobots,
    URLNoPermitida,
)
from precia.navegador.url import Validador, validar_url
from precia.nucleo.dominios import normalizar_dominio
from precia.nucleo.modelos import (
    ConsultaBusqueda,
    Descartado,
    EstadoPaso,
    EstadoPerfil,
    GrupoProducto,
    Modo,
    OrigenDato,
    Paso,
    ProgresoTienda,
    ResultadoBusqueda,
    Tienda,
)
from precia.perfiles.configurador import configurar
from precia.perfiles.descubrimiento import MIN_PALABRA, parece_listado
from precia.perfiles.protocolos import Navegador as NavegadorDePerfiles
from precia.perfiles.reparador import reparar
from precia.perfiles.tipos import ConfiguracionFallida, Perfil, ReparacionFallida
from precia.perfiles.validador import (
    NOMBRE_PLATAFORMA,
    PLATAFORMA_DE_TIPO,
    de_la_tienda,
    ofertas_de_api,
    url_de_busqueda,
)

MAX_FICHAS = 5
MAX_OFERTAS = 60
"""Ofertas que llegan al agrupador: con más de 150 el emparejado ya es lento."""
GENERAL = "PrecIA"
"""Nombre del progreso con los avisos que no son de una tienda (IA, filtro, emparejado)."""
BLOQUEO = "Bloqueo (CAPTCHA o anti-bot): no se evade"
SIN_IA = "IA no disponible"
MAX_DETALLE = 300
PERMITE = "Permite la búsqueda"
IGNORADO = "Lo prohíbe; se ignora por decisión del usuario (ADR-017)"
INESPERADO = "Error inesperado: la tienda se salta en esta búsqueda (detalle en el registro)"
ESTRUCTURADOS = "Selectores sin resultados: se usan los datos estructurados de la página"

registro = logging.getLogger(__name__)


def _ahora() -> datetime:
    return datetime.now(UTC)


def _recortar(texto: str, maximo: int = MAX_DETALLE) -> str:
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def estado_de(perfil: Perfil) -> EstadoPerfil:
    """Estado de la tienda según su perfil vigente (memoria §4: sin revisión no se fija)."""
    if not perfil.revisado:
        return EstadoPerfil.PENDIENTE_REVISION
    return EstadoPerfil.REPARADO if "reparador" in perfil.autor else EstadoPerfil.ACTIVO


class _NavegadorVigilado:
    """Envuelve el navegador del configurador para saber si todo lo prohibió el robots.txt.

    El configurador se traga `ProhibidoPorRobots` (prueba otras rutas); sin esto, una tienda
    que lo prohíbe todo saldría como «no se encuentra la búsqueda» en vez de como omitida.
    """

    def __init__(self, navegador: NavegadorDePerfiles) -> None:
        self._navegador = navegador
        self.intentos = 0
        self.prohibidas = 0

    async def obtener(self, url: str, *, js: bool = False) -> Pagina:
        self.intentos += 1
        try:
            return await self._navegador.obtener(url, js=js)
        except ProhibidoPorRobots:
            self.prohibidas += 1
            raise

    @property
    def todo_prohibido(self) -> bool:
        return self.intentos > 0 and self.prohibidas == self.intentos


class _ExtractorVigilado:
    """El extractor de respaldo de una tienda, que se apaga tras el primer error de la IA.

    `extraer_ficha` se traga el `ErrorIA` (lo deja como aviso de la ficha) y seguiría llamando
    en cada ficha: con la API caída, un timeout por ficha; con salidas no válidas, dos llamadas
    de pago por ficha. Aquí se recuerda el error para que la tienda siga sin IA (ADR-002).
    """

    def __init__(self, extractor: ExtractorIA) -> None:
        self._extractor = extractor
        self.error: ErrorIA | None = None

    async def extraer(self, texto_pagina: str, campos: tuple[str, ...]) -> dict[str, str]:
        try:
            return await self._extractor.extraer(texto_pagina, campos)
        except ErrorIA as error:
            self.error = error
            raise


class Navegador(NavegadorDePerfiles, Protocol):
    """Lo que necesita el orquestador del navegador: lo de `perfiles` y si se ignoró robots."""

    def robots_ignorado(self, dominio: str) -> bool: ...


@dataclass
class _Salida:
    progreso: ProgresoTienda
    candidatas: list[OfertaCandidata] = field(default_factory=list)


class Orquestador:
    def __init__(
        self,
        almacen: Almacen,
        navegador: Navegador,
        servicio_ia: ServicioIA | None,
        *,
        max_fichas: int = MAX_FICHAS,
        max_ofertas: int = MAX_OFERTAS,
        validador: Validador = validar_url,
        reloj: Callable[[], datetime] = _ahora,
        cronometro: Callable[[], float] = time.monotonic,
        motivo_sin_ia: str = "",
    ) -> None:
        """`servicio_ia` es `None` si no hay IA (sin clave): Auto e IA van como el clásico.

        `motivo_sin_ia` se añade al aviso «IA no disponible» (p. ej. «falta la clave»).
        """
        if max_fichas < 1 or max_ofertas < 1:
            raise ValueError("max_fichas y max_ofertas tienen que ser al menos 1")
        self._almacen = almacen
        self._navegador = navegador
        self._servicio = servicio_ia
        self._max_fichas = max_fichas
        self._max_ofertas = max_ofertas
        self._validador = validador
        self._reloj = reloj
        self._cronometro = cronometro
        self._motivo_sin_ia = motivo_sin_ia

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda:
        inicio = self._cronometro()
        generales: list[Paso] = []
        sesion: SesionIA | None = None
        if consulta.modo is not Modo.CLASICO:
            if self._servicio is not None:
                sesion = self._servicio.nueva_busqueda()
            else:
                motivo = f" ({self._motivo_sin_ia})" if self._motivo_sin_ia else ""
                generales.append(
                    Paso("IA", EstadoPaso.AVISO, f"{SIN_IA}{motivo}: se busca como en modo clásico")
                )
                if consulta.filtro:
                    generales.append(
                        Paso("filtro", EstadoPaso.AVISO, "El filtro necesita la IA: no se aplica")
                    )

        tiendas = self._tiendas(consulta, generales)
        salidas = await asyncio.gather(
            *(_EnTienda(self, t, consulta, sesion).ejecutar() for t in tiendas)
        )
        candidatas = [c for s in salidas for c in s.candidatas]
        if len(candidatas) > self._max_ofertas:
            generales.append(
                Paso(
                    "emparejado",
                    EstadoPaso.AVISO,
                    f"Hay {len(candidatas)} ofertas: solo se comparan las {self._max_ofertas} "
                    "primeras",
                )
            )
            candidatas = candidatas[: self._max_ofertas]
        grupos, descartados = await self._agrupar(consulta, candidatas, sesion, generales)
        self._guardar_historico(grupos, generales)

        progreso = [s.progreso for s in salidas]
        if generales:
            progreso.insert(0, ProgresoTienda(GENERAL, tuple(generales)))
        if consulta.descubrir:
            progreso.insert(
                0,
                ProgresoTienda(
                    "Google",
                    (
                        Paso(
                            "descubrir",
                            EstadoPaso.AVISO,
                            "El descubrimiento con Google aún no está disponible: no se consulta",
                        ),
                    ),
                ),
            )
        llamadas = sesion.llamadas if sesion is not None else 0
        coste = sesion.coste_usd if sesion is not None else Decimal(0)
        duracion = max(self._cronometro() - inicio, 0.0)
        # Solo son métricas para la evaluación: si no se pueden guardar, la búsqueda vale igual.
        with contextlib.suppress(ErrorAlmacen):
            self._almacen.registrar_busqueda(
                consulta.texto, consulta.modo, self._reloj(), llamadas, coste, duracion
            )
        return ResultadoBusqueda(
            consulta=consulta,
            grupos=tuple(grupos),
            descartados=tuple(descartados),
            progreso=tuple(progreso),
            llamadas_ia=llamadas,
            coste_ia_usd=coste,
            duracion_s=duracion,
            es_demo=False,
        )

    def _tiendas(self, consulta: ConsultaBusqueda, generales: list[Paso]) -> list[Tienda]:
        """Solo tiendas dadas de alta: un dominio cualquiera de la consulta no se visita nunca."""
        todas = self._almacen.tiendas()
        if not consulta.tiendas:
            return todas
        pedidas = {normalizar_dominio(t) for t in consulta.tiendas}
        elegidas = [t for t in todas if t.dominio in pedidas]
        if len(elegidas) < len(set(consulta.tiendas)):
            generales.append(
                Paso(
                    "tiendas",
                    EstadoPaso.AVISO,
                    "Se ignoran las tiendas que no están dadas de alta.",
                )
            )
        return elegidas

    async def _agrupar(
        self,
        consulta: ConsultaBusqueda,
        candidatas: Sequence[OfertaCandidata],
        sesion: SesionIA | None,
        generales: list[Paso],
    ) -> tuple[list[GrupoProducto], list[Descartado]]:
        try:
            return await agrupar(consulta, candidatas, sesion_ia=sesion)
        except ErrorIA as error:
            generales.append(
                Paso(
                    "emparejado",
                    EstadoPaso.AVISO,
                    _recortar(f"🤖 Emparejado sin IA ({error}): se repite en modo clásico"),
                )
            )
            if consulta.filtro:
                generales.append(
                    Paso("filtro", EstadoPaso.AVISO, "Sin la IA, el filtro no se ha aplicado")
                )
            return await agrupar(consulta, candidatas, sesion_ia=None)

    def _guardar_historico(self, grupos: Sequence[GrupoProducto], generales: list[Paso]) -> None:
        fecha = self._reloj()
        fallidas = 0
        for grupo in grupos:
            for oferta in grupo.ofertas:
                if not oferta.precio_anclado:
                    continue  # Un precio que no está en la página nunca entra en el histórico.
                try:
                    self._almacen.guardar_precio(
                        grupo.id,
                        grupo.nombre,
                        grupo.referencia_oem,
                        oferta.tienda,
                        oferta.url,
                        oferta.precio,
                        fecha,
                        titulo=oferta.titulo,
                        campos=oferta.campos,
                        disponible=oferta.disponible,
                        criterio=grupo.criterio,
                    )
                except ErrorAlmacen:
                    fallidas += 1
        if fallidas:
            generales.append(
                Paso(
                    "histórico",
                    EstadoPaso.AVISO,
                    f"{fallidas} ofertas no se han podido guardar en el histórico",
                )
            )


class _EnTienda:
    """La búsqueda en una tienda: sus pasos de progreso y lo que hay que guardar de ella."""

    def __init__(
        self,
        orquestador: Orquestador,
        tienda: Tienda,
        consulta: ConsultaBusqueda,
        sesion: SesionIA | None,
    ) -> None:
        self.o = orquestador
        self.tienda = tienda
        self.dominio = tienda.dominio
        self.consulta = consulta
        self.sesion = sesion
        self.pasos: list[Paso] = []
        self.fase = "perfil"
        self.estado: EstadoPerfil | None = None
        self.robots: tuple[str, bool] | None = None
        self.bloqueado = False
        self.visitada = False

    @property
    def con_ia(self) -> bool:
        return self.sesion is not None

    @property
    def campos(self) -> tuple[str, ...]:
        return tuple(self.consulta.campos)

    def paso(self, nombre: str, estado: EstadoPaso, detalle: str = "") -> None:
        self.pasos.append(Paso(nombre, estado, _recortar(detalle)))

    def sin_ia(self, error: ErrorIA, que: str) -> None:
        """Un error de la IA no para la búsqueda: se avisa y la tienda sigue sin IA."""
        self.paso("IA", EstadoPaso.AVISO, f"🤖 {que}: {error}. Se sigue sin IA")
        self.sesion = None

    async def ejecutar(self) -> _Salida:
        candidatas: list[OfertaCandidata] = []
        try:
            candidatas = await self._ejecutar()
        except ProhibidoPorRobots as error:
            self.visitada = True
            if self.robots is not None and self.robots[1]:
                # La búsqueda estaba permitida: no es una tienda omitida, sino un paso que no
                # ha podido terminar.
                self.paso(self.fase, EstadoPaso.ERROR, str(error))
            else:
                self.robots = (str(error), False)
                self.pasos.insert(0, Paso("robots.txt", EstadoPaso.OMITIDO, _recortar(str(error))))
        except Bloqueado as error:
            self.visitada = True
            self.bloqueado = True
            self.paso(self.fase, EstadoPaso.ERROR, f"{BLOQUEO} ({error.motivo})")
        except ErrorAlmacen as error:
            self.paso(self.fase, EstadoPaso.ERROR, f"No se pudo guardar en el almacén: {error}")
        except Exception:
            # Último recurso: un fallo que nadie espera (Playwright, la librería HTTP, un dato
            # raro de la página...) es un error de esta tienda. Sin esto, `asyncio.gather`
            # tumbaría la búsqueda entera (un 500) y dejaría a las demás tiendas sin recoger.
            # El detalle va al registro y no a la página: puede llevar rutas o datos internos.
            registro.exception("Error inesperado en %s (%s)", self.dominio, self.fase)
            self.paso(self.fase, EstadoPaso.ERROR, INESPERADO)
        self._actualizar_tienda()
        return _Salida(ProgresoTienda(self.dominio, tuple(self.pasos)), candidatas)

    async def _ejecutar(self) -> list[OfertaCandidata]:
        perfil = self.o._almacen.perfil_vigente(self.dominio)
        nuevo = perfil is None
        if perfil is None:
            perfil = await self._configurar()
            if perfil is None:
                return []
        pagina = await self._buscar(perfil, nuevo)
        if pagina is None:
            return []
        if perfil.es_api:
            return await self._de_la_api(perfil, pagina)
        listado = await self._listado(perfil, pagina)
        if listado is None:
            return []
        perfil, resultados, estructurado = listado
        if not resultados:
            self._sin_resultados()
            return []
        candidatas = await self._fichas(perfil, resultados)
        if not estructurado or self.bloqueado or any(_valida(c) for c in candidatas):
            return candidatas
        # Los datos estructurados no han dado ninguna oferta con precio: el perfil sigue roto.
        roto = await self._perfil_roto(perfil, pagina, "")
        if roto is None:
            return candidatas
        perfil, resultados = roto
        return await self._fichas(perfil, resultados) if resultados else candidatas

    def _sin_resultados(self, donde: str = "en esta tienda") -> None:
        self.paso(
            "extracción", EstadoPaso.AVISO, f"Sin resultados para «{self.consulta.texto}» {donde}"
        )

    # --- Perfil -----------------------------------------------------------------------------

    async def _configurar(self) -> Perfil | None:
        if not self.con_ia:
            detalle = (
                "Sin perfil (en modo clásico no se configura)"
                if self.consulta.modo is Modo.CLASICO
                else f"Sin perfil: {SIN_IA.lower()} para configurarlo"
            )
            self.paso("perfil", EstadoPaso.OMITIDO, detalle)
            return None
        assert self.sesion is not None
        navegador = _NavegadorVigilado(self.o._navegador)
        self.visitada = True
        try:
            perfil = await configurar(
                self.dominio,
                navegador,
                self.sesion,
                self.consulta.texto,
                validador=self.o._validador,
                reloj=self.o._reloj,
            )
        except ConfiguracionFallida as error:
            if navegador.todo_prohibido:
                raise ProhibidoPorRobots(
                    f"El robots.txt de {self.dominio} no permite visitar la tienda."
                ) from error
            self.paso("perfil", EstadoPaso.ERROR, f"🤖 Configurador: {error}")
            return None
        except ErrorIA as error:
            self.paso(
                "perfil",
                EstadoPaso.AVISO,
                f"🤖 Configurador sin IA: {error}. La tienda sigue sin perfil",
            )
            return None
        except (ErrorDescarga, URLNoPermitida) as error:
            self.paso("perfil", EstadoPaso.ERROR, f"🤖 Configurador: {error}")
            return None
        guardado = self.o._almacen.guardar_perfil(perfil)
        self.estado = EstadoPerfil.PENDIENTE_REVISION
        if guardado.es_api:
            # Sin IA: el perfil sale de la API pública de la plataforma (coste 0).
            nombre = NOMBRE_PLATAFORMA[PLATAFORMA_DE_TIPO[guardado.tipo]]
            self.paso(
                "perfil",
                EstadoPaso.AVISO,
                f"Configurador: perfil nuevo v{guardado.version} con la API pública de {nombre}, "
                "sin IA; pendiente de revisión (se usa de forma provisional)",
            )
        else:
            self.paso(
                "perfil",
                EstadoPaso.IA,
                f"🤖 Configurador: perfil nuevo v{guardado.version}, pendiente de revisión "
                "(se usa de forma provisional)",
            )
        return guardado

    def _paso_perfil(self, perfil: Perfil) -> None:
        if perfil.revisado:
            self.paso("perfil", EstadoPaso.OK, f"v{perfil.version} ({perfil.autor}, revisado)")
        else:
            self.paso(
                "perfil",
                EstadoPaso.AVISO,
                f"v{perfil.version} ({perfil.autor}) pendiente de revisión: se usa de forma "
                "provisional",
            )

    # --- Búsqueda y listado -----------------------------------------------------------------

    async def _buscar(self, perfil: Perfil, nuevo: bool) -> Pagina | None:
        self.fase = "búsqueda"
        self.visitada = True
        try:
            url = await self.o._validador(url_de_busqueda(perfil.url_busqueda, self.consulta.texto))
            # Una API devuelve JSON: Chromium no pinta nada ahí.
            js = perfil.necesita_js and not perfil.es_api
            pagina = await self.o._navegador.obtener(url, js=js)
        except (ErrorDescarga, URLNoPermitida, Bloqueado) as error:
            # Si el robots.txt lo prohíbe, la tienda sale solo como omitida (sin el perfil).
            if not nuevo:
                self._paso_perfil(perfil)
            if isinstance(error, Bloqueado):
                raise
            self.paso("búsqueda", EstadoPaso.ERROR, str(error))
            return None
        if not nuevo:
            self._paso_perfil(perfil)
        if self.o._navegador.robots_ignorado(urlsplit(url).hostname or self.dominio):
            # ADR-017: el usuario decidió ignorar el robots.txt; se visita, pero se enseña.
            self.robots = (IGNORADO, False)
            self.pasos.insert(0, Paso("robots.txt", EstadoPaso.AVISO, IGNORADO))
        else:
            self.robots = (PERMITE, True)
            self.pasos.insert(0, Paso("robots.txt", EstadoPaso.OK, PERMITE))
        if not 200 <= pagina.estado < 300:
            self.paso(
                "búsqueda", EstadoPaso.ERROR, f"La tienda responde con el estado {pagina.estado}"
            )
            return None
        if not de_la_tienda(pagina, self.dominio):
            self.paso("búsqueda", EstadoPaso.ERROR, "La búsqueda redirige fuera de la tienda")
            return None
        detalle = "Resultados descargados"
        if perfil.es_api:
            detalle += " de la API pública de la tienda"
        elif perfil.necesita_js:
            detalle += " con Chromium"
        if pagina.desde_cache:
            detalle += " (caché)"
        self.paso("búsqueda", EstadoPaso.OK, detalle)
        return pagina

    async def _listado(
        self, perfil: Perfil, pagina: Pagina
    ) -> tuple[Perfil, list[ResultadoListado], bool] | None:
        """El listado con el perfil (reparado si hace falta), o `None` si está roto.

        El tercer valor dice si los resultados salen de los datos estructurados de la página y
        no de los selectores: entonces aún hay que ver si dan ofertas válidas.
        """
        self.fase = "perfil"
        try:
            resultados = extraer_listado(pagina, perfil)
        except SelectorNoValido as error:
            # Un selector fuera del subconjunto es un perfil roto aunque haya datos estructurados.
            roto = await self._perfil_roto(perfil, pagina, f"selector no válido: {error}")
            return None if roto is None else (*roto, False)
        if resultados:
            self.estado = self.estado or estado_de(perfil)
            return perfil, resultados, False
        estructurados = _de_la_consulta(
            listado_estructurado(pagina, self.dominio), self.consulta.texto
        )
        if estructurados:
            self.paso("perfil", EstadoPaso.AVISO, ESTRUCTURADOS)
            self.estado = self.estado or estado_de(perfil)
            return perfil, estructurados, True
        roto = await self._perfil_roto(perfil, pagina, "")
        return None if roto is None else (*roto, False)

    async def _perfil_roto(
        self, perfil: Perfil, pagina: Pagina, motivo: str
    ) -> tuple[Perfil, list[ResultadoListado]] | None:
        """Listado vacío con el perfil: sin resultados de verdad, reparado o `None` (roto).

        Sin `motivo` (no hay un selector inválido), solo está roto si la página tiene productos
        con precio.
        """
        self.fase = "perfil"
        if not motivo:
            # Una página sin productos con precio no es un perfil roto: la tienda no tiene lo
            # buscado. Así tampoco se gasta una llamada al reparador cada vez que pasa (ni si
            # la tienda vacía el listado a propósito para provocarlas).
            if not parece_listado(pagina, self.dominio, self.consulta.texto, portada=None):
                self.estado = self.estado or estado_de(perfil)
                return perfil, []
            motivo = "el listado sale vacío"
        reparado = await self._reparar(perfil, pagina, motivo)
        if reparado is None:
            self.estado = EstadoPerfil.ROTO
            return None
        return reparado, extraer_listado(pagina, reparado)

    async def _reparar(self, perfil: Perfil, pagina: Pagina, motivo: str) -> Perfil | None:
        if not self.con_ia:
            if self.consulta.modo is Modo.CLASICO:
                detalle = f"Perfil roto ({motivo}): en modo clásico no se repara"
            else:
                detalle = f"Perfil roto ({motivo}): sin IA no se puede reparar"
            self.paso("perfil", EstadoPaso.ERROR, detalle)
            return None
        assert self.sesion is not None
        try:
            nuevo = await reparar(
                perfil,
                pagina,
                self.o._navegador,
                self.sesion,
                self.consulta.texto,
                validador=self.o._validador,
                reloj=self.o._reloj,
            )
        except ReparacionFallida as error:
            self.paso("perfil", EstadoPaso.ERROR, f"Perfil roto ({motivo}). 🤖 Reparador: {error}")
            return None
        except ErrorIA as error:
            self.sin_ia(error, "Reparador")
            self.paso("perfil", EstadoPaso.ERROR, f"Perfil roto ({motivo}): no se ha reparado")
            return None
        except (ErrorDescarga, URLNoPermitida, ProhibidoPorRobots) as error:
            self.paso(
                "perfil",
                EstadoPaso.ERROR,
                f"Perfil roto ({motivo}). No se pudo validar la reparación: {error}",
            )
            return None
        guardado = self.o._almacen.guardar_perfil(nuevo)
        self.estado = EstadoPerfil.PENDIENTE_REVISION
        self.paso(
            "perfil",
            EstadoPaso.IA,
            f"🤖 Reparador: el perfil v{perfil.version} estaba roto ({motivo}); propone "
            f"v{guardado.version}, pendiente de revisión",
        )
        return guardado

    # --- API pública ------------------------------------------------------------------------

    async def _de_la_api(self, perfil: Perfil, pagina: Pagina) -> list[OfertaCandidata]:
        """Ofertas de la respuesta de la API de un perfil `api_*`, sin abrir ninguna ficha."""
        self.fase = "extracción"
        self.estado = self.estado or estado_de(perfil)
        fichas = await ofertas_de_api(
            perfil,
            pagina,
            self.o._navegador,
            self.campos,
            validador=self.o._validador,
            maximo=self.o._max_fichas,
        )
        if not fichas:
            self._sin_resultados("en la API de esta tienda")
            return []
        nombre = NOMBRE_PLATAFORMA[PLATAFORMA_DE_TIPO[perfil.tipo]]
        sin_precio = sum(f.precio is None for f in fichas)
        detalle = f"{len(fichas)} productos de la API pública de {nombre}, sin abrir cada ficha"
        if sin_precio:
            detalle += f" · {sin_precio} sin precio"
        self.paso("extracción", EstadoPaso.AVISO if sin_precio else EstadoPaso.OK, detalle)
        avisos = [a for f in fichas for a in f.avisos if a != AVISO_AGOTADO]
        self.paso(
            *self._paso_verificacion(0, avisos)
            if avisos
            else ("verificación", EstadoPaso.OK, "Datos de la propia tienda (su API pública)")
        )
        return [
            OfertaCandidata(self.dominio, f, f.url, disponible=AVISO_AGOTADO not in f.avisos)
            for f in fichas
        ]

    # --- Fichas -----------------------------------------------------------------------------

    async def _fichas(
        self, perfil: Perfil, resultados: Sequence[ResultadoListado]
    ) -> list[OfertaCandidata]:
        self.fase = "extracción"
        candidatas: list[OfertaCandidata] = []
        fallidas = sin_precio = con_ia = no_anclados = 0
        avisos: list[str] = []
        bloqueo: Bloqueado | None = None
        vigilado = None
        if self.sesion is not None:
            vigilado = _ExtractorVigilado(ExtractorRespaldoIA(self.sesion))
        for resultado in resultados[: self.o._max_fichas]:
            try:
                url = await self.o._validador(resultado.url)
                pagina = await self.o._navegador.obtener(url, js=perfil.necesita_js)
            except Bloqueado as error:
                bloqueo = error
                break  # Se para: ni una petición más a una tienda que nos ha bloqueado.
            except (ProhibidoPorRobots, ErrorDescarga, URLNoPermitida):
                fallidas += 1
                continue
            if not 200 <= pagina.estado < 300 or not de_la_tienda(pagina, self.dominio):
                fallidas += 1
                continue
            # Si la IA ha fallado en una ficha anterior, la tienda sigue sin IA.
            extractor = vigilado if vigilado is not None and vigilado.error is None else None
            ficha = await extraer_ficha(pagina, self.campos, perfil=perfil, extractor_ia=extractor)
            con_ia += any(v.origen is OrigenDato.IA for v in ficha.campos.values())
            no_anclados += sum(not v.anclado for v in ficha.campos.values())
            sin_precio += ficha.precio is None
            avisos.extend(ficha.avisos)
            candidatas.append(OfertaCandidata(self.dominio, ficha, ficha.url))

        if vigilado is not None and vigilado.error is not None:
            self.sin_ia(vigilado.error, "Extractor de respaldo")
        if bloqueo is not None:
            self.bloqueado = True
            self.paso(
                "extracción",
                EstadoPaso.ERROR,
                f"{BLOQUEO} ({bloqueo.motivo}); {len(candidatas)} fichas leídas antes del bloqueo",
            )
        elif not candidatas:
            self.paso("extracción", EstadoPaso.ERROR, "No se ha podido abrir ninguna ficha")
        else:
            self.paso(*self._paso_extraccion(len(candidatas), fallidas, sin_precio, con_ia))
        if candidatas:
            self.paso(*self._paso_verificacion(no_anclados, avisos))
        return candidatas

    @staticmethod
    def _paso_extraccion(
        leidas: int, fallidas: int, sin_precio: int, con_ia: int
    ) -> tuple[str, EstadoPaso, str]:
        partes = [f"{leidas} fichas: JSON-LD y selectores del perfil"]
        estado = EstadoPaso.OK
        if con_ia:
            partes.append(f"🤖 extractor de respaldo en {con_ia}")
            estado = EstadoPaso.IA
        if fallidas:
            partes.append(f"{fallidas} no se han podido abrir")
            estado = EstadoPaso.AVISO
        if sin_precio:
            partes.append(f"{sin_precio} sin precio")
            estado = EstadoPaso.AVISO
        return "extracción", estado, " · ".join(partes)

    @staticmethod
    def _paso_verificacion(no_anclados: int, avisos: Sequence[str]) -> tuple[str, EstadoPaso, str]:
        if no_anclados:
            detalle = f"{no_anclados} valores descartados: no aparecen en la página"
            if avisos:
                detalle += f" · {avisos[0]}"
            return "verificación", EstadoPaso.AVISO, detalle
        if avisos:
            return "verificación", EstadoPaso.AVISO, f"{len(avisos)} avisos: {avisos[0]}"
        return "verificación", EstadoPaso.OK, "Todos los valores anclados en la página"

    # --- Almacén ----------------------------------------------------------------------------

    def _actualizar_tienda(self) -> None:
        robots, permite = self.robots if self.robots is not None else (None, None)
        try:
            self.o._almacen.actualizar_tienda(
                self.dominio,
                estado=self.estado,
                robots=robots,
                robots_permite=permite,
                ultima_visita=self.o._reloj() if self.visitada else None,
                bloqueos=self.tienda.bloqueos + 1 if self.bloqueado else None,
            )
        except ErrorAlmacen as error:
            self.paso("tienda", EstadoPaso.AVISO, f"No se pudo actualizar la tienda: {error}")


def _de_la_consulta(
    resultados: Sequence[ResultadoListado], consulta: str
) -> list[ResultadoListado]:
    """Los resultados de los datos estructurados que mencionan la consulta.

    El JSON embebido de una página sin resultados suele traer igualmente productos (los
    «destacados» o «más vendidos» del diseño de la tienda), con título, enlace y precio: si se
    usaran, cada búsqueda sin resultados abriría fichas de otras piezas y las daría como
    ofertas. Por eso cada resultado tiene que nombrar alguna palabra de la consulta (como en
    `parece_listado`) en su título o en la ruta de su enlace, que suele llevar el nombre o la
    referencia. Solo comparaciones de subcadenas: los títulos y rutas ya vienen recortados.
    """
    palabras = [p for p in normalizar(consulta).split() if len(p) >= MIN_PALABRA]
    if not palabras:
        return list(resultados)
    relacionados: list[ResultadoListado] = []
    for resultado in resultados:
        texto = normalizar(f"{resultado.titulo} {urlsplit(resultado.url).path}")
        if any(palabra in texto for palabra in palabras):
            relacionados.append(resultado)
    return relacionados


def _valida(candidata: OfertaCandidata) -> bool:
    """Oferta con un precio leído y anclado en la página (el único que cuenta como precio)."""
    return candidata.ficha.precio is not None
