"""A7 (revisión): el tiempo de una descarga es total, contado desde que empieza.

Antes, cada salto de una redirección empezaba otro límite y, con urllib, el límite del cuerpo
empezaba después de las cabeceras. Una descarga cancelada podía seguir en su hilo mucho más que el
margen con que el limitador da el dominio por ocupado (C1), y coincidir con la siguiente
(ADR-017). Ahora dura como mucho su tiempo total y, con urllib, lo que dura una última lectura.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from precia.navegador.http import TIMEOUT_HTTP_S, DescargadorHTTP, RespuestaCruda
from precia.navegador.tipos import ErrorDescarga
from tests.navegador.falsos import Reloj, validar_con_dns_publico
from tests.navegador.test_http import Cuerpo

UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
URL = "https://www.tienda.example/p/0"


def _redireccion(numero: int) -> RespuestaCruda:
    return RespuestaCruda(estado=302, cabeceras={"location": f"/p/{numero}"}, cuerpo=Cuerpo(b""))


def _html(reloj: Reloj | None = None) -> RespuestaCruda:
    return RespuestaCruda(
        estado=200,
        cabeceras={"content-type": "text/html; charset=utf-8"},
        cuerpo=Cuerpo(b"<html><body>" + b"x" * 200_000 + b"</body></html>", reloj),
    )


@dataclass
class TransporteLento:
    """Cada petición tarda `segundos` (en el reloj falso) y redirige `saltos` veces."""

    reloj: Reloj
    segundos: float
    saltos: int
    con_huella: bool = True
    tiempos: list[float] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        return self.pedir(url, cabeceras, timeout, 0)

    def pedir(
        self, url: str, cabeceras: Mapping[str, str], timeout: float, max_bytes: int
    ) -> RespuestaCruda:
        self.tiempos.append(timeout)
        self.reloj.ahora += self.segundos
        if len(self.tiempos) <= self.saltos:
            return _redireccion(len(self.tiempos))
        return _html()

    def cerrar(self) -> None:
        pass


@dataclass
class TransporteURLLibLento:
    """Como `TransporteURLLib` (sin `pedir`): las cabeceras tardan `segundos`."""

    reloj: Reloj
    segundos: float
    tiempos: list[float] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        self.tiempos.append(timeout)
        self.reloj.ahora += self.segundos
        return _html(self.reloj)  # Cada lectura del cuerpo tarda 5 s.


def test_cada_salto_de_una_redireccion_tiene_solo_el_tiempo_que_queda() -> None:
    reloj = Reloj()
    transporte = TransporteLento(reloj, segundos=3.0, saltos=2)
    http = DescargadorHTTP(
        UA, validador=validar_con_dns_publico, transporte=transporte, reloj=reloj
    )
    pagina = asyncio.run(http.descargar(URL))
    assert pagina.estado == 200
    assert transporte.tiempos == [TIMEOUT_HTTP_S, 7.0, 4.0]


def test_si_las_redirecciones_agotan_el_tiempo_no_se_pide_el_siguiente_salto() -> None:
    reloj = Reloj()
    transporte = TransporteLento(reloj, segundos=5.0, saltos=4)
    http = DescargadorHTTP(
        UA, validador=validar_con_dns_publico, transporte=transporte, reloj=reloj
    )
    with pytest.raises(ErrorDescarga, match="tiempo"):
        asyncio.run(http.descargar(URL))
    assert transporte.tiempos == [TIMEOUT_HTTP_S, 5.0]


def test_con_urllib_el_cuerpo_cuenta_el_tiempo_que_tardaron_las_cabeceras() -> None:
    reloj = Reloj()
    transporte = TransporteURLLibLento(reloj, segundos=8.0)
    http = DescargadorHTTP(
        UA, validador=validar_con_dns_publico, transporte=transporte, reloj=reloj
    )
    inicio = reloj.ahora
    with pytest.raises(ErrorDescarga, match="tiempo"):
        asyncio.run(http.descargar(URL))
    # 8 s de cabeceras y una lectura de 5 s: se corta en la siguiente comprobación, a los 13 s
    # (antes seguía leyendo hasta 8 + 10 s y una lectura más).
    assert reloj.ahora - inicio == 13.0
