"""Aplicación web de PrecIA (FastAPI + Jinja2 + HTMX).

La web solo habla con una `FuenteDatos`: la `FuenteDemo` (datos de ejemplo, en memoria) o la
`FuenteReal` (orquestador + SQLite). `crear_app` recibe la fuente ya hecha y la cierra al apagar;
`aplicacion_desde_config` elige cuál según la configuración (`--demo` o `PRECIA_DEMO`).

Una búsqueda va en segundo plano (C1, `nucleo.tareas`): `GET /buscar` la lanza y lleva a
`/busquedas/{id}`, cuya página sondea con htmx el estado de cada tienda. Atrás o F5 piden esa
URL y ven lo que hay en memoria: no se vuelve a buscar.
"""

import mimetypes
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from precia import __version__
from precia.emparejado.referencias import referencias_en
from precia.nucleo import formato
from precia.nucleo.config import Config
from precia.nucleo.fuente_datos import AlertaNoExiste, ErrorFuente, FuenteDatos
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import (
    CAMPOS,
    TRANSPORTES_HTTP,
    Ajustes,
    EstadoTienda,
    InformeAlertas,
    Modo,
)
from precia.nucleo.tareas import DemasiadasBusquedas, GestorBusquedas, TareaBusqueda
from precia.seguimiento.graficas import grafica_historico
from precia.seguimiento.planificador import Planificador
from precia.web.formularios import (
    ErrorFormulario,
    Parametros,
    consulta_desde_parametros,
    datos_de_alta,
    leer_entrada,
    leer_formulario,
    leer_importe,
    leer_version,
    parametros_de_consulta,
    primero,
)
from precia.web.seguridad import MiddlewareSeguridad

RAIZ = Path(__file__).parent

# En Windows `mimetypes` lee el registro, que puede asociar `.js` a `text/plain`. Con la cabecera
# `nosniff` el navegador se negaría entonces a ejecutar HTMX, así que se fijan los tipos a mano.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
HOSTS_LOCALES = ("127.0.0.1", "localhost")
CANALES = ("email", "telegram")
PROVEEDORES = ("api", "ollama")
MAX_ENTRADA_ESCRITA = 600
"""Lo que se devuelve al formulario de lo que escribió el usuario, para que lo corrija."""
ESPERA_INICIAL_S = 0.5
"""Lo que espera `GET /buscar` a que termine la búsqueda antes de enseñar el progreso (C1): la
demo, la caché o una búsqueda repetida salen ya terminadas, sin pasar por el sondeo."""
_ID_BUSQUEDA = re.compile(r"[0-9a-f]{16}")
_ID_ALERTA = re.compile(r"[1-9][0-9]{0,17}")
"""Un entero positivo que cabe en el INTEGER de SQLite; lo demás es un 404."""
ACCIONES_ALERTA = ("pausar", "reanudar", "borrar")
ETIQUETAS_ESTADO = {
    EstadoTienda.EN_COLA: "En cola",
    EstadoTienda.DESCARGANDO: "Descargando",
    EstadoTienda.CONFIGURANDO: "Configurando",
    EstadoTienda.HECHA: "Hecha",
    EstadoTienda.BLOQUEADA: "Bloqueada",
    EstadoTienda.EN_ESPERA: "En espera",
    EstadoTienda.PLAZO: "Plazo agotado",
    EstadoTienda.CANCELADA: "Cancelada",
}
"""Etiqueta del estado de cada tienda en la página de una búsqueda (C1)."""

NAVEGACION = (
    ("/", "Buscar"),
    ("/tiendas", "Tiendas"),
    ("/alertas", "Alertas"),
    ("/ia", "Registro de IA"),
    ("/evaluacion", "Evaluación"),
    ("/ajustes", "Ajustes"),
)


