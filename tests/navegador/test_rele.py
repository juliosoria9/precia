"""Relé SOCKS5 local de Chromium, contra un proxy y una tienda falsos en 127.0.0.1.

Chromium solo habla con el relé (`socks5://127.0.0.1:N`). El relé valida cada conexión con el mismo
`Validador` que el descargador y conecta él: directo (a la IP validada) o por el proxy residencial,
al que añade `Proxy-Authorization` sin que Chromium conozca las credenciales.
"""

import asyncio
import socket
import time
from typing import Any

import pytest

from precia.navegador.proxy import Proxy
from precia.navegador.rele import MAX_CONEXIONES_PENDIENTES, ReleProxy
from precia.navegador.tipos import URLNoPermitida
from tests.navegador.falsos import validar_con_dns_publico
from tests.navegador.servidores_proxy import CUERPO_TIENDA, proxy_falso, socks, tienda

USUARIO = "usuario-rele-prueba"
SECRETO = "contrasena-rele-prueba_country-ES"
IP_TIENDA = "93.184.215.14"
GET = b"GET /ficha HTTP/1.1\r\nHost: tienda.example\r\nConnection: close\r\n\r\n"


def _proxy(puerto: int) -> Proxy:
    return Proxy("127.0.0.1", puerto, USUARIO, SECRETO, nombre="Proxy de prueba")


def _siempre_remoto(_: object) -> bool:
    return True


def _loopback(ip: Any) -> bool:
    return bool(ip.is_loopback)


class ResolverA:
    """`getaddrinfo` de mentira: todo nombre resuelve a `ip`. Apunta lo que se le pregunta."""

    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.consultas: list[str] = []

    def __call__(self, host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        self.consultas.append(host)
        familia = socket.AF_INET6 if ":" in self.ip else socket.AF_INET
        return [(familia, socket.SOCK_STREAM, 6, "", (self.ip, puerto))]


def resolver_a(ip: str) -> ResolverA:
    return ResolverA(ip)


class ValidadorApuntado:
    """Validador que apunta cada URL: deja pasar (tal cual) o rechaza todo."""

    def __init__(self, permitir: bool = True) -> None:
        self.permitir = permitir
        self.urls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.urls.append(url)
        if not self.permitir:
            raise URLNoPermitida("Rechazada en el test.")
        return url


def rep(respuesta: bytes) -> int | None:
    """Código de respuesta SOCKS5, `0xFF` si no aceptó el método o `None` si cerró sin contestar."""
    if len(respuesta) >= 2 and respuesta[:2] == b"\x05\xff":
        return 0xFF
    if len(respuesta) >= 4 and respuesta[:2] == b"\x05\x00" and respuesta[2] == 5:
        return respuesta[3]
    return None


async def _pedir(rele: ReleProxy, peticion: bytes, despues: bytes = b"") -> bytes:
    """Manda el saludo y la petición; si el relé abre el túnel, `despues` y lee hasta EOF."""
    servidor = await rele.abrir()
    assert servidor.startswith("socks5://127.0.0.1:")
    puerto = int(servidor.rsplit(":", 1)[1])
    lector, escritor = await asyncio.open_connection("127.0.0.1", puerto)
    try:
        escritor.write(peticion)
        await escritor.drain()
        respuesta = b""
        # Saludo (2 bytes) y respuesta a CONNECT con una IPv4 (10 bytes).
        while len(respuesta) < 12:
            trozo = await asyncio.wait_for(lector.read(12 - len(respuesta)), 5)
            if not trozo:
                return respuesta
            respuesta += trozo
        if despues and rep(respuesta) == 0:
            escritor.write(despues)
            await escritor.drain()
            # Hasta EOF: la respuesta de la tienda puede llegar en varios trozos (la GET lleva
            # `Connection: close` y el relé cierra cuando la tienda cierra).
            while trozo := await asyncio.wait_for(lector.read(65536), 5):
                respuesta += trozo
        return respuesta
    finally:
        escritor.close()
        await rele.cerrar()


def pedir(rele: ReleProxy, peticion: bytes, despues: bytes = b"") -> bytes:
    return asyncio.run(_pedir(rele, peticion, despues))


# --- Encadenado al proxy residencial -------------------------------------------------------------


def test_socks_se_reenvia_al_proxy_con_las_credenciales() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=_proxy(1).cabecera_autorizacion) as (
            puerto,
            estado,
        ),
    ):
        rele = ReleProxy(
            _proxy(puerto), validador=validar_con_dns_publico, proxy_remoto=_siempre_remoto
        )
        respuesta = pedir(rele, socks("tienda.example", 80), GET)
    assert rep(respuesta) == 0
    assert CUERPO_TIENDA.encode() in respuesta
    assert estado.connects == ["CONNECT tienda.example:80 HTTP/1.1"]
    assert estado.autorizaciones == [_proxy(1).cabecera_autorizacion]
    # La tienda no ve nada del proxy.
    assert not any(k.startswith("proxy-") for k in servidor.peticiones[0][1])


