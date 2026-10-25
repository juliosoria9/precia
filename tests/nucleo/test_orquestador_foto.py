"""La foto principal en una búsqueda: de la ficha, si no de la tarjeta, y al histórico."""

from precia.navegador.tipos import Bloqueado
from precia.nucleo.modelos import Modo
from tests.nucleo.test_orquestador import montar, nuevo_almacen, ofertas
from tests.perfiles.sitio import (
    BUSQUEDA,
    CAMPOS_OK,
    PRODUCTOS,
    TIENDA,
    NavegadorFalso,
    html_listado,
    paginas_sitio,
    perfil,
)


def _foto_ficha(ruta: str) -> str:
    return f"{TIENDA}/img{ruta}-1.jpg"


def _foto_tarjeta(ruta: str) -> str:
    return f"{TIENDA}/miniaturas{ruta}.jpg"


def _sitio_con_miniaturas(*, jsonld: tuple[int, ...] = (1, 3)) -> dict[str, str]:
    paginas = paginas_sitio(jsonld=jsonld)
    listado = html_listado()
    for p in PRODUCTOS:
        enlace = f'<h3 class="nombre"><a class="enlace" href="{p.ruta}">'
        listado = listado.replace(
            enlace, f'<img src="/img/lazy.gif" data-src="/miniaturas{p.ruta}.jpg">{enlace}'
        )
    paginas[BUSQUEDA] = listado
    return paginas


def test_la_foto_de_la_ficha_llega_a_la_oferta_y_al_historico() -> None:
    m = montar(nuevo_almacen(perfil()), NavegadorFalso(_sitio_con_miniaturas()))
    r = m.buscar(modo=Modo.CLASICO)
    # La ficha tiene foto (selector `fotos` del perfil): gana a la miniatura del listado.
    assert {o.url: o.foto for o in ofertas(r)} == {p.url: _foto_ficha(p.ruta) for p in PRODUCTOS}
    for grupo in r.grupos:
        guardado = m.almacen.producto(grupo.id)
        assert guardado is not None
        assert {o.url: o.foto for o in guardado.ofertas} == {o.url: o.foto for o in grupo.ofertas}
        assert guardado.foto == grupo.foto is not None


def test_sin_foto_en_la_ficha_usa_la_de_la_tarjeta() -> None:
    # Ni JSON-LD (lleva `image`) ni selector `fotos` en el perfil: la ficha no da foto.
    campos = {k: v for k, v in CAMPOS_OK.items() if k != "fotos"}
    sin_fotos = perfil(sel_campos=campos)
    m = montar(nuevo_almacen(sin_fotos), NavegadorFalso(_sitio_con_miniaturas(jsonld=())))
    r = m.buscar(modo=Modo.CLASICO)
    assert {o.url: o.foto for o in ofertas(r)} == {p.url: _foto_tarjeta(p.ruta) for p in PRODUCTOS}


def test_sin_foto_en_ningun_sitio() -> None:
    campos = {k: v for k, v in CAMPOS_OK.items() if k != "fotos"}
    m = montar(nuevo_almacen(perfil(sel_campos=campos)), NavegadorFalso(paginas_sitio(jsonld=())))
    r = m.buscar(modo=Modo.CLASICO)
    assert ofertas(r)
    assert all(o.foto is None for o in ofertas(r))
    for grupo in r.grupos:
        guardado = m.almacen.producto(grupo.id)
        assert guardado is not None and all(o.foto is None for o in guardado.ofertas)


def test_las_ofertas_del_listado_tras_un_bloqueo_llevan_la_foto_de_la_tarjeta() -> None:
    bloqueo = Bloqueado(PRODUCTOS[1].url, "hCaptcha")
    navegador = NavegadorFalso(_sitio_con_miniaturas(), errores={PRODUCTOS[1].url: bloqueo})
    m = montar(nuevo_almacen(perfil()), navegador)
    r = m.buscar(modo=Modo.CLASICO)
    fotos = {o.url: o.foto for o in ofertas(r)}
    assert fotos[PRODUCTOS[0].url] == _foto_ficha(PRODUCTOS[0].ruta)
    for p in PRODUCTOS[1:]:
        assert fotos[p.url] == _foto_tarjeta(p.ruta)