def _entorno() -> Environment:
    # Autoescape siempre activo: títulos y campos vienen de webs ajenas (dato no confiable).
    entorno = Environment(loader=FileSystemLoader(RAIZ / "plantillas"), autoescape=True)
    entorno.filters["euros"] = formato.euros
    entorno.filters["dolares"] = formato.dolares
    entorno.filters["miles"] = formato.miles
    entorno.filters["decimal"] = formato.decimal
    entorno.filters["fecha_corta"] = formato.fecha_corta
    entorno.filters["fecha_hora"] = formato.fecha_hora
    entorno.filters["url_segura"] = url_segura
    entorno.filters["foto_segura"] = foto_segura
    # «Sin resultados» no propone escribir una referencia OEM si ya se ha escrito una (B5).
    entorno.filters["con_referencia_oem"] = lambda texto: bool(referencias_en(texto))
    entorno.globals["CAMPOS"] = CAMPOS
    entorno.globals["NAVEGACION"] = NAVEGACION
    entorno.globals["MODOS"] = Modo
    entorno.globals["VERSION"] = __version__
    entorno.globals["ETIQUETAS_ESTADO"] = ETIQUETAS_ESTADO
    entorno.globals["parametros_de_consulta"] = parametros_de_consulta
    return entorno


def url_segura(url: str) -> str:
    """Las URL vienen de las tiendas: solo se enlazan las http(s); `javascript:` y demás, no."""
    return url if urlsplit(url).scheme in ("http", "https") else "#"


MAX_FOTO = 2000
_NO_EN_FOTO = frozenset("\"'<>`\\")


def foto_segura(url: object) -> str | None:
    """Última barrera antes del `<img src>`: la URL si es `https` con host y sin nada raro.

    La foto ya llega validada desde la extracción y el almacén; aquí se repite lo mínimo por si
    una fuente de datos (o una base de datos tocada a mano) trae otra cosa. `None`: sin foto.
    """
    if not isinstance(url, str) or len(url) > MAX_FOTO or not url.startswith("https://"):
        return None
    if any(c.isspace() or not c.isprintable() or c in _NO_EN_FOTO for c in url):
        return None
    try:
        return url if urlsplit(url).hostname else None
    except ValueError:
        return None


def _ahora() -> datetime:
    """Hora local con zona, como las fechas que guarda el almacén."""
    return datetime.now().astimezone()


def _es_htmx(request: Request) -> bool:
    # Al pulsar «Atrás» (historyCacheSize=0) HTMX pide la página con HX-Request y la pone entera
    # en <body>: en ese caso hay que devolver la página completa, no el fragmento.
    return (
        request.headers.get("hx-request") == "true"
        and request.headers.get("hx-history-restore-request") != "true"
    )


def _parametros(request: Request) -> dict[str, list[str]]:
    parametros: dict[str, list[str]] = {}
    for clave, valor in request.query_params.multi_items():
        parametros.setdefault(clave, []).append(valor)
    return parametros


