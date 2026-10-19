"""Aplicación web de PrecIA (FastAPI + Jinja2 + HTMX).

La web solo habla con una `FuenteDatos`: la `FuenteDemo` (datos de ejemplo, en memoria) o la
`FuenteReal` (orquestador + SQLite). `crear_app` recibe la fuente ya hecha y la cierra al apagar;
`aplicacion_desde_config` elige cuál según la configuración (`--demo` o `PRECIA_DEMO`).
"""

import mimetypes
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
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
from precia.nucleo import formato
from precia.nucleo.config import Config
from precia.nucleo.fuente_datos import ErrorFuente, FuenteDatos
from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.fuente_real import FuenteReal
from precia.nucleo.modelos import CAMPOS, Ajustes, Modo
from precia.seguimiento.graficas import grafica_historico
from precia.web.formularios import (
    ErrorFormulario,
    Parametros,
    consulta_desde_parametros,
    datos_de_alta,
    leer_formulario,
    leer_importe,
    leer_version,
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
    entorno.globals["CAMPOS"] = CAMPOS
    entorno.globals["NAVEGACION"] = NAVEGACION
    entorno.globals["MODOS"] = Modo
    entorno.globals["VERSION"] = __version__
    return entorno


def url_segura(url: str) -> str:
    """Las URL vienen de las tiendas: solo se enlazan las http(s); `javascript:` y demás, no."""
    return url if urlsplit(url).scheme in ("http", "https") else "#"


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
    fuente: FuenteDatos | None = None, *, hosts: tuple[str, ...] = HOSTS_LOCALES
) -> FastAPI:
    datos: FuenteDatos = fuente if fuente is not None else FuenteDemo()
    plantillas = Jinja2Templates(env=_entorno())

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        # Al apagar se cierran el navegador (Chromium) y la base de datos de la fuente real.
        try:
            yield
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
        }
        return plantillas.TemplateResponse(
            request, plantilla, {**base, **contexto}, status_code=estado
        )

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
    async def buscar(request: Request) -> HTMLResponse:
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
                contexto["resultado"] = await datos.buscar(consulta)
        plantilla = "_resultados.html" if _es_htmx(request) else "inicio.html"
        respuesta = pagina(request, plantilla, contexto, estado)
        # La misma URL devuelve página o fragmento según HX-Request: que ninguna caché los mezcle.
        respuesta.headers["Vary"] = "HX-Request"
        return respuesta

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
            # La fuente valida el dominio (sintaxis y anti-SSRF) antes de guardarlo.
            nueva = await datos.alta_tienda(dominio, nombre)
        except (ErrorFormulario, ErrorFuente) as error:
            contexto = {
                "tiendas": datos.tiendas(),
                "error": str(error),
                "dominio": primero(formulario, "dominio")[:300],
                "nombre": primero(formulario, "nombre")[:300],
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
        }
        return pagina(request, "tienda.html", contexto)

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

    @app.get("/alertas", response_class=HTMLResponse)
    async def alertas(request: Request) -> HTMLResponse:
        return pagina(request, "alertas.html", {"alertas": datos.alertas()})

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
            datos.crear_alerta(id_producto, leer_importe(primero(formulario, "umbral")), canal)
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
        return pagina(request, "ajustes.html", {"ajustes": datos.ajustes(), "guardado": guardado})

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
                {"ajustes": datos.ajustes(), "guardado": False, "error": str(error)},
                422,
            )
        datos.guardar_ajustes(nuevos)
        return RedirectResponse("/ajustes?guardado=1", status_code=303)

    return app


def aplicacion_desde_config(config: Config) -> FastAPI:
    """La aplicación con la fuente que pide la configuración: la demo o los datos reales."""
    fuente: FuenteDatos = FuenteDemo() if config.demo else FuenteReal(config)
    return crear_app(fuente)


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
    )