def test_respuesta_sin_credenciales_aunque_el_proxy_rechace() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada="Basic otra") as (puerto, _),
    ):
        rele = ReleProxy(
            _proxy(puerto), validador=validar_con_dns_publico, proxy_remoto=_siempre_remoto
        )
        respuesta = pedir(rele, socks("tienda.example", 443))
    assert rep(respuesta) not in (0, None)
    assert SECRETO.encode() not in respuesta and USUARIO.encode() not in respuesta
    assert servidor.peticiones == []


def test_proxy_local_conecta_a_la_ip_validada() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=_proxy(1).cabecera_autorizacion) as (
            puerto,
            estado,
        ),
    ):
        rele = ReleProxy(
            _proxy(puerto), validador=validar_con_dns_publico, resolver=resolver_a(IP_TIENDA)
        )
        respuesta = pedir(rele, socks("tienda.example", 80), GET)
    assert rep(respuesta) == 0
    assert estado.connects == [f"CONNECT {IP_TIENDA}:80 HTTP/1.1"]


@pytest.mark.parametrize("ip", ["192.168.1.1", "127.0.0.1", "169.254.169.254"])
def test_proxy_local_no_lleva_a_la_red_interna(ip: str) -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=_proxy(1).cabecera_autorizacion) as (
            puerto,
            estado,
        ),
    ):
        rele = ReleProxy(_proxy(puerto), validador=validar_con_dns_publico, resolver=resolver_a(ip))
        respuesta = pedir(rele, socks("rebind.example", 80))
    assert rep(respuesta) == 0x02
    assert estado.connects == []


def test_proxy_remoto_tambien_valida_el_destino() -> None:
    with proxy_falso(1, esperada=None) as (puerto, estado):
        validador = ValidadorApuntado(permitir=False)
        rele = ReleProxy(_proxy(puerto), validador=validador, proxy_remoto=_siempre_remoto)
        respuesta = pedir(rele, socks("interno.example", 443))
    assert rep(respuesta) == 0x02
    assert validador.urls == ["https://interno.example/"]
    assert estado.connects == []


def test_proxy_inalcanzable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as libre:
        puerto = libre.getsockname()[1]
    rele = ReleProxy(
        _proxy(puerto), validador=validar_con_dns_publico, proxy_remoto=_siempre_remoto
    )
    respuesta = pedir(rele, socks("tienda.example", 80))
    assert rep(respuesta) in (0x01, 0x05)


# --- Solo SOCKS5 CONNECT sin autenticación ------------------------------------------------------

_SOCKS4 = b"\x04\x01\x00\x50" + socket.inet_aton("93.184.215.14") + b"\x00"


