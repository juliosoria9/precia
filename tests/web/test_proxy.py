"""Sección «Proxy» de Ajustes: solo lectura, sin credenciales y con el uso de la ejecución."""

from dataclasses import dataclass, field

from precia.nucleo.fuente_demo import FuenteDemo
from precia.nucleo.modelos import EstadoProxy
from precia.web.app import crear_app
from tests.asgi import ClienteASGI


@dataclass
class FuenteConProxy(FuenteDemo):
    estado: EstadoProxy = field(default_factory=EstadoProxy)

    def estado_proxy(self) -> EstadoProxy:
        return self.estado


def ajustes(estado: EstadoProxy) -> str:
    respuesta = ClienteASGI(crear_app(FuenteConProxy(estado=estado))).get("/ajustes")
    assert respuesta.estado == 200
    return respuesta.texto


def test_sin_proxy_dice_como_configurarlo() -> None:
    texto = ajustes(EstadoProxy())
    assert "<h2>Proxy</h2>" in texto
    assert "Sin proxy" in texto
    for variable in ("PRECIA_PROXY", "EVOMI_API_KEY", "PRECIA_PROXY_MODO", ".env"):
        assert variable in texto


def test_proxy_activo_con_su_uso() -> None:
    texto = ajustes(
        EstadoProxy(
            configurado=True,
            activo=True,
            nombre="Evomi core residential (ES)",
            pais="ES",
            modo="bloqueo",
            peticiones=12,
            bytes=3 * 1024 * 1024 + 300 * 1024,
            dominios=("b-parts.example", "ecooparts.example"),
        )
    )
    assert "Activo" in texto
    assert "Evomi core residential (ES)" in texto
    assert "12" in texto and "3,29 MB" in texto
    assert "b-parts.example" in texto and "ecooparts.example" in texto
    assert "solo si la tienda bloquea" in texto


def test_pendiente_hasta_la_primera_busqueda() -> None:
    texto = ajustes(EstadoProxy(configurado=True, nombre="Evomi", pais="ES", pendiente=True))
    assert "primera búsqueda" in texto


def test_fallo_de_la_api_muestra_el_motivo_escapado() -> None:
    texto = ajustes(
        EstadoProxy(
            configurado=True,
            nombre="Evomi",
            error="La API de Evomi rechazó la clave (HTTP 401): <revisa> EVOMI_API_KEY.",
        )
    )
    assert "No se usa" in texto
    assert "HTTP 401" in texto
    assert "<revisa>" not in texto and "&lt;revisa&gt;" in texto


def test_la_seccion_no_tiene_campos_de_formulario() -> None:
    texto = ajustes(EstadoProxy(configurado=True, activo=True, nombre="proxy propio"))
    seccion = texto[texto.index("<h2>Proxy</h2>") :]
    seccion = seccion[: seccion.index("</section>")]
    assert "<input" not in seccion and "<select" not in seccion
