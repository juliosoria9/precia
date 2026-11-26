# Uso de IA en el desarrollo de PrecIA

El enunciado exige documentar en cada entrega todo uso de IA. Este fichero se genera a partir del **diario de sesiones** del vault (`../precia-vault/10 - Diario de Sesiones.md`): cada entrada del diario registra qué generó la IA, con qué petición y qué se decidió.

**Herramienta principal:** Claude Code (agente en terminal) sobre este repositorio.
**Flujo:** definición de la tarea con criterio de aceptación → generación (TDD: primero el test) → revisión humana → `ruff` + `mypy` + `pytest` en local y en GitHub Actions → commit marcado con `Co-Authored-By`.

---

## Entrega en curso (desde 2026-10-19)

**Sesiones cubiertas:** S01 (2026-10-19), S02 (2026-10-21) y S03 (del 2026-10-22 al 25), cada una con su sección más abajo.

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

## S02 (2026-10-21): análisis de los bloqueos de Cloudflare

**Qué se pidió (literal):** «usa siempre sub agentes si algun sub agentes es bloqueado revisa por que ha sido bloqueado cambia lo que le pediste para que no lo bloquee y vuelve a lanzarlo», más un texto pegado con el objetivo «solucionar los bloqueos de Cloudflare en PrecIA» (analizar la arquitectura, cabeceras, huella TLS y HTTP/2, JavaScript y sesiones), que pedía, entre otras cosas, «eliminar la identificación incorrecta como Chrome cuando se usa urllib» y «no falsificar huellas TLS».

**Cómo se usó la IA:** un flujo de Claude Code con 6 subagentes de solo lectura: 5 lectores en paralelo (cliente HTTP, Chromium, sesiones y cookies, reglas y ADR, tests y herramientas) y 1 sintetizador que contrastó sus afirmaciones con el código. No se modificó ningún fichero.

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-21 | Diagnóstico del acceso web | Claude Code (flujo `precia-cloudflare-understand`, 6 subagentes) | Informe de diagnóstico con los defectos propios (Chromium sacaba su red por Node con `route.fetch`, sin cookies en HTTP, desafío en `robots.txt` que bloqueaba también a Chromium, `Pagina` sin cabeceras de respuesta, sin `Retry-After`) | Aceptado como base; Julio fijó el alcance a las 19:33 |

**Aceptado, modificado y descartado:**
- **Refutado por la IA:** la premisa de la petición «urllib se identifica como Chrome» no se cumplía: el User-Agent por defecto era `PrecIA/0.1 (proyecto universitario; uso personal)` y no había «Chrome/» en `src`.
- **Corregido por el sintetizador:** 3 afirmaciones de los lectores (números de línea inexistentes, cookies de Chromium en las redirecciones y la premisa del User-Agent).
- **Conflicto señalado y no resuelto en S02:** ADR-021 permite falsear la huella y la petición del día lo prohibía. Se resolvió en S03 con ADR-022 y ADR-023, elegidas por Julio.

**Validación:** solo lectura. Estado de la suite según el lector de tests: 3120 en verde y 2 en rojo (tope de 1 s en `tests/extraccion/test_embebidos.py`, con la máquina cargada).

## S03 (2026-10-22 a 2026-10-24): TLS y HTTP/2, eficiencia y campaña multi-sector

Fuente: `../precia-vault/sesiones/2026-10-22 S03 - TLS y HTTP2 en el scraper.md`. Rama `core/tls-http2` (desde `083851b`), con el commit `41bea7a` que Julio autorizó; la tanda de eficiencia y la campaña multi-sector van en `core/eficiencia`, **sin commit**.

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

### S03, segunda parte: tanda de eficiencia y facilidad de uso (2026-10-22, rama `core/eficiencia`, sin commit)

**Qué se pidió:** «necesito que sea un scraper bien eficiente y facil de usar».

