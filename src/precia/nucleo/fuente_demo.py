"""Fuente de datos de ejemplo (DEMO) para la interfaz web del hito H1W.

Las tiendas usan el dominio reservado `.example` y los precios son inventados: no representan a
ninguna tienda real. Aun así, el comportamiento imita el diseño de la memoria, para que la
interfaz enseñe la diferencia entre modos: en modo clásico la tienda con el perfil roto no da
datos y no se gasta nada en IA; en modo IA el reparador la recupera, el filtro semántico descarta
accesorios y algún valor se rechaza por no estar anclado en la página.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from precia.nucleo.modelos import (
    Ajustes,
    Alerta,
    ConsultaBusqueda,
    Descartado,
    EstadoPaso,
    EstadoPerfil,
    GrupoProducto,
    LlamadaIA,
    Modo,
    Oferta,
    OrigenDato,
    Paso,
    ProgresoTienda,
    PuntoHistorico,
    ResultadoBusqueda,
    ResultadoEvaluacion,
    ResumenIA,
    SerieHistorico,
    Tienda,
    ValorCampo,
    VersionPerfil,
)

AHORA = datetime(2026, 10, 19, 17, 0)

_SELECTORES_V1 = {
    "resultado": "ul.listado > li.producto",
    "titulo": "h2.nombre a",
    "precio": ".precio .importe",
    "enlace": "h2.nombre a @href",
}

TIENDAS: tuple[Tienda, ...] = (
    Tienda(
        "recambiosnorte.example",
        "Recambios Norte",
        EstadoPerfil.ACTIVO,
        "Permite /buscar y /producto",
        True,
        AHORA - timedelta(hours=2),
        0,
        (
            VersionPerfil(
                1,
                datetime(2026, 10, 5, 11, 20),
                "🤖 configurador · revisado por Julio",
                "https://recambiosnorte.example/buscar?q={consulta}",
                _SELECTORES_V1,
                True,
                "Validado en 5 fichas: 5/5 campos encontrados.",
            ),
        ),
    ),
    Tienda(
        "piezasya.example",
        "PiezasYa",
        EstadoPerfil.REPARADO,
        "Permite todo salvo /cuenta",
        True,
        AHORA - timedelta(hours=2),
        0,
        (
            VersionPerfil(
                1,
                datetime(2026, 10, 6, 9, 5),
                "🤖 configurador · revisado por Julio",
                "https://piezasya.example/search?text={consulta}",
                {**_SELECTORES_V1, "precio": ".price-box .price"},
                True,
            ),
            VersionPerfil(
                2,
                datetime(2026, 10, 12, 18, 40),
                "🤖 reparador · revisado por Julio",
                "https://piezasya.example/search?text={consulta}",
                {**_SELECTORES_V1, "precio": "[data-testid=precio-final]"},
                True,
                "La tienda cambió su HTML: «.price-box .price» dejó de existir.",
            ),
        ),
    ),
    Tienda(
        "desguacesur.example",
        "Desguace Sur",
        EstadoPerfil.PENDIENTE_REVISION,
        "Permite /buscar, retardo pedido de 5 s",
        True,
        AHORA - timedelta(days=1),
        0,
        (
            VersionPerfil(
                1,
                datetime(2026, 10, 18, 20, 15),
                "🤖 configurador",
                "https://desguacesur.example/piezas?ref={consulta}",
                {**_SELECTORES_V1, "estado": ".estado-pieza"},
                True,
                "Validado automáticamente en 3 fichas. Falta la revisión humana.",
            ),
        ),
    ),
    Tienda(
        "motorpiezas.example",
        "MotorPiezas",
        EstadoPerfil.ROTO,
        "Permite /buscar",
        True,
        AHORA - timedelta(hours=2),
        1,
        (
            VersionPerfil(
                1,
                datetime(2026, 10, 2, 16, 0),
                "🤖 configurador · revisado por Julio",
                "https://motorpiezas.example/b/{consulta}",
                _SELECTORES_V1,
                False,
                "Desde el 17 oct el selector de precio no encuentra nada.",
            ),
        ),
    ),
    Tienda(
        "autodescuento.example",
        "AutoDescuento",
        EstadoPerfil.SIN_PERFIL,
        "Prohíbe /buscar (Disallow): no se visita",
        False,
        None,
        0,
    ),
)


@dataclass(frozen=True)
class _OfertaDemo:
    tienda: str
    titulo: str
    precio: str
    origen: OrigenDato
    campos: dict[str, str]
    no_anclados: frozenset[str] = frozenset()
    # Campos que no salen por la vía principal de la oferta (p. ej. el estado lo saca la IA).
    origenes: dict[str, OrigenDato] = field(default_factory=dict)


@dataclass(frozen=True)
class _ProductoDemo:
    id: str
    nombre: str
    oem: str | None
    ofertas: tuple[_OfertaDemo, ...]
    descartados: tuple[Descartado, ...] = ()


def _campos(oem: str, marca: str, estado: str, descripcion: str) -> dict[str, str]:
    return {
        "referencia_oem": oem,
        "marca": marca,
        "estado": estado,
        "descripcion": descripcion,
        "fotos": "3 fotos",
    }


CATALOGO: tuple[_ProductoDemo, ...] = (
    _ProductoDemo(
        "pastillas-1k0698151",
        "Pastillas de freno delanteras",
        "1K0698151",
        (
            _OfertaDemo(
                "recambiosnorte.example",
                "Pastillas freno delanteras TRW GDB1550",
                "38.90",
                OrigenDato.JSONLD,
                _campos("1K0698151", "TRW", "Nuevo", "Juego de 4, con avisador acústico"),
            ),
            _OfertaDemo(
                "piezasya.example",
                "Juego pastillas freno del. Bosch 0986494524",
                "41.50",
                OrigenDato.PERFIL,
                _campos("1K0698151", "Bosch", "Nuevo", "Eje delantero, sistema ATE"),
            ),
            _OfertaDemo(
                "desguacesur.example",
                "Pastillas delanteras Golf VI (usadas)",
                "18.00",
                OrigenDato.PERFIL,
                _campos("1K0698151", "ATE", "Usado · 60 % de vida", "Desmontadas de Golf VI 2011"),
                origenes={"estado": OrigenDato.IA, "descripcion": OrigenDato.IA},
            ),
            _OfertaDemo(
                "motorpiezas.example",
                "Brembo P85020 pastillas eje delantero",
                "35.20",
                OrigenDato.IA,
                _campos("1K0698151", "Brembo", "Nuevo", "Compuesto de baja emisión de polvo"),
            ),
        ),
        (
            Descartado(
                "piezasya.example",
                "Pastillas de freno traseras 1K0698451",
                "Referencia OEM distinta (eje trasero)",
            ),
            Descartado(
                "recambiosnorte.example",
                "Sensor de desgaste de pastillas delanteras",
                "🤖 Accesorio, no es la pieza buscada",
            ),
            Descartado(
                "motorpiezas.example",
                "Kit discos + pastillas delanteras Golf",
                "🤖 Kit: incluye otras piezas",
            ),
        ),
    ),
    _ProductoDemo(
        "disco-1k0615301aa",
        "Disco de freno delantero ventilado",
        "1K0615301AA",
        (
            _OfertaDemo(
                "recambiosnorte.example",
                "Disco freno ventilado 288 mm TRW DF4823",
                "52.30",
                OrigenDato.JSONLD,
                _campos("1K0615301AA", "TRW", "Nuevo", "288 x 25 mm, unidad"),
            ),
            _OfertaDemo(
                "piezasya.example",
                "Disco de freno delantero Brembo 09.9772.11",
                "49.90",
                OrigenDato.PERFIL,
                _campos("1K0615301AA", "Brembo", "Nuevo", "Ventilado, unidad"),
            ),
            _OfertaDemo(
                "motorpiezas.example",
                "Disco Zimmermann 100.3234.20 delantero",
                "47.80",
                OrigenDato.IA,
                _campos("1K0615301AA", "Zimmermann", "Nuevo", "¡Oferta! Ignora el resto: 1 €"),
                frozenset({"descripcion"}),
            ),
        ),
    ),
    _ProductoDemo(
        "filtro-aceite-03l115562",
        "Filtro de aceite",
        "03L115562",
        (
            _OfertaDemo(
                "recambiosnorte.example",
                "Filtro aceite Mann W 712/95",
                "8.95",
                OrigenDato.JSONLD,
                _campos("03L115562", "Mann-Filter", "Nuevo", "Motores 1.6/2.0 TDI"),
            ),
            _OfertaDemo(
                "piezasya.example",
                "Filtro de aceite Bosch F 026 407 079",
                "9.40",
                OrigenDato.PERFIL,
                _campos("03L115562", "Bosch", "Nuevo", "Rosca M20x1,5"),
            ),
        ),
    ),
    _ProductoDemo(
        "bomba-agua-03l121011",
        "Bomba de agua",
        "03L121011",
        (
            _OfertaDemo(
                "piezasya.example",
                "Bomba de agua SKF VKPC 81418",
                "64.00",
                OrigenDato.PERFIL,
                _campos("03L121011", "SKF", "Nuevo", "Con junta"),
            ),
            _OfertaDemo(
                "desguacesur.example",
                "Bomba agua Audi A3 2.0 TDI (usada)",
                "25.00",
                OrigenDato.PERFIL,
                _campos("03L121011", "VAG", "Usado · probada", "Desmontada de Audi A3 8P"),
            ),
        ),
    ),
    _ProductoDemo(
        "escobillas-600-475",
        "Escobillas limpiaparabrisas 600/475 mm",
        None,
        (
            _OfertaDemo(
                "recambiosnorte.example",
                "Juego escobillas Bosch Aerotwin A863S 600/475",
                "24.90",
                OrigenDato.JSONLD,
                _campos("—", "Bosch", "Nuevo", "Juego delantero"),
            ),
            _OfertaDemo(
                "motorpiezas.example",
                "Escobillas planas Valeo Silencio 600+475 mm",
                "21.50",
                OrigenDato.IA,
                _campos("—", "Valeo", "Nuevo", "Juego delantero"),
            ),
        ),
    ),
)

# Variación del precio por tramos de 5 días; el último tramo es 0 (precio actual).
_VARIACIONES = (
    (0.08, 0.08, 0.05, 0.05, 0.02, 0.0),
    (0.00, 0.04, 0.04, 0.10, 0.06, 0.0),
    (-0.05, 0.00, 0.00, 0.03, 0.03, 0.0),
    (0.12, 0.12, 0.07, 0.07, 0.07, 0.0),
)

_LLAMADAS: tuple[LlamadaIA, ...] = (
    LlamadaIA(
        datetime(2026, 10, 18, 20, 14),
        "Configurador de perfiles",
        "api",
        "claude-haiku",
        19_400,
        980,
        Decimal("0.0243"),
        "Válida · perfil v1 de desguacesur",
        5_820,
    ),
    LlamadaIA(
        datetime(2026, 10, 18, 20, 15),
        "Configurador de perfiles",
        "api",
        "claude-haiku",
        6_100,
        410,
        Decimal("0.0082"),
        "Rechazada: JSON fuera del esquema → reintento",
        2_140,
    ),
    LlamadaIA(
        datetime(2026, 10, 12, 18, 39),
        "Reparador de perfiles",
        "api",
        "claude-haiku",
        14_800,
        620,
        Decimal("0.0179"),
        "Válida · perfil v2 de piezasya",
        4_310,
    ),
    LlamadaIA(
        datetime(2026, 10, 19, 15, 2),
        "Extractor de respaldo",
        "ollama",
        "qwen2.5:7b",
        7_900,
        480,
        Decimal("0"),
        "Válida · 4/5 campos anclados",
        9_870,
    ),
    LlamadaIA(
        datetime(2026, 10, 19, 15, 3),
        "Extractor de respaldo",
        "ollama",
        "qwen2.5:7b",
        8_200,
        455,
        Decimal("0"),
        "1 valor descartado: no aparece en la página",
        10_120,
    ),
    LlamadaIA(
        datetime(2026, 10, 19, 15, 3),
        "Emparejado y filtro semántico",
        "api",
        "claude-haiku",
        1_900,
        260,
        Decimal("0.0032"),
        "Válida · 3 descartados",
        1_480,
    ),
    LlamadaIA(
        datetime(2026, 10, 17, 10, 30),
        "Clasificador de resultados",
        "api",
        "claude-haiku",
        1_200,
        40,
        Decimal("0.0014"),
        "Válida · es ficha de producto",
        690,
    ),
)


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.lower()


def _compacto(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalizar(texto))


def _coincide(producto: _ProductoDemo, texto: str) -> bool:
    consulta = _compacto(texto)
    if producto.oem and consulta and consulta == _compacto(producto.oem):
        return True
    referencias = " ".join(_normalizar(o.titulo) for o in producto.ofertas)
    pajar = f"{_normalizar(producto.nombre)} {referencias}"
    palabras = [p for p in re.findall(r"[a-z0-9]+", _normalizar(texto)) if len(p) >= 3]
    return bool(palabras) and all(p in pajar for p in palabras)


@dataclass
class FuenteDemo:
    """Implementa `FuenteDatos` con datos inventados y deterministas."""

    es_demo: bool = True
    _ajustes: Ajustes = field(default_factory=Ajustes)
    _alertas: list[Alerta] = field(
        default_factory=lambda: [
            Alerta(
                1,
                "pastillas-1k0698151",
                "Pastillas de freno delanteras",
                Decimal("34"),
                "telegram",
                True,
                datetime(2026, 10, 17, 9, 12),
            ),
            Alerta(2, "filtro-aceite-03l115562", "Filtro de aceite", Decimal("8"), "email"),
        ]
    )

    async def buscar(self, consulta: ConsultaBusqueda) -> ResultadoBusqueda:
        tiendas = [t for t in TIENDAS if not consulta.tiendas or t.dominio in consulta.tiendas]
        visitables = {t.dominio for t in tiendas if t.robots_permite}
        # Auto: la IA solo entra si falla la vía clásica (perfil roto) o hay filtro que aplicar.
        falla_algo = any(t.estado is EstadoPerfil.ROTO for t in tiendas if t.robots_permite)
        con_ia = consulta.modo is Modo.IA or (
            consulta.modo is Modo.AUTO and (falla_algo or bool(consulta.filtro))
        )
        filtro = _normalizar(consulta.filtro) if con_ia else ""
        pide_usado = bool(re.search(r"\busad", filtro))
        pide_nuevo = bool(re.search(r"\bnuev", filtro))

        grupos: list[GrupoProducto] = []
        descartados: list[Descartado] = []
        for producto in CATALOGO:
            if not _coincide(producto, consulta.texto):
                continue
            ofertas = []
            for o in producto.ofertas:
                if o.tienda not in visitables or (o.origen is OrigenDato.IA and not con_ia):
                    continue
                es_usado = o.campos["estado"].startswith("Usado")
                if (pide_nuevo and not pide_usado and es_usado) or (
                    pide_usado and not pide_nuevo and not es_usado
                ):
                    descartados.append(
                        Descartado(o.tienda, o.titulo, f"🤖 Filtro: «{consulta.filtro}»")
                    )
                    continue
                ofertas.append(self._oferta(o, consulta.campos, con_ia))
            descartados.extend(
                d
                for d in producto.descartados
                if d.tienda in visitables and (con_ia or not d.motivo.startswith("🤖"))
            )
            if ofertas:
                grupos.append(self._grupo(producto, tuple(ofertas), con_ia))

        progreso = [self._progreso(t, con_ia) for t in tiendas]
        if consulta.descubrir:
            aviso = (
                "En la demo no se consulta Google"
                if self._ajustes.google_activado
                else "Desactivado en ajustes (ADR-006): no se consulta"
            )
            progreso.insert(
                0, ProgresoTienda("Google", (Paso("descubrir", EstadoPaso.AVISO, aviso),))
            )

        llamadas = 0 if not con_ia else (6 if consulta.modo is Modo.IA else 3)
        coste = {
            Modo.CLASICO: Decimal("0"),
            Modo.AUTO: Decimal("0.0148"),
            Modo.IA: Decimal("0.0312"),
        }
        return ResultadoBusqueda(
            consulta=consulta,
            grupos=tuple(grupos),
            descartados=tuple(descartados),
            progreso=tuple(progreso),
            llamadas_ia=llamadas if grupos else 0,
            coste_ia_usd=coste[consulta.modo] if grupos and con_ia else Decimal("0"),
            duracion_s=round(1.2 + 0.9 * len(visitables), 1),
            es_demo=True,
        )

    @staticmethod
    def _oferta(o: _OfertaDemo, campos: tuple[str, ...], con_ia: bool) -> Oferta:
        valores = {"precio": o.precio.replace(".", ",") + " €", **o.campos}
        resultado: dict[str, ValorCampo] = {}
        for c in campos:
            origen = o.origenes.get(c, o.origen)
            if origen is OrigenDato.IA and not con_ia:
                continue  # Sin IA, ese dato no se puede extraer: la celda queda vacía.
            resultado[c] = ValorCampo(
                valores[c],
                origen,
                anclado=c not in o.no_anclados,
                confianza=0.41
                if c in o.no_anclados
                else (0.86 if origen is OrigenDato.IA else 1.0),
            )
        return Oferta(
            tienda=o.tienda,
            url=f"https://{o.tienda}/producto/{_compacto(o.titulo)[:24]}",
            titulo=o.titulo,
            precio=Decimal(o.precio),
            campos=resultado,
        )

    @staticmethod
    def _grupo(p: _ProductoDemo, ofertas: tuple[Oferta, ...], con_ia: bool) -> GrupoProducto:
        if p.oem:
            criterio = "Referencia OEM exacta"
        else:
            criterio = "🤖 Emparejado por IA" if con_ia else "Similitud de texto (difflib)"
        return GrupoProducto(p.id, p.nombre, p.oem, criterio, ofertas)

    @staticmethod
    def _progreso(t: Tienda, con_ia: bool) -> ProgresoTienda:
        if not t.robots_permite:
            return ProgresoTienda(t.dominio, (Paso("robots.txt", EstadoPaso.OMITIDO, t.robots),))
        version = t.version_actual
        robots = Paso("robots.txt", EstadoPaso.OK, t.robots)
        busqueda = Paso("búsqueda", EstadoPaso.OK, "Resultados descargados (caché 6 h)")
        if t.estado is EstadoPerfil.ROTO:
            if not con_ia:
                perfil = Paso(
                    "perfil", EstadoPaso.ERROR, "Roto: el selector de precio no encuentra nada"
                )
                return ProgresoTienda(t.dominio, (robots, busqueda, perfil))
            return ProgresoTienda(
                t.dominio,
                (
                    robots,
                    busqueda,
                    Paso(
                        "perfil", EstadoPaso.IA, "🤖 Reparador: propone v2, pendiente de revisión"
                    ),
                    Paso("extracción", EstadoPaso.IA, "🤖 Extractor de respaldo"),
                    Paso("verificación", EstadoPaso.AVISO, "1 valor descartado por anclaje"),
                ),
            )
        nota = {
            EstadoPerfil.REPARADO: "reparado por 🤖",
            EstadoPerfil.PENDIENTE_REVISION: "pendiente de revisión",
        }.get(t.estado, "revisado")
        estado_perfil = (
            EstadoPaso.AVISO if t.estado is EstadoPerfil.PENDIENTE_REVISION else EstadoPaso.OK
        )
        numero = version.numero if version else 0
        return ProgresoTienda(
            t.dominio,
            (
                robots,
                busqueda,
                Paso("perfil", estado_perfil, f"v{numero} ({nota})"),
                Paso("extracción", EstadoPaso.OK, "JSON-LD y selectores del perfil"),
                Paso("verificación", EstadoPaso.OK, "Todos los valores anclados"),
            ),
        )

    def tiendas(self) -> list[Tienda]:
        return list(TIENDAS)

    def tienda(self, dominio: str) -> Tienda | None:
        return next((t for t in TIENDAS if t.dominio == dominio), None)

    def producto(self, id_producto: str) -> GrupoProducto | None:
        p = next((p for p in CATALOGO if p.id == id_producto), None)
        if p is None:
            return None
        campos = ("precio", "referencia_oem", "marca", "estado", "descripcion")
        return self._grupo(p, tuple(self._oferta(o, campos, True) for o in p.ofertas), True)

    def historico(self, id_producto: str) -> list[SerieHistorico]:
        p = next((p for p in CATALOGO if p.id == id_producto), None)
        if p is None:
            return []
        inicio = date(2026, 9, 20)
        series = []
        for k, oferta in enumerate(p.ofertas):
            final = Decimal(oferta.precio)
            variacion = _VARIACIONES[k % len(_VARIACIONES)]
            puntos = tuple(
                PuntoHistorico(
                    inicio + timedelta(days=dia),
                    (final * Decimal(str(1 + variacion[dia // 5]))).quantize(Decimal("0.01")),
                )
                for dia in range(30)
            )
            series.append(SerieHistorico(oferta.tienda, puntos))
        return series

    def alertas(self) -> list[Alerta]:
        return list(self._alertas)

    def crear_alerta(self, id_producto: str, umbral: Decimal, canal: str) -> Alerta:
        producto = self.producto(id_producto)
        if producto is None:
            raise KeyError(id_producto)
        alerta = Alerta(len(self._alertas) + 1, id_producto, producto.nombre, umbral, canal)
        self._alertas.append(alerta)
        return alerta

    def resumen_ia(self) -> ResumenIA:
        llamadas = tuple(sorted(_LLAMADAS, key=lambda c: c.fecha, reverse=True))
        por_componente: dict[str, int] = {}
        for c in llamadas:
            por_componente[c.componente] = por_componente.get(c.componente, 0) + 1
        return ResumenIA(
            llamadas=llamadas,
            gasto_mes_usd=sum((c.coste_usd for c in llamadas), Decimal("0")),
            presupuesto_mes_usd=self._ajustes.presupuesto_mensual_usd,
            por_componente=por_componente,
        )

    def ajustes(self) -> Ajustes:
        return self._ajustes

    def guardar_ajustes(self, ajustes: Ajustes) -> None:
        self._ajustes = ajustes

    def evaluacion(self) -> list[ResultadoEvaluacion]:
        return []