@pytest.mark.parametrize(
    ("peticion", "esperado"),
    [
        (b"CONNECT tienda.example:80 HTTP/1.1\r\nHost: tienda.example:80\r\n\r\n", None),
        (b"GET http://tienda.example/ HTTP/1.1\r\nHost: tienda.example\r\n\r\n", None),
        (b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n", None),
        (_SOCKS4, None),
        (socks("tienda.example", 80, metodos=b"\x02"), 0xFF),
        (b"\x05\x00", 0xFF),
        (socks("tienda.example", 80, cmd=2), 0x07),
        (socks("tienda.example", 80, cmd=3), 0x07),
        (socks(b"\x00\x00\x00\x00", 80, atyp=5), 0x08),
        (socks(b"", 80), 0x02),
        (socks("tienda.example", 80)[:8], None),
        (b"basura\r\n\r\n", None),
    ],
    ids=[
        "connect-http-texto",
        "get-absoluto",
        "post-de-una-web",
        "socks4",
        "sin-metodo-0",
        "sin-metodos",
        "bind",
        "udp",
        "atyp-desconocido",
        "dominio-vacio",
        "truncada",
        "basura",
    ],
)
def test_solo_acepta_socks5_connect_bien_formado(peticion: bytes, esperado: int | None) -> None:
    with proxy_falso(1, esperada=None) as (puerto, estado):
        validador = ValidadorApuntado()
        # Con el plazo corto, la petición truncada se corta enseguida.
        rele = ReleProxy(
            _proxy(puerto), validador=validador, proxy_remoto=_siempre_remoto, timeout=1
        )
        respuesta = pedir(rele, peticion)
    assert rep(respuesta) == esperado
    assert validador.urls == []
    assert estado.connects == []


def test_cliente_que_no_saluda_se_corta() -> None:
    async def probar() -> bytes:
        rele = ReleProxy(None, validador=ValidadorApuntado(), timeout=0.3)
        servidor = await rele.abrir()
        lector, escritor = await asyncio.open_connection(
            "127.0.0.1", int(servidor.rsplit(":", 1)[1])
        )
        try:
            return await asyncio.wait_for(lector.read(100), 5)
        finally:
            escritor.close()
            await rele.cerrar()

    inicio = time.monotonic()
    assert asyncio.run(probar()) == b""
    assert time.monotonic() - inicio < 3


# --- Modo directo (sin proxy) -------------------------------------------------------------------


def _directo(validador: Any, resolver: ResolverA, puerto: int) -> ReleProxy:
    """Sin proxy, contra la tienda local (en un puerto que en producción no se admite)."""
    return ReleProxy(
        None,
        validador=validador,
        resolver=resolver,
        ip_permitida=_loopback,
        puertos=frozenset({puerto}),
    )


def test_directo_conecta_a_la_ip_validada() -> None:
    with tienda() as servidor:
        puerto = servidor.server_address[1]
        resolver = resolver_a("127.0.0.1")
        validador = ValidadorApuntado()
        rele = _directo(validador, resolver, puerto)
        respuesta = pedir(rele, socks("tienda.example", puerto), GET)
    assert rep(respuesta) == 0
    assert CUERPO_TIENDA.encode() in respuesta
    assert validador.urls == [f"http://tienda.example:{puerto}/"]
    assert resolver.consultas == ["tienda.example"]
    assert servidor.peticiones[0][0] == "/ficha"


def test_directo_rechaza_lo_que_no_pasa_el_validador() -> None:
    with tienda() as servidor:
        puerto = servidor.server_address[1]
        resolver = resolver_a("127.0.0.1")
        rele = _directo(ValidadorApuntado(False), resolver, puerto)
        respuesta = pedir(rele, socks("interno.example", puerto), GET)
    assert rep(respuesta) == 0x02
    assert resolver.consultas == []
    assert servidor.peticiones == []


def test_directo_contra_rebinding() -> None:
    # El validador dio por buena la URL, pero al conectar el nombre resuelve a una IP interna.
    resolver = resolver_a("10.0.0.1")
    rele = ReleProxy(None, validador=ValidadorApuntado(), resolver=resolver)
    respuesta = pedir(rele, socks("rebind.example", 443))
    assert rep(respuesta) == 0x02
    assert resolver.consultas == ["rebind.example"]


def test_directo_tienda_que_rechaza_la_conexion() -> None:
    with socket.create_server(("127.0.0.1", 0)) as libre:
        puerto = libre.getsockname()[1]
    rele = _directo(ValidadorApuntado(), resolver_a("127.0.0.1"), puerto)
    respuesta = pedir(rele, socks("tienda.example", puerto))
    assert rep(respuesta) == 0x05


@pytest.mark.parametrize("puerto", [8080, 22, 6379, 0])
def test_solo_puertos_80_y_443(puerto: int) -> None:
    # Comprobación propia del relé: aunque el validador lo dejara pasar todo.
    validador = ValidadorApuntado()
    resolver = resolver_a("127.0.0.1")
    rele = ReleProxy(None, validador=validador, resolver=resolver, ip_permitida=_loopback)
    respuesta = pedir(rele, socks("tienda.example", puerto))
    assert rep(respuesta) == 0x02
    assert validador.urls == [] and resolver.consultas == []


@pytest.mark.parametrize(
    ("puerto", "url"), [(80, "http://tienda.example/"), (443, "https://tienda.example/")]
)
def test_el_puerto_va_en_el_esquema_de_lo_que_se_valida(puerto: int, url: str) -> None:
    validador = ValidadorApuntado(permitir=False)
    pedir(ReleProxy(None, validador=validador), socks("tienda.example", puerto))
    assert validador.urls == [url]


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "169.254.169.254"])
def test_ip_literal_en_campo_de_dominio(host: str) -> None:
    resolver = resolver_a("127.0.0.1")
    rele = ReleProxy(
        None, validador=validar_con_dns_publico, resolver=resolver, ip_permitida=_loopback
    )
    respuesta = pedir(rele, socks(host, 80))
    assert rep(respuesta) == 0x02
    assert resolver.consultas == []


