# Uso de IA en el desarrollo de PrecIA

El enunciado exige documentar en cada entrega todo uso de IA. Este fichero se genera a partir del **diario de sesiones** del vault (`../precia-vault/10 - Diario de Sesiones.md`): cada entrada del diario registra qué generó la IA, con qué petición y qué se decidió.

**Herramienta principal:** Claude Code (agente en terminal) sobre este repositorio.
**Flujo:** definición de la tarea con criterio de aceptación → generación (TDD: primero el test) → revisión humana → `ruff` + `mypy` + `pytest` en local y en GitHub Actions → commit marcado con `Co-Authored-By`.

---

## Entrega en curso (desde 2026-10-19)

**Sesiones cubiertas:** S01 (2026-10-19) y S03 (2026-10-22, sección propia más abajo). S02 (2026-10-21) está en su nota del diario y falta pasarla aquí.

### Tareas asistidas por IA

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-19 | Montar el vault de Obsidian del proyecto | Claude Code | 14 notas base a partir de la memoria, plantilla de sesión y configuración | Pendiente de revisión |
| 2026-10-19 | `CLAUDE.md` con protocolo de diario de sesión | Claude Code | Fichero completo | Pendiente de revisión |
| 2026-10-19 | Esqueleto del repositorio | Claude Code | `pyproject.toml`, paquetes de la memoria §6, test de humo, CI, este fichero | Pendiente de revisión |
| 2026-10-19 | Commit inicial, repositorio privado en GitHub y CI | Claude Code | Commit, `gh repo create`, actualización de las acciones de la CI (Node 20 obsoleto) | Repositorio y push aceptados por Julio; cambio de la CI pendiente de revisión |
| 2026-10-19 | Interfaz web completa H1W (8 pantallas) con datos de ejemplo | Claude Code | Modelos, fuente de ejemplo, app FastAPI, middleware de seguridad, plantillas, CSS, gráfica SVG, 139 tests | Pendiente de revisión |
| 2026-10-19 | Revisión visual en el navegador | Claude Code + Playwright | Capturas en escritorio, móvil, claro y oscuro; 9 correcciones de UX | Aceptadas por Claude; pendiente de revisión |
| 2026-10-19 | Revisión adversarial de H1W | Claude Code (workflow, 3 subagentes: pentester, código, fidelidad/UX) | 25 hallazgos (21 distintos) reproducidos con 28 tests en rojo y corregidos | Pendiente de revisión |
| 2026-10-19 | Revisión adversarial del montaje | Claude Code (workflow, 3 subagentes de solo lectura) | 16 hallazgos (fidelidad a la memoria, protocolo del diario, esqueleto) | Aceptados y corregidos por Claude; pendiente de revisión |

### Prompts relevantes

- «Hay que hacer el proyecto. Empieza conectando el vault y añade el CLAUDE.md que, conforme vaya haciendo cosas en la sesión, vaya escribiendo qué hace.»
- «Ahora haz un cuaderno de bitácora donde apuntes los avances, fallos, qué haces y eso, además del vault, para hacer después la memoria.»
- «Según el planning que hay que hacer, yo lo haría versión web, igual es más fácil de ver, se ve más bonito» + «hacer todo el proyecto web, al menos la UX, para que sea más simple y fácil de ver».

### Aceptado, modificado y descartado

- **Corregido tras la revisión con IA:** el registro del diario de S01 se había escrito al final y no en el momento (se reconoce en la nota y se endurece el protocolo); `setuptools` se había añadido sin aprobación (pasa a pendiente); el resumen legal suavizaba la fuente (reescrito); `.gitignore` demasiado amplio; faltaba `tests/__init__.py`.
- *(El resto se completa tras la revisión de Julio.)*

### Validación

- Test de humo escrito **antes** que los paquetes: falló (11 de 12, `ModuleNotFoundError`) y pasó tras crearlos (12/12).
- `ruff check`, `ruff format --check` y `mypy --strict` sin errores.
- Revisión adversarial con 3 subagentes; los hallazgos del código se reprodujeron antes de corregirlos.

---

## S03 (2026-10-22): TLS y HTTP/2 en el scraper