**Cómo se usó la IA:**
- **Diagnóstico con 4 auditores** (subagentes de solo lectura) y una síntesis que propuso mejoras con su coste y su efecto medidos sin cambiar nada.
- **Decisiones de Julio**, a cuatro preguntas con alternativas: «Paquete recomendado (Recommended)» (B4 precio tachado, B5 OEM equivalentes, A2 filtrar antes de abrir fichas, A3 caché y corte del extractor de IA, A1 recordar configuraciones fallidas, C1 búsqueda en segundo plano; C2 fuera), «Commit S03 y rama nueva (Recommended)» (commit `41bea7a` en `core/tls-http2` y rama `core/eficiencia`), «Sí, etiquetadas aparte (Recommended)» (equivalentes y revisiones distintas de «exacta», alertas solo de exactas) y «Usa esos valores (Recommended)» (esperas de 1 h y 1 día, consulta genérica filtro/faro/freno, corte del extractor en la 1.ª ficha, caché con el TTL de las páginas).
- **6 planificadores**, uno por mejora, con el plan paso a paso y los tests; después, **implementación con TDD en serie** (B4 → B5 → A2 → A3 → A1 → C1): test en rojo, código, puertas completas y documentación de cada mejora antes de la siguiente.
- **Revisión con 4 lentes** (seguridad y rendimiento, corrección, tests y reglas del proyecto) y **verificación adversarial**: cada hallazgo lo comprobaron 2 verificadores antes de darlo por bueno. 29 hallazgos, 21 confirmados y 20 corregidos con TDD; el otro (sin conexión, todas las tiendas en espera) lo decide Julio.

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-22 | Diagnóstico de eficiencia y facilidad de uso | Claude Code (Workflow: 4 auditores + síntesis) | Mediciones sin red, propuestas con coste y preguntas con alternativas | Julio eligió las opciones recomendadas |
| 2026-10-22 | Commit de S03 y rama `core/eficiencia` | Claude Code | Revisión de secretos, normalización a CRLF, commit `41bea7a` con `Co-Authored-By` | Commit autorizado por Julio |
| 2026-10-22 | B4, B5, A2, A3, A1 y C1 | Claude Code (6 planificadores + implementadores con TDD, en serie) | Precio vigente y no el tachado, emparejado por OEM con revisiones y equivalentes (migración 5), filtro por la consulta antes de abrir fichas, caché del extractor de IA (migración 6), configuraciones fallidas con espera y consulta genérica (migraciones 7 y 8), búsqueda en segundo plano con progreso, plazo y Cancelar; de 3307 a 3593 tests | Pendiente de revisión |
| 2026-10-22/23 | Revisión adversarial de la tanda | Claude Code (Workflow: 4 lentes + 2 verificadores por hallazgo) | 20 correcciones con TDD (3633 tests) | Pendiente de revisión |

### S03, tercera parte: campaña de pruebas reales multi-sector (2026-10-23/24, rama `core/eficiencia`, sin commit)

**Qué se pidió (literal):** «continua con el scraper tambien lanza pruebas co varias web no solo recambios ropa zapatillas cosas asi y busca que va fallando y ve corrigiendolo recuerda hacer todo con subagetes y si fallan revisa por que han fallado edita el promt y vuelve a lanzarlos».

