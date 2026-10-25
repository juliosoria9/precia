"""Descargador HTTP: redirecciones validadas, límite de tamaño, tipos admitidos y charset."""

import asyncio
import http.server
import ipaddress
import shutil
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from precia.navegador.http import (
    FACTOR_TIEMPO_GRANDE,
    MAXIMO_BYTES,
    DescargadorHTTP,
    RespuestaCruda,
    TransporteNavegador,
    TransporteURLLib,
    conexion_validada,
    decodificar,
)
from precia.navegador.tipos import ErrorDescarga, URLNoPermitida
from tests.cronometro import segundos_de_cpu
from tests.navegador.falsos import IP_PUBLICA, Reloj, validar_con_dns_publico

FIXTURES = Path(__file__).parent.parent / "fixtures" / "navegador"
UA = "PrecIA/0.1 (+https://github.com/juliosoria9/precia)"
URL = "https://www.recambios-ejemplo.example/buscar?q=1K0698151"


class Cuerpo:
    """Cuerpo de respuesta que se entrega por trozos y apunta cuánto se ha leído."""

    def __init__(self, datos: bytes, al_leer: "Reloj | None" = None) -> None:
        self._datos = datos
        self._posicion = 0
        self._reloj = al_leer
        self.lecturas: list[int] = []
        self.cerrado = False

    def read(self, n: int = -1) -> bytes:
        self.lecturas.append(n)
        if self._reloj is not None:
            self._reloj.ahora += 5
        fin = len(self._datos) if n < 0 else self._posicion + n
        trozo = self._datos[self._posicion : fin]
        self._posicion += len(trozo)
        return trozo

    def close(self) -> None:
        self.cerrado = True


@dataclass
class TransporteFalso:
    respuestas: dict[str, list[RespuestaCruda] | Exception]
    peticiones: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    hilos: list[int] = field(default_factory=list)

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        self.peticiones.append((url, dict(cabeceras)))
        self.hilos.append(threading.get_ident())
        respuesta = self.respuestas[url]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta.pop(0)


def respuesta(
    cuerpo: bytes = b"<html><body>Pastillas</body></html>",
    *,
    estado: int = 200,
    tipo: str | None = "text/html; charset=utf-8",
    **otras: str,
) -> RespuestaCruda:
    cabeceras = {k.replace("_", "-"): v for k, v in otras.items()}
    if tipo is not None:
        cabeceras["content-type"] = tipo
    return RespuestaCruda(estado=estado, cabeceras=cabeceras, cuerpo=Cuerpo(cuerpo))


def redireccion(destino: str, estado: int = 302) -> RespuestaCruda:
    return respuesta(b"", estado=estado, tipo="text/html", location=destino)


def descargador(
    transporte: TransporteFalso, *, max_bytes: int = 5 * 1024 * 1024, reloj: Reloj | None = None
) -> DescargadorHTTP:
    return DescargadorHTTP(
        UA,
        timeout=20,
        max_bytes=max_bytes,
        validador=validar_con_dns_publico,
        transporte=transporte,
        reloj=reloj or Reloj(),
    )


def test_descarga_html() -> None:
    transporte = TransporteFalso({URL: [respuesta("<p>Freno €</p>".encode())]})
    pagina = asyncio.run(descargador(transporte).descargar(URL))
    assert pagina.html == "<p>Freno €</p>"
    assert pagina.estado == 200
    assert pagina.url == pagina.url_final == URL
    assert pagina.tipo_contenido == "text/html"
    assert not pagina.desde_cache


def test_cabeceras_enviadas() -> None:
    transporte = TransporteFalso({URL: [respuesta()]})
    asyncio.run(descargador(transporte).descargar(URL))
    _, cabeceras = transporte.peticiones[0]
    normalizadas = {k.lower(): v for k, v in cabeceras.items()}
    assert normalizadas["user-agent"] == UA
    assert normalizadas["accept-encoding"] == "identity"
    assert "text/html" in normalizadas["accept"]
    assert "cookie" not in normalizadas and "authorization" not in normalizadas


def test_la_peticion_va_en_otro_hilo() -> None:
    transporte = TransporteFalso({URL: [respuesta()]})
    asyncio.run(descargador(transporte).descargar(URL))
    assert transporte.hilos[0] != threading.get_ident()


