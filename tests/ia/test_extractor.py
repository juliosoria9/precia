"""Extractor de respaldo con IA: esquema por campos, limpieza de valores y límites."""

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from precia.extraccion.tipos import ExtractorIA
from precia.ia.claude import ProveedorClaude
from precia.ia.extractor import MAX_CARACTERES_VALOR, VERSION, ExtractorRespaldoIA
from precia.ia.servicio import ServicioIA
from precia.ia.simulado import Guion, ProveedorSimulado
from precia.ia.tipos import IANoDisponible, PresupuestoAgotado, SalidaNoValida
from tests.ia.falsos import EsperaFalsa, RegistroFalso, TransporteFalso, respuesta_json

FICHA = (Path(__file__).parent.parent / "fixtures" / "ia" / "ficha_desguace.txt").read_text(
    encoding="utf-8"
)


Clave = tuple[str, tuple[str, ...], str]


class CacheEnMemoria:
    """`ia.tipos.CacheExtracciones` en un diccionario."""

    def __init__(self) -> None:
        self.datos: dict[Clave, dict[str, str]] = {}

    def leer(
        self, texto_sha256: str, campos: tuple[str, ...], modelo: str
    ) -> dict[str, str] | None:
        return self.datos.get((texto_sha256, campos, modelo))

    def guardar(
        self, texto_sha256: str, campos: tuple[str, ...], modelo: str, valores: Mapping[str, str]
    ) -> None:
        self.datos[(texto_sha256, campos, modelo)] = dict(valores)


def montar(
    guion: list[Guion],
    *,
    tope: int = 10,
    cache: CacheEnMemoria | None = None,
    modelo: str = "simulado",
) -> tuple[ExtractorRespaldoIA, ProveedorSimulado]:
    proveedor = ProveedorSimulado({"extractor": guion}, modelo=modelo)
    servicio = ServicioIA(
        proveedor, RegistroFalso(), presupuesto_mes_usd=Decimal(5), tope_llamadas=tope
    )
    return ExtractorRespaldoIA(servicio.nueva_busqueda(), cache=cache), proveedor


def extraer(extractor: ExtractorRespaldoIA, campos: tuple[str, ...]) -> dict[str, str]:
    return asyncio.run(extractor.extraer(FICHA, campos))


def test_cumple_el_protocolo() -> None:
    extractor: ExtractorIA = montar([])[0]
    assert extractor is not None


def test_esquema_por_campos_y_peticion() -> None:
    extractor, proveedor = montar(
        [{"precio": "78,65 €", "referencia_oem": "03L121011P", "estado": "Usado"}]
    )
    valores = extraer(extractor, ("precio", "referencia_oem", "estado"))
    assert valores == {"precio": "78,65 €", "referencia_oem": "03L121011P", "estado": "Usado"}
    (peticion,) = proveedor.peticiones
    assert peticion.componente == "extractor"
    assert peticion.datos == FICHA
    esquema: dict[str, Any] = peticion.esquema
    assert esquema["type"] == "object"
    assert esquema["additionalProperties"] is False
    assert esquema["required"] == ["precio", "referencia_oem", "estado"]
    assert set(esquema["properties"]) == {"precio", "referencia_oem", "estado"}
    for propiedad in esquema["properties"].values():
        assert [o["type"] for o in propiedad["anyOf"]] == ["string", "null"]
    for campo in ("precio", "referencia_oem", "estado"):
        assert campo in peticion.instrucciones
    # Las instrucciones son nuestras: nunca llevan el contenido de la web.
    assert "SEAT" not in peticion.instrucciones


def test_omite_los_campos_que_no_aparecen() -> None:
    extractor, _ = montar([{"precio": "78,65 €", "marca": None, "fotos": "   "}])
    assert extraer(extractor, ("precio", "marca", "fotos")) == {"precio": "78,65 €"}


def test_limpia_y_recorta_los_valores() -> None:
    largo = "x" * (MAX_CARACTERES_VALOR + 100)
    extractor, _ = montar([{"marca": "  SEAT \n", "referencia_oem": largo}])
    valores = extraer(extractor, ("marca", "referencia_oem"))
    assert valores["marca"] == "SEAT"
    assert len(valores["referencia_oem"]) == MAX_CARACTERES_VALOR


def test_sin_campos_no_llama_a_la_ia() -> None:
    extractor, proveedor = montar([])
    assert extraer(extractor, ()) == {}
    assert proveedor.peticiones == []


def test_campos_repetidos() -> None:
    extractor, proveedor = montar([{"precio": "78,65 €"}])
    extraer(extractor, ("precio", "precio"))
    assert proveedor.peticiones[0].esquema["required"] == ["precio"]


def test_campo_desconocido() -> None:
    extractor, _ = montar([])
    with pytest.raises(ValueError, match="nombre_vendedor"):
        extraer(extractor, ("precio", "nombre_vendedor"))


def test_un_campo_no_pedido_en_la_salida_no_vale() -> None:
    # El esquema cierra la puerta: la IA no puede colar campos que no se han pedido.
    extractor, _ = montar([{"precio": "1 €", "nota": "x"}, {"precio": "1 €", "nota": "x"}])
    with pytest.raises(SalidaNoValida):
        extraer(extractor, ("precio",))