**Cómo se usó la IA:**
- **Rondas reales** (3): un subagente comprobó las portadas, escribió el arnés fuera del repo (las mismas clases que la web, modo Auto, copia de la BD, sin proxy ni Google) y en cada ronda lanzó 12 tiendas nuevas más los reintentos, y clasificó cada tienda contra el HTML guardado.
- **Diagnóstico** por subagentes que reprodujeron cada causa sin red con el código de la app, y **verificación adversarial de las causas**: dos verificadores votaron cada propuesta de arreglo (qué parte hacer y qué parte no) antes de tocar el código.
- **Arreglos con TDD**, un subagente por causa: recorte byte a byte de la página real como fixture, test en rojo por el motivo esperado, arreglo genérico sin código por dominio, barrido del corpus guardado (hasta 431 páginas) para ver que no cambiaba nada más, puertas completas y documentación.
- **Relanzamientos:** cuando el arnés no probaba de verdad un arreglo (perfil viejo) se añadió `--reconfigurar` y se repitieron las tiendas.

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-23 | Diseño de la campaña y arnés | Claude Code (subagente) | Lista de 36 tiendas en 3 oleadas, arnés y comprobación de portadas, todo fuera del repo | Aceptado por Claude; pendiente de revisión |
| 2026-10-23/24 | Rondas 1, 2 y 3 | Claude Code (subagentes de prueba y clasificación) | 52 visitas a 36 tiendas, tablas tienda a tienda, 124 llamadas de la IA de la app por 0,1465 USD | Resultados registrados (📏 en `BITACORA.md`) |
| 2026-10-23/24 | Diagnóstico y verificación de causas | Claude Code (diagnosticadores + 2 verificadores adversariales) | 34 causas con su evidencia y el arreglo votado | 29 arregladas; 5 fuera de alcance o sin decidir |
| 2026-10-23/24 | Arreglos con TDD | Claude Code (un subagente por causa) | 29 arreglos genéricos, 39 recortes de páginas reales como fixtures, migración 9; de 3633 a 4132 tests | Pendiente de revisión, igual que los recortes |
| 2026-10-24 | Cierre documental | Claude Code (subagente) | Bitácora, notas 03, 04, 05, 12, 14, 15 y 16, este apartado, resumen de la sesión | Pendiente de revisión |

**Aceptado, modificado y descartado:**
- **Descartado por los verificadores** antes de escribir código: rehacer el buscador «como un usuario» con Chromium (cambia el protocolo `Navegador`), la prueba de sensibilidad con una segunda descarga (rechazaría búsquedas buenas sin resultados), abrir una URL de cada sitemap, el «precio más repetido» para elegir variante, ampliar el lenguaje de selectores (ADR-015) y el sku como referencia OEM.
- **No hecho porque lo decide Julio:** la consulta de prueba y los prompts sin sesgo de recambios (C4), el respaldo por texto de Thomann en modo Auto, un `OrigenDato.LISTADO` para los precios del listado, el proxy para los bloqueos reales.

**Errores de la IA detectados** (también en «Errores de la IA detectados» de `BITACORA.md`, 41 filas del 23 y el 24):
- **Código de sesiones anteriores** que la campaña destapó: `parece_listado` aceptaba la portada; el catálogo por sitemap tiraba los neutros, se quedaba con el primero, ordenaba por URL y leía solo 10 hijos; el resumen para la IA no cumplía lo que prometía su docstring; `_principal` tomaba otra variante o el primer producto de un `ItemList`; `NOMBRES_BUSQUEDA` no se sincronizó con `PARAMETROS_ENLACE`; topes de tokens puestos a ojo; `dom.invisible` sin el *streaming* de React; `vendor` como marca; el validador que solo contaba que hubiera algo; la regla del bloque JSON-LD que se cumplía siempre; `Nodo.texto` sin los céntimos aparte; el robots.txt con desafío que cortaba antes del rescate; y el propio filtro A2 de esta sesión.
- **Errores de los subagentes al arreglar:** «product» dentro de «reproductores», 50 000 hijos en la cola, finales de línea mal leídos con `file | sed`, tests demasiado estrictos o con la confianza equivocada, un recorte que pegaba el `<h1>` al precio, una regex 7 veces más lenta, un `extraer_ficha` sin `await` en un barrido, nombres de variable reutilizados (lo cazó `mypy`) y `referencias_en` que toma «038253014G» por gramos.
- **Errores del arnés de la campaña:** gastaba el tope de páginas guardadas en robots.txt y portada, y reintentaba con el perfil viejo; y un recuento de llamadas escrito sin sumar.
- En la tanda de eficiencia, la revisión adversarial encontró 21 defectos confirmados en el código recién generado (DoS de CPU en `texto_de_precio`, fallos de plazo sin recordar, llamadas de IA sin registrar al apagar, comparación de fechas como texto…) y el mojibake de dos filas de la bitácora.

