"""Fuente de datos real: el almacén SQLite y el orquestador detrás de la interfaz `FuenteDatos`.

La web no cambia respecto a la demo. Lo caro se crea tarde: el navegador (y con él Chromium) en
la primera búsqueda, y se vuelve a crear si cambian el User-Agent, la espera entre peticiones o
el cliente HTTP (`curl` o `urllib`, ADR-022).

El servicio de IA se monta en cada búsqueda con los ajustes de ese momento (proveedor, modelo,
presupuesto y tope), así que un cambio en «Ajustes» vale desde la búsqueda siguiente.

Sin clave de la API (y con el proveedor `api`) no hay IA: los modos Auto e IA funcionan como el
clásico y la búsqueda lo dice. La clave nunca se muestra: ni en los avisos ni en los errores.

Con un proxy configurado (`PRECIA_PROXY` o `EVOMI_API_KEY`, ver `nucleo.config`), los
descargadores de HTTP y de Chromium van envueltos en `DescargadorConProxy`. Las credenciales de
Evomi se piden una sola vez, al crear el primer navegador, con el User-Agent de los ajustes. Si
no se consiguen, se avisa (sin secretos), se sigue sin proxy y «Ajustes» enseña el motivo.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from precia.almacen.sqlite import (
    AlertaNoEncontrada,
    Almacen,
    DatoNoValido,
    TiendaDuplicada,
    TiendaNoEncontrada,
)
from precia.emparejado.agrupador import componente_revision
from precia.evaluacion.informe import leer_resultados
from precia.ia.claude import ProveedorClaude
from precia.ia.ollama import ProveedorOllama
from precia.ia.servicio import LlamadasEnVuelo, ServicioIA
from precia.ia.tipos import ErrorIA, IANoDisponible, ProveedorIA
from precia.navegador.cache import CachePaginas
from precia.navegador.chromium import DescargadorChromium
from precia.navegador.con_proxy import DescargadorConProxy, UsoProxy
from precia.navegador.cortesia import LimitadorDominio
from precia.navegador.curl import TransporteCurl
from precia.navegador.evomi import credenciales_evomi
from precia.navegador.http import (
    FACTOR_TIEMPO_GRANDE,
    TIMEOUT_HTTP_S,
    DescargadorHTTP,
    Transporte,
    TransporteURLLib,
)
from precia.navegador.navegador import Navegador
from precia.navegador.proxy import (
    NOMBRE_PROPIO,
    ErrorProxy,
    ModoProxy,
    Proxy,
    URLProxyNoValida,
    proxy_desde_url,
)
from precia.navegador.robots import ServicioRobots
from precia.navegador.tipos import Descargador
from precia.navegador.url import Validador, validar_url
from precia.nucleo.config import Config
from precia.nucleo.dominios import dominio_de_tienda, normalizar_dominio
from precia.nucleo.fuente_datos import (
    AlertaNoExiste,
    ErrorFuente,
    RevisionNoValida,
    TiendaNoExiste,
    TiendaYaExiste,
    URLEntradaNoValida,
    entrada_normalizada,
)
from precia.nucleo.modelos import (
    TRANSPORTES_HTTP,
    Ajustes,
    Alerta,
    ConsultaBusqueda,
    EstadoProxy,
    GrupoProducto,
    InformeAlertas,
    ResultadoBusqueda,
    ResultadoEvaluacion,
    ResumenIA,
    Seguimiento,
    SerieHistorico,
    Tienda,
)
from precia.nucleo.orquestador import (
    CONSULTAS_CONFIGURACION,
    MAX_FICHAS,
    PLAZO_TIENDA_S,
    Orquestador,
    TurnosDePerfil,
    estado_de,
)
from precia.seguimiento.alertas import comprobar
from precia.seguimiento.avisos import Canal, canales_desde

registro = logging.getLogger(__name__)

TTL_CACHE_S = 6 * 3600.0
MARGEN_CANCELADO_S = TIMEOUT_HTTP_S * FACTOR_TIEMPO_GRANDE + TIMEOUT_HTTP_S
"""Tras cancelar una descarga, cuánto se da su dominio por ocupado (C1): la descarga sigue en su
hilo hasta su tiempo total (10 s, el triple en las grandes, A7; redirecciones incluidas) y, con
urllib, una última lectura que puede pasarse otros 10 s (`http.TIMEOUT_HTTP_S`). No puede
coincidir con otra (ADR-017)."""
ESPERA_IA_AL_CERRAR_S = 60.0
"""Lo que `cerrar` espera como mucho a las llamadas de IA en vuelo: el timeout de la API de
Claude (`ProveedorClaude`), así que en la práctica espera a que terminen."""
MAX_LLAMADAS_RESUMEN = 500
FICHERO_EVALUACION = "evaluacion.json"
"""Donde `python -m precia.evaluacion` deja sus resultados, junto a la base de datos."""
MAX_NOMBRE_TIENDA = 200
COMPONENTES = {
    "configurador": "Configurador de perfiles",
    "reparador": "Reparador de perfiles",
    "extractor": "Extractor de respaldo",
    "emparejado": "Emparejado y filtro semántico",
    "clasificador": "Clasificador de resultados",
}
"""Nombre que enseña la web para cada componente que registra el servicio de IA."""
RESULTADOS = {
    "ok": "Válida",
    "salida_no_valida": "Rechazada: JSON fuera del esquema",
    "rechazo": "Rechazada por el modelo",
    "no_disponible": "IA no disponible",
    "error": "Error del proveedor",
}

CrearDescargadores = Callable[[Ajustes], tuple[Descargador, Descargador | None]]
"""Descargador HTTP y, si lo hay, Chromium, con el User-Agent de los ajustes."""
CrearProveedor = Callable[[Ajustes], tuple[ProveedorIA | None, str]]
"""Proveedor de IA según los ajustes, o `None` y el motivo por el que no hay IA."""
CredencialesEvomi = Callable[..., Awaitable[Proxy]]
"""`credenciales_evomi(clave, *, user_agent, producto, pais)`: la API de Evomi (o un doble)."""


def _ahora() -> datetime:
    # Hora local con zona: las fechas de la web se leen en la hora del portátil, y el almacén
    # cuenta el gasto del mes con la misma convención que las llamadas (ver `almacen._mes`).
    return datetime.now().astimezone()


def proveedor_desde_ajustes(config: Config, ajustes: Ajustes) -> tuple[ProveedorIA | None, str]:
    """El proveedor que piden los ajustes, o `None` con el motivo (para el aviso de la web)."""
    if ajustes.proveedor == "ollama":
        return ProveedorOllama(modelo=ajustes.modelo_local), ""
    if not config.clave_api:
        return None, "falta la clave ANTHROPIC_API_KEY en el entorno o en .env"
    try:
        return ProveedorClaude(config.clave_api, modelo=ajustes.modelo_api), ""
    except IANoDisponible:
        # El mensaje de ProveedorClaude no lleva la clave, pero aquí no hace falta ni citarlo.
        return None, "la clave ANTHROPIC_API_KEY no tiene un formato válido"
    except ErrorIA as error:
        # Un modelo sin tarifa conocida: sin coste no se puede respetar el presupuesto.
        return None, str(error)


def _reservadas(consulta: ConsultaBusqueda, revisar: bool) -> tuple[str, ...]:
    """El componente que revisará los resultados de esta búsqueda, si los va a revisar."""
    componente = componente_revision(consulta, revisar)
    return () if componente is None else (componente,)


def _transporte(nombre: str, proxy: Proxy | None) -> Transporte:
    """El cliente HTTP de los ajustes (ADR-022). Uno desconocido usa el de por defecto."""
    if nombre not in TRANSPORTES_HTTP:
        # El almacén no valida el valor: una BD editada a mano no debe romper cada búsqueda.
        defecto = Ajustes().transporte_http
        registro.warning("Cliente HTTP desconocido en los ajustes: se usa %s.", defecto)
        nombre = defecto
    if nombre == "curl":
        return TransporteCurl(proxy=proxy)
    return TransporteURLLib(proxy=proxy)


class FuenteReal:
    """Implementa `FuenteDatos` con datos reales (memoria §6)."""

    es_demo = False

    def __init__(
        self,
        config: Config,
        *,
        crear_descargadores: CrearDescargadores | None = None,
        crear_proveedor: CrearProveedor | None = None,
        validador: Validador = validar_url,
        reloj: Callable[[], datetime] = _ahora,
        max_fichas: int = MAX_FICHAS,
        credenciales_evomi: CredencialesEvomi = credenciales_evomi,
        consultas_configuracion: Sequence[str] = CONSULTAS_CONFIGURACION,
        canales: Mapping[str, Canal] | None = None,
    ) -> None:
        """`validador` es el anti-SSRF de todo el flujo; solo se cambia en los tests.

        `consultas_configuracion` son las consultas de prueba con las que se configura una tienda
        sin perfil (A1); los tests las cambian para que coincidan con su tienda falsa.

        El proxy solo se usa con los descargadores reales: con `crear_descargadores` inyectado
        (tests, demo) no se interpreta ni se piden credenciales.

        `canales` son los canales de aviso de las alertas (H7); por defecto, los que estén
        configurados en el entorno o en `.env` (`config.avisos`).
        """
        self._config = config
        self._validador = validador
        self._reloj = reloj
        self._max_fichas = max_fichas
        self._consultas_configuracion = tuple(consultas_configuracion)
        self._crear_descargadores = crear_descargadores or self._descargadores_reales
        self._descargadores_propios = crear_descargadores is None
        self._credenciales_evomi = credenciales_evomi
        self._proxy: Proxy | None = None
        self._proxy_resuelto = False
        self._error_proxy = ""
        self._uso_proxy = UsoProxy()
        """Compartido por todos los navegadores: el gasto y las tiendas marcadas de la ejecución."""
        self._crear_proveedor: CrearProveedor = crear_proveedor or (
            lambda ajustes: proveedor_desde_ajustes(config, ajustes)
        )
        config.ruta_bd.parent.mkdir(parents=True, exist_ok=True)
        self._almacen = Almacen(config.ruta_bd, reloj=reloj)
        self._navegador: Navegador | None = None
        self._clave_navegador: tuple[str, float, Proxy | None, str] | None = None
        self._usos: dict[Navegador, int] = {}
        """Búsquedas en vuelo por navegador (el actual y los sustituidos que aún se usan)."""
        self._limitador: LimitadorDominio | None = None
        """El limitador del navegador actual: el siguiente hereda sus márgenes (`heredar`)."""
        self._ia_en_vuelo = LlamadasEnVuelo()
        """Llamadas de IA en vuelo de todas las búsquedas: `cerrar` espera las de las canceladas y
        su coste máximo cuenta en el presupuesto del mes hasta que se registran (B6)."""
        self._turnos_perfil = TurnosDePerfil()
        """Compartidos por las búsquedas simultáneas: una tienda no se configura ni se repara
        dos veces a la vez (B6)."""
        self._candado = asyncio.Lock()
        self._motivos_canal: Mapping[str, str] = {}
        if canales is None:
            canales, self._motivos_canal = canales_desde(config.avisos.get)
        self._canales = canales
        self.plazo_tienda_s = PLAZO_TIENDA_S
        """Lo que puede tardar cada tienda en una búsqueda (C1); los tests lo acortan."""

    @property
    def almacen(self) -> Almacen:
        return self._almacen

    # --- Búsqueda ---------------------------------------------------------------------------

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        ajustes = self.ajustes()
        navegador = await self._tomar_navegador(ajustes)
        try:
            return await self._buscar(consulta, ajustes, navegador, seguimiento)
        finally:
            # También si se cancela (C1): el navegador se suelta y se cierra si lo sustituyeron.
            await self._soltar_navegador(navegador)

    async def _buscar(
        self,
        consulta: ConsultaBusqueda,
        ajustes: Ajustes,
        navegador: Navegador,
        seguimiento: Seguimiento | None,
    ) -> ResultadoBusqueda:
        proveedor, motivo = self._crear_proveedor(ajustes)
        servicio = None
        if proveedor is not None:
            servicio = ServicioIA(
                proveedor,
                self._almacen,
                presupuesto_mes_usd=ajustes.presupuesto_mensual_usd,
                tope_llamadas=ajustes.tope_llamadas_busqueda,
                reloj=self._reloj,
                en_vuelo=self._ia_en_vuelo,
                # La revisión de los resultados tiene su llamada (nota 05), solo si la va a
                # hacer y con el componente que va a usar: si no, quitaría una a los demás.
                reservadas=_reservadas(consulta, ajustes.revisar_resultados),
            )
        orquestador = Orquestador(
            self._almacen,
            navegador,
            servicio,
            max_fichas=self._max_fichas,
            validador=self._validador,
            reloj=self._reloj,
            motivo_sin_ia=motivo,
            # Lo que extrae la IA de una ficha vale lo mismo que la ficha en caché (A3).
            ttl_cache_ia=TTL_CACHE_S,
            consultas_configuracion=self._consultas_configuracion,
            plazo_tienda_s=self.plazo_tienda_s,
            revisar_resultados=ajustes.revisar_resultados,
            turnos_perfil=self._turnos_perfil,
            google_activado=ajustes.google_activado,
        )
        return await orquestador.buscar(consulta, seguimiento=seguimiento)

    async def _tomar_navegador(self, ajustes: Ajustes) -> Navegador:
        """El navegador para una búsqueda; hay que devolverlo con `_soltar_navegador`.

        Si los ajustes han cambiado, el navegador viejo no se cierra mientras lo use otra
        búsqueda en vuelo: cerrarlo le cortaría Chromium a media descarga y, peor, su
        descargador volvería a arrancar otro Chromium que ya no cerraría nadie.
        """
        async with self._candado:
            if self._descargadores_propios and not self._proxy_resuelto:
                await self._resolver_proxy(ajustes)
            clave = (
                ajustes.user_agent,
                ajustes.retardo_dominio_s,
                self._proxy,
                ajustes.transporte_http,
            )
            if self._navegador is not None and self._clave_navegador != clave:
                viejo, self._navegador = self._navegador, None
                if not self._usos.get(viejo):
                    await viejo.cerrar()
            if self._navegador is None:
                http, chromium = self._crear_descargadores(ajustes)
                limitador = LimitadorDominio(
                    ajustes.retardo_dominio_s, margen_cancelado_s=MARGEN_CANCELADO_S
                )
                if self._limitador is not None:
                    limitador.heredar(self._limitador)
                self._limitador = limitador
                self._navegador = Navegador(
                    http=http,
                    chromium=chromium,
                    robots=ServicioRobots(http, ajustes.user_agent),
                    limitador=limitador,
                    cache=CachePaginas(self._config.ruta_cache, TTL_CACHE_S),
                    validador=self._validador,
                )
                self._clave_navegador = clave
            navegador = self._navegador
            self._usos[navegador] = self._usos.get(navegador, 0) + 1
            return navegador

    async def _soltar_navegador(self, navegador: Navegador) -> None:
        async with self._candado:
            quedan = self._usos.get(navegador, 0) - 1
            if quedan > 0:
                self._usos[navegador] = quedan
                return
            self._usos.pop(navegador, None)
            if navegador is not self._navegador:
                # Lo habían sustituido unos ajustes nuevos: era la última búsqueda que lo usaba.
                await navegador.cerrar()

    async def _resolver_proxy(self, ajustes: Ajustes) -> None:
        """Interpreta `PRECIA_PROXY` o pide las credenciales a Evomi. Solo se intenta una vez.

        Se da por resuelto al acabar el intento, falle o no. Si se cancela a medias (la primera
        búsqueda se cancela), queda pendiente y se intenta en la siguiente. Un fallo nunca rompe
        la búsqueda: se avisa y se sigue con la conexión directa.
        """
        config = self._config
        if config.hay_proxy and config.proxy_modo is not ModoProxy.NUNCA:
            try:
                self._proxy = await self._obtener_proxy(config, ajustes)
            except ErrorProxy as error:
                # Mensajes propios, sin secretos: se pueden registrar y enseñar.
                self._error_proxy = str(error)
                registro.warning("No se usa el proxy: %s Se sigue con la conexión directa.", error)
            except Exception as error:
                # Un error ajeno (`UnicodeEncodeError` de una cabecera...) puede citar la clave:
                # solo se apunta su tipo.
                self._error_proxy = "No se pudo obtener el proxy (error inesperado)."
                registro.warning(
                    "No se usa el proxy: error inesperado (%s). Se sigue con la conexión directa.",
                    type(error).__name__,
                )
        self._proxy_resuelto = True

    async def _obtener_proxy(self, config: Config, ajustes: Ajustes) -> Proxy | None:
        if config.proxy_url:
            try:
                return proxy_desde_url(config.proxy_url)
            except URLProxyNoValida as error:
                # Sus mensajes son propios y nunca citan la URL.
                raise ErrorProxy(f"PRECIA_PROXY no es válida: {error}") from None
            except ValueError:
                raise ErrorProxy("PRECIA_PROXY no es válida.") from None
        if config.clave_evomi:
            return await self._credenciales_evomi(
                config.clave_evomi,
                user_agent=ajustes.user_agent,
                producto=config.evomi_producto,
                pais=config.proxy_pais,
            )
        return None

    def _descargadores_reales(self, ajustes: Ajustes) -> tuple[Descargador, Descargador | None]:
        # Chromium no arranca hasta que una tienda lo necesita (`necesita_js` o el rescate tras un
        # bloqueo por HTTP, ADR-023); el del proxy, hasta que una tienda tiene que ir por él. No
        # recibe el UA de Ajustes: usa el de Chrome sacado de su propio binario.
        ua = ajustes.user_agent
        http = DescargadorHTTP(
            ua, validador=self._validador, transporte=_transporte(ajustes.transporte_http, None)
        )
        chromium = DescargadorChromium(validador=self._validador)
        if self._proxy is None:
            return http, chromium

        def envolver(directo: Descargador, por_proxy: Descargador) -> DescargadorConProxy:
            return DescargadorConProxy(
                directo,
                por_proxy,
                modo=self._config.proxy_modo,
                uso=self._uso_proxy,
                # El reintento por el proxy espera lo mismo que dos peticiones seguidas a la tienda.
                espera_reintento_s=ajustes.retardo_dominio_s,
            )

        transporte = _transporte(ajustes.transporte_http, self._proxy)
        return (
            envolver(http, DescargadorHTTP(ua, validador=self._validador, transporte=transporte)),
            envolver(chromium, DescargadorChromium(validador=self._validador, proxy=self._proxy)),
        )

    def estado_proxy(self) -> EstadoProxy:
        config = self._config
        if not config.hay_proxy:
            return EstadoProxy(modo=config.proxy_modo.value)
        evomi = not config.proxy_url
        if self._proxy is not None:
            nombre = self._proxy.nombre
        else:
            nombre = "Evomi" if evomi else NOMBRE_PROPIO
        uso = self._uso_proxy
        return EstadoProxy(
            configurado=True,
            activo=self._proxy is not None,
            nombre=nombre,
            pais=config.proxy_pais if evomi else "",
            modo=config.proxy_modo.value,
            peticiones=uso.peticiones,
            bytes=uso.bytes,
            dominios=tuple(sorted(uso.dominios)),
            error=self._error_proxy,
            pendiente=(
                self._descargadores_propios
                and not self._proxy_resuelto
                and config.proxy_modo is not ModoProxy.NUNCA
            ),
        )

    async def cerrar(self) -> None:
        async with self._candado:
            # Al apagar se cierran todos, también los que aún usaba una búsqueda sin terminar.
            pendientes = dict.fromkeys([*self._usos, self._navegador])
            self._usos.clear()
            self._navegador = None
            for navegador in pendientes:
                if navegador is not None:
                    await navegador.cerrar()
        # Las llamadas de IA de las búsquedas canceladas se registran al terminar: con la base
        # cerrada se perderían y el gasto del mes se quedaría corto.
        await self._ia_en_vuelo.esperar(ESPERA_IA_AL_CERRAR_S)
        self._almacen.cerrar()

    # --- Tiendas ----------------------------------------------------------------------------

    def tiendas(self) -> list[Tienda]:
        return self._almacen.tiendas()

    def tienda(self, dominio: str) -> Tienda | None:
        normalizado = normalizar_dominio(dominio)
        return self._almacen.tienda(normalizado) if normalizado else None

    async def alta_tienda(self, dominio: str, nombre: str, *, entrada: str | None = None) -> Tienda:
        normalizado = await dominio_de_tienda(dominio, validador=self._validador)
        nombre = nombre.strip() or normalizado
        if len(nombre) > MAX_NOMBRE_TIENDA or not nombre.isprintable():
            raise ErrorFuente(
                f"El nombre de la tienda tiene que tener de 1 a {MAX_NOMBRE_TIENDA} caracteres."
            )
        url_entrada = entrada_normalizada(normalizado, entrada)
        try:
            return self._almacen.alta_tienda(normalizado, nombre, entrada=url_entrada)
        except TiendaDuplicada as error:
            raise TiendaYaExiste(f"La tienda {normalizado} ya está dada de alta.") from error
        except DatoNoValido as error:
            raise ErrorFuente("Los datos de la tienda no son válidos.") from error

    def cambiar_entrada(self, dominio: str, entrada: str | None) -> Tienda:
        normalizado = normalizar_dominio(dominio)
        if normalizado is None or self._almacen.tienda(normalizado) is None:
            raise TiendaNoExiste(f"No hay ninguna tienda {dominio}.")
        url_entrada = entrada_normalizada(normalizado, entrada)
        try:
            return self._almacen.cambiar_entrada(normalizado, url_entrada)
        except TiendaNoEncontrada as error:
            raise TiendaNoExiste(f"No hay ninguna tienda {normalizado}.") from error
        except DatoNoValido as error:
            raise URLEntradaNoValida("La URL de entrada no es válida.") from error

    def reintentar_configuracion(self, dominio: str) -> Tienda:
        normalizado = normalizar_dominio(dominio)
        if normalizado is None or self._almacen.tienda(normalizado) is None:
            raise TiendaNoExiste(f"No hay ninguna tienda {dominio}.")
        if self._navegador is not None:
            # A8: también olvida el bloqueo recordado tras el proxy y Chromium.
            self._navegador.olvidar_bloqueo(normalizado)
        try:
            return self._almacen.levantar_espera(normalizado)
        except TiendaNoEncontrada as error:
            raise TiendaNoExiste(f"No hay ninguna tienda {normalizado}.") from error

    def revisar_perfil(self, dominio: str, version: int) -> Tienda:
        normalizado = normalizar_dominio(dominio)
        tienda = self._almacen.tienda(normalizado) if normalizado else None
        if tienda is None or normalizado is None:
            raise TiendaNoExiste(f"No hay ninguna tienda {dominio}.")
        vigente = self._almacen.perfil_vigente(normalizado)
        if vigente is None:
            raise RevisionNoValida("La tienda no tiene perfil que revisar.")
        if version != vigente.version:
            raise RevisionNoValida(f"Solo se puede fijar la versión actual (v{vigente.version}).")
        if vigente.revisado:
            raise RevisionNoValida(f"La versión v{vigente.version} ya está revisada.")
        self._almacen.marcar_revisado(normalizado, version)
        return self._almacen.actualizar_tienda(
            normalizado, estado=estado_de(replace(vigente, revisado=True))
        )

    # --- Productos y alertas ----------------------------------------------------------------

    def producto(self, id_producto: str) -> GrupoProducto | None:
        return self._almacen.producto(id_producto)

    def historico(self, id_producto: str) -> list[SerieHistorico]:
        return self._almacen.historico(id_producto)

    def alertas(self) -> list[Alerta]:
        return self._almacen.alertas()

    def crear_alerta(
        self, id_producto: str, umbral: Decimal, canal: str, *, solo_exactas: bool = True
    ) -> Alerta:
        return self._almacen.crear_alerta(id_producto, umbral, canal, solo_exactas=solo_exactas)

    def cambiar_alerta(self, id_alerta: int, *, activa: bool) -> None:
        try:
            self._almacen.cambiar_alerta(id_alerta, activa=activa)
        except AlertaNoEncontrada as error:
            raise AlertaNoExiste("La alerta ya no existe.") from error

    def borrar_alerta(self, id_alerta: int) -> None:
        try:
            self._almacen.borrar_alerta(id_alerta)
        except AlertaNoEncontrada as error:
            raise AlertaNoExiste("La alerta ya no existe.") from error

    async def comprobar_alertas(self) -> InformeAlertas:
        """Repite, en modo clásico, la búsqueda de cada producto con alertas activas y avisa.

        Las búsquedas pasan por `buscar`: mismo navegador, misma cortesía por dominio y los
        precios quedan en el histórico. Sin IA: no gasta presupuesto.
        """

        def anotar(
            id_alerta: int,
            *,
            comprobada: datetime,
            avisada: bool | None = None,
            error: str = "",
        ) -> None:
            # Si la borraron mientras se comprobaba, no hay nada que apuntar.
            with contextlib.suppress(AlertaNoEncontrada):
                self._almacen.anotar_alerta(
                    id_alerta, comprobada=comprobada, avisada=avisada, error=error
                )

        return await comprobar(
            self._almacen.alertas(),
            producto=self._almacen.producto,
            buscar=self.buscar,
            canales=self._canales,
            motivos=self._motivos_canal,
            anotar=anotar,
            reloj=self._reloj,
        )

    def canales_aviso(self) -> dict[str, str]:
        estado: dict[str, str] = {}
        for canal in ("email", "telegram"):
            falta = self._motivos_canal.get(canal, "Sin configurar.")
            estado[canal] = "" if canal in self._canales else falta
        return estado

    # --- IA, ajustes y evaluación -----------------------------------------------------------

    def resumen_ia(self) -> ResumenIA:
        ahora = self._reloj()
        llamadas = tuple(
            replace(
                c,
                componente=COMPONENTES.get(c.componente, c.componente),
                resultado=RESULTADOS.get(c.resultado, c.resultado),
            )
            for c in self._almacen.llamadas(MAX_LLAMADAS_RESUMEN)
            if (c.fecha.year, c.fecha.month) == (ahora.year, ahora.month)
        )
        por_componente: dict[str, int] = {}
        for c in llamadas:
            por_componente[c.componente] = por_componente.get(c.componente, 0) + 1
        return ResumenIA(
            llamadas=llamadas,
            gasto_mes_usd=self._almacen.gasto_mes_usd(),
            presupuesto_mes_usd=self.ajustes().presupuesto_mensual_usd,
            por_componente=por_componente,
        )

    def ajustes(self) -> Ajustes:
        return self._almacen.leer_ajustes()

    def guardar_ajustes(self, ajustes: Ajustes) -> None:
        self._almacen.guardar_ajustes(ajustes)

    def evaluacion(self) -> list[ResultadoEvaluacion]:
        """Lo último que midió el banco (`python -m precia.evaluacion`), que lo guarda junto a
        la base de datos. La tasa de bloqueo no la mide el banco: es la de las tiendas dadas de
        alta que han devuelto algún bloqueo en las búsquedas reales."""
        tiendas = self._almacen.tiendas()
        bloqueadas = sum(1 for t in tiendas if t.bloqueos > 0)
        tasa = bloqueadas / len(tiendas) if tiendas else 0.0
        ruta = self._config.ruta_bd.parent / FICHERO_EVALUACION
        return leer_resultados(ruta, tasa_bloqueo=tasa)
