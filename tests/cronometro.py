"""Cronómetro de CPU para los tests que acotan el coste de un caso hostil (bug 6).

Un tope sobre el tiempo de reloj falla en la suite completa cuando la máquina está cargada: el
reloj cuenta también lo que hacen otros procesos. Por eso aquí se mide el tiempo de CPU de este
proceso (`time.process_time`) y se toma el mejor de hasta tres intentos, que descarta las pausas
puntuales. Ni así es constante: con la frecuencia del procesador (turbo al empezar, más lenta con
carga sostenida) el mismo trabajo pasa de 0,35 a 0,9 s de CPU. De ahí las dos reglas:

- `segundos_de_cpu`: para un tope absoluto. El tope tiene que dejar al menos tres veces lo medido
  con la máquina libre; lo que protegen (un coste cuadrático, un tope que no se aplica) cuesta
  decenas de veces más y sigue pasándose en los tres intentos.
- `proporcion_de_cpu`: cuando no hay ese margen, el coste se compara con el de otra operación de
  referencia sobre la misma entrada (construir el árbol, por ejemplo), medidas una detrás de otra:
  la frecuencia afecta a las dos por igual.

Los tests que miden esperas de verdad (un timeout de red, un proceso externo como Chromium) no
usan esto: esperar no gasta CPU y un cuelgue saldría gratis. Esos siguen con el reloj y con un
margen relativo a su propio timeout.

En Windows `process_time` avanza a saltos de 15,6 ms: no sirve para topes de pocos milisegundos.
"""

import math
import time
from collections.abc import Callable

INTENTOS = 3


def _cpu(funcion: Callable[[], object]) -> float:
    inicio = time.process_time()
    funcion()
    return time.process_time() - inicio


def segundos_de_cpu(
    funcion: Callable[[], object], limite: float, intentos: int = INTENTOS
) -> float:
    """El menor tiempo de CPU, en segundos, de hasta `intentos` ejecuciones de `funcion`.

    Para en cuanto una baja de `limite`; si ninguna baja, devuelve la mejor (y el test falla).
    """
    mejor = math.inf
    for _ in range(intentos):
        mejor = min(mejor, _cpu(funcion))
        if mejor < limite:
            break
    return mejor


def proporcion_de_cpu(
    funcion: Callable[[], object],
    referencia: Callable[[], object],
    limite: float,
    intentos: int = INTENTOS,
) -> float:
    """Cuántas veces cuesta `funcion` lo que `referencia`, con el mejor de cada una.

    Se miden alternadas, hasta `intentos` veces cada una, y para en cuanto la proporción entre
    las dos mejores baja de `limite`.
    """
    mejor_funcion = mejor_referencia = math.inf
    proporcion = math.inf
    for _ in range(intentos):
        mejor_referencia = min(mejor_referencia, _cpu(referencia))
        mejor_funcion = min(mejor_funcion, _cpu(funcion))
        # Media marca de 15,6 ms de suelo: que una referencia de «0 s» no divida entre cero.
        proporcion = mejor_funcion / max(mejor_referencia, 0.008)
        if proporcion < limite:
            break
    return proporcion
