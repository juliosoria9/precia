"""Sitemaps comprimidos y ficheros grandes en `DescargadorHTTP` (nota 16, sección B).

La descompresión está acotada en bytes **descomprimidos**: una bomba gzip (pocos MB que se
convierten en GB) se para en el tope sin agotar la memoria ni el tiempo.
"""

import asyncio
import gzip
import http.server
import ipaddress
import struct
import threading
import time
import tracemalloc
import zlib
from collections.abc import Iterator
from functools import cache

import pytest

from precia.navegador.http import (
    MAX_BYTES_SITEMAP,
    MAX_CAPAS_GZIP,
    MAX_MIEMBROS_GZIP,
    MAXIMO_BYTES,
    DescargadorHTTP,
    RespuestaCruda,
    TransporteURLLib,
)
from precia.navegador.tipos import DemasiadoGrande, ErrorDescarga, Pagina, URLNoPermitida
from tests.cronometro import segundos_de_cpu
from tests.navegador.falsos import Reloj, validar_con_dns_publico
from tests.navegador.test_http import UA, Cuerpo, TransporteFalso, descargador, respuesta

TIENDA = "https://www.recambios-ejemplo.example"
SITEMAP_GZ = f"{TIENDA}/sitemap-productos.xml.gz"
SITEMAP = f"{TIENDA}/sitemap.xml"
XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<url><loc>{TIENDA}/pastillas-freno-brembo-p85075.html</loc></url>"
    "<url><loc>https://www.recambios-ejemplo.example/disco-freno-zimmermann.html</loc></url>"
    "</urlset>\n"
)


def gz(datos: bytes) -> bytes:
    return gzip.compress(datos, mtime=0)


def bajar(
    url: str, cruda: RespuestaCruda, *, grande: bool = False, max_bytes: int = MAXIMO_BYTES
) -> str:
    transporte = TransporteFalso({url: [cruda]})
    d = descargador(transporte, max_bytes=max_bytes)
    pagina = asyncio.run(d.descargar_grande(url) if grande else d.descargar(url))
    return pagina.html


def test_tope_de_los_sitemaps() -> None:
    assert MAX_BYTES_SITEMAP == 20 * 1024 * 1024
    assert MAXIMO_BYTES == 5 * 1024 * 1024


# --- Tipos y codificaciones admitidos -----------------------------------------------------------


@pytest.mark.parametrize("tipo", ["application/gzip", "application/x-gzip", "APPLICATION/X-GZIP"])
def test_tipos_gzip_se_descomprimen(tipo: str) -> None:
    transporte = TransporteFalso({SITEMAP_GZ: [respuesta(gz(XML.encode()), tipo=tipo)]})
    pagina = asyncio.run(descargador(transporte).descargar(SITEMAP_GZ))
    assert pagina.html == XML
    # Lo descomprimido es el XML del sitemap: así lo ve quien lo lea.
    assert pagina.tipo_contenido == "application/xml"


def test_tipo_gzip_vale_aunque_la_url_no_acabe_en_gz() -> None:
    url = f"{TIENDA}/sitemap?formato=gz"
    assert bajar(url, respuesta(gz(XML.encode()), tipo="application/gzip")) == XML


@pytest.mark.parametrize("tipo", ["application/xml", "text/xml; charset=utf-8"])
@pytest.mark.parametrize("codificacion", ["gzip", "x-gzip", " GZIP "])
def test_content_encoding_gzip_en_un_xml_gz(tipo: str, codificacion: str) -> None:
    cruda = respuesta(gz(XML.encode()), tipo=tipo, content_encoding=codificacion)
    transporte = TransporteFalso({SITEMAP_GZ: [cruda]})
    pagina = asyncio.run(descargador(transporte).descargar(SITEMAP_GZ))
    assert pagina.html == XML
    assert pagina.tipo_contenido == tipo.split(";")[0]


@pytest.mark.parametrize("url", [SITEMAP, f"{TIENDA}/ficha.html", f"{TIENDA}/datos.gz"])
def test_content_encoding_gzip_fuera_de_un_xml_gz_sigue_rechazado(url: str) -> None:
    # Pedimos `identity`: comprimir sin que se pida solo se tolera en el fichero `.xml.gz`.
    cruda = respuesta(gz(XML.encode()), tipo="application/xml", content_encoding="gzip")
    with pytest.raises(ErrorDescarga, match="comprimido"):
        bajar(url, cruda)


