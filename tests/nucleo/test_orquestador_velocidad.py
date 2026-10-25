"""Velocidad, tanda 3 (plan medido de S03): resultados parciales por ficha (A1), la ficha
siguiente se descarga mientras se extrae la actual (A2) y la IA del extractor deja de pedir los
campos que no salen anclados (A11).

Sin red: la tienda es la de `tests.perfiles.sitio` y la IA, el `ProveedorSimulado`.
"""

import asyncio
from decimal import Decimal
from typing import Any

from precia.ia.servicio import ServicioIA
from precia.ia.tipos import PeticionIA, RespuestaIA
from precia.navegador.tipos import Bloqueado, ErrorDescarga, Pagina
from precia.nucleo.modelos import ConsultaBusqueda, EstadoPaso, Modo, ResultadoBusqueda
from precia.nucleo.orquestador import DESCARGANDO_BUSQUEDA, PRIMERA_VEZ, Orquestador
from tests.navegador.falsos import validar_con_dns_publico
from tests.nucleo.falsos import SeguimientoFalso
from tests.nucleo.test_orquestador import (
    AHORA,
    _sitio_doble,
    montar,
    nuevo_almacen,
    ofertas,
    paso,
    progreso,
    redirigida,
)
from tests.perfiles.sitio import (
    CAMPOS_OK,
    CONSULTA,
    DOMINIO,
    LISTADO_OK,
    PRODUCTOS,
    NavegadorFalso,
    paginas_sitio,
    perfil,
)

N = len(PRODUCTOS)


def _buscar(m: Any, seguimiento: SeguimientoFalso | None = None, **opciones: Any) -> Any:
    opciones.setdefault("texto", CONSULTA)
    consulta = ConsultaBusqueda(**opciones)
    return asyncio.run(m.orquestador.buscar(consulta, seguimiento=seguimiento))


# --- A1: resultados parciales por ficha ---------------------------------------------------------


def test_cada_ficha_leida_llega_en_un_parcial_antes_de_que_acabe_la_tienda() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    seg = SeguimientoFalso()
    r = _buscar(m, seg, modo=Modo.CLASICO)

    assert [len(ofertas(p)) for p in seg.parciales] == [*range(1, N + 1), N]
    assert all(p.provisional for p in seg.parciales)
    # Mientras la tienda sigue no hay progreso suyo: solo el de las que han terminado.
    assert all(p.progreso == () for p in seg.parciales[:-1])
    assert [t.tienda for t in seg.parciales[-1].progreso] == [DOMINIO]
    assert {o.url for o in ofertas(seg.parciales[-1])} == {o.url for o in ofertas(r)}


def test_el_parcial_por_ficha_junta_las_terminadas_y_las_leidas_en_curso() -> None:
    otra = "desguace-prueba.es"
    otro_perfil = perfil(
        dominio=otra, url_busqueda=f"https://www.{otra}/buscar?orden=relevancia&q={{consulta}}"
    )
    almacen = nuevo_almacen(perfil(), otro_perfil, tiendas=(DOMINIO, otra))
    m = montar(almacen, NavegadorFalso(_sitio_doble()))
    seg = SeguimientoFalso()
    _buscar(m, seg, modo=Modo.CLASICO)

    # Las ofertas de un parcial nunca desaparecen en el siguiente (el agrupado es el clásico).
    for antes, despues in zip(seg.parciales, seg.parciales[1:], strict=False):
        assert {o.url for o in ofertas(antes)} <= {o.url for o in ofertas(despues)}
    assert len(ofertas(seg.parciales[-1])) == 2 * N


def test_un_bloqueo_en_las_fichas_no_quita_las_ya_mostradas() -> None:
    bloqueo = Bloqueado(PRODUCTOS[2].url, "reCAPTCHA")
    navegador = NavegadorFalso(paginas_sitio(), errores={PRODUCTOS[2].url: bloqueo})
    m = montar(nuevo_almacen(perfil()), navegador)
    seg = SeguimientoFalso()
    r = _buscar(m, seg, modo=Modo.CLASICO)

    assert [len(ofertas(p)) for p in seg.parciales][:2] == [1, 2]
    assert {o.url for o in ofertas(seg.parciales[1])} <= {o.url for o in ofertas(r)}


# --- A2: la ficha siguiente se descarga mientras se extrae la actual ---------------------------


