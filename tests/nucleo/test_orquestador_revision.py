"""Revisión de los resultados en una búsqueda completa: el caso de la puerta y la cerradura."""

import asyncio
from typing import Any

from precia.ia.tipos import ErrorIA
from precia.nucleo.modelos import ConsultaBusqueda, EstadoPaso, Modo, Paso, ResultadoBusqueda
from precia.nucleo.orquestador import _de_la_consulta
from tests.nucleo.falsos import SeguimientoFalso
from tests.nucleo.test_orquestador import Montaje, montar, nuevo_almacen, ofertas, progreso
from tests.perfiles.sitio import (
    PLANTILLA,
    PORTADA,
    NavegadorFalso,
    Producto,
    html_ficha,
    html_listado,
    html_portada,
    perfil,
)

PUERTA = "puerta delantera derecha"
BUSQUEDA_PUERTA = PLANTILLA.format(consulta="puerta+delantera+derecha")
CERRADURA = "Cerradura de la puerta delantera derecha"
PRODUCTOS_PUERTA = (
    Producto(
        "/p/cerradura-puerta-delantera-derecha",
        CERRADURA,
        "35,00 €",
        "35.00",
        "6J4837016",
        "Seat",
        "Usado",
        "Cerradura de la puerta delantera derecha, con motor.",
    ),
    Producto(
        "/p/puerta-delantera-derecha-ibiza",
        "Puerta delantera derecha Seat Ibiza",
        "180,00 €",
        "180.00",
        "6J4831056",
        "Seat",
        "Usado",
        "Puerta delantera derecha en blanco, sin golpes.",
    ),
    Producto(
        "/p/puerta-delantera-derecha-leon",
        "Puerta delantera derecha Seat León",
        "210,00 €",
        "210.00",
        "1P0831056",
        "Seat",
        "Usado",
        "Puerta delantera derecha en gris.",
    ),
)
MOTIVO = "Es la cerradura, no la puerta"


def sitio() -> dict[str, str]:
    paginas = {PORTADA: html_portada(), BUSQUEDA_PUERTA: html_listado(PRODUCTOS_PUERTA)}
    for p in PRODUCTOS_PUERTA:
        paginas[p.url] = html_ficha(p)
    return paginas


def revision() -> dict[str, Any]:
    """Respuesta de la IA en el orden de las ofertas: la cerradura se abre la última (regla del
    núcleo al ordenar), así que es la tercera."""
    return {
        "clasificaciones": [
            {"indice": 0, "clase": "misma_pieza", "motivo": ""},
            {"indice": 1, "clase": "misma_pieza", "motivo": ""},
            {"indice": 2, "clase": "accesorio", "motivo": MOTIVO},
        ]
    }


TODO_OTRA = {
    "clasificaciones": [{"indice": i, "clase": "otra", "motivo": "Ignorado"} for i in range(3)]
}
"""Lo que haría una prompt injection eficaz: tirar todo lo de la competencia."""


def buscar(m: Montaje, modo: Modo = Modo.AUTO) -> ResultadoBusqueda:
    return m.buscar(texto=PUERTA, modo=modo, campos=("precio",))


def montar_puerta(guion: dict[str, list[Any]] | None, **opciones: Any) -> Montaje:
    opciones.setdefault("revisar_resultados", True)
    return montar(nuevo_almacen(perfil()), NavegadorFalso(sitio()), guion, **opciones)


def pasos_generales(r: ResultadoBusqueda) -> list[Paso]:
    return (
        list(progreso(r, "PrecIA").pasos) if any(p.tienda == "PrecIA" for p in r.progreso) else []
    )


def test_el_caso_de_julio_la_cerradura_sale_descartada_por_la_ia() -> None:
    m = montar_puerta({"revision": [revision()]})
    r = buscar(m)
    assert m.peticiones_ia == ["revision"]
    titulos = {o.titulo for o in ofertas(r)}
    assert CERRADURA not in titulos
    assert len(titulos) == 2
    [cerradura] = r.descartados
    assert (cerradura.titulo, cerradura.origen) == (CERRADURA, "ia")
    assert cerradura.motivo == f"IA · Pieza o accesorio de lo buscado: {MOTIVO}"
    assert r.llamadas_por_componente == {"revision": 1}
    [paso] = [p for p in pasos_generales(r) if p.nombre == "revisión"]
    assert paso.estado is EstadoPaso.OK
    assert "1 de 3 descartadas" in paso.detalle