@pytest.mark.parametrize("codificacion", ["br", "deflate", "compress", "gzip, br"])
def test_otras_codificaciones_rechazadas_tambien_en_xml_gz(codificacion: str) -> None:
    cruda = respuesta(gz(XML.encode()), tipo="application/xml", content_encoding=codificacion)
    with pytest.raises(ErrorDescarga, match="comprimido"):
        bajar(SITEMAP_GZ, cruda)


@pytest.mark.parametrize("tipo", ["application/octet-stream", "binary/octet-stream", None])
def test_octet_stream_en_un_xml_gz_con_gzip_dentro(tipo: str | None) -> None:
    # nginx sin `gz` en mime.types, S3 y muchos CDN sirven el `.xml.gz` así (o sin tipo).
    assert bajar(SITEMAP_GZ, respuesta(gz(XML.encode()), tipo=tipo)) == XML


@pytest.mark.parametrize("tipo", ["application/octet-stream", None])
def test_octet_stream_en_un_xml_gz_sin_gzip_dentro_se_rechaza(tipo: str | None) -> None:
    with pytest.raises(ErrorDescarga, match="tipo"):
        bajar(SITEMAP_GZ, respuesta(b"MZ\x90\x00 un ejecutable", tipo=tipo))


def test_octet_stream_fuera_de_un_xml_gz_se_rechaza_sin_leer() -> None:
    cuerpo = Cuerpo(gz(XML.encode()))
    cruda = RespuestaCruda(200, {"content-type": "application/octet-stream"}, cuerpo)
    with pytest.raises(ErrorDescarga, match="tipo"):
        bajar(f"{TIENDA}/sitemap.gz", cruda)
    assert cuerpo.lecturas == []


def test_xml_gz_con_tipo_xml_y_gzip_dentro_sin_cabecera() -> None:
    # Servidor mal configurado: tipo del `.xml` y sin `Content-Encoding`. Un XML nunca empieza
    # por los bytes mágicos de gzip, así que no hay ambigüedad.
    assert bajar(SITEMAP_GZ, respuesta(gz(XML.encode()), tipo="application/xml")) == XML


def test_xml_gz_ya_descomprimido_por_el_servidor_se_lee_tal_cual() -> None:
    assert bajar(SITEMAP_GZ, respuesta(XML.encode(), tipo="application/xml")) == XML


def test_gzip_con_charset_declarado_en_el_xml() -> None:
    texto = '<?xml version="1.0" encoding="ISO-8859-1"?><urlset><loc>/frenos-señal</loc></urlset>'
    pagina = bajar(SITEMAP_GZ, respuesta(gz(texto.encode("latin-1")), tipo="application/x-gzip"))
    assert "frenos-señal" in pagina


def test_varios_miembros_gzip_concatenados() -> None:
    cuerpo = gz(XML[:100].encode()) + gz(XML[100:].encode())
    assert bajar(SITEMAP_GZ, respuesta(cuerpo, tipo="application/gzip")) == XML


def test_relleno_de_ceros_al_final_se_ignora() -> None:
    cuerpo = gz(XML.encode()) + b"\x00" * 512
    assert bajar(SITEMAP_GZ, respuesta(cuerpo, tipo="application/gzip")) == XML


def test_doble_compresion_fichero_y_transporte() -> None:
    # Un `.xml.gz` servido además con `Content-Encoding: gzip`: dos capas.
    cuerpo = gz(gz(XML.encode()))
    cruda = respuesta(cuerpo, tipo="application/x-gzip", content_encoding="gzip")
    assert bajar(SITEMAP_GZ, cruda) == XML


def test_demasiadas_capas_se_rechazan() -> None:
    cuerpo = XML.encode()
    for _ in range(MAX_CAPAS_GZIP + 1):
        cuerpo = gz(cuerpo)
    with pytest.raises(ErrorDescarga, match="capas"):
        bajar(SITEMAP_GZ, respuesta(cuerpo, tipo="application/gzip"))


# --- Ficheros rotos -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cuerpo",
    [
        b"",
        b"<urlset>no es gzip</urlset>",
        b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xffbasura que no es deflate",
        gz(XML.encode())[:-12],  # cortado: sin el final del flujo
        gz(XML.encode())[:-8] + b"\x00\x00\x00\x00" + gz(XML.encode())[-4:],  # CRC falso
        gz(XML.encode())[:-4] + struct.pack("<I", 7),  # tamaño falso
    ],
    ids=["vacio", "sin-magia", "deflate-roto", "cortado", "crc", "tamano"],
)
def test_gzip_roto_es_un_error_de_descarga(cuerpo: bytes) -> None:
    with pytest.raises(ErrorDescarga, match="comprimido"):
        bajar(SITEMAP_GZ, respuesta(cuerpo, tipo="application/gzip"))