Fuente: `../precia-vault/sesiones/2026-10-22 S03 - TLS y HTTP2 en el scraper.md`. Rama `core/tls-http2` (desde `083851b`), **sin commit**.

### Qué se pidió

> «todo lo que vayas ha hacer en esta sesion va a ser con sub agentes si algun subajentes se corta por que se bloquee mira por que se acortado vuelve a escribir como lo va ha hacer y vuelve a lanzarlo tienes que arreglar que se pueda usar TLS/HTTP2 para mejorar la eficacia del scraper»

Decisiones de Julio durante la sesión, a preguntas de Claude Code con alternativas: «Chromium + curl_cffi» (con aprobación explícita de `curl_cffi==0.16.3`, `cffi`, `pycparser` y `certifi`), «Las de Chrome» (cabeceras), «Commit de core/proxy primero», «curl por defecto (Recommended)», «la que sea mejor» (proxy local), «Aprobar ADR-023 entero (Recommended)» y «Chromium por el proxy (Recommended)». Para seguir: «co tinua».

### Cómo se usó la IA

- **Orquestación con subagentes** (Workflow de Claude Code): toda la sesión la hicieron subagentes lanzados por un guion de orquestación; la sesión principal repartía las tareas, recogía los resultados y relanzaba los que se cortaban.
- **Diseño con crítica adversarial:** un subagente planificador propuso el plan de cada vía (curl y Chromium) y un crítico adversarial lo atacó antes de escribir código. El plan corregido salió de esa crítica. Las preguntas que no podía decidir la IA se le hicieron a Julio, y sus respuestas quedaron como ADR-022 y ADR-023.
- **Implementación con TDD:** subagentes implementadores, siempre con el test en rojo antes del código. Cuando el test cubría código que ya existía (y no podía empezar en rojo), se comprobó que muerde con **mutantes** (se rompe el código a propósito, el test tiene que fallar, y se restaura con `diff -q`).
- **Revisión con verificación adversarial:** revisores por lentes (seguridad, corrección, tests y reglas). Cada hallazgo se verificó (reproducido o con un mutante) antes de darlo por bueno. Se corrigieron los 5 confirmados de gravedad media y 4 de gravedad baja.
- **Medición real:** un guion fuera del repo midió la huella (tls.peet.ws) y la eficacia contra 5 tiendas, sin proxy, con una petición por vía y URL.

### Tareas asistidas por IA

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-22 | Commit del proxy (`083851b` en `core/proxy`) y rama `core/tls-http2` | Claude Code | Revisión de secretos, mensaje con `Co-Authored-By`, entradas de la bitácora | Commit autorizado por Julio; el código del proxy sigue pendiente de revisión |
| 2026-10-22 | Diseño de TLS/HTTP2 (vía curl y vía Chromium) | Claude Code (Workflow: planificador + crítico adversarial) | Preguntas con alternativas, planes corregidos, ADR-022 y ADR-023 | ADR aprobadas por Julio; planes pendientes de revisión |
| 2026-10-22 | Instalar `curl_cffi` y registrarlo | Claude Code | `pip install`, línea de `pyproject.toml`, 4 filas en la nota 06 | Aprobado por Julio de forma explícita |
| 2026-10-22 | Vía curl (ADR-022) | Claude Code (subagente implementador, TDD) | `navegador/curl.py` (`TransporteCurl`), protocolo `TransporteNavegador`, `RespuestaCruda.descomprimida`, ajuste «Cliente HTTP» con `curl` por defecto, cableado en `FuenteReal`; +95 tests | Pendiente de revisión |
| 2026-10-22 | Vía Chromium (ADR-023) | Claude Code (subagente implementador, TDD) | Relé SOCKS5 que valida cada conexión, `chromium.py` con red propia y huella de Chrome por CDP, rescate con Chromium en `navegador.py`, 403 fuera de `RECHAZOS`; +72 tests | Pendiente de revisión |
| 2026-10-22 | Revisión adversarial de la rama | Claude Code (Workflow: revisores por lentes + verificación de cada hallazgo) | 5 hallazgos de gravedad media y 4 de baja, corregidos con tests (+18) | Correcciones pendientes de revisión; el de la CI lo decide Julio |
| 2026-10-22 | Medición real de la huella y de la eficacia | Claude Code | 2 guiones fuera del repo, 18 peticiones de primer nivel, tablas en la bitácora | Resultado registrado (📏, ✅ y ❌ en `BITACORA.md`) |
| 2026-10-22 | Cierre documental | Claude Code | Notas 03, 04, 06, 08, 12, 14 y 15 del vault, resumen de la sesión, esta sección | Pendiente de revisión |