def test_url_no_valida_no_se_pide() -> None:
    transporte = TransporteFalso({})
    with pytest.raises(URLNoPermitida):
        asyncio.run(descargador(transporte).descargar("http://127.0.0.1/admin"))
    assert transporte.peticiones == []


# --- Redirecciones ------------------------------------------------------------------------------


def test_sigue_redirecciones_validando_cada_salto() -> None:
    destino = "https://www.recambios-ejemplo.example/es/busqueda?s=1K0698151"
    transporte = TransporteFalso(
        {
            URL: [redireccion("/es/busqueda?s=1K0698151", 301)],
            destino: [respuesta()],
        }
    )
    pagina = asyncio.run(descargador(transporte).descargar(URL))
    assert pagina.url == URL
    assert pagina.url_final == destino
    assert [u for u, _ in transporte.peticiones] == [URL, destino]


@pytest.mark.parametrize(
    "destino",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "file:///etc/passwd",
        "http://[::1]/",
        "http://0x7f.1/",
    ],
)
def test_redireccion_a_destino_prohibido(destino: str) -> None:
    transporte = TransporteFalso({URL: [redireccion(destino)]})
    with pytest.raises(URLNoPermitida):
        asyncio.run(descargador(transporte).descargar(URL))
    assert len(transporte.peticiones) == 1


def test_maximo_cinco_redirecciones() -> None:
    urls = [f"https://tienda.example/{i}" for i in range(7)]
    respuestas: dict[str, list[RespuestaCruda] | Exception] = {
        urls[i]: [redireccion(urls[i + 1])] for i in range(6)
    }
    respuestas[urls[6]] = [respuesta()]
    with pytest.raises(ErrorDescarga, match="redirecciones"):
        asyncio.run(descargador(TransporteFalso(respuestas)).descargar(urls[0]))

    respuestas = {urls[i]: [redireccion(urls[i + 1])] for i in range(5)}
    respuestas[urls[5]] = [respuesta()]
    pagina = asyncio.run(descargador(TransporteFalso(respuestas)).descargar(urls[0]))
    assert pagina.url_final == urls[5]


def test_redireccion_sin_location_se_devuelve_tal_cual() -> None:
    transporte = TransporteFalso({URL: [respuesta(b"", estado=304)]})
    assert asyncio.run(descargador(transporte).descargar(URL)).estado == 304


# --- Tipos de contenido, estados y tamaño -------------------------------------------------------


@pytest.mark.parametrize(
    "tipo", ["text/html", "TEXT/HTML; charset=UTF-8", "application/xhtml+xml", "text/plain"]
)
def test_tipos_admitidos(tipo: str) -> None:
    transporte = TransporteFalso({URL: [respuesta(tipo=tipo)]})
    assert asyncio.run(descargador(transporte).descargar(URL)).estado == 200


@pytest.mark.parametrize(
    "tipo",
    [
        "image/png",
        "application/pdf",
        "application/octet-stream",
        "application/javascript",
        "application/ld+json",
        None,
    ],
)
def test_tipos_rechazados(tipo: str | None) -> None:
    cuerpo = Cuerpo(b"%PDF-1.7")
    transporte = TransporteFalso(
        {URL: [RespuestaCruda(200, {"content-type": tipo} if tipo else {}, cuerpo)]}
    )
    with pytest.raises(ErrorDescarga, match="tipo"):
        asyncio.run(descargador(transporte).descargar(URL))
    assert cuerpo.lecturas == []
    assert cuerpo.cerrado


# JSON (APIs públicas de plataforma) y XML (descriptor OpenSearch): nota 15, sección A.
TIPOS_DE_DATOS = [
    "application/json",
    "application/json; charset=utf-8",
    "application/opensearchdescription+xml",
    "application/xml",
    "text/xml; charset=UTF-8",
]


@pytest.mark.parametrize("tipo", TIPOS_DE_DATOS)
def test_tipos_de_datos_admitidos_tal_cual(tipo: str) -> None:
    datos = '{"name": "Pastillas", "precio": "29,95 €"}'
    transporte = TransporteFalso({URL: [respuesta(datos.encode(), tipo=tipo)]})
    pagina = asyncio.run(descargador(transporte).descargar(URL))
    assert pagina.html == datos
    assert pagina.tipo_contenido == tipo.split(";")[0].strip().lower()