def crear_app(
    fuente: FuenteDatos | None = None,
    *,
    hosts: tuple[str, ...] = HOSTS_LOCALES,
    espera_inicial_s: float = ESPERA_INICIAL_S,
    gestor: GestorBusquedas | None = None,
    reloj: Callable[[], datetime] = _ahora,
    cada_alertas_s: float = 0.0,
) -> FastAPI:
    """`gestor` lleva las búsquedas en segundo plano (C1); por defecto, uno sobre `fuente`.

    `cada_alertas_s` es cada cuánto comprueba las alertas el planificador (H7); con 0 solo se
    comprueban con el botón «Comprobar ahora».

    `reloj` da el `ahora` de las plantillas: con él se ve si la espera de una configuración
    fallida (A1) sigue en pie o ya ha vencido.
    """
    datos: FuenteDatos = fuente if fuente is not None else FuenteDemo()
    busquedas = gestor if gestor is not None else GestorBusquedas(datos)
    planificador: Planificador[InformeAlertas] = Planificador(
        datos.comprobar_alertas, cada_alertas_s
    )
    plantillas = Jinja2Templates(env=_entorno())

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        # Al apagar se cancelan las búsquedas en marcha y luego se cierran el navegador
        # (Chromium) y la base de datos de la fuente real: en ese orden, para que ninguna
        # búsqueda siga usando un navegador cerrado. Antes que nada se para el planificador de
        # las alertas (H7), que también busca.
        planificador.arrancar()
        try:
            yield
        finally:
            try:
                await planificador.cerrar()
            finally:
                try:
                    await busquedas.cerrar()
                finally:
                    await datos.cerrar()

    app = FastAPI(
        title="PrecIA",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=ciclo_de_vida,
    )
    app.state.fuente = datos
    app.state.gestor = busquedas
    app.state.planificador = planificador
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))
    app.add_middleware(MiddlewareSeguridad)
    app.mount("/estaticos", StaticFiles(directory=RAIZ / "estaticos"), name="estaticos")

    def pagina(
        request: Request, plantilla: str, contexto: dict[str, Any], estado: int = 200
    ) -> HTMLResponse:
        base = {
            "es_demo": datos.es_demo,
            "ruta": request.url.path,
            "es_htmx": _es_htmx(request),
            "google_activado": datos.ajustes().google_activado,
            "ahora": reloj(),
        }
        return plantillas.TemplateResponse(
            request, plantilla, {**base, **contexto}, status_code=estado
        )

    def url_busqueda(tarea: TareaBusqueda) -> str:
        """La URL de una búsqueda: con su consulta, para el formulario si la tarea ya no está."""
        return f"/busquedas/{tarea.id}?{parametros_de_consulta(tarea.consulta)}"

    def tarea_de(id_busqueda: str) -> TareaBusqueda | None:
        return busquedas.tarea(id_busqueda) if _ID_BUSQUEDA.fullmatch(id_busqueda) else None

    def fragmento_busqueda(
        request: Request, tarea: TareaBusqueda | None, estado: int = 200
    ) -> HTMLResponse:
        """La búsqueda en curso o terminada (`_busqueda.html`); sin tarea, el aviso de que ya no
        está, sin sondeo."""
        contexto = {
            "tarea": tarea,
            "consulta": tarea.consulta if tarea else None,
            # «Sin resultados» dice si no hay ninguna tienda dada de alta.
            "tiendas": datos.tiendas(),
        }
        return pagina(request, "_busqueda.html", contexto, estado)

    def contexto_producto(id_producto: str) -> dict[str, Any] | None:
        grupo = datos.producto(id_producto)
        if grupo is None:
            return None
        series = datos.historico(id_producto)
        return {
            "grupo": grupo,
            "series": series,
            # La gráfica escapa por dentro todos los datos variables.
            "grafica": Markup(grafica_historico(series)),
            "alertas": [a for a in datos.alertas() if a.producto_id == id_producto],
        }

    @app.exception_handler(HTTPException)
    async def error_http(request: Request, error: HTTPException) -> Response:
        mensaje = "Página no encontrada" if error.status_code == 404 else str(error.detail)
        return pagina(request, "error.html", {"mensaje": mensaje}, error.status_code)

    @app.get("/", response_class=HTMLResponse)
    async def inicio(request: Request) -> HTMLResponse:
        return pagina(request, "inicio.html", {"tiendas": datos.tiendas(), "consulta": None})

    @app.get("/buscar", response_class=HTMLResponse)
    async def buscar(request: Request) -> Response:
        """Lanza la búsqueda en segundo plano y lleva a su página, `/busquedas/{id}` (C1).

        Sin htmx, un 303. Con htmx, el fragmento de la búsqueda y `HX-Push-Url`: `/buscar` no
        queda en el historial, así que Atrás o F5 no la relanzan.
        """
        parametros = _parametros(request)
        contexto: dict[str, Any] = {"tiendas": datos.tiendas(), "consulta": None}
        estado = 200
        try:
            consulta = consulta_desde_parametros(parametros)
        except ErrorFormulario as error:
            contexto["error"] = str(error)
            estado = 422
        else:
            # Solo tiendas conocidas: desde H2 cada tienda es un dominio que se visita, y
            # aceptar cualquiera abriría la puerta a SSRF (169.254.169.254, localhost...).
            conocidas = {t.dominio for t in contexto["tiendas"]}
            elegidas = tuple(t for t in consulta.tiendas if t in conocidas)
            if consulta.tiendas and not elegidas:
                # Una lista vacía significa «todas»: pedir solo tiendas que no existen no puede
                # acabar visitando todas las tiendas (y gastando IA en cada una).
                contexto["error"] = "Ninguna de las tiendas elegidas está dada de alta."
                estado = 422
            else:
                consulta = replace(consulta, tiendas=elegidas)
                contexto["consulta"] = consulta
                todas = elegidas or tuple(t.dominio for t in contexto["tiendas"])
                try:
                    tarea = busquedas.lanzar(consulta, todas)
                except DemasiadasBusquedas as error:
                    contexto["error"] = str(error)
                    estado = 429
                else:
                    # Si acaba enseguida (demo, caché), se pinta ya terminada, sin sondeo.
                    await busquedas.esperar(tarea, espera_inicial_s)
                    respuesta: Response
                    if _es_htmx(request):
                        respuesta = fragmento_busqueda(request, tarea)
                        respuesta.headers["HX-Push-Url"] = url_busqueda(tarea)
                    else:
                        respuesta = RedirectResponse(url_busqueda(tarea), status_code=303)
                    respuesta.headers["Vary"] = "HX-Request"
                    return respuesta
        plantilla = "_resultados.html" if _es_htmx(request) else "inicio.html"
        respuesta = pagina(request, plantilla, contexto, estado)
        # La misma URL devuelve página o fragmento según HX-Request: que ninguna caché los mezcle.
        respuesta.headers["Vary"] = "HX-Request"
        return respuesta

    @app.get("/busquedas/{id_busqueda}", response_class=HTMLResponse)
    async def busqueda(request: Request, id_busqueda: str) -> HTMLResponse:
        """La página de una búsqueda, en curso o terminada. Siempre entera: es la URL a la que
        vuelven Atrás y F5 (también la restauración del historial de htmx)."""
        tarea = tarea_de(id_busqueda)
        contexto: dict[str, Any] = {"tiendas": datos.tiendas(), "tarea": tarea}
        if tarea is not None:
            contexto["consulta"] = tarea.consulta
            return pagina(request, "inicio.html", contexto)
        # Caducada o de antes de reiniciar: el formulario, relleno con la consulta de la URL.
        try:
            contexto["consulta"] = consulta_desde_parametros(_parametros(request))
        except ErrorFormulario:
            contexto["consulta"] = None
        contexto["perdida"] = True
        return pagina(request, "inicio.html", contexto, 404)

    @app.get("/busquedas/{id_busqueda}/estado", response_class=HTMLResponse)
    async def estado_busqueda(request: Request, id_busqueda: str) -> Response:
        """El fragmento que sondea la página cada segundo; 204 si no ha cambiado (`v`)."""
        tarea = tarea_de(id_busqueda)
        if tarea is None:
            return fragmento_busqueda(request, None, 404)
        if tarea.activa and request.query_params.get("v") == str(tarea.version):
            # Sin repintar: así no se cierran los desplegables ni salta el foco.
            return Response(status_code=204)
        return fragmento_busqueda(request, tarea)

    @app.post("/busquedas/{id_busqueda}/cancelar", response_class=HTMLResponse)
    async def cancelar_busqueda(request: Request, id_busqueda: str) -> Response:
        tarea = tarea_de(id_busqueda)
        if tarea is None:
            if _es_htmx(request):
                return fragmento_busqueda(request, None, 404)
            raise HTTPException(404)
        await busquedas.cancelar(tarea.id)
        if not _es_htmx(request):
            return RedirectResponse(url_busqueda(tarea), status_code=303)
        return fragmento_busqueda(request, tarea)

    @app.get("/productos/{id_producto}", response_class=HTMLResponse)
    async def producto(request: Request, id_producto: str) -> HTMLResponse:
        contexto = contexto_producto(id_producto)
        if contexto is None:
            raise HTTPException(404)
        return pagina(request, "producto.html", contexto)

    @app.get("/tiendas", response_class=HTMLResponse)
    async def tiendas(request: Request) -> HTMLResponse:
        return pagina(request, "tiendas.html", {"tiendas": datos.tiendas()})

    @app.post("/tiendas", response_class=HTMLResponse)
    async def alta_tienda(request: Request) -> Response:
        formulario: Parametros = {}
        try:
            formulario = leer_formulario(await request.body())
            dominio, nombre = datos_de_alta(formulario)
            entrada = leer_entrada(formulario)
            # La fuente valida el dominio (sintaxis y anti-SSRF) y la URL de entrada (mismo
            # host, marcadores...) antes de guardarlos.
            nueva = await datos.alta_tienda(dominio, nombre, entrada=entrada)
        except (ErrorFormulario, ErrorFuente) as error:
            contexto = {
                "tiendas": datos.tiendas(),
                "error": str(error),
                "dominio": primero(formulario, "dominio")[:300],
                "nombre": primero(formulario, "nombre")[:300],
                "entrada": primero(formulario, "entrada")[:MAX_ENTRADA_ESCRITA],
            }
            return pagina(request, "tiendas.html", contexto, 422)
        return RedirectResponse(f"/tiendas/{quote(nueva.dominio)}?alta=1", status_code=303)

    @app.get("/tiendas/{dominio}", response_class=HTMLResponse)
    async def tienda(request: Request, dominio: str) -> HTMLResponse:
        encontrada = datos.tienda(dominio)
        if encontrada is None:
            raise HTTPException(404)
        contexto = {
            "tienda": encontrada,
            "alta": request.query_params.get("alta") == "1",
            "revisado": request.query_params.get("revisado") == "1",
            "entrada_cambiada": request.query_params.get("entrada") == "1",
            "reintentar": request.query_params.get("reintentar") == "1",
        }
        return pagina(request, "tienda.html", contexto)

    @app.post("/tiendas/{dominio}/entrada", response_class=HTMLResponse)
    async def cambiar_entrada(request: Request, dominio: str) -> Response:
        """Nota 16 §A: pone, cambia o (vacía) quita la URL de entrada de la tienda."""
        encontrada = datos.tienda(dominio)
        if encontrada is None:
            raise HTTPException(404)
        formulario: Parametros = {}
        try:
            formulario = leer_formulario(await request.body())
            cambiada = datos.cambiar_entrada(encontrada.dominio, leer_entrada(formulario))
        except (ErrorFormulario, ErrorFuente) as error:
            contexto = {
                "tienda": datos.tienda(encontrada.dominio) or encontrada,
                "error_entrada": str(error),
                "entrada": primero(formulario, "entrada")[:MAX_ENTRADA_ESCRITA],
            }
            return pagina(request, "tienda.html", contexto, 422)
        return RedirectResponse(f"/tiendas/{quote(cambiada.dominio)}?entrada=1", status_code=303)

    @app.post("/tiendas/{dominio}/revisar", response_class=HTMLResponse)
    async def revisar_perfil(request: Request, dominio: str) -> Response:
        encontrada = datos.tienda(dominio)
        if encontrada is None:
            raise HTTPException(404)
        try:
            formulario = leer_formulario(await request.body())
            version = leer_version(primero(formulario, "version"))
            revisada = datos.revisar_perfil(encontrada.dominio, version)
        except (ErrorFormulario, ErrorFuente) as error:
            actual = datos.tienda(encontrada.dominio) or encontrada
            return pagina(request, "tienda.html", {"tienda": actual, "error": str(error)}, 422)
        return RedirectResponse(f"/tiendas/{quote(revisada.dominio)}?revisado=1", status_code=303)

    @app.post("/tiendas/{dominio}/reintentar", response_class=HTMLResponse)
    async def reintentar_configuracion(request: Request, dominio: str) -> Response:
        """A1: quita la espera tras una configuración fallida."""
        encontrada = datos.tienda(dominio)
        if encontrada is None:
            raise HTTPException(404)
        try:
            leer_formulario(await request.body())
            tienda = datos.reintentar_configuracion(encontrada.dominio)
        except (ErrorFormulario, ErrorFuente) as error:
            actual = datos.tienda(encontrada.dominio) or encontrada
            return pagina(request, "tienda.html", {"tienda": actual, "error": str(error)}, 422)
        return RedirectResponse(f"/tiendas/{quote(tienda.dominio)}?reintentar=1", status_code=303)

    @app.get("/alertas", response_class=HTMLResponse)
    async def alertas(request: Request) -> HTMLResponse:
        return pagina(
            request,
            "alertas.html",
            {
                "alertas": datos.alertas(),
                "con_acciones": True,
                "canales": datos.canales_aviso(),
                "comprobando": planificador.en_curso,
                "informe": planificador.ultimo,
                "cada_h": planificador.cada_s / 3600,
            },
        )

    @app.post("/alertas/comprobar", response_class=HTMLResponse)
    async def comprobar_alertas(request: Request) -> Response:
        """Lanza la comprobación en segundo plano (puede tardar minutos) y vuelve a la lista.
        Si ya hay una en marcha no lanza otra."""
        leer_formulario(await request.body())
        planificador.lanzar()
        return RedirectResponse("/alertas", status_code=303)

    @app.post("/alertas/{id_alerta}/{accion}", response_class=HTMLResponse)
    async def cambiar_alerta(request: Request, id_alerta: str, accion: str) -> Response:
        if accion not in ACCIONES_ALERTA or not _ID_ALERTA.fullmatch(id_alerta):
            raise HTTPException(404)
        leer_formulario(await request.body())
        try:
            if accion == "borrar":
                datos.borrar_alerta(int(id_alerta))
            else:
                datos.cambiar_alerta(int(id_alerta), activa=accion == "reanudar")
        except AlertaNoExiste:
            raise HTTPException(404) from None
        return RedirectResponse("/alertas", status_code=303)

    @app.post("/alertas", response_class=HTMLResponse)
    async def crear_alerta(request: Request) -> Response:
        id_producto = ""
        try:
            formulario = leer_formulario(await request.body())
            id_producto = primero(formulario, "producto_id")
            if datos.producto(id_producto) is None:
                raise ErrorFormulario("El producto no existe.")
            canal = primero(formulario, "canal", "email")
            if canal not in CANALES:
                raise ErrorFormulario("Canal de aviso no válido.")
            # B5: sin marcar la casilla, la alerta solo cuenta las ofertas exactas.
            solo_exactas = primero(formulario, "equivalentes", "") != "1"
            datos.crear_alerta(
                id_producto,
                leer_importe(primero(formulario, "umbral")),
                canal,
                solo_exactas=solo_exactas,
            )
        except ErrorFormulario as error:
            if _es_htmx(request):
                # El error va junto al formulario, sin borrar la lista de alertas.
                respuesta = pagina(request, "_error.html", {"error": str(error)}, 422)
                respuesta.headers["HX-Retarget"] = "#error-alerta"
                respuesta.headers["HX-Reswap"] = "innerHTML"
                return respuesta
            contexto = contexto_producto(id_producto)
            if contexto is None:
                return pagina(request, "error.html", {"mensaje": str(error)}, 422)
            return pagina(request, "producto.html", {**contexto, "error": str(error)}, 422)
        if not _es_htmx(request):
            return RedirectResponse(f"/productos/{id_producto}", status_code=303)
        lista = [a for a in datos.alertas() if a.producto_id == id_producto]
        return pagina(request, "_lista_alertas.html", {"alertas": lista, "limpiar_error": True})

    @app.get("/ia", response_class=HTMLResponse)
    async def registro_ia(request: Request) -> HTMLResponse:
        return pagina(request, "ia.html", {"resumen": datos.resumen_ia()})

    @app.get("/evaluacion", response_class=HTMLResponse)
    async def evaluacion(request: Request) -> HTMLResponse:
        return pagina(request, "evaluacion.html", {"resultados": datos.evaluacion()})

    @app.get("/ajustes", response_class=HTMLResponse)
    async def ajustes(request: Request) -> HTMLResponse:
        guardado = request.query_params.get("guardado") == "1"
        return pagina(
            request,
            "ajustes.html",
            {"ajustes": datos.ajustes(), "guardado": guardado, "proxy": datos.estado_proxy()},
        )

    @app.post("/ajustes", response_class=HTMLResponse)
    async def guardar_ajustes(request: Request) -> Response:
        try:
            nuevos = _ajustes_desde_formulario(
                leer_formulario(await request.body()), datos.ajustes()
            )
        except ErrorFormulario as error:
            return pagina(
                request,
                "ajustes.html",
                {
                    "ajustes": datos.ajustes(),
                    "guardado": False,
                    "error": str(error),
                    "proxy": datos.estado_proxy(),
                },
                422,
            )
        datos.guardar_ajustes(nuevos)
        return RedirectResponse("/ajustes?guardado=1", status_code=303)

    return app