**Validación:**
- TDD en cada arreglo: rojo por el motivo esperado antes del código y verde después; cada recorte de página real reproduce el fallo por sí solo.
- Puertas al terminar (2026-10-24 01:35): `ruff check .` y `ruff format --check .` (201 ficheros) en verde, `mypy` sin errores (197 ficheros), `pytest` completo 4131 en verde y 1 en rojo: el test de cronómetro de `test_embebidos` (bug 6), que pasa lanzado solo.
- Prueba con las tiendas reales: los arreglos de las rondas 1 y 2 se comprobaron en la ronda siguiente; los de la ronda 3 solo sin red.
- **Todo el código, los recortes de páginas reales y la documentación de la tanda y de la campaña quedan pendientes de revisión de Julio.**

### S03, cuarta parte: rondas 4 y 5 de la campaña multi-sector (2026-10-24, rama `core/eficiencia`, sin commit)

**Qué se pidió (literal):** la misma petición de la campaña («continua con el scraper tambien lanza pruebas co varias web no solo recambios [...] busca que va fallando y ve corrigiendolo») y, al reanudar tras el corte del flujo, «continua mejorandolo» y «continua».

**Cómo se usó la IA:**
- **Mismo método que en las rondas 1-3:** un subagente lanzó cada ronda con el arnés fuera del repo y clasificó tienda a tienda contra el HTML guardado; diagnosticadores reprodujeron cada causa sin red; 2 verificadores adversariales votaron cada propuesta; un subagente por causa la arregló con TDD y un recorte byte a byte de la página real.
- **Relanzamiento tras un corte:** el flujo se paró a las 02:15 (terminó el proceso de Claude Code, no la tarea) con las búsquedas de la ronda 4 hechas y sin clasificar. Se reescribió el prompt para que reutilizara esas búsquedas sin repetir ninguna petición y se relanzó.
- **Cierre de discrepancias** de la verificación independiente: el test intermitente del bug 6 y el origen de las fichas de Oscaro.

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-24 | Ronda 4: 9 reintentos y oleada 4 (8 sectores nuevos) | Claude Code (subagentes de prueba y clasificación) | 19 visitas, tabla tienda a tienda, 45 llamadas de la IA de la app por 0,0401 USD | Resultados registrados (📏 en `BITACORA.md`) |
| 2026-10-24 | Diagnóstico y arreglos de la ronda 4 | Claude Code (diagnosticadores, 2 verificadores por causa, un implementador por causa con TDD) | 9 causas; 8 arreglos genéricos (R4-A, B, C, D, E, G, H, I) con 14 recortes de páginas reales | Pendiente de revisión |
| 2026-10-24 | Ronda 5: 8 reintentos con `--reconfigurar` | Claude Code (subagente) | Tabla tienda a tienda, 26 llamadas por 0,0175 USD | Resultados registrados |
| 2026-10-24 | Diagnóstico y arreglos de la ronda 5 | Claude Code (mismo método) | 9 causas nuevas más R4-F; 9 arreglos (R5-A, B, C, D, E, F, H, I, J) con 9 recortes nuevos y la migración 10 (`catalogos.idioma`) | Pendiente de revisión |
| 2026-10-24 | Bug 6 y README de las fichas de Oscaro | Claude Code (subagente cerrar-discrepancias) | Test de escala entre tamaños en `test_embebidos.py`; `tests/fixtures/extraccion/README.md` | Pendiente de revisión |
| 2026-10-24 | Cierre documental de las rondas 4-5 | Claude Code (subagente) | Bitácora, notas 04, 05, 12, 14 y 15, este apartado, resumen de la sesión | Pendiente de revisión |

**Resultado:** acumulado de las cinco rondas, 45 tiendas de 20 sectores, 79 visitas, 52 causas y 46 arregladas con TDD, 195 llamadas de la IA de la app por 0,2041 USD; en su última visita, 28 ok, 6 parciales, 4 fallos, 6 bloqueadas y 1 fuera de alcance. Los 9 arreglos de la ronda 5 solo están comprobados sin red.