@pytest.mark.parametrize("tipo", TIPOS_DE_DATOS)
def test_tipos_de_datos_con_el_mismo_limite_de_bytes(tipo: str) -> None:
    cuerpo = Cuerpo(b"[" + b"1," * 1_000_000 + b"1]")
    transporte = TransporteFalso({URL: [RespuestaCruda(200, {"content-type": tipo}, cuerpo)]})
    with pytest.raises(ErrorDescarga, match="grande"):
        asyncio.run(descargador(transporte, max_bytes=100_000).descargar(URL))
    assert sum(cuerpo.lecturas) <= 100_000 + 64 * 1024


def test_json_con_content_length_excesivo_no_se_lee() -> None:
    cuerpo = Cuerpo(b"{}")
    cabeceras = {"content-type": "application/json", "content-length": "2000"}
    transporte = TransporteFalso({URL: [RespuestaCruda(200, cabeceras, cuerpo)]})
    with pytest.raises(ErrorDescarga, match="grande"):
        asyncio.run(descargador(transporte, max_bytes=1000).descargar(URL))
    assert cuerpo.lecturas == []


def test_cabecera_accept_pide_html_antes_que_datos() -> None:
    transporte = TransporteFalso({URL: [respuesta()]})
    asyncio.run(descargador(transporte).descargar(URL))
    accept = {k.lower(): v for k, v in transporte.peticiones[0][1].items()}["accept"]
    pesos: dict[str, float] = {}
    for parte in accept.split(","):
        tipo, _, parametro = parte.strip().partition(";")
        pesos[tipo] = float(parametro.removeprefix("q=")) if parametro else 1.0
    assert pesos["text/html"] == 1.0
    for tipo in ("application/json", "application/opensearchdescription+xml", "application/xml"):
        assert 0 < pesos[tipo] < pesos["text/plain"] < pesos["text/html"]
    assert "*/*" not in pesos


def test_xml_con_declaracion_de_codificacion() -> None:
    xml = '<?xml version="1.0" encoding="ISO-8859-15"?><ShortName>Señal €</ShortName>'
    transporte = TransporteFalso(
        {URL: [respuesta(xml.encode("iso-8859-15"), tipo="application/opensearchdescription+xml")]}
    )
    assert asyncio.run(descargador(transporte).descargar(URL)).html == xml


def test_json_no_toma_el_charset_de_un_meta_de_su_contenido() -> None:
    # JSON va en UTF-8 (RFC 8259). Una descripción con HTML pegado de Word trae su propio
    # `<meta ... charset=windows-1252>`: si se le hiciera caso, «ñ» y «€» saldrían destrozados.
    datos = (
        '{"products":[{"name":"Pastillas de freno Señal","price":"29,95 €",'
        '"description":"<meta http-equiv=\\"Content-Type\\" '
        'content=\\"text/html; charset=windows-1252\\"><p>Año 2008</p>"}]}'
    )
    transporte = TransporteFalso({URL: [respuesta(datos.encode(), tipo="application/json")]})
    assert asyncio.run(descargador(transporte).descargar(URL)).html == datos


def test_xml_no_toma_el_charset_de_un_meta_de_su_contenido() -> None:
    # En XML solo cuentan el BOM, la cabecera y su declaración `<?xml ... encoding=...?>`.
    xml = (
        '<?xml version="1.0"?><OpenSearchDescription><Description>'
        "<![CDATA[<meta charset=koi8-r>]]> Señal €</Description></OpenSearchDescription>"
    )
    transporte = TransporteFalso(
        {URL: [respuesta(xml.encode(), tipo="application/opensearchdescription+xml")]}
    )
    assert asyncio.run(descargador(transporte).descargar(URL)).html == xml


@pytest.mark.parametrize(
    ("cuerpo", "mime"),
    [
        (b'<?xml version="1.0" encoding="UTF-16"?><a>Se\xc3\xb1al</a>', "application/xml"),
        (b'<meta charset="utf-16"><p>Se\xc3\xb1al</p>', "text/html"),
        (b'<meta charset="UTF-32LE"><p>Se\xc3\xb1al</p>', "text/html"),
    ],
)
def test_utf16_declarado_dentro_de_un_documento_ascii_se_ignora(cuerpo: bytes, mime: str) -> None:
    # Si la declaración se ha podido leer como ASCII, el documento no está en UTF-16 ni UTF-32
    # (WHATWG lo trata como UTF-8): hacerle caso convertiría toda la página en basura ilegible.
    assert "Señal" in decodificar(cuerpo, mime=mime)