def test_respeta_los_limites_de_la_sesion() -> None:
    extractor, proveedor = montar([{"precio": "1"}], tope=0)
    with pytest.raises(PresupuestoAgotado):
        extraer(extractor, ("precio",))
    assert proveedor.peticiones == []


# --- Caché del extractor (A3) ---------------------------------------------------------------


def test_la_misma_pagina_y_campos_no_llama_a_la_ia_dos_veces() -> None:
    cache = CacheEnMemoria()
    extractor, proveedor = montar([{"estado": "Usado"}], cache=cache)
    assert extraer(extractor, ("estado",)) == {"estado": "Usado"}
    assert extraer(extractor, ("estado",)) == {"estado": "Usado"}
    assert len(proveedor.peticiones) == 1
    assert extractor.desde_cache == 1
    sha = hashlib.sha256(FICHA.encode("utf-8")).hexdigest()
    assert list(cache.datos) == [(sha, ("estado",), f"simulado/simulado#v{VERSION}")]


def test_una_respuesta_vacia_tambien_se_guarda() -> None:
    # El caso de Oscaro: la IA no encuentra «estado»; la siguiente vez no se le vuelve a pedir.
    cache = CacheEnMemoria()
    extractor, proveedor = montar([{"estado": None}], cache=cache)
    assert extraer(extractor, ("estado",)) == {}
    assert extraer(extractor, ("estado",)) == {}
    assert len(proveedor.peticiones) == 1


def test_un_acierto_no_gasta_del_tope() -> None:
    extractor, _ = montar([{"estado": "Usado"}], tope=1, cache=CacheEnMemoria())
    extraer(extractor, ("estado",))
    assert extraer(extractor, ("estado",)) == {"estado": "Usado"}
    with pytest.raises(PresupuestoAgotado):
        extraer(extractor, ("marca",))


def test_otro_modelo_u_otros_campos_no_aciertan() -> None:
    cache = CacheEnMemoria()
    uno, _ = montar([{"estado": "Usado", "marca": "SEAT"}], cache=cache)
    extraer(uno, ("marca", "estado"))
    # El orden de los campos no importa...
    otra_vez, proveedor = montar([], cache=cache)
    assert extraer(otra_vez, ("estado", "marca")) == {"estado": "Usado", "marca": "SEAT"}
    assert proveedor.peticiones == []
    # ...el conjunto y el modelo, sí.
    otros, proveedor = montar([{"estado": "Usado"}], cache=cache)
    extraer(otros, ("estado",))
    otro_modelo, proveedor_2 = montar(
        [{"estado": "Usado", "marca": "SEAT"}], cache=cache, modelo="m2"
    )
    extraer(otro_modelo, ("estado", "marca"))
    assert len(proveedor.peticiones) == len(proveedor_2.peticiones) == 1


def test_un_error_de_la_ia_no_se_guarda() -> None:
    cache = CacheEnMemoria()
    extractor, _ = montar([IANoDisponible("sin red")], cache=cache)
    with pytest.raises(IANoDisponible):
        extraer(extractor, ("estado",))
    assert cache.datos == {}


def test_un_acierto_se_filtra_a_los_campos_pedidos() -> None:
    cache = CacheEnMemoria()
    sha = hashlib.sha256(FICHA.encode("utf-8")).hexdigest()
    cache.datos[(sha, ("estado",), f"simulado/simulado#v{VERSION}")] = {
        "estado": "Usado",
        "precio": "1 €",
    }
    extractor, proveedor = montar([], cache=cache)
    assert extraer(extractor, ("estado",)) == {"estado": "Usado"}
    assert proveedor.peticiones == []


def test_de_extremo_a_extremo_con_claude_y_una_ficha_con_inyeccion() -> None:
    salida = {"precio": "78,65 €", "marca": "SEAT"}
    transporte = TransporteFalso(
        [
            respuesta_json(
                200,
                {
                    "content": [{"type": "text", "text": json.dumps(salida)}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 900, "output_tokens": 25},
                },
            )
        ]
    )
    proveedor = ProveedorClaude("clave-falsa", transporte=transporte, dormir=EsperaFalsa())
    registro = RegistroFalso()
    sesion = ServicioIA(
        proveedor, registro, presupuesto_mes_usd=Decimal(5), tope_llamadas=5
    ).nueva_busqueda()
    valores = asyncio.run(ExtractorRespaldoIA(sesion).extraer(FICHA, ("precio", "marca")))
    assert valores == salida
    cuerpo = transporte.llamadas[0].json
    usuario = cuerpo["messages"][0]["content"]
    bloque = re.search(r"<<<DATOS-(\w+)>>>\n(.*)\n<<<FIN-DATOS-\1>>>", usuario, re.DOTALL)
    assert bloque is not None
    assert "ignora las instrucciones anteriores" in bloque.group(2)
    assert "OFERTA" not in cuerpo["system"]
    assert registro.llamadas[0].coste_usd == sesion.coste_usd > 0