def test_demasiados_miembros_se_rechazan() -> None:
    cuerpo = gz(b"<a/>") * (MAX_MIEMBROS_GZIP + 1)
    with pytest.raises(ErrorDescarga, match="comprimido"):
        bajar(SITEMAP_GZ, respuesta(cuerpo, tipo="application/gzip"))


def test_miles_de_miembros_vacios_cortan_pronto() -> None:
    # Hostil: 1 MB de miembros vacíos (20 bytes cada uno) son 50 000 descompresores.
    cuerpo = gz(b"") * 50_000

    def cortar() -> None:
        with pytest.raises(ErrorDescarga, match="comprimido"):
            bajar(SITEMAP_GZ, respuesta(cuerpo, tipo="application/gzip"))

    assert segundos_de_cpu(cortar, 1.0) < 1.0


def test_cabecera_gzip_con_nombre_gigante_respeta_el_tope_comprimido() -> None:
    # FNAME de 10 MB sin terminar: no produce salida, pero el tope de lo leído lo para.
    cabecera = b"\x1f\x8b\x08\x08\x00\x00\x00\x00\x00\xff" + b"a" * 10_000_000
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP_GZ, respuesta(cabecera, tipo="application/gzip"), max_bytes=100_000)


# --- Bombas gzip --------------------------------------------------------------------------------


@cache
def bomba_gzip(descomprimidos: int) -> bytes:
    """gzip de `descomprimidos` bytes de ceros, generado sin comprimir de verdad tantos datos.

    Con `Z_FULL_FLUSH` cada trozo de 16 MiB se comprime siempre igual: se repite su salida. El
    final del flujo es falso (CRC y tamaño): quien descomprima con tope no debe llegar a él.
    """
    trozo = 16 * 1024 * 1024
    compresor = zlib.compressobj(9, zlib.DEFLATED, -15)
    segmento = compresor.compress(bytes(trozo)) + compresor.flush(zlib.Z_FULL_FLUSH)
    cabecera = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    final = compresor.flush(zlib.Z_FINISH) + b"\x00" * 8
    return cabecera + segmento * (descomprimidos // trozo) + final


GIB = 1024 * 1024 * 1024


def test_bomba_de_10_mb_que_serian_10_gb_se_para_en_el_tope() -> None:
    bomba = bomba_gzip(10 * GIB)
    assert len(bomba) < 11 * 1024 * 1024  # unos 10 MB por la red
    transporte = TransporteFalso({SITEMAP_GZ: [respuesta(bomba, tipo="application/gzip")]})
    d = descargador(transporte)
    tracemalloc.start()
    # Tiempo de CPU (bug 6), de un solo intento: el transporte falso da la respuesta una vez.
    inicio = time.process_time()
    try:
        with pytest.raises(ErrorDescarga, match="grande"):
            asyncio.run(d.descargar_grande(SITEMAP_GZ))
        cpu = time.process_time() - inicio
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert cpu < 5.0
    # Lo comprimido (10 MB) más, como mucho, el tope descomprimido y un trozo: nunca los 10 GB.
    assert pico < 2 * len(bomba) + MAX_BYTES_SITEMAP + 16 * 1024 * 1024


def test_bomba_pequena_en_una_descarga_normal_se_para_en_5_mib() -> None:
    bomba = bomba_gzip(1 * GIB)  # unos 1 MB comprimidos

    def parar() -> None:
        with pytest.raises(ErrorDescarga, match="grande"):
            bajar(SITEMAP_GZ, respuesta(bomba, tipo="application/gzip"))

    assert segundos_de_cpu(parar, 2.0) < 2.0


def test_la_bomba_de_10_mb_no_pasa_ni_el_tope_comprimido_normal() -> None:
    cuerpo = Cuerpo(bomba_gzip(10 * GIB))
    cruda = RespuestaCruda(200, {"content-type": "application/gzip"}, cuerpo)
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP_GZ, cruda)
    assert sum(cuerpo.lecturas) <= MAXIMO_BYTES + 64 * 1024