### Aceptado, modificado y descartado

- **Aceptado por Julio:** ADR-022 y ADR-023, `curl` como transporte por defecto, las cuatro dependencias, el commit de `core/proxy`.
- **Elegido por la IA con delegación de Julio («la que sea mejor»):** con un proxy local, el transporte del proxy sigue siendo `urllib` (sin la API privada `curl_cffi._wrapper`). Pendiente de revisión.
- **Descartado durante el diseño:** Chrome for Testing (`channel='chromium'`), porque hace DoH a `dns.google` saltándose el relé; `CONNECT_TO` con la API privada de `curl_cffi`; `httpx[http2]` y un HTTP/2 propio sobre `ssl` (no alcanzan la huella de Chrome).
- **Desviaciones del plan hechas por la IA** (anotadas en el diario y pendientes de revisión): el descargador pasa todas sus cabeceras al transporte; un valor desconocido de `transporte_http` usa `curl`; parámetros `puertos` y `abrir_conexion` del relé, solo para tests; `ReleProxy.cerrar()` con un plazo de 1 s; el test de netlog admite la sonda IPv6 de Chromium (sin bytes enviados); campo nuevo `Pagina.por_chromium` en el contrato.
- **Corregido tras la revisión adversarial:** relé SOCKS5 que podía ocupar el executor por defecto, errores de Playwright sin traducir y Chromium muerto reutilizado, tres tests de seguridad de curl que no podían fallar, doble descarga con Chromium, y cuatro de gravedad baja (plazo de menos de 1 ms, IP del proxy fija, textos del User-Agent y del 429).
- **No cambiado:** instalar el headless shell en la CI (lo decide Julio).

### Errores de la IA detectados en la sesión

También están en la tabla «Errores de la IA detectados» de `BITACORA.md`.

- Un **subagente planificador** lanzó experimentos con Chrome for Testing que salieron a Internet (DoH a 8.8.8.8 y `dns.google`), aunque sus instrucciones decían «no hagas peticiones a Internet». Lo detectó el crítico adversarial en el netlog.
- Un test del relé, escrito por la IA en una sesión anterior, leía el túnel con una sola `read` y fallaba 3 de cada 5 veces.
- Un test de cookies esperaba que una cookie solo de host llegara a `www.`: curl tenía razón y el test estaba mal.
- Los 5 hallazgos de gravedad media de la revisión eran errores del código generado en la misma sesión. El más grave: una página hostil podía congelar la aplicación a través del relé.
- Errores menores que salieron en la primera ejecución y se corrigieron: un `NameError` (faltaba el logger), un test con un plazo mal puesto, un test de DNS lento que pasaba en falso y un `contextlib.suppress` usado como decorador.
- **Protocolo del diario:** por cuarta vez en el proyecto, horas escritas antes de consultar el reloj (corregidas o avisadas en la nota). En la medición, un guion se escribió antes de anotar la entrada de la ejecución anterior. Además, una frase de la bitácora afirmaba sin comprobarlo por qué Chromium no supera el AWS WAF; se rebajó.

### Validación

- TDD en cada paso: rojo antes de implementar (o mutante si el código ya existía) y verde después.
- Puertas finales: `ruff check .`, `ruff format --check .` y `mypy` (180 ficheros) en verde. `pytest` con los tests de Chromium real: 3305 en verde de 3307. Fallan los 2 intermitentes conocidos de cronómetro de `test_embebidos`, en un fichero que la rama no toca. `test_curl*.py` + `test_rele.py`, 5 veces seguidas: 133 en verde las 5.
- Medición real, sin proxy: con curl, Oscaro, Ecooparts y Ovoko pasan del desafío de Cloudflare a la portada. eBay solo pasa con el rescate de Chromium. La búsqueda de B-Parts sigue bloqueada por AWS WAF con las tres vías.
- **Todo el código de la sesión queda pendiente de revisión de Julio.**