def test_declaracion_xml_hostil_no_dispara_el_tiempo() -> None:
    hostil = b"<?xml " + b" " * 3000 + b"encoding" * 5000 + b"=" * 100_000
    assert segundos_de_cpu(lambda: decodificar(hostil), 1) < 1


def test_errores_http_se_devuelven_como_pagina() -> None:
    transporte = TransporteFalso({URL: [respuesta(b"<h1>No encontrada</h1>", estado=404)]})
    pagina = asyncio.run(descargador(transporte).descargar(URL))
    assert pagina.estado == 404
    assert "No encontrada" in pagina.html


def test_error_http_con_tipo_raro_no_lee_el_cuerpo() -> None:
    transporte = TransporteFalso(
        {URL: [respuesta(b"%PDF-1.7", estado=503, tipo="application/pdf")]}
    )
    pagina = asyncio.run(descargador(transporte).descargar(URL))
    assert pagina.estado == 503 and pagina.html == ""


def test_contenido_comprimido_no_pedido() -> None:
    transporte = TransporteFalso({URL: [respuesta(b"\x1f\x8b", content_encoding="gzip")]})
    with pytest.raises(ErrorDescarga):
        asyncio.run(descargador(transporte).descargar(URL))


def test_content_length_excesivo_no_se_lee() -> None:
    cuerpo = Cuerpo(b"x" * 10)
    transporte = TransporteFalso(
        {
            URL: [
                RespuestaCruda(200, {"content-type": "text/html", "content-length": "2000"}, cuerpo)
            ]
        }
    )
    with pytest.raises(ErrorDescarga, match="grande"):
        asyncio.run(descargador(transporte, max_bytes=1000).descargar(URL))
    assert cuerpo.lecturas == []


def test_cuerpo_excesivo_se_corta_leyendo_por_trozos() -> None:
    cuerpo = Cuerpo(b"x" * 10_000_000)
    transporte = TransporteFalso(
        {URL: [RespuestaCruda(200, {"content-type": "text/html"}, cuerpo)]}
    )
    with pytest.raises(ErrorDescarga, match="grande"):
        asyncio.run(descargador(transporte, max_bytes=100_000).descargar(URL))
    assert all(0 < n <= 64 * 1024 for n in cuerpo.lecturas)
    assert sum(cuerpo.lecturas) <= 100_000 + 64 * 1024
    assert cuerpo.cerrado


def test_cuerpo_justo_en_el_limite() -> None:
    transporte = TransporteFalso({URL: [respuesta(b"x" * 1000)]})
    assert len(asyncio.run(descargador(transporte, max_bytes=1000).descargar(URL)).html) == 1000


def test_tiempo_total_limitado() -> None:
    # Una web que entrega un byte cada poco nunca agota el timeout del socket: hace falta un
    # límite de tiempo total.
    reloj = Reloj()
    cuerpo = Cuerpo(b"x" * 1_000_000, al_leer=reloj)
    transporte = TransporteFalso(
        {URL: [RespuestaCruda(200, {"content-type": "text/html"}, cuerpo)]}
    )
    with pytest.raises(ErrorDescarga, match="tiempo"):
        asyncio.run(descargador(transporte, reloj=reloj).descargar(URL))


@pytest.mark.parametrize(
    "error", [OSError("conexión rechazada"), TimeoutError("timed out"), socket.gaierror("dns")]
)
def test_errores_de_red(error: Exception) -> None:
    with pytest.raises(ErrorDescarga):
        asyncio.run(descargador(TransporteFalso({URL: error})).descargar(URL))


# --- Charset -----------------------------------------------------------------------------------


def test_charset_de_la_cabecera() -> None:
    html = "<p>Señal de freno: 38,90 €</p>"
    transporte = TransporteFalso(
        {URL: [respuesta(html.encode("cp1252"), tipo="text/html; charset=windows-1252")]}
    )
    assert asyncio.run(descargador(transporte).descargar(URL)).html == html