def test_descomprimido_justo_en_el_tope() -> None:
    datos = b"<urlset>" + b"a" * (1000 - len(b"<urlset></urlset>")) + b"</urlset>"
    assert len(datos) == 1000
    html = bajar(SITEMAP_GZ, respuesta(gz(datos), tipo="application/gzip"), max_bytes=1000)
    assert len(html) == 1000
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP_GZ, respuesta(gz(datos + b" "), tipo="application/gzip"), max_bytes=1000)


def test_content_length_es_de_lo_comprimido() -> None:
    # El tope vale para lo leído y para lo descomprimido; Content-Length mide lo leído.
    descomprimido = XML.encode() * 50
    comprimido = gz(descomprimido)
    assert len(comprimido) < len(descomprimido) // 10
    cruda = respuesta(comprimido, tipo="application/gzip", content_length=str(len(comprimido)))
    assert bajar(SITEMAP_GZ, cruda, max_bytes=len(descomprimido)).startswith("<?xml")
    # Un Content-Length por encima del tope sigue sin leerse, aunque lo de dentro cupiera.
    cuerpo = Cuerpo(comprimido)
    cabeceras = {"content-type": "application/gzip", "content-length": str(len(descomprimido) + 1)}
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP_GZ, RespuestaCruda(200, cabeceras, cuerpo), max_bytes=len(descomprimido))
    assert cuerpo.lecturas == []


# --- descargar_grande ---------------------------------------------------------------------------


def test_descargar_grande_admite_hasta_20_mib() -> None:
    grande = b"<urlset>" + b"x" * (8 * 1024 * 1024) + b"</urlset>"
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP, respuesta(grande, tipo="application/xml"))
    assert len(bajar(SITEMAP, respuesta(grande, tipo="application/xml"), grande=True)) == len(
        grande
    )


def test_descargar_grande_tambien_tiene_tope() -> None:
    cuerpo = Cuerpo(b"x" * (MAX_BYTES_SITEMAP + 1))
    cruda = RespuestaCruda(200, {"content-type": "text/xml"}, cuerpo)
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP, cruda, grande=True)
    assert sum(cuerpo.lecturas) <= MAX_BYTES_SITEMAP + 64 * 1024


def test_descargar_grande_content_length_excesivo_no_se_lee() -> None:
    cuerpo = Cuerpo(b"x")
    cabeceras = {"content-type": "application/xml", "content-length": str(MAX_BYTES_SITEMAP + 1)}
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP, RespuestaCruda(200, cabeceras, cuerpo), grande=True)
    assert cuerpo.lecturas == []


def test_descargar_grande_con_tope_propio() -> None:
    transporte = TransporteFalso({SITEMAP: [respuesta(b"x" * 2000, tipo="text/xml")]})
    with pytest.raises(ErrorDescarga, match="grande"):
        asyncio.run(descargador(transporte).descargar_grande(SITEMAP, max_bytes=1000))


def test_descargar_grande_valida_cada_redireccion() -> None:
    validadas: list[str] = []

    async def validador(url: str) -> str:
        validadas.append(url)
        return await validar_con_dns_publico(url)

    destino = f"{TIENDA}/sitemaps/productos.xml.gz"
    transporte = TransporteFalso(
        {
            SITEMAP: [respuesta(b"", estado=301, tipo="text/html", location=destino)],
            destino: [respuesta(gz(XML.encode()), tipo="application/gzip")],
        }
    )
    d = DescargadorHTTP(UA, validador=validador, transporte=transporte, reloj=Reloj())
    pagina = asyncio.run(d.descargar_grande(SITEMAP))
    assert pagina.html == XML and pagina.url_final == destino
    assert validadas == [SITEMAP, destino]


def test_descargar_grande_redireccion_interna_se_rechaza() -> None:
    transporte = TransporteFalso(
        {SITEMAP: [respuesta(b"", estado=302, tipo="text/html", location="http://10.0.0.1/s.xml")]}
    )
    with pytest.raises(URLNoPermitida):
        asyncio.run(descargador(transporte).descargar_grande(SITEMAP))