**Aceptado, modificado y descartado:**
- **Modificado por los verificadores** antes de escribir código: `NIVELES_CONTENEDOR` a 5 y no a 6 como proponía el diagnóstico (a 6, el enlace de marca de una ficha de Coolmod se llevaba el precio); rutas de búsqueda por «empieza por» y no también por «termina por» (habría colado `/opensearch.xml`); en R5-I, grupos por turnos y no rellenar primero con el grupo mayor (no arreglaba Decántalo); en R5-D, sin cortar variantes por cuerpo igual (el hash cambiaba en cada variante).
- **No hecho porque lo decide Julio:** la consulta de prueba sin sesgo de recambios (C4); seguir por sitemap tras un bloqueo con Chromium (Back Market); un tope menor para la búsqueda tras configurar; la Public Suffix List para subdominios (dependencia fuera de la nota 06).
- **Sin arreglar:** R5-G (Carrefour solo lee el primer sitemap declarado, bug 28).

**Errores de la IA detectados** (también en «Errores de la IA detectados» de `BITACORA.md`):
- **Elegir mal la muestra:** el subagente de la campaña eligió Bebitus mirando solo que la portada respondía 200; ya no es una tienda.
- **Arreglos que se quedaron cortos o rompieron otra cosa:** R3-C destapó una regresión (un 403 seco en `/sitemap.xml` bloqueaba la tienda, R4-D); R4-B y R4-C pasaban sus tests pero no llegaban al flujo real (el validador y el almacén rechazaban el subdominio; el configurador pasaba solo el origen), y salieron en la ronda 5 como R5-B y R5-C; el arreglo de R3-C había aceptado gastar la llamada de IA del sitemap en fichas sin precio y lo fijó en un test (R5-F).
- **Código de sesiones anteriores** que destaparon estas rondas: `_palabras_slug` con el «/p» final, el filtro de mismo host en `candidatos`, `NIVELES_CONTENEDOR` = 4, `elegir_fichas` sin desempate, la ruta de búsqueda por subcadena, `buscar_catalogo` con el máximo de palabras sin peso y por subcadena, `desde_perfil` con la primera coincidencia y el modo sitemap con `necesita_js=False` fijo.
- **Errores de los subagentes al arreglar:** un `registro` sin definir en `catalogo.py` (lo cazaron los tests), la URL del índice de Kenay sin `www.` en un README, el diagnóstico de R4-G que proponía 6 niveles, un `sed -i` sobre `tests/perfiles/sitio.py` y, en el bug 6, dar por buena la primera versión con el test solo y suponer carga externa cuando era la frecuencia de la CPU.
- **Protocolo del diario:** un subagente volvió a meter un CRLF real en una entrada (corregido al releerla) y otro casi lo repite (lo paró la aserción de su script); en este cierre, una hora escrita antes de consultar el reloj (18:01 en vez de 18:00), corregida al momento.

**Validación:**
- TDD en cada arreglo: rojo por el motivo esperado antes del código (o guarda que ya pasaba, dicha como tal) y verde después; los arreglos de la ronda 4 se comprobaron en real en la ronda 5.
- Bug 6: el test nuevo falla con dos implementaciones malas simuladas fuera del repo; la suite pasó 5 veces seguidas.
- Puertas al cerrar (2026-10-24 17:59): `ruff check .` y `ruff format --check .` (211 ficheros) en verde, `mypy` sin errores (206 ficheros), `pytest` completo **4332 en verde y 0 en rojo** (167,8 s).
- **Todo el código, los 23 recortes nuevos y la documentación de las rondas 4-5 quedan pendientes de revisión de Julio.**

### S03: revisión con IA de los resultados (2026-10-24, rama `core/eficiencia`, sin commit)

**Qué se pidió (literal):** «ademas me gustaria que la ia revise los resultados por ejemplo he buscado puerta delantera derecha y me ha salido la cerradura de la puerta delantera derecha».

**Cómo se usó la IA:** un flujo de Claude Code leyó el código sin editarlo y escribió la causa y el diseño (regla sin IA del núcleo nominal + revisión con IA en una llamada, caché, salvaguardas, coste estimado). Después un subagente lo implementó con TDD en paralelo a otros dos flujos (amazon.es y desafíos JS), sin tocar sus ficheros.

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-24 | Causa y diseño de la revisión de resultados | Claude Code (flujo de diseño) | Diagnóstico (Auto no miraba el significado; A2 solo contaba palabras), diseño, riesgos y coste | Implementado; ADR-024 propuesto, pendiente de Julio |
| 2026-10-24 | Implementación con TDD | Claude Code (subagente) | `emparejado/nucleo_nominal.py`, `revisar` con caché en `semantico.py`, agrupador, migración 12, reserva de llamada, orquestador, web; 9 ficheros de tests nuevos | Pendiente de revisión |