def test_charset_del_meta_si_la_cabecera_no_lo_dice() -> None:
    html = (FIXTURES / "listado_desguace.html").read_text(encoding="utf-8")
    transporte = TransporteFalso({URL: [respuesta(html.encode("cp1252"), tipo="text/html")]})
    pagina = asyncio.run(descargador(transporte).descargar(URL))
    assert "Resultados de búsqueda" in pagina.html
    assert "¿No encuentras la pieza?" in pagina.html


@pytest.mark.parametrize(
    ("cuerpo", "charset", "esperado"),
    [
        ("€ ñ".encode("cp1252"), "iso-8859-1", "€ ñ"),  # latin-1 se lee como windows-1252
        ("ñ".encode(), None, "ñ"),
        ("﻿ñ".encode(), "iso-8859-1", "ñ"),  # el BOM manda sobre la cabecera
        ("ñ".encode("utf-16"), None, "ñ"),
        ("ñ".encode(), "charset-inventado", "ñ"),
        ("ñ".encode(), "base64", "ñ"),
        (b"+ADw-script+AD4-", "utf-7", "+ADw-script+AD4-"),
        (b"\xfa\xfb caf\xe9", "utf-8", None),
        (
            '<meta charset="iso-8859-15">€'.encode("iso-8859-15"),
            None,
            '<meta charset="iso-8859-15">€',
        ),
    ],
)
def test_decodificar(cuerpo: bytes, charset: str | None, esperado: str | None) -> None:
    resultado = decodificar(cuerpo, charset)
    if esperado is None:
        assert "�" in resultado  # bytes inválidos: se sustituyen, no se lanza error
    else:
        assert resultado == esperado


# --- Transporte real con urllib (solo contra 127.0.0.1) ------------------------------------------


def test_conexion_validada_rechaza_ip_internas_al_conectar() -> None:
    # DNS rebinding: el nombre resolvió a una IP pública al validar y a 127.0.0.1 al conectar.
    conectadas: list[tuple[str, int]] = []

    def resolver(host: str, puerto: int, **_: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", puerto))]

    def conectar(direccion: tuple[str, int], *_: object) -> socket.socket:
        conectadas.append(direccion)
        raise AssertionError("no debería conectar")

    crear = conexion_validada(resolver=resolver, conectar=conectar)
    with pytest.raises(URLNoPermitida):
        crear(("tienda.example", 443), 5)
    assert conectadas == []


def test_conexion_validada_conecta_a_la_ip_comprobada() -> None:
    conectadas: list[tuple[str, int]] = []
    sentinela = socket.socket()

    def resolver(host: str, puerto: int, **_: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (IP_PUBLICA, puerto))]

    def conectar(direccion: tuple[str, int], *_: object) -> socket.socket:
        conectadas.append(direccion)
        return sentinela

    try:
        assert (
            conexion_validada(resolver=resolver, conectar=conectar)(("t.example", 80), 5)
            is sentinela
        )
        assert conectadas == [(IP_PUBLICA, 80)]
    finally:
        sentinela.close()


class _Manejador(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.rutas.append(self.path)  # type: ignore[attr-defined]
        if self.path == "/redirige":
            self.send_response(302)
            self.send_header("Location", "/destino")
            self.end_headers()
            return
        cuerpo = "<p>Hola desde el servidor local ñ</p>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def servidor_local() -> Iterator[tuple[str, list[str]]]:
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Manejador)
    servidor.rutas = []  # type: ignore[attr-defined]
    hilo = threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    hilo.start()
    try:
        yield f"http://127.0.0.1:{servidor.server_port}", servidor.rutas  # type: ignore[attr-defined]
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_transporte_urllib_no_sigue_redirecciones_solo(
    servidor_local: tuple[str, list[str]],
) -> None:
    base, rutas = servidor_local
    transporte = TransporteURLLib(ip_permitida=lambda ip: ip == ipaddress.ip_address("127.0.0.1"))
    crudo = transporte(f"{base}/redirige", {"User-Agent": UA}, 5)
    crudo.cuerpo.close()
    assert crudo.estado == 302
    assert crudo.cabeceras["location"] == "/destino"
    assert rutas == ["/redirige"]


