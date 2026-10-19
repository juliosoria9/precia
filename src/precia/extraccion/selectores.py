"""Motor de selectores CSS restringido (ADR-015).

Los selectores de los perfiles los escribe una persona o los genera la IA a partir de páginas no
confiables, así que el lenguaje es deliberadamente pequeño y todo lo que no está en él se
rechaza con `SelectorNoValido`:

- tipo (`div`), universal (`*`), `#id`, `.clase` (varias), `[attr]`, `[attr=v]`, `[attr~=v]`,
  `[attr^=v]`, `[attr$=v]` y `[attr*=v]` (con el valor entre comillas o sin ellas);
- combinadores descendiente (espacio) e hijo (`>`) y grupos (`,`);
- `:nth-of-type(n)` con `n` entero positivo y `:first-of-type`.

`extraer_valor` admite además el sufijo ` @atributo` para leer un atributo en vez del texto.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache

from precia.extraccion.dom import Nodo
from precia.extraccion.tipos import SelectorNoValido

MAX_LONGITUD = 512
MAX_GRUPO = 16
MAX_COMPUESTOS = 16
MAX_CONDICIONES = 16

_IDENT = re.compile(r"(?:--|-?[_a-zA-Z\u00a0-\U0010ffff])[_a-zA-Z0-9\u00a0-\U0010ffff-]*")
_VALOR_SIN_COMILLAS = re.compile(r"[_a-zA-Z0-9\u00a0-\U0010ffff-]+")
_VALOR_CON_COMILLAS = re.compile(r"\"([^\"\\\n\r]*)\"|'([^'\\\n\r]*)'")
_ESPACIO = re.compile(r"[ \t\n\r\f]*")
_NTH = re.compile(r"nth-of-type\([ \t]*([1-9][0-9]{0,5})[ \t]*\)")
_OPERADORES = ("~=", "^=", "$=", "*=", "=")
_SUFIJO_ATRIBUTO = re.compile(r"(?:^|\s+)@([A-Za-z_][A-Za-z0-9_:.-]*)\s*$")


@dataclass(frozen=True, slots=True)
class _CondicionAtributo:
    nombre: str
    operador: str | None
    valor: str


@dataclass(frozen=True, slots=True)
class _Compuesto:
    tipo: str | None
    id: str | None
    clases: tuple[str, ...]
    atributos: tuple[_CondicionAtributo, ...]
    posicion: int | None
    """Para `:nth-of-type(n)` (1 en `:first-of-type`)."""


@dataclass(frozen=True, slots=True)
class _Complejo:
    compuestos: tuple[_Compuesto, ...]
    combinadores: tuple[str, ...]
    """`combinadores[i]` une `compuestos[i]` con `compuestos[i + 1]`: `" "` o `">"`."""


def seleccionar(raiz: Nodo, selector: str) -> list[Nodo]:
    """Descendientes de `raiz` que encajan con `selector`, en orden del documento y sin repetir.

    Como `querySelectorAll`: para encajar cuentan también `raiz` y sus ancestros.
    """
    if not isinstance(selector, str):
        raise SelectorNoValido("El selector debe ser texto.")
    grupo = _analizar(selector)
    contexto = _Contexto()
    comprobadores = [contexto.comprobador(complejo) for complejo in grupo]
    return [n for n in raiz.descendientes() if any(c(n) for c in comprobadores)]


def extraer_valor(raiz: Nodo, selector: str) -> str | None:
    """Texto (o atributo, con ` @atributo`) de la primera coincidencia que no esté vacía.

    `"@href"` sin selector lee el atributo de la propia `raiz`.
    """
    return next(_valores(raiz, selector), None)


def extraer_valores(raiz: Nodo, selector: str) -> list[str]:
    """Como `extraer_valor`, pero con todas las coincidencias que no estén vacías."""
    return list(_valores(raiz, selector))


def dividir_selector(selector: str) -> tuple[str, str | None]:
    """`"img.foto @src"` → `("img.foto", "src")`; sin sufijo, el atributo es `None`."""
    if not isinstance(selector, str):
        raise SelectorNoValido("El selector debe ser texto.")
    # Antes de la expresión del sufijo, que es cuadrática con muchos espacios seguidos.
    if len(selector) > MAX_LONGITUD + 64:
        raise SelectorNoValido(f"Selector demasiado largo (máximo {MAX_LONGITUD} caracteres).")
    limpio = selector.strip()
    sufijo = _SUFIJO_ATRIBUTO.search(limpio)
    if sufijo is None:
        return limpio, None
    return limpio[: sufijo.start()].strip(), sufijo.group(1).lower()


def _valores(raiz: Nodo, selector: str) -> Iterator[str]:
    css, atributo = dividir_selector(selector)
    # Se analiza ya para que un selector no válido falle aunque no se consuma el iterador.
    nodos = [raiz] if atributo is not None and not css else seleccionar(raiz, css)
    return (
        valor
        for nodo in nodos
        if (valor := nodo.texto() if atributo is None else (nodo.atributo(atributo) or "").strip())
    )


class _Contexto:
    """Memoria de una llamada a `seleccionar`, para no recorrer los ancestros una y otra vez."""

    def __init__(self) -> None:
        self._posiciones: dict[int, dict[int, int]] = {}

    def posicion_de_tipo(self, nodo: Nodo) -> int:
        padre = nodo.padre
        if padre is None:
            return 1
        posiciones = self._posiciones.get(id(padre))
        if posiciones is None:
            posiciones = {}
            contadores: dict[str, int] = {}
            for hermano in padre.hijos:
                contadores[hermano.etiqueta] = contadores.get(hermano.etiqueta, 0) + 1
                posiciones[id(hermano)] = contadores[hermano.etiqueta]
            self._posiciones[id(padre)] = posiciones
        return posiciones[id(nodo)]

    def comprobador(self, complejo: _Complejo) -> Callable[[Nodo], bool]:
        compuestos = complejo.compuestos
        combinadores = complejo.combinadores
        encaja_memo: dict[tuple[int, int], bool] = {}
        # (i, nodo) → el nodo o alguno de sus ancestros encaja con los compuestos 0..i.
        incluye_memo: dict[tuple[int, int], bool] = {}

        def encaja(nodo: Nodo, i: int) -> bool:
            clave = (i, id(nodo))
            resultado = encaja_memo.get(clave)
            if resultado is not None:
                return resultado
            resultado = self.encaja_compuesto(nodo, compuestos[i])
            if resultado and i > 0:
                padre = _padre_elemento(nodo)
                if combinadores[i - 1] == ">":
                    resultado = padre is not None and encaja(padre, i - 1)
                else:
                    resultado = padre is not None and incluye(padre, i - 1)
            encaja_memo[clave] = resultado
            return resultado

        def incluye(nodo: Nodo, i: int) -> bool:
            camino: list[Nodo] = []
            actual: Nodo | None = nodo
            resultado = False
            while actual is not None:
                previo = incluye_memo.get((i, id(actual)))
                if previo is not None:
                    resultado = previo
                    break
                camino.append(actual)
                if encaja(actual, i):
                    resultado = True
                    break
                actual = _padre_elemento(actual)
            for visitado in camino:
                incluye_memo[(i, id(visitado))] = resultado
            return resultado

        ultimo = len(compuestos) - 1
        return lambda nodo: encaja(nodo, ultimo)

    def encaja_compuesto(self, nodo: Nodo, compuesto: _Compuesto) -> bool:
        if compuesto.tipo is not None and nodo.etiqueta != compuesto.tipo:
            return False
        if compuesto.id is not None and nodo.atributos.get("id") != compuesto.id:
            return False
        if compuesto.clases:
            clases = nodo.clases
            if not all(c in clases for c in compuesto.clases):
                return False
        for condicion in compuesto.atributos:
            if not _encaja_atributo(nodo.atributos.get(condicion.nombre), condicion):
                return False
        return compuesto.posicion is None or self.posicion_de_tipo(nodo) == compuesto.posicion


def _padre_elemento(nodo: Nodo) -> Nodo | None:
    padre = nodo.padre
    return None if padre is None or padre.padre is None else padre


def _encaja_atributo(valor: str | None, condicion: _CondicionAtributo) -> bool:
    if valor is None:
        return False
    esperado = condicion.valor
    match condicion.operador:
        case None:
            return True
        case "=":
            return valor == esperado
        case "~=":
            return (
                bool(esperado)
                and not any(c.isspace() for c in esperado)
                and (esperado in valor.split())
            )
        case "^=":
            return bool(esperado) and valor.startswith(esperado)
        case "$=":
            return bool(esperado) and valor.endswith(esperado)
        case _:
            return bool(esperado) and esperado in valor


@lru_cache(maxsize=512)
def _analizar(selector: str) -> tuple[_Complejo, ...]:
    if len(selector) > MAX_LONGITUD:
        raise SelectorNoValido(f"Selector demasiado largo (máximo {MAX_LONGITUD} caracteres).")
    return _Analizador(selector).grupo()


class _Analizador:
    def __init__(self, selector: str) -> None:
        self.texto = selector
        self.pos = 0

    def error(self, motivo: str) -> SelectorNoValido:
        return SelectorNoValido(f"Selector no admitido ({motivo}) en la posición {self.pos}.")

    def fin(self) -> bool:
        return self.pos >= len(self.texto)

    def actual(self) -> str:
        return self.texto[self.pos] if self.pos < len(self.texto) else ""

    def saltar_espacios(self) -> bool:
        fin = _ESPACIO.match(self.texto, self.pos)
        assert fin is not None
        avanzado = fin.end() > self.pos
        self.pos = fin.end()
        return avanzado

    def leer(self, patron: re.Pattern[str]) -> re.Match[str] | None:
        encontrado = patron.match(self.texto, self.pos)
        if encontrado is not None:
            self.pos = encontrado.end()
        return encontrado

    def grupo(self) -> tuple[_Complejo, ...]:
        complejos = [self.complejo()]
        while not self.fin():
            if self.actual() != ",":
                raise self.error(f"carácter inesperado {self.actual()!r}")
            self.pos += 1
            complejos.append(self.complejo())
            if len(complejos) > MAX_GRUPO:
                raise self.error(f"más de {MAX_GRUPO} selectores en el grupo")
        return tuple(complejos)

    def complejo(self) -> _Complejo:
        self.saltar_espacios()
        compuestos = [self.compuesto()]
        combinadores: list[str] = []
        while True:
            hubo_espacio = self.saltar_espacios()
            if self.fin() or self.actual() == ",":
                break
            if self.actual() == ">":
                self.pos += 1
                self.saltar_espacios()
                combinadores.append(">")
            elif hubo_espacio:
                combinadores.append(" ")
            else:
                raise self.error(f"carácter inesperado {self.actual()!r}")
            compuestos.append(self.compuesto())
            if len(compuestos) > MAX_COMPUESTOS:
                raise self.error(f"más de {MAX_COMPUESTOS} pasos")
        return _Complejo(tuple(compuestos), tuple(combinadores))

    def compuesto(self) -> _Compuesto:
        inicio = self.pos
        tipo: str | None = None
        id_: str | None = None
        clases: list[str] = []
        atributos: list[_CondicionAtributo] = []
        posicion: int | None = None
        if self.actual() == "*":
            self.pos += 1
        elif (ident := self.leer(_IDENT)) is not None:
            tipo = ident.group().lower()
        condiciones = 0
        while not self.fin():
            caracter = self.actual()
            if caracter == "#":
                self.pos += 1
                if id_ is not None:
                    raise self.error("dos #id en el mismo paso")
                id_ = self.identificador()
            elif caracter == ".":
                self.pos += 1
                clases.append(self.identificador())
            elif caracter == "[":
                self.pos += 1
                atributos.append(self.atributo())
            elif caracter == ":":
                self.pos += 1
                if posicion is not None:
                    raise self.error("dos pseudoclases en el mismo paso")
                posicion = self.pseudoclase()
            else:
                break
            condiciones += 1
            if condiciones > MAX_CONDICIONES:
                raise self.error("demasiadas condiciones en un paso")
        if self.pos == inicio:
            raise self.error("se esperaba un selector")
        return _Compuesto(tipo, id_, tuple(clases), tuple(atributos), posicion)

    def identificador(self) -> str:
        ident = self.leer(_IDENT)
        if ident is None:
            raise self.error("identificador no válido")
        return ident.group()

    def atributo(self) -> _CondicionAtributo:
        self.saltar_espacios()
        nombre = self.identificador().lower()
        self.saltar_espacios()
        operador = next((op for op in _OPERADORES if self.texto.startswith(op, self.pos)), None)
        valor = ""
        if operador is not None:
            self.pos += len(operador)
            self.saltar_espacios()
            if (comillas := self.leer(_VALOR_CON_COMILLAS)) is not None:
                valor = comillas.group(1) if comillas.group(1) is not None else comillas.group(2)
            elif (simple := self.leer(_VALOR_SIN_COMILLAS)) is not None:
                valor = simple.group()
            else:
                raise self.error("valor de atributo no válido")
            self.saltar_espacios()
        if self.actual() != "]":
            raise self.error("se esperaba ']'")
        self.pos += 1
        return _CondicionAtributo(nombre, operador, valor)

    def pseudoclase(self) -> int:
        if self.texto.startswith("first-of-type", self.pos):
            self.pos += len("first-of-type")
            if self.leer(_IDENT_CONTINUA) is not None:
                raise self.error("pseudoclase no admitida")
            return 1
        nth = self.leer(_NTH)
        if nth is None:
            raise self.error("pseudoclase no admitida")
        return int(nth.group(1))


_IDENT_CONTINUA = re.compile(r"[_a-zA-Z0-9\u00a0-\U0010ffff(-]")
