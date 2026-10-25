"""B6: dos búsquedas a la vez con la misma tienda no la configuran ni la reparan dos veces.

Las búsquedas simultáneas comparten unos `TurnosDePerfil` (los da `FuenteReal`): quien llega
después a una tienda sin perfil, o con el perfil roto, espera a que termine la otra y usa lo que
haya dejado en el almacén.
"""

import asyncio
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from precia.almacen.sqlite import Almacen
from precia.ia.servicio import ServicioIA
from precia.ia.simulado import Guion, ProveedorSimulado
from precia.nucleo.modelos import ConsultaBusqueda, EstadoPaso, Modo, ResultadoBusqueda
from precia.nucleo.orquestador import Orquestador, TurnosDePerfil
from tests.navegador.falsos import validar_con_dns_publico
from tests.nucleo.test_orquestador import (
    AHORA,
    PROPUESTA,
    ROTO,
    nuevo_almacen,
    ofertas,
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
)


def _dos_a_la_vez(
    almacen: Almacen,
    navegador: Any,
    guion: Mapping[str, Iterable[Guion]],
    turnos: TurnosDePerfil | None,
) -> tuple[ProveedorSimulado, list[ResultadoBusqueda]]:
    proveedor = ProveedorSimulado(guion, coste_usd=Decimal("0.001"))
    servicio = ServicioIA(proveedor, almacen, presupuesto_mes_usd=Decimal(10), tope_llamadas=20)

    def orquestador() -> Orquestador:
        return Orquestador(
            almacen,
            navegador,
            servicio,
            validador=validar_con_dns_publico,
            reloj=lambda: AHORA,
            consultas_configuracion=(CONSULTA,),
            turnos_perfil=turnos,
        )

    async def ambas() -> list[ResultadoBusqueda]:
        consulta = ConsultaBusqueda(texto=CONSULTA, modo=Modo.AUTO)
        return list(
            await asyncio.gather(orquestador().buscar(consulta), orquestador().buscar(consulta))
        )

    return proveedor, asyncio.run(ambas())


def _detalles_perfil(resultado: ResultadoBusqueda) -> list[str]:
    return [p.detalle for p in progreso(resultado).pasos if p.nombre == "perfil"]


def test_la_misma_tienda_sin_perfil_se_configura_una_sola_vez() -> None:
    almacen = nuevo_almacen()
    guion = {"configurador": [LISTADO_OK, CAMPOS_OK]}
    proveedor, resultados = _dos_a_la_vez(almacen, redirigida(), guion, TurnosDePerfil())

    assert [p.componente for p in proveedor.peticiones] == ["configurador", "configurador"]
    vigente = almacen.perfil_vigente(DOMINIO)
    assert vigente is not None and vigente.version == 1
    assert all(len(ofertas(r)) == len(PRODUCTOS) for r in resultados)
    esperaron = [
        r for r in resultados if any("otra búsqueda" in d.lower() for d in _detalles_perfil(r))
    ]
    assert len(esperaron) == 1
    assert "v1" in _detalles_perfil(esperaron[0])[-1]


def test_si_la_otra_configuracion_falla_no_se_repite_enseguida() -> None:
    almacen = nuevo_almacen()
    sitio = paginas_sitio(busqueda=None, formulario=False)
    _, resultados = _dos_a_la_vez(
        almacen, NavegadorFalso(sitio), {"configurador": []}, TurnosDePerfil()
    )

    tienda = almacen.tienda(DOMINIO)
    assert tienda is not None and tienda.fallo is not None and tienda.fallo.intentos == 1
    estados = sorted(progreso(r).pasos[-1].estado.value for r in resultados)
    assert estados == sorted([EstadoPaso.ERROR.value, EstadoPaso.OMITIDO.value])


def test_un_perfil_roto_se_repara_una_sola_vez() -> None:
    almacen = nuevo_almacen(ROTO)
    guion = {"reparador": [PROPUESTA]}
    proveedor, resultados = _dos_a_la_vez(
        almacen, NavegadorFalso(paginas_sitio()), guion, TurnosDePerfil()
    )

    assert [p.componente for p in proveedor.peticiones] == ["reparador"]
    vigente = almacen.perfil_vigente(DOMINIO)
    assert vigente is not None and vigente.version == 2
    assert all(len(ofertas(r)) == len(PRODUCTOS) for r in resultados)
    assert any("otra búsqueda" in d.lower() for r in resultados for d in _detalles_perfil(r))


def test_si_la_otra_reparacion_falla_no_se_repite_enseguida() -> None:
    # Revisión de B6: sin apuntar el fallo, la búsqueda que esperaba encontraba la misma versión
    # y volvía a pagar la reparación (IA y peticiones de validación a la tienda).
    almacen = nuevo_almacen(ROTO)
    proveedor, resultados = _dos_a_la_vez(
        almacen, NavegadorFalso(paginas_sitio()), {"reparador": []}, TurnosDePerfil()
    )

    assert [p.componente for p in proveedor.peticiones] == ["reparador"]
    vigente = almacen.perfil_vigente(DOMINIO)
    assert vigente is not None and vigente.version == ROTO.version
    detalles = [d for r in resultados for d in _detalles_perfil(r)]
    assert any("otra búsqueda" in d.lower() and "no ha podido" in d.lower() for d in detalles)


def test_una_reparacion_fallida_no_impide_reparar_en_una_busqueda_posterior() -> None:
    # Solo se salta quien esperaba a la que falló; la siguiente búsqueda lo vuelve a intentar.
    almacen = nuevo_almacen(ROTO)
    turnos = TurnosDePerfil()
    _dos_a_la_vez(almacen, NavegadorFalso(paginas_sitio()), {"reparador": []}, turnos)
    proveedor, _ = _dos_a_la_vez(
        almacen, NavegadorFalso(paginas_sitio()), {"reparador": [PROPUESTA]}, turnos
    )

    assert [p.componente for p in proveedor.peticiones] == ["reparador"]
    vigente = almacen.perfil_vigente(DOMINIO)
    assert vigente is not None and vigente.version == ROTO.version + 1