def test_descargador_completo_contra_servidor_local(servidor_local: tuple[str, list[str]]) -> None:
    base, rutas = servidor_local

    async def permisivo(url: str) -> str:
        return url

    transporte = TransporteURLLib(ip_permitida=lambda ip: ip == ipaddress.ip_address("127.0.0.1"))
    d = DescargadorHTTP(UA, validador=permisivo, transporte=transporte)
    pagina = asyncio.run(d.descargar(f"{base}/redirige"))
    assert pagina.url_final == f"{base}/destino"
    assert pagina.html == "<p>Hola desde el servidor local ñ</p>"
    assert rutas == ["/redirige", "/destino"]


def test_transporte_por_defecto_no_conecta_a_loopback(
    servidor_local: tuple[str, list[str]],
) -> None:
    # Aunque un validador mal configurado dejara pasar 127.0.0.1, el socket no se abre.
    base, rutas = servidor_local

    async def permisivo(url: str) -> str:
        return url

    with pytest.raises(URLNoPermitida):
        asyncio.run(DescargadorHTTP(UA, validador=permisivo).descargar(f"{base}/"))
    assert rutas == []


def _servidor_goteo(inicio: bytes) -> Iterator[str]:
    """Servidor hostil: manda `inicio` y después el resto de la respuesta byte a byte.

    Cada byte llega antes del timeout del socket, así que solo un límite de tiempo total lo para.
    """
    escucha = socket.create_server(("127.0.0.1", 0))
    parar = threading.Event()

    def atender() -> None:
        escucha.settimeout(5)
        try:
            conexion, _ = escucha.accept()
        except OSError:
            return
        with conexion:
            conexion.recv(4096)
            try:
                conexion.sendall(inicio)
                # Acotado a 20 s para que, si el límite falla, el test falle en vez de colgarse.
                for _ in range(200):
                    if parar.wait(0.1):
                        break
                    conexion.sendall(b"a")
            except OSError:
                pass

    hilo = threading.Thread(target=atender, daemon=True)
    hilo.start()
    try:
        yield f"127.0.0.1:{escucha.getsockname()[1]}"
    finally:
        parar.set()
        escucha.close()
        hilo.join(5)


@pytest.fixture
def goteo_http() -> Iterator[str]:
    yield from _servidor_goteo(b"HTTP/1.1 200 OK\r\nX-Relleno: ")


@pytest.fixture
def goteo_tls() -> Iterator[str]:
    # Cabecera de un registro TLS de handshake de 16 000 bytes: el cliente espera el registro
    # entero antes de seguir, sin que haga falta un certificado.
    yield from _servidor_goteo(bytes([0x16, 0x03, 0x03, 0x3E, 0x80]))


def _descargar_a_goteo(url: str) -> None:
    async def permisivo(url: str) -> str:
        return url

    transporte = TransporteURLLib(ip_permitida=lambda ip: ip == ipaddress.ip_address("127.0.0.1"))
    d = DescargadorHTTP(UA, timeout=1, validador=permisivo, transporte=transporte)
    inicio = time.monotonic()
    with pytest.raises(ErrorDescarga):
        asyncio.run(asyncio.wait_for(d.descargar(url), 15))
    assert time.monotonic() - inicio < 5


def test_cabeceras_a_goteo_respetan_el_tiempo_total(goteo_http: str) -> None:
    _descargar_a_goteo(f"http://{goteo_http}/")


def test_handshake_tls_a_goteo_respeta_el_tiempo_total(goteo_tls: str) -> None:
    _descargar_a_goteo(f"https://{goteo_tls}/")