def test_descargar_grande_tiene_mas_tiempo_total_pero_limitado() -> None:
    # Cada lectura «tarda» 5 s y el timeout es de 20 s: 20 MiB no caben en 20 s con una conexión
    # normal, así que el tiempo total de un fichero grande es mayor, pero sigue acotado.
    reloj = Reloj()
    cuerpo = Cuerpo(b"x" * (10 * 64 * 1024), al_leer=reloj)
    transporte = TransporteFalso(
        {SITEMAP: [RespuestaCruda(200, {"content-type": "text/xml"}, cuerpo)]}
    )
    with pytest.raises(ErrorDescarga, match="tiempo"):
        asyncio.run(descargador(transporte, reloj=reloj).descargar(SITEMAP))

    reloj = Reloj()
    cuerpo = Cuerpo(b"x" * (10 * 64 * 1024), al_leer=reloj)
    transporte = TransporteFalso(
        {SITEMAP: [RespuestaCruda(200, {"content-type": "text/xml"}, cuerpo)]}
    )
    assert len(
        asyncio.run(descargador(transporte, reloj=reloj).descargar_grande(SITEMAP)).html
    ) == (10 * 64 * 1024)

    reloj = Reloj()
    cuerpo = Cuerpo(b"x" * MAX_BYTES_SITEMAP, al_leer=reloj)
    transporte = TransporteFalso(
        {SITEMAP: [RespuestaCruda(200, {"content-type": "text/xml"}, cuerpo)]}
    )
    with pytest.raises(ErrorDescarga, match="tiempo"):
        asyncio.run(descargador(transporte, reloj=reloj).descargar_grande(SITEMAP))


def test_muchos_miembros_y_una_cola_larga_no_se_copia_en_cada_miembro() -> None:
    # Hostil: 64 miembros pequeños y 20 MiB de ceros detrás. Mirar «¿solo queda relleno?» en cada
    # miembro copiaría la cola 64 veces.
    cuerpo = gz(b"<a/>") * MAX_MIEMBROS_GZIP + b"\x00" * (MAX_BYTES_SITEMAP - 4096)
    paginas: list[Pagina] = []

    def descargar() -> None:
        # Un transporte nuevo en cada intento: el falso da cada respuesta una sola vez.
        transporte = TransporteFalso({SITEMAP_GZ: [respuesta(cuerpo, tipo="application/gzip")]})
        paginas.append(asyncio.run(descargador(transporte).descargar_grande(SITEMAP_GZ)))

    assert segundos_de_cpu(descargar, 0.5) < 0.5
    assert paginas[-1].html == "<a/>" * MAX_MIEMBROS_GZIP


# --- Contra un servidor local (127.0.0.1), con el transporte real -------------------------------


