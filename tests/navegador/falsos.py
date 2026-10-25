"""Dobles de prueba del paquete `navegador`: DNS, reloj y descargadores sin red."""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta

from precia.navegador.tipos import ErrorDescarga, Pagina, URLNoPermitida
from precia.navegador.url import validar_url

IP_PUBLICA = "93.184.215.14"
INSTANTE = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


class ResolverFalso:
    """Resolver DNS de mentira: los tests nunca preguntan a un DNS real."""

    def __init__(self, tabla: Mapping[str, Iterable[str]]) -> None:
        self._tabla = {host: list(ips) for host, ips in tabla.items()}
        self.consultas: list[str] = []

    async def __call__(self, host: str) -> list[str]:
        self.consultas.append(host)
        if host not in self._tabla:
            raise URLNoPermitida(f"No se puede resolver {host}")
        return self._tabla[host]


async def _todo_publico(host: str) -> list[str]:
    return [IP_PUBLICA]


async def validar_con_dns_publico(url: str) -> str:
    """`validar_url` con las reglas reales y un DNS de mentira en el que todo nombre es público."""
    return await validar_url(url, resolver=_todo_publico)


class Reloj:
    """Reloj manual: `dormir` adelanta el tiempo en lugar de esperar."""

    def __init__(self, inicio: float = 1000.0) -> None:
        self.ahora = inicio
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.ahora

    async def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.ahora += max(segundos, 0.0)


class RelojFecha:
    def __init__(self, inicio: datetime = INSTANTE) -> None:
        self.ahora = inicio

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora += timedelta(seconds=segundos)


def pagina(
    url: str,
    html: str = "<html><body>ok</body></html>",
    *,
    estado: int = 200,
    tipo: str = "text/html",
    url_final: str | None = None,
) -> Pagina:
    return Pagina(
        url=url,
        url_final=url_final or url,
        estado=estado,
        html=html,
        tipo_contenido=tipo,
        obtenida=INSTANTE,
    )


class DescargadorFalso:
    """Devuelve páginas preparadas por URL y apunta lo que se le pide."""

    def __init__(self, respuestas: Mapping[str, Pagina | Exception] | None = None) -> None:
        self.respuestas: dict[str, Pagina | Exception] = dict(respuestas or {})
        self.pedidas: list[str] = []
        self.cerrado = False

    async def descargar(self, url: str) -> Pagina:
        self.pedidas.append(url)
        respuesta = self.respuestas.get(url)
        if respuesta is None:
            raise ErrorDescarga(f"Sin respuesta preparada para {url}")
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    async def cerrar(self) -> None:
        self.cerrado = True


class DescargadorGrandeFalso(DescargadorFalso):
    """Como `DescargadorFalso`, con `descargar_grande` (sitemaps): apunta el tope que se pide."""

    def __init__(self, respuestas: Mapping[str, Pagina | Exception] | None = None) -> None:
        super().__init__(respuestas)
        self.grandes: list[tuple[str, int]] = []

    async def descargar_grande(self, url: str, *, max_bytes: int) -> Pagina:
        self.grandes.append((url, max_bytes))
        return await self.descargar(url)