class NavegadorApuntado(NavegadorFalso):
    """Apunta en `eventos` cuándo empieza y acaba cada descarga de una ficha."""

    eventos: list[str]

    async def obtener(self, url: str, *, js: bool = False, grande: bool = False) -> Pagina:
        ficha = "/p/" in url
        if ficha:
            self.eventos.append(f"pide {url.rsplit('/', 1)[-1]}")
        pagina = await super().obtener(url, js=js, grande=grande)
        if ficha:
            await asyncio.sleep(0)
            self.eventos.append(f"tiene {url.rsplit('/', 1)[-1]}")
        return pagina


class ProveedorApuntado:
    """La IA del extractor: apunta cuándo empieza y acaba cada llamada."""

    nombre = "simulado"
    modelo = "simulado"

    def __init__(self, eventos: list[str]) -> None:
        self.eventos = eventos
        self.peticiones: list[PeticionIA] = []

    async def completar(self, peticion: PeticionIA) -> RespuestaIA:
        self.peticiones.append(peticion)
        self.eventos.append("ia empieza")
        for _ in range(5):
            await asyncio.sleep(0)
        self.eventos.append("ia acaba")
        producto = PRODUCTOS[(len(self.peticiones) - 1) % N]
        return RespuestaIA({"estado": producto.estado}, 10, 2, Decimal(0), 5, "simulado", "x")


def _con_ia_apuntada(navegador: NavegadorFalso, eventos: list[str]) -> Orquestador:
    sin_estado = perfil(sel_campos={k: v for k, v in CAMPOS_OK.items() if k != "estado"})
    almacen = nuevo_almacen(sin_estado)
    proveedor = ProveedorApuntado(eventos)
    servicio = ServicioIA(proveedor, almacen, presupuesto_mes_usd=Decimal(10), tope_llamadas=20)
    return Orquestador(
        almacen,
        navegador,
        servicio,
        validador=validar_con_dns_publico,
        reloj=lambda: AHORA,
    )


def test_la_ficha_siguiente_se_pide_mientras_la_ia_extrae_la_actual() -> None:
    eventos: list[str] = []
    navegador = NavegadorApuntado(paginas_sitio(jsonld=()))
    navegador.eventos = eventos
    orquestador = _con_ia_apuntada(navegador, eventos)
    consulta = ConsultaBusqueda(CONSULTA, modo=Modo.AUTO, campos=("precio", "estado"))
    r = asyncio.run(orquestador.buscar(consulta))

    assert len(ofertas(r)) == N
    segunda = PRODUCTOS[1].url.rsplit("/", 1)[-1]
    # La segunda ficha se pide antes de que acabe la IA de la primera...
    assert eventos.index(f"pide {segunda}") < eventos.index("ia acaba")
    # ...pero nunca hay dos fichas en vuelo a la vez: se pide después de tener la anterior.
    en_vuelo = maximo = 0
    for evento in eventos:
        en_vuelo += evento.startswith("pide ") - evento.startswith("tiene ")
        maximo = max(maximo, en_vuelo)
    assert maximo == 1
    # Las mismas peticiones que antes: cada ficha una vez.
    fichas = [u for u in navegador.urls if "/p/" in u]
    assert sorted(fichas) == sorted(p.url for p in PRODUCTOS)


def test_un_bloqueo_en_la_ficha_adelantada_para_la_tienda_sin_tareas_vivas() -> None:
    eventos: list[str] = []
    bloqueo = Bloqueado(PRODUCTOS[1].url, "reCAPTCHA")
    navegador = NavegadorApuntado(paginas_sitio(jsonld=()), errores={PRODUCTOS[1].url: bloqueo})
    navegador.eventos = eventos
    orquestador = _con_ia_apuntada(navegador, eventos)
    consulta = ConsultaBusqueda(CONSULTA, modo=Modo.AUTO, campos=("precio", "estado"))

    async def escenario() -> ResultadoBusqueda:
        resultado = await orquestador.buscar(consulta)
        vivas = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert vivas == []
        return resultado

    r = asyncio.run(escenario())
    detalle = next(p for p in progreso(r).pasos if p.estado is EstadoPaso.ERROR).detalle
    assert "reCAPTCHA" in detalle and "1 fichas leídas" in detalle
    # Ni una petición más tras el bloqueo.
    fichas = [u for u in navegador.urls if "/p/" in u]
    assert fichas == [PRODUCTOS[0].url, PRODUCTOS[1].url]