class _ServidorSitemaps(http.server.BaseHTTPRequestHandler):
    """Sirve el mismo `.xml.gz` como lo hacen Apache, nginx y un servidor con una bomba."""

    def do_GET(self) -> None:
        if self.path == "/apache/sitemap.xml.gz":
            cuerpo, tipo, codificacion = gz(XML.encode()), "application/xml", "gzip"
        elif self.path == "/nginx/sitemap.xml.gz":
            cuerpo, tipo, codificacion = gz(XML.encode()), "application/octet-stream", None
        else:
            cuerpo, tipo, codificacion = bomba_gzip(1 * GIB), "application/x-gzip", None
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        if codificacion:
            self.send_header("Content-Encoding", codificacion)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def servidor_sitemaps() -> Iterator[str]:
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ServidorSitemaps)
    hilo = threading.Thread(
        target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    hilo.start()
    try:
        yield f"http://127.0.0.1:{servidor.server_port}"
    finally:
        servidor.shutdown()
        servidor.server_close()


def _descargador_local() -> DescargadorHTTP:
    async def permisivo(url: str) -> str:
        return url

    transporte = TransporteURLLib(ip_permitida=lambda ip: ip == ipaddress.ip_address("127.0.0.1"))
    return DescargadorHTTP(UA, validador=permisivo, transporte=transporte)


@pytest.mark.parametrize("servidor", ["apache", "nginx"])
def test_sitemap_gz_real_contra_servidor_local(servidor_sitemaps: str, servidor: str) -> None:
    url = f"{servidor_sitemaps}/{servidor}/sitemap.xml.gz"
    pagina = asyncio.run(_descargador_local().descargar_grande(url))
    assert pagina.html == XML


def test_bomba_real_contra_servidor_local_se_para(servidor_sitemaps: str) -> None:
    inicio = time.perf_counter()
    with pytest.raises(ErrorDescarga, match="grande"):
        asyncio.run(_descargador_local().descargar_grande(f"{servidor_sitemaps}/bomba.xml.gz"))
    assert time.perf_counter() - inicio < 5.0


@pytest.mark.parametrize("estado", [404, 503])
def test_error_http_con_octet_stream_en_un_xml_gz_no_lee_el_cuerpo(estado: int) -> None:
    # Igual que con cualquier tipo raro: en un error lo que importa es el estado.
    cuerpo = Cuerpo(b"MZ no es gzip")
    cruda = RespuestaCruda(estado, {"content-type": "application/octet-stream"}, cuerpo)
    transporte = TransporteFalso({SITEMAP_GZ: [cruda]})
    pagina = asyncio.run(descargador(transporte).descargar(SITEMAP_GZ))
    assert pagina.estado == estado and pagina.html == ""
    assert cuerpo.lecturas == []


# --- Respuestas ya descomprimidas por el transporte (curl, ADR-022) -----------------------------


def ya_descomprimida(cuerpo: bytes, tipo: str | None) -> RespuestaCruda:
    """Como la entrega `TransporteCurl`: curl ya quitó el `Content-Encoding` (y la cabecera)."""
    cruda = respuesta(cuerpo, tipo=tipo)
    return RespuestaCruda(cruda.estado, cruda.cabeceras, cruda.cuerpo, descomprimida=True)


@pytest.mark.parametrize(
    ("url", "cruda", "tipo", "texto"),
    [
        pytest.param(
            f"{TIENDA}/ficha",
            ya_descomprimida(b"<p>Freno</p>", "text/html; charset=utf-8"),
            "text/html",
            "<p>Freno</p>",
            id="html",
        ),
        pytest.param(
            SITEMAP_GZ,
            ya_descomprimida(XML.encode(), "application/octet-stream"),
            "application/xml",
            XML,
            id="xml-gz-binario-con-xml-plano",
        ),
        pytest.param(
            SITEMAP_GZ,
            ya_descomprimida(XML.encode(), "application/gzip"),
            "application/xml",
            XML,
            id="xml-gz-gzip-con-xml-plano",
        ),
        pytest.param(
            SITEMAP_GZ,
            ya_descomprimida(gz(XML.encode()), "application/gzip"),
            "application/xml",
            XML,
            id="xml-gz-gzip-con-gzip-dentro",
        ),
        pytest.param(
            f"{TIENDA}/sitemap?formato=gz",
            ya_descomprimida(XML.encode(), "application/gzip"),
            "application/xml",
            XML,
            id="gzip-fuera-de-xml-gz-con-xml-plano",
        ),
    ],
)
def test_respuesta_ya_descomprimida(url: str, cruda: RespuestaCruda, tipo: str, texto: str) -> None:
    transporte = TransporteFalso({url: [cruda]})
    pagina = asyncio.run(descargador(transporte).descargar(url))
    assert pagina.tipo_contenido == tipo
    assert pagina.html == texto


def test_ya_descomprimida_con_bomba_dentro_respeta_el_tope() -> None:
    cruda = ya_descomprimida(gz(b"\x00" * (MAXIMO_BYTES + 1)), "application/gzip")
    with pytest.raises(ErrorDescarga, match="grande"):
        bajar(SITEMAP_GZ, cruda)


def test_sin_descomprimir_sigue_rechazando_lo_comprimido_sin_pedirlo() -> None:
    cruda = respuesta(gz(b"<p>Hola</p>"), content_encoding="gzip")
    assert not cruda.descomprimida
    with pytest.raises(ErrorDescarga, match="sin pedirlo"):
        bajar(f"{TIENDA}/ficha", cruda)


# --- El tope de tamaño es su propio error (A5 del plan de velocidad) ----------------------------


def test_el_tope_de_tamano_es_demasiado_grande() -> None:
    # El catálogo deja de leer un índice cuyos hijos no caben: tiene que distinguir este fallo.
    assert issubclass(DemasiadoGrande, ErrorDescarga)
    cuerpo = Cuerpo(b"x" * (MAX_BYTES_SITEMAP + 1))
    with pytest.raises(DemasiadoGrande):
        bajar(SITEMAP, RespuestaCruda(200, {"content-type": "text/xml"}, cuerpo), grande=True)
    cabeceras = {"content-type": "application/xml", "content-length": str(MAX_BYTES_SITEMAP + 1)}
    with pytest.raises(DemasiadoGrande):
        bajar(SITEMAP, RespuestaCruda(200, cabeceras, Cuerpo(b"x")), grande=True)
    comprimido = gz(b"<urlset>" + b"a" * 2000 + b"</urlset>")
    with pytest.raises(DemasiadoGrande):
        bajar(SITEMAP_GZ, respuesta(comprimido, tipo="application/gzip"), max_bytes=1000)