**Aceptado, modificado y descartado:**
- **Modificado durante la implementación:** la regla del núcleo solo descarta si alguna oferta es de verdad lo buscado (lo destapó un test existente con «freno golf»); en modo IA la revisión conserva el componente `emparejado` y en Auto usa `revision`; el constructor del orquestador deja la revisión apagada por defecto y la búsqueda real la activa desde el ajuste.
- **No hecho (opcional, lo decide Julio):** R3-D también en Auto; columna `imagen` en `productos`.

**Errores de la IA detectados:** la regla descartaba «pastillas de freno» al buscar «freno golf»; una hora del diario escrita sin mirar el reloj (corregida); un `sed` que convirtió `\u2013` en «2013» en un test (corregido).

**Validación:** TDD (rojo por el motivo esperado y verde después). Puertas al cerrar (2026-10-24 21:31): `ruff check .` y `ruff format --check .` (232 ficheros) en verde, `mypy` sin errores (227 ficheros), `pytest` completo **4575 en verde**. Coste de la revisión estimado (≈ 0,001 $ por búsqueda con 60 ofertas), sin medir todavía con el banco. **Todo el código queda pendiente de revisión de Julio.**

### S03: rediseño de la interfaz sin emojis (2026-10-25, rama `core/eficiencia`, sin commit)

**Qué se pidió (literal):** «necesito que este acabado hoy mejora la interfaz /find-skills  busca una skill para hacer una interfaz bomnita y que no parezca ia quita lo emojies y termina el trabajo tienes 1h».

**Cómo se usó la IA:** Claude Code usó la skill find-skills para buscar en skills.sh e instaló `anthropics/skills@frontend-design`, una guía de diseño para el asistente (no es una dependencia de PrecIA). Un flujo con subagentes repartió el trabajo: uno diseñó y reescribió el CSS y las plantillas siguiendo la skill, otro quitó a la vez los emojis de los textos de los `.py`, un tercero revisó el resultado con capturas de Playwright y el último pasó las puertas y documentó.

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-25 | Búsqueda e instalación de la skill | Claude Code (find-skills) | Elección de `frontend-design` (fuente oficial) | Instalada en el entorno del asistente |
| 2026-10-25 | Plan y rediseño | Claude Code (subagente con frontend-design) | Plan de tokens; `estilos.css` reescrito, `icono.svg` y diez plantillas | Pendiente de revisión |
| 2026-10-25 | Quitar emojis del código Python | Claude Code (subagente) | Textos con prefijo `IA · ` en 8 módulos de `src/` y 24 ficheros de tests | Pendiente de revisión |
| 2026-10-25 | Crítica visual | Claude Code (subagente, Playwright) | 9 páginas en 1366/390 px, claro y oscuro; 5 correcciones de CSS | Pendiente de revisión |
| 2026-10-25 | Puertas y documentación | Claude Code (subagente) | Verificación y textos del diario, la bitácora, la nota 12 y este documento | Pendiente de revisión |

**Aceptado, modificado y descartado:**
- **Modificado:** la primera idea de barra lateral blanca con el activo en azul claro se cambió por grafito con marca amarilla, porque la propia revisión contra la skill la vio genérica. Dos textos que comprueban los tests («incluye equivalentes y revisiones», «Falló · En espera hasta el») volvieron a su redacción original en vez de cambiar los tests.
- **No hecho:** puntos medios y una flecha en textos que genera el núcleo (`nucleo/fuente_demo.py`: «JSON fuera del esquema → reintento»; «Usado · 60 % de vida»), porque tocan `.py` del núcleo y tests.

**Errores de la IA detectados:** el primer pase del rediseño cambió dos textos comprobados por tests; el script de los emojis no cambió un texto sin emoji que un test esperaba con la palabra nueva («extractor de respaldo en 1»); la crítica visual encontró cinco fallos de maquetación que los tests no veían (detalle en la bitácora).

