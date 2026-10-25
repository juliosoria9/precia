"""La foto principal en la web: miniatura enlazada (nunca descargada), escapada y con la CSP."""

from dataclasses import replace

import pytest

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import ConsultaBusqueda, GrupoProducto, ResultadoBusqueda, Seguimiento
from precia.web.app import crear_app, foto_segura
from tests.asgi import ClienteASGI

PASTILLAS = "pastillas-1k0698151"
FOTO = "https://recambiosnorte.example/img/pastillas.jpg?v=2&w=800"
TITULO_HOSTIL = 'Pastillas "><script>alert(1)</script>'


def _con_fotos(
    grupo: GrupoProducto, fotos: list[str | None], titulo: str | None = None
) -> GrupoProducto:
    ofertas = tuple(
        replace(o, foto=fotos[i] if i < len(fotos) else None, titulo=titulo or o.titulo)
        for i, o in enumerate(grupo.ofertas)
    )
    return replace(grupo, ofertas=ofertas)


class FuenteConFotos(FuenteDemo):
    def __init__(self, fotos: list[str | None], titulo: str | None = None) -> None:
        super().__init__()
        self.fotos = fotos
        self.titulo = titulo

    async def buscar(
        self, consulta: ConsultaBusqueda, *, seguimiento: Seguimiento | None = None
    ) -> ResultadoBusqueda:
        resultado = await super().buscar(consulta, seguimiento=seguimiento)
        grupos = tuple(_con_fotos(g, self.fotos, self.titulo) for g in resultado.grupos)
        return replace(resultado, grupos=grupos)

    def producto(self, id_producto: str) -> GrupoProducto | None:
        grupo = super().producto(id_producto)
        return None if grupo is None else _con_fotos(grupo, self.fotos, self.titulo)


def _buscar(fuente: FuenteDemo) -> str:
    cliente = ClienteASGI(crear_app(fuente))
    return cliente.get("/buscar?texto=1K0698151", cabeceras={"HX-Request": "true"}).texto


def _imgs(html: str, clase: str = "foto-") -> list[str]:
    """Los `<img>` de fotos de artículos (no el logo de la cabecera), sin el `<img `."""
    imgs = [trozo.split(">", 1)[0] for trozo in html.split("<img ")[1:]]
    return [i for i in imgs if f'class="{clase}' in i]


def test_la_csp_permite_imagenes_https_y_nada_mas() -> None:
    csp = ClienteASGI(crear_app()).get("/").cabeceras["content-security-policy"]
    directiva = next(d.strip() for d in csp.split(";") if d.strip().startswith("img-src"))
    assert directiva == "img-src 'self' https:"
    assert "data:" not in csp and "blob:" not in csp and "*" not in csp


def test_resultados_pintan_la_miniatura() -> None:
    html = _buscar(FuenteConFotos([FOTO] * 10))
    imgs = _imgs(html, "foto-miniatura")
    assert imgs
    img = imgs[0]
    assert 'src="https://recambiosnorte.example/img/pastillas.jpg?v=2&amp;w=800"' in img
    for atributo in ('loading="lazy"', 'referrerpolicy="no-referrer"', 'width="64"', 'height="64"'):
        assert atributo in img
    assert "onerror" not in html


def test_el_alt_va_escapado() -> None:
    html = _buscar(FuenteConFotos([FOTO] * 10, titulo=TITULO_HOSTIL))
    assert "<script>alert(1)</script>" not in html
    assert 'alt="Pastillas &#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in html


def test_sin_foto_no_hay_img() -> None:
    html = _buscar(FuenteConFotos([]))
    assert _imgs(html) == []
    assert "foto-vacia" in html


@pytest.mark.parametrize(
    "foto",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "http://recambiosnorte.example/a.jpg",
        'https://a.example/x" onerror="alert(1)',
        "https://a.example/x y.jpg",
    ],
)
def test_una_foto_envenenada_no_se_pinta(foto: str) -> None:
    html = _buscar(FuenteConFotos([foto] * 10))
    assert _imgs(html) == []
    assert foto not in html


def test_ficha_del_producto_con_foto_del_grupo_y_miniaturas() -> None:
    cliente = ClienteASGI(crear_app(FuenteConFotos([FOTO] * 10)))
    html = cliente.get(f"/productos/{PASTILLAS}").texto
    assert 'class="foto-grupo"' in html
    assert len(_imgs(html)) >= 2


def test_ficha_del_producto_sin_fotos() -> None:
    html = ClienteASGI(crear_app(FuenteConFotos([]))).get(f"/productos/{PASTILLAS}").texto
    assert _imgs(html) == []


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        (FOTO, FOTO),
        ("http://a.example/x.jpg", None),
        ("javascript:alert(1)", None),
        ("https://a.example/x y.jpg", None),
        ("https://a.example/x'.jpg", None),
        ("https://a.example/" + "a" * 2000, None),
        ("https://", None),
        (None, None),
        (12, None),
    ],
)
def test_filtro_foto_segura(url: object, esperada: str | None) -> None:
    assert foto_segura(url) == esperada