def test_el_coste_de_la_revision_queda_en_llamadas_ia() -> None:
    m = montar_puerta({"revision": [revision()]})
    r = buscar(m)
    [llamada] = m.almacen.llamadas()
    assert (llamada.componente, llamada.resultado) == ("revision", "ok")
    assert llamada.coste_usd == r.coste_ia_usd > 0


def test_la_segunda_busqueda_igual_sale_de_la_cache_sin_llamar() -> None:
    almacen = nuevo_almacen(perfil())
    primera = montar(
        almacen, NavegadorFalso(sitio()), {"revision": [revision()]}, revisar_resultados=True
    )
    buscar(primera)
    segunda = montar(almacen, NavegadorFalso(sitio()), {"revision": []}, revisar_resultados=True)
    r = buscar(segunda)
    assert segunda.peticiones_ia == []
    assert [d.origen for d in r.descartados] == ["ia"]
    [paso] = [p for p in pasos_generales(r) if p.nombre == "revisión"]
    assert "3 desde la caché" in paso.detalle


def test_json_invalido_o_fallo_de_la_ia_no_pierde_resultados_y_aplica_la_regla() -> None:
    for fallo in ("esto no es JSON", ErrorIA("El servidor de IA no responde")):
        m = montar_puerta({"revision": [fallo, fallo]})
        r = buscar(m)
        # Nada se pierde: las puertas quedan y la cerradura sale por la regla, a la vista.
        assert len(ofertas(r)) == 2
        assert [(d.titulo, d.origen) for d in r.descartados] == [(CERRADURA, "regla")]
        avisos = [p for p in pasos_generales(r) if p.estado is EstadoPaso.AVISO]
        assert any("Revisión sin IA" in p.detalle and "regla" in p.detalle for p in avisos)


def test_inyeccion_en_un_titulo_no_vacia_los_resultados() -> None:
    m = montar_puerta({"revision": [TODO_OTRA]})
    r = buscar(m)
    assert len(ofertas(r)) == 2
    assert [d.origen for d in r.descartados] == ["regla"]
    assert any("anómala" in p.detalle for p in pasos_generales(r))


def test_clasico_no_llama_a_la_ia_y_descarta_por_la_regla() -> None:
    m = montar_puerta({"revision": [revision()]})
    r = buscar(m, Modo.CLASICO)
    assert m.peticiones_ia == []
    assert [(d.titulo, d.origen) for d in r.descartados] == [(CERRADURA, "regla")]
    assert r.llamadas_ia == 0


def test_auto_sin_el_ajuste_no_revisa_con_ia() -> None:
    m = montar_puerta({"revision": [revision()]}, revisar_resultados=False)
    r = buscar(m)
    assert m.peticiones_ia == []
    assert [d.origen for d in r.descartados] == ["regla"]


def test_el_resultado_provisional_no_descarta_nada() -> None:
    m = montar_puerta({"revision": [revision()]})
    seguimiento = SeguimientoFalso()
    consulta = ConsultaBusqueda(PUERTA, modo=Modo.AUTO, campos=("precio",))
    asyncio.run(m.orquestador.buscar(consulta, seguimiento=seguimiento))
    # A1: uno por ficha y otro al terminar la tienda; ninguno descarta nada.
    assert all(p.descartados == () for p in seguimiento.parciales)
    parcial = seguimiento.parciales[-1]
    assert parcial.descartados == ()
    assert len([o for g in parcial.grupos for o in g.ofertas]) == 3


def test_la_cerradura_se_abre_la_ultima() -> None:
    class R:
        def __init__(self, titulo: str) -> None:
            self.titulo = titulo
            self.url = "https://t.example/p"

    resultados = [R(CERRADURA), R("Puerta delantera derecha Seat Ibiza")]
    ordenados = _de_la_consulta(resultados, PUERTA, ordenar=True)
    assert [r.titulo for r in ordenados] == ["Puerta delantera derecha Seat Ibiza", CERRADURA]