**Validación:** puertas al cerrar (2026-10-25): `ruff check .` y `ruff format --check .` (243 ficheros) en verde, `mypy` sin errores (238 ficheros), `pytest` completo **4744 en verde y 4 saltados** (openssl no instalado); 0 emojis en `src/`; sin desbordamiento horizontal en las capturas. **Todo el código queda pendiente de revisión de Julio.**

### S03: foto principal, amazon.es y velocidad (2026-10-24, rama `core/eficiencia`, sin commit)

**Qué se pidió (literal):** «y poder ver la foto principal de el articulo»; «amazon no funciona»; «se puede hacer que todo esto funcione mas rapido».

**Cómo se usó la IA:** subagentes de Claude Code con TDD para cada arreglo, analistas de solo lectura para medir dónde se iba el tiempo, rondas reales con el arnés de la campaña (rondas 6 a 11) y dos revisiones escépticas con subagentes distintos de los que implementaron. Las decisiones de velocidad las tomó Julio (BITACORA 2026-10-24 21:04).

| Fecha | Tarea | Herramienta | Qué generó la IA | Decisión |
|---|---|---|---|---|
| 2026-10-24 | Foto principal del artículo | Claude Code (subagente, TDD) | `extraccion/imagenes.py`, migración 11, macro de la foto y CSP `img-src 'self' https:`; la foto nunca sale de la IA | Pendiente de revisión |
| 2026-10-24 | amazon.es | Claude Code (subagentes, TDD) y rondas reales 6, 9 y 10 | Buscador reconocido por su rol, caché sin páginas a medias, resumen de ficha para la IA, precio partido en elementos hermanos (R9-P) | Pendiente de revisión |
| 2026-10-24 | Medida de la velocidad | Claude Code (4 analistas de solo lectura) | Reparto del tiempo: cortesía 67 %, red 34 %, IA y CPU 1-3 % | Julio decide cortesía de 1,0 s, varias búsquedas a la vez y plantillas de la IA en paralelo |
| 2026-10-24 | Tres tandas de velocidad | Claude Code (subagentes, TDD) | Timeouts más cortos, resultados parciales por ficha, red e IA en paralelo, 3 búsquedas a la vez | Pendiente de revisión |
| 2026-10-24 | Revisiones escépticas | Claude Code (2 subagentes por revisión) | Foto y revisión con IA: 12 señalados, 10 reales; velocidad: 8 señalados, 7 ciertos | Corregidos con TDD; pendiente de revisión |

**Aceptado, modificado y descartado:**
- **Rechazado por Julio:** precios provisionales, número de fichas ajustable, modo rápido, cortar tiendas lentas, leer menos sitemaps y validar con su consulta.
- **Descartado:** resolver el CAPTCHA de b-parts.com; ni el proxy residencial ni Chrome con ventana lo evitaron (5 de 5 cargas con el CAPTCHA de AWS WAF).

**Errores de la IA detectados:** tests mal montados y un escape perdido en un heredoc al implementar la foto; con 3 búsquedas a la vez se podía romper la cortesía (robots.txt fuera del turno, filas distintas para `www.` y sin `www.`); un `sed -i` y tres tests mal montados en la tanda 3; el primer arreglo del precio partido aceptaba otro importe. Todos están en la tabla «Errores de la IA detectados» de `BITACORA.md`.

**Validación:** medida real antes y después (BITACORA 2026-10-24 23:44): dos búsquedas sobre 4 tiendas pasan de 23,3 s una tras otra a 11,2 s a la vez, sin solapes de peticiones al mismo host y sin ningún 429 ni 403; amazon.es se configura sola en la ronda 10 con 5 ofertas y el precio vigente anclado, por 0,014 USD.

### S06: alertas, Google, banco de evaluación y Docker (2026-11-24/25, rama `h7/envio-alertas`, sin commit)

**Herramienta:** Claude Code (modelo Claude Fable 5.2), con un subagente para etiquetar los casos del banco.

**Petición de Julio:** «hay que terminarlo».