@pytest.mark.parametrize(
    ("host", "atyp"),
    [("127.0.0.1", 1), ("10.0.0.7", 1), ("::1", 4), ("::ffff:10.0.0.1", 4), ("fe80::1", 4)],
)
def test_ip_interna_por_direccion(host: str, atyp: int) -> None:
    resolver = resolver_a("127.0.0.1")
    rele = ReleProxy(
        None, validador=validar_con_dns_publico, resolver=resolver, ip_permitida=_loopback
    )
    respuesta = pedir(rele, socks(host, 443, atyp=atyp))
    assert rep(respuesta) == 0x02
    assert resolver.consultas == []


def test_ipv6_publica_por_direccion_se_valida_entre_corchetes() -> None:
    validador = ValidadorApuntado(permitir=False)
    pedir(ReleProxy(None, validador=validador), socks("2001:db8::1", 443, atyp=4))
    assert validador.urls == ["https://[2001:db8::1]/"]


@pytest.mark.parametrize(
    "host",
    [
        "tienda.example#@127.0.0.1",
        "tienda.example?",
        "tienda.example/x",
        "tienda example",
        "tiénda.example",
        "[::1]",
        "tienda.example:80",
        "usu@tienda.example",
        "a" * 254,
    ],
)
def test_host_con_caracteres_raros_se_rechaza(host: str) -> None:
    # `validar_url("http://tienda.com#@127.0.0.1:80/")` da «http://tienda.com/»: si el relé
    # validara eso y luego conectara al host pedido, iría a 127.0.0.1.
    validador = ValidadorApuntado()
    resolver = resolver_a("127.0.0.1")
    rele = ReleProxy(None, validador=validador, resolver=resolver, ip_permitida=_loopback)
    respuesta = pedir(rele, socks(host.encode("utf-8"), 80))
    assert rep(respuesta) == 0x02
    assert validador.urls == [] and resolver.consultas == []


def test_conecta_al_host_que_valido_el_validador() -> None:
    with (
        tienda() as servidor,
        proxy_falso(servidor.server_address[1], esperada=_proxy(1).cabecera_autorizacion) as (
            puerto,
            estado,
        ),
    ):
        # Proxy local: se resuelve el nombre normalizado por el validador.
        resolver = resolver_a(IP_TIENDA)
        rele = ReleProxy(_proxy(puerto), validador=validar_con_dns_publico, resolver=resolver)
        assert rep(pedir(rele, socks("TIENDA.Example.", 80))) == 0
        assert resolver.consultas == ["tienda.example"]
        # Proxy remoto: el CONNECT lleva el nombre normalizado.
        rele = ReleProxy(
            _proxy(puerto), validador=validar_con_dns_publico, proxy_remoto=_siempre_remoto
        )
        assert rep(pedir(rele, socks("TIENDA.Example.", 80))) == 0
    assert estado.connects[-1] == "CONNECT tienda.example:80 HTTP/1.1"


def test_directo_conecta_al_host_devuelto_por_el_validador() -> None:
    async def a_la_tienda(url: str) -> str:
        return url.replace("otra.example", "tienda.example")

    resolver = resolver_a("10.0.0.1")  # rechazada: solo interesa a quién se resuelve
    pedir(ReleProxy(None, validador=a_la_tienda, resolver=resolver), socks("otra.example", 80))
    assert resolver.consultas == ["tienda.example"]


# --- Varios -------------------------------------------------------------------------------------


def test_repr_sin_credenciales() -> None:
    texto = repr(ReleProxy(_proxy(1000)))
    assert SECRETO not in texto and USUARIO not in texto
    assert repr(ReleProxy()) == "ReleProxy(None)"


def test_cerrar_no_espera_para_siempre_a_las_conexiones(monkeypatch: pytest.MonkeyPatch) -> None:
    # Al cerrar Chromium, alguna conexión con el relé acaba en RST. En Windows, asyncio (3.13)
    # lanza ConnectionResetError en `_call_connection_lost` y nunca da esa conexión por cerrada,
    # así que `Server.wait_closed()` no vuelve (visto con Chromium real). Se simula aquí.
    async def nunca(_: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio.Server, "wait_closed", nunca)

    async def probar() -> None:
        rele = ReleProxy(None, validador=ValidadorApuntado())
        await rele.abrir()
        await asyncio.wait_for(rele.cerrar(), 5)

    asyncio.run(probar())


# --- Conexiones que cuelgan (una página hostil no congela la aplicación) ------------------------


def _puerto_cerrado() -> int:
    with socket.create_server(("127.0.0.1", 0)) as libre:
        return int(libre.getsockname()[1])