@pytest.fixture
def certificado(tmp_path: Path) -> tuple[Path, Path]:
    """Certificado autofirmado para 127.0.0.1, generado al vuelo (no se guarda ninguna clave)."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl no está instalado")
    clave, cert = tmp_path / "clave.pem", tmp_path / "cert.pem"
    orden = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1"]
    orden += ["-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"]
    orden += ["-keyout", str(clave), "-out", str(cert)]
    subprocess.run(
        orden,
        check=True,
        capture_output=True,
        timeout=60,
    )
    return clave, cert


def test_cabeceras_a_goteo_por_https_respetan_el_tiempo_total(
    certificado: tuple[Path, Path],
) -> None:
    # Por HTTPS el socket que abre `conexion_validada` lo envuelve `ssl` (y queda separado de su
    # descriptor): el corte tiene que llegar también a la conexión cifrada.
    clave, cert = certificado
    servidor_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    servidor_tls.load_cert_chain(cert, clave)
    escucha = socket.create_server(("127.0.0.1", 0))
    parar = threading.Event()

    def atender() -> None:
        escucha.settimeout(5)
        try:
            crudo, _ = escucha.accept()
            conexion = servidor_tls.wrap_socket(crudo, server_side=True)
        except OSError:
            return
        with conexion:
            try:
                conexion.recv(4096)
                conexion.sendall(b"HTTP/1.1 200 OK\r\nX-Relleno: ")
                for _ in range(200):
                    if parar.wait(0.1):
                        break
                    conexion.sendall(b"a")
            except OSError:
                pass

    hilo = threading.Thread(target=atender, daemon=True)
    hilo.start()

    async def permisivo(url: str) -> str:
        return url

    cliente_tls = ssl.create_default_context(cafile=str(cert))
    transporte = TransporteURLLib(
        ip_permitida=lambda ip: ip == ipaddress.ip_address("127.0.0.1"), contexto_tls=cliente_tls
    )
    d = DescargadorHTTP(UA, timeout=1, validador=permisivo, transporte=transporte)
    inicio = time.monotonic()
    try:
        with pytest.raises(ErrorDescarga):
            asyncio.run(
                asyncio.wait_for(d.descargar(f"https://127.0.0.1:{escucha.getsockname()[1]}/"), 15)
            )
        assert time.monotonic() - inicio < 5
    finally:
        parar.set()
        escucha.close()
        hilo.join(5)


# --- Transportes con huella de navegador (`TransporteNavegador`, ADR-022) -----------------------


@dataclass
class TransporteNavegadorFalso:
    """Cumple `TransporteNavegador`: recibe el tiempo total y el tope exacto de cada descarga."""

    llamadas: list[tuple[str, dict[str, str], float, int]] = field(default_factory=list)
    cerrado: bool = False

    def __call__(self, url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        raise AssertionError("Con un TransporteNavegador se usa `pedir`.")

    def pedir(
        self, url: str, cabeceras: Mapping[str, str], timeout: float, max_bytes: int
    ) -> RespuestaCruda:
        self.llamadas.append((url, dict(cabeceras), timeout, max_bytes))
        return respuesta()

    def cerrar(self) -> None:
        self.cerrado = True


def test_transporte_navegador_es_un_transporte_navegador() -> None:
    assert isinstance(TransporteNavegadorFalso(), TransporteNavegador)
    assert not isinstance(TransporteURLLib(), TransporteNavegador)


def test_transporte_navegador_recibe_tiempo_y_tope() -> None:
    transporte = TransporteNavegadorFalso()
    d = DescargadorHTTP(UA, timeout=7, validador=validar_con_dns_publico, transporte=transporte)
    asyncio.run(d.descargar(URL))
    asyncio.run(d.descargar_grande(URL, max_bytes=1234))
    (_, cabeceras, tiempo, tope), (_, _, tiempo_grande, tope_grande) = transporte.llamadas
    assert (tiempo, tope) == (7, MAXIMO_BYTES)
    assert (tiempo_grande, tope_grande) == (7 * FACTOR_TIEMPO_GRANDE, 1234)
    # Las mismas cabeceras que con urllib: el transporte decide cuáles usa (curl, solo las suyas;
    # su respaldo urllib con un proxy local, todas).
    assert cabeceras["User-Agent"] == UA and cabeceras["Accept-Encoding"] == "identity"


def test_cerrar_cierra_el_transporte_navegador() -> None:
    transporte = TransporteNavegadorFalso()
    d = DescargadorHTTP(UA, validador=validar_con_dns_publico, transporte=transporte)
    asyncio.run(d.cerrar())
    assert transporte.cerrado


def test_transporte_plano_recibe_el_timeout_de_siempre() -> None:
    tiempos: list[float] = []

    def transporte(url: str, cabeceras: Mapping[str, str], timeout: float) -> RespuestaCruda:
        tiempos.append(timeout)
        return respuesta()

    d = DescargadorHTTP(UA, timeout=7, validador=validar_con_dns_publico, transporte=transporte)
    asyncio.run(d.descargar(URL))
    asyncio.run(d.descargar_grande(URL))
    asyncio.run(d.cerrar())
    assert tiempos == [7, 7]