**Qué generó la IA (todo pendiente de revisión de Julio):**
- **H7:** `seguimiento/avisos.py` (email con `smtplib` y Telegram con `urllib`), `seguimiento/alertas.py`, `seguimiento/planificador.py`, la migración 13 del almacén, las rutas y la pantalla de Alertas, y sus tests.
- **H5:** `fuentes/google.py`, `fuentes/clasificador.py`, `fuentes/descubridor.py` y su integración en el orquestador, con sus tests y un fixture de resultados de Google **sintético**.
- **H8:** el paquete `evaluacion/` (métricas, casos, banco, informe y CLI), la pantalla de Evaluación y los 25 casos etiquetados de `evaluacion/casos/recambios.json` (etiquetas puestas por un subagente leyendo el HTML guardado, sin usar el extractor de PrecIA).
- **H9:** `Dockerfile`, `docker-compose.yml`, `.dockerignore` y el ajuste `PRECIA_HOST`.
- Documentación: README, `.env.example`, bitácora, diario y notas del vault.

**Decisiones que tomó la IA y debe confirmar Julio:** alerta con el precio estrictamente por debajo del objetivo; 12 h entre comprobaciones; «Borrar» sin confirmación; topes del descubrimiento (8 páginas y 3 tiendas nuevas por búsqueda) y su lista negra; que el banco mida la extracción de fichas guardadas y no búsquedas completas; el diseño del despliegue (puerto solo en el `127.0.0.1` del servidor y túnel SSH).

**Lo que la IA no hizo, y por qué:** no resolvió el CAPTCHA de Google (bloqueó las tres vías); no guardó una página real de resultados de Google (el permiso para usar un navegador externo se denegó y no se rodeó); no ejecutó el banco en modo `api` (gasta dinero de la clave) ni en modo `local`; no desplegó en el VPS; no probó un envío real de email ni de Telegram (no hay credenciales); no hizo commit ni push.

**Errores de la IA detectados:** dos veces escribió tests e implementación seguidos sin ver antes el test en rojo (`avisos.py` y el CLI del banco); un `Dockerfile` que no se construía (faltaba `libatomic1`); un import que faltaba en el orquestador (lo vieron `ruff` y `mypy`). Están en la tabla «Errores de la IA detectados» de la bitácora.

**Validación:** `ruff check .`, `ruff format --check .` (269 ficheros), `mypy` (263 ficheros) y `pytest` completo: **5034 passed** en 353,6 s. Pruebas reales: pantalla de Alertas en el navegador (modo demo), medición de Google (0 de 3 vías), banco en modo clásico (cobertura 67 %, exactitud 98 %) y contenedor de Docker construido y arrancado.

**Entrega final (2026-11-25).** A partir del enunciado que pegó Julio, Claude Code midió Bing y DuckDuckGo como alternativa a Google (a petición de Julio: «sobre todo con bing»), buscó credenciales en el repositorio, repitió las puertas y redactó en `../memoria-entrega-final/` el borrador de la memoria final (con su PDF y las evidencias) y una presentación de 9 diapositivas con notas. **Todo pendiente de revisión de Julio**; la exposición y el vídeo los hace él. Error de la IA detectado al verificar el borrador: una cifra sin respaldo y dos casos de prueba que no correspondían a tests (en la tabla de la bitácora).

**Cierre de la entrega final (2026-11-26).** Julio preguntó si la entrega tenía un avance «contundente» y aprobó el plan que propuso Claude Code («vale pues hazlo»). La IA hizo cinco commits por bloques del trabajo de S06, los subió y abrió la PR #2; arregló un aviso de `ruff` que solo salía con la versión de la CI; ejecutó el banco en modo API (cobertura 94 % frente a 67 %, exactitud 87 % frente a 98 %, 0,0035 $) y actualizó la memoria y la presentación. **No integró DuckDuckGo:** el día anterior había dado 10 tiendas de 10 y ahora pedía un CAPTCHA por todas las vías; fue un error de la IA apoyar la propuesta en una sola muestra (en la tabla de la bitácora). No se pudo enviar un aviso real (no hay Telegram ni correo configurados) ni fusionar la PR (permiso denegado): quedan para Julio.