class ResolverLento(ResolverA):
    """Resuelve a `ip` tras `espera` segundos, como un DNS que tarda (bloquea el hilo)."""

    def __init__(self, ip: str, espera: float) -> None:
        super().__init__(ip)
        self.espera = espera

    def __call__(self, host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        time.sleep(self.espera)
        return super().__call__(host, puerto)


class ResolverVarias:
    """Todo nombre resuelve a varias IP públicas (que en el test nunca contestan)."""

    def __call__(self, host: str, puerto: int, **_: object) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"93.184.216.{i}", puerto))
            for i in range(1, 6)
        ]


class ConexionColgada:
    """`asyncio.open_connection` de mentira contra IP que descartan el SYN: nunca vuelve."""

    def __init__(self) -> None:
        self.intentos: list[str] = []
        self.a_la_vez = 0
        self.max_a_la_vez = 0

    async def __call__(self, host: str, puerto: int, **_: object) -> Any:
        self.intentos.append(host)
        self.a_la_vez += 1
        self.max_a_la_vez = max(self.max_a_la_vez, self.a_la_vez)
        try:
            await asyncio.Event().wait()
        finally:
            self.a_la_vez -= 1


async def _cliente(puerto: int, n: int, destino: int = 80) -> bytes:
    lector, escritor = await asyncio.open_connection("127.0.0.1", puerto)
    try:
        escritor.write(socks(f"h{n}.atacante.example", destino))
        await escritor.drain()
        respuesta = b""
        while len(respuesta) < 12:
            trozo = await lector.read(12 - len(respuesta))
            if not trozo:
                break
            respuesta += trozo
        return respuesta
    finally:
        escritor.close()


def test_dns_lento_no_ocupa_los_hilos_de_la_aplicacion() -> None:
    # Sin proxy, cada conexión resolvía en el executor por defecto, el mismo de las descargas, la
    # caché, el almacén y la IA: 32 conexiones colgadas lo llenaban y todo `to_thread` esperaba.
    puerto = _puerto_cerrado()

    async def probar() -> float:
        rele = ReleProxy(
            None,
            validador=ValidadorApuntado(),
            resolver=ResolverLento("127.0.0.1", 0.8),
            ip_permitida=_loopback,
            puertos=frozenset({puerto}),
        )
        servidor = await rele.abrir()
        puerto_rele = int(servidor.rsplit(":", 1)[1])
        clientes = [asyncio.create_task(_cliente(puerto_rele, n, puerto)) for n in range(32)]
        try:
            await asyncio.sleep(0.2)
            inicio = time.monotonic()
            await asyncio.to_thread(lambda: None)
            return time.monotonic() - inicio
        finally:
            await rele.cerrar()
            for cliente in clientes:
                cliente.cancel()
            await asyncio.gather(*clientes, return_exceptions=True)

    assert asyncio.run(probar()) < 0.3


def test_directo_el_plazo_es_total_para_todas_las_ip() -> None:
    # Antes cada IP tenía el plazo entero: 5 IP que descartan el SYN eran 5 plazos por conexión.
    colgada = ConexionColgada()

    async def probar() -> bytes:
        rele = ReleProxy(
            None,
            validador=ValidadorApuntado(),
            resolver=ResolverVarias(),
            timeout=0.4,
            abrir_conexion=colgada,
        )
        servidor = await rele.abrir()
        try:
            return await asyncio.wait_for(_cliente(int(servidor.rsplit(":", 1)[1]), 0), 5)
        finally:
            await rele.cerrar()

    inicio = time.monotonic()
    respuesta = asyncio.run(probar())
    assert rep(respuesta) == 0x05
    assert time.monotonic() - inicio < 1.0
    assert colgada.intentos and colgada.intentos[0] == "93.184.216.1"
    assert colgada.a_la_vez == 0


def test_directo_tope_de_conexiones_pendientes_a_la_vez() -> None:
    colgada = ConexionColgada()

    async def probar() -> None:
        rele = ReleProxy(
            None,
            validador=ValidadorApuntado(),
            resolver=ResolverVarias(),
            timeout=5,
            abrir_conexion=colgada,
        )
        servidor = await rele.abrir()
        puerto_rele = int(servidor.rsplit(":", 1)[1])
        clientes = [asyncio.create_task(_cliente(puerto_rele, n)) for n in range(40)]
        try:
            await asyncio.sleep(0.5)
            assert colgada.max_a_la_vez == MAX_CONEXIONES_PENDIENTES
        finally:
            await rele.cerrar()
            for cliente in clientes:
                cliente.cancel()
            await asyncio.gather(*clientes, return_exceptions=True)
        # Al cerrar el relé no queda ningún intento de conexión vivo.
        assert colgada.a_la_vez == 0

    asyncio.run(probar())