def aplicacion_desde_config(config: Config) -> FastAPI:
    """La aplicación con la fuente que pide la configuración: la demo o los datos reales."""
    fuente: FuenteDatos = FuenteDemo() if config.demo else FuenteReal(config)
    # La demo no visita tiendas: su planificador no repite nada.
    cada_s = 0.0 if config.demo else config.alertas_cada_h * 3600
    return crear_app(fuente, cada_alertas_s=cada_s)


def _ajustes_desde_formulario(formulario: Parametros, actuales: Ajustes) -> Ajustes:
    """Los campos que no vienen en el formulario conservan su valor actual."""

    def texto(clave: str, etiqueta: str, actual: str, maximo: int = 200) -> str:
        valor = primero(formulario, clave, actual).strip()
        if not valor or len(valor) > maximo or not valor.isprintable():
            raise ErrorFormulario(f"«{etiqueta}» no es válido (de 1 a {maximo} caracteres).")
        return valor

    def entero(clave: str, etiqueta: str, actual: int, minimo: int, maximo: int) -> int:
        valor = primero(formulario, clave, str(actual)).strip()
        # Solo dígitos ASCII: `isdigit()` acepta «²», que luego `int()` no sabe convertir.
        if not re.fullmatch(r"[0-9]{1,4}", valor) or not minimo <= int(valor) <= maximo:
            raise ErrorFormulario(f"«{etiqueta}» tiene que estar entre {minimo} y {maximo}.")
        return int(valor)

    proveedor = primero(formulario, "proveedor", actuales.proveedor)
    if proveedor not in PROVEEDORES:
        raise ErrorFormulario("Proveedor de IA no válido.")
    transporte_http = primero(formulario, "transporte_http", actuales.transporte_http)
    if transporte_http not in TRANSPORTES_HTTP:
        raise ErrorFormulario("Cliente HTTP no válido.")
    presupuesto = actuales.presupuesto_mensual_usd
    if "presupuesto_mensual_usd" in formulario:
        presupuesto = leer_importe(primero(formulario, "presupuesto_mensual_usd"))
        if presupuesto > Decimal("100"):
            raise ErrorFormulario("El presupuesto mensual no puede pasar de 100 $.")
    return Ajustes(
        proveedor=proveedor,
        modelo_api=texto("modelo_api", "Modelo de la API", actuales.modelo_api, 80),
        modelo_local=texto("modelo_local", "Modelo local", actuales.modelo_local, 80),
        presupuesto_mensual_usd=presupuesto,
        tope_llamadas_busqueda=entero(
            "tope_llamadas_busqueda",
            "Tope de llamadas por búsqueda",
            actuales.tope_llamadas_busqueda,
            1,
            100,
        ),
        # Una casilla sin marcar no se envía: solo cuenta si el formulario trae el resto.
        google_activado=(
            primero(formulario, "google_activado") == "on"
            if "enviado" in formulario
            else actuales.google_activado
        ),
        revisar_resultados=(
            primero(formulario, "revisar_resultados") == "on"
            if "enviado" in formulario
            else actuales.revisar_resultados
        ),
        retardo_dominio_s=float(
            entero(
                "retardo_dominio_s",
                "Espera mínima entre peticiones",
                int(actuales.retardo_dominio_s),
                1,
                60,
            )
        ),
        user_agent=texto("user_agent", "User-Agent", actuales.user_agent),
        transporte_http=transporte_http,
    )