def test_un_error_que_no_es_bloqueo_en_la_ficha_adelantada_cuenta_como_fallida() -> None:
    navegador = NavegadorFalso(
        paginas_sitio(), errores={PRODUCTOS[1].url: ErrorDescarga("Tiempo agotado")}
    )
    m = montar(nuevo_almacen(perfil()), navegador)
    r = _buscar(m, modo=Modo.CLASICO)
    assert len(ofertas(r)) == N - 1
    assert "1 no se han podido abrir" in paso(r, "extracción").detalle


# --- A11: no pedir a la IA campos que no salen anclados ----------------------------------------


SIN_DOS = perfil(sel_campos={k: v for k, v in CAMPOS_OK.items() if k not in ("estado", "marca")})
"""Un perfil que no lee «estado» ni «marca»: los busca el extractor de respaldo."""


def test_un_campo_que_la_ia_no_ancla_en_dos_fichas_ya_no_se_le_pide() -> None:
    # La IA encuentra «estado» en todas, pero «marca» nunca sale anclada (se la inventa).
    guion = {"extractor": [{"estado": p.estado, "marca": "Marca inventada"} for p in PRODUCTOS]}
    m = montar(nuevo_almacen(SIN_DOS), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    r = _buscar(m, modo=Modo.AUTO, campos=("precio", "estado", "marca"))

    assert m.proveedor is not None
    pedidos = [p.esquema["required"] for p in m.proveedor.peticiones]
    assert len(pedidos) == N
    assert all("marca" in campos for campos in pedidos[:2])
    assert all("marca" not in campos for campos in pedidos[2:])
    assert all("estado" in campos for campos in pedidos)
    assert len(ofertas(r)) == N
    assert "marca" in paso(r, "extracción").detalle


def test_un_campo_anclado_alguna_vez_se_sigue_pidiendo() -> None:
    # «marca» sale anclada en la primera ficha: aunque luego falle, se sigue pidiendo.
    guion = {
        "extractor": [
            {"estado": p.estado, "marca": p.marca if i == 0 else "Marca inventada"}
            for i, p in enumerate(PRODUCTOS)
        ]
    }
    m = montar(nuevo_almacen(SIN_DOS), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    _buscar(m, modo=Modo.AUTO, campos=("precio", "estado", "marca"))
    assert m.proveedor is not None
    assert all("marca" in p.esquema["required"] for p in m.proveedor.peticiones)


def test_el_precio_se_sigue_pidiendo_aunque_no_salga_anclado() -> None:
    # «estado» sale anclado (el extractor sigue encendido), el precio de la IA nunca.
    sin_dos = perfil(
        sel_campos={k: v for k, v in CAMPOS_OK.items() if k not in ("precio", "estado")}
    )
    guion = {"extractor": [{"precio": "1,00 €", "estado": p.estado} for p in PRODUCTOS]}
    m = montar(nuevo_almacen(sin_dos), NavegadorFalso(paginas_sitio(jsonld=())), guion)
    _buscar(m, modo=Modo.AUTO, campos=("precio", "estado"))
    assert m.proveedor is not None
    pedidos = [p.esquema["required"] for p in m.proveedor.peticiones]
    assert len(pedidos) == N
    assert all("precio" in campos for campos in pedidos)


# --- A10: progreso fino por tienda -------------------------------------------------------------


def test_el_seguimiento_recibe_en_que_ficha_va_cada_tienda() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(paginas_sitio()))
    seg = SeguimientoFalso()
    _buscar(m, seg, modo=Modo.CLASICO)
    detalles = seg.detalles_de(DOMINIO)
    assert detalles[0] == DESCARGANDO_BUSQUEDA
    assert [d for d in detalles if d.startswith("Ficha")] == [
        f"Ficha {i} de {N}" for i in range(1, N + 1)
    ]


def test_una_tienda_nueva_avisa_de_que_la_primera_vez_tarda_mas() -> None:
    m = montar(nuevo_almacen(), redirigida(), {"configurador": [LISTADO_OK, CAMPOS_OK]})
    seg = SeguimientoFalso()
    _buscar(m, seg, modo=Modo.AUTO)
    configurando = [d for d in seg.detalles_de(DOMINIO) if d.startswith(PRIMERA_VEZ)]
    assert configurando, seg.detalles_de(DOMINIO)
    # Se va viendo que avanza: páginas probadas y llamadas a la IA.
    assert any("páginas probadas" in d for d in configurando)
    assert any("1 llamada a la IA" in d for d in configurando)
    assert any("2 llamadas a la IA" in d for d in configurando)
    # Después ya busca la consulta del usuario.
    assert seg.detalles_de(DOMINIO)[-1] == f"Ficha {N} de {N}"
