# Cuaderno de bitácora de PrecIA

Registro cronológico de **avances, fallos, soluciones, decisiones y aprendizajes** del desarrollo. Es la materia prima de la **memoria final**: cada entrada indica a qué sección de la memoria alimenta.

No sustituye al diario de sesiones del vault (`../precia-vault/10 - Diario de Sesiones.md`), que apunta *todo* lo que se hace. Aquí solo entra lo que merece contarse en la memoria.

## Cómo se escribe

- Se añade **al final**, en el momento en que ocurre (no se reconstruye después), con fecha y hora real.
- Una entrada por hecho, con este formato:

```markdown
### AAAA-MM-DD HH:MM · <tipo> · Título corto
- **Qué pasó:** …
- **Causa / contexto:** … (en fallos: por qué falló)
- **Qué se hizo:** … (en fallos: cómo se resolvió, o «sin resolver»)
- **Lección:** … (opcional)
- **Para la memoria:** §N · sesión SNN
```

**Tipos:** ✅ Avance · ❌ Fallo · 🔧 Solución · 🧭 Decisión · 💡 Aprendizaje · 📏 Medida

**Secciones de la memoria:** §1 estado del arte · §2 planteamiento y valor de la IA · §3 uso de IA en el desarrollo · §4 IA integrada y riesgos · §5 flujo · §6 hitos y estructura · §7 tecnologías · §8 hardware y costes · Eval: banco de evaluación.

- Los **fallos se apuntan siempre**, también los de la IA y los propios: son lo que demuestra que el trabajo se ha validado de verdad. Cada error de la IA va además a la tabla de abajo.

## Errores de la IA detectados

Alimenta la memoria §3 («método de validación del código generado»).

| Fecha | Qué hizo mal la IA | Cómo se detectó | Corrección |
|---|---|---|---|
| 2026-10-19 | Escribió el diario de sesión al final y con horas estimadas, en contra del protocolo que ella misma había redactado | Revisión adversarial (agente «protocolo»), comparando con las fechas de los ficheros | Nota rehecha con las horas reales, aviso de reconstrucción y protocolo más estricto |
| 2026-10-19 | Añadió `setuptools` como *build backend* sin pedir aprobación (regla de dependencias) | Revisión adversarial (agentes «fidelidad» y «esqueleto») | Marcado como pendiente de aprobar |
| 2026-10-19 | Resumió la nota legal suavizando la fuente («puede prohibir» → «está prohibido»; omitió que el uso privado no cubre bases electrónicas y que el módulo de Google incumple sus condiciones de servicio) | Revisión adversarial (agente «fidelidad») contrastando con el estado del arte | Nota 08 reescrita fiel a la fuente |
| 2026-10-19 | Inventó detalles que nadie había decidido: una fecha de entrega (27/09) y servir HTMX en local | Revisión adversarial (agente «fidelidad») | Marcados como pendientes o propuestas |
| 2026-10-19 | Dio como «decisiones cerradas» propuestas suyas (src layout, CLAUDE.md mínimo) | Revisión adversarial (agente «fidelidad») | ADR separados en «Cerradas» y «Propuestas» |
| 2026-10-19 | `.gitignore` con patrones sin anclar que ignoraban código futuro (`navegador/cache/`) | Revisión adversarial + `git check-ignore` | Patrones anclados a la raíz |
| 2026-10-19 | Faltaba `tests/__init__.py`: dos tests con el mismo nombre habrían chocado | Revisión adversarial; se reprodujo con dos `test_dup.py` | `__init__.py` en `tests/` y convención en la nota 03 |
| 2026-10-19 | 6 docstrings de más de 100 caracteres | `ruff check` | Reescritos en formato PEP 257 |
| 2026-10-19 | Sirvió HTMX como `text/plain` en Windows (con `nosniff`, no se habría ejecutado) | Test de ficheros estáticos | Tipos MIME fijados a mano + test |
| 2026-10-19 | Usó un test de Jinja (`match`) que no existe | Al renderizar la plantilla | Filtro propio `miles` |
| 2026-10-19 | UX: insignia ilegible, decimales con punto, tabla ilegible en móvil, campo vacío tras pulsar un ejemplo | Revisión visual con Playwright (capturas) | 9 correcciones de CSS y plantillas |
| 2026-10-19 | No comprobó el anclaje del **precio** (memoria §4) | Revisión adversarial (agente «fidelidad/UX») | `mejor_oferta` lo excluye; celda tachada; test |
| 2026-10-19 | El botón Atrás rompía la página por `historyCacheSize: 0` | Revisión adversarial (agentes «seguridad» y «código») | Página completa en restauración + `Vary` |
| 2026-10-19 | Límite de cuerpo saltable con CL+TE; 500 sin cabeceras con «²»; GET de pago lanzable desde otra web | Agente «pentester» contra el servidor real | Middleware endurecido + 6 tests |
| 2026-10-19 | El filtro «solo usadas» hacía lo contrario; el modo Auto siempre cobraba | Revisión adversarial (agente «código») | Lógica corregida + tests |
| 2026-10-19 | Contraste de bordes 1,26:1 (WCAG pide 3:1); gráfica que solo distinguía por color | Revisión adversarial (agente «fidelidad/UX») | `--borde-control` a 3,86:1; trazos distintos |
| 2026-10-19 | Volvió a escribir una hora sin consultar el reloj en 2 entradas de esta bitácora (17:31 en vez de 17:16), el mismo fallo que en el diario por la mañana | Al cotejar la bitácora con las horas del diario (sacadas de `date`) | Horas corregidas; la documentación se genera con un script que pone la hora real |

---

## Registro

### 2026-10-19 16:20 · 🧭 Decisión · Organización de la documentación del desarrollo
- **Qué pasó:** se decide documentar el desarrollo en un vault de Obsidian propio (`precia-vault/`), con un `CLAUDE.md` que obliga a la IA a llevar un diario por sesión.
- **Causa / contexto:** la asignatura exige documentar todo uso de IA en cada entrega; sin un registro sobre la marcha, eso se reconstruye de memoria y se pierde información.
- **Qué se hizo:** Julio eligió vault propio junto al código y el alcance de la primera sesión (vault + CLAUDE.md + esqueleto).
- **Para la memoria:** §3 · S01

### 2026-10-19 16:26 · ✅ Avance · Hito H0: vault, CLAUDE.md y esqueleto del repositorio
- **Qué pasó:** quedan montados el vault (14 notas sacadas de la memoria), el `CLAUDE.md` y el repo `precia/` con los 11 paquetes de la memoria §6, `pyproject.toml`, CI de GitHub Actions y `USO-IA.md`.
- **Qué se hizo:** TDD desde el primer fichero: el test de humo se escribió antes que los paquetes.
- **Para la memoria:** §6 (estructura), §7 (pytest, ruff, mypy, GitHub Actions) · S01

### 2026-10-19 16:26 · 📏 Medida · Primer ciclo TDD
- **Qué pasó:** test de humo en rojo (11 de 12 fallan por `ModuleNotFoundError`) → verde tras crear los paquetes (12/12). `mypy --strict` y `ruff` limpios.
- **Para la memoria:** §3 (validación del código generado) · S01

### 2026-10-19 16:29 · ❌ Fallo · La IA no siguió su propio protocolo de diario
- **Qué pasó:** la nota de sesión S01 se escribió de golpe al final, con horas estimadas (una incluso posterior a la hora real), cuando el `CLAUDE.md` exige escribir cada entrada en el momento.
- **Causa / contexto:** la IA priorizó terminar el trabajo y dejó la documentación para el final, que es justo el riesgo que el protocolo quería evitar.
- **Qué se hizo:** se rehízo la nota con las horas reales de los ficheros, con un aviso de que las primeras entradas son reconstruidas. El protocolo exige ahora crear la nota como primera acción y escribir cada entrada antes de pasar a la siguiente.
- **Lección:** un protocolo de documentación necesita verificación externa; la IA no es fiable cumpliendo sus propias reglas sin control.
- **Para la memoria:** §3 · S01

### 2026-10-19 16:29 · 💡 Aprendizaje · La revisión adversarial con varios agentes detecta errores que la generación no ve
- **Qué pasó:** tres subagentes de solo lectura, cada uno con un enfoque distinto (fidelidad a la memoria, protocolo del diario, corrección del esqueleto), encontraron 16 problemas (1 alto, 9 medios y 6 bajos) en un trabajo que pasaba todos los tests.
- **Causa / contexto:** los tests solo comprueban el código; los errores estaban en la documentación (datos inventados, resumen legal suavizado) y en configuración que todavía no se usaba (`.gitignore`, choque futuro de tests).
- **Qué se hizo:** cada hallazgo se comprobó antes de corregirlo (los del código, reproduciéndolos). Están en la tabla «Errores de la IA detectados».
- **Lección:** «CI en verde» no basta para validar lo que genera la IA; hace falta revisión, idealmente con enfoques distintos y contrastando con las fuentes.
- **Para la memoria:** §3 (método de validación) · S01

### 2026-10-19 16:30 · 🔧 Solución · Corrección de los 16 hallazgos de la revisión
- **Qué pasó:** se corrigieron todos los hallazgos: protocolo del diario unificado, ADR separados en cerradas y propuestas, nota legal fiel a la fuente, `.gitignore` anclado y `tests/__init__.py`.
- **Qué se hizo:** verificación final: pytest 12/12, ruff y mypy limpios, ningún enlace roto en el vault.
- **Para la memoria:** §3 · S01

### 2026-10-19 16:30 · 🧭 Decisión · Riesgo legal del módulo de Google, asumido y documentado
- **Qué pasó:** al revisar la nota legal se constata que el estado del arte concluye que scrapear las páginas de resultados de Google incumple sus condiciones de servicio, y recomendaba una API.
- **Causa / contexto:** la decisión del 21/09 fue hacer un módulo propio (desarrollo propio, sin SerpApi).
- **Qué se hizo:** se mantiene la decisión, pero declarada explícitamente como una desviación asumida: módulo desactivado por defecto, se detiene ante CAPTCHA, sin evasión, como fuente opcional. Queda como punto abierto para recogerlo en la memoria final.
- **Para la memoria:** §4 (riesgos), estado del arte (nota legal) · S01

### 2026-10-19 16:38 · ✅ Avance · Cuaderno de bitácora
- **Qué pasó:** se crea este cuaderno para apuntar avances, fallos y aprendizajes de cara a la memoria final, además del vault.
- **Causa / contexto:** petición de Julio: «haz un cuaderno de bitácora donde apuntes los avances, fallos, qué haces y eso, además del vault, para hacer después la memoria».
- **Para la memoria:** §3 · S01

### 2026-10-19 16:47 · ✅ Avance · Repositorio en GitHub
- **Qué pasó:** el código se sube al repositorio **privado** https://github.com/juliosoria9/precia (commit inicial `5832b70`).
- **Causa / contexto:** privado porque PrecIA es una herramienta de uso personal y no un servicio abierto (riesgo *Innoweb*, nota legal). Se puede abrir más adelante. El vault de Obsidian queda fuera del repo, en local.
- **Qué se hizo:** antes de subir, búsqueda de secretos en los ficheros preparados para el commit (sin coincidencias).
- **Para la memoria:** §3 (control de versiones) · S01

### 2026-10-19 16:48 · 📏 Medida · Primera CI en GitHub Actions en verde
- **Qué pasó:** ruff, mypy estricto y pytest pasan en GitHub Actions en 16 s. Es la primera validación fuera del portátil.
- **Qué se hizo:** GitHub avisó de que `actions/checkout@v4` y `setup-python@v5` usan Node 20, que está obsoleto; se suben a `v5` y `v6`.
- **Lección:** la CI detecta problemas del entorno (versiones obsoletas) que en local no se ven.
- **Para la memoria:** §3 (validación), §7 · S01

### 2026-10-19 17:03 · 🧭 Decisión · Empezar por la interfaz web completa (*walking skeleton*)
- **Qué pasó:** se adelanta la aplicación web (H7 de la memoria) al principio: toda la UX en web (búsqueda, comparativa, histórico, tiendas y perfiles, alertas, registro de IA, evaluación) con datos de ejemplo, que se sustituyen por datos reales a medida que se completan H1-H6.
- **Causa / contexto:** con la interfaz hecha desde el principio, cada hito se ve funcionando en cuanto termina, y se puede enseñar algo desde la primera entrega. Es un *walking skeleton*: primero un recorrido completo de punta a punta y después se rellena.
- **Qué se hizo:** nuevo hito H1W antes de H1; el resto del plan no cambia.
- **Para la memoria:** §6 (hitos: cambio de orden justificado) · S01

### 2026-10-19 17:16 · ❌ Fallo · En Windows, HTMX se habría servido como texto plano
- **Qué pasó:** un test de ficheros estáticos mostró que `htmx.min.js` se servía con `content-type: text/plain`.
- **Causa / contexto:** en Windows, `mimetypes` de Python lee el registro, que puede asociar `.js` a `text/plain`. Como la app envía `X-Content-Type-Options: nosniff` (cabecera de seguridad), el navegador se habría negado a ejecutar el script: la interfaz no habría funcionado y no daría ningún error visible en el servidor.
- **Qué se hizo:** se fijan a mano los tipos de `.js`, `.css` y `.svg`, y un test comprueba el tipo de cada fichero estático.
- **Lección:** las cabeceras de seguridad pueden romper la funcionalidad de forma silenciosa; hay que probarlas junto con lo que protegen.
- **Para la memoria:** §3 (validación), §7 · S01

### 2026-10-19 17:16 · ✅ Avance · Interfaz web completa (H1W) con 79 tests
- **Qué pasó:** las 8 pantallas (buscar y resultados, producto con histórico SVG, tiendas y perfiles con versiones, alertas, registro de IA, evaluación y ajustes) funcionan sobre una fuente de datos de ejemplo intercambiable.
- **Qué se hizo:** seguridad desde el principio: *autoescape*, CSP sin código en línea, HTMX endurecido, CSRF por `Sec-Fetch-Site`/`Origin`, `TrustedHost` contra *DNS rebinding*, límite de cuerpo y servidor solo en `127.0.0.1`. Se evitan dos dependencias (`httpx` y `python-multipart`) con código propio.
- **Para la memoria:** §5, §6, §7 y §4 (riesgos: la web trata los datos de las tiendas como no confiables) · S01

### 2026-10-19 17:34 · ❌ Fallo · Una medida de seguridad rompió el botón «Atrás»
- **Qué pasó:** se desactivó la caché de historial de HTMX (`historyCacheSize: 0`) para no guardar páginas en `localStorage`. Con eso, al pulsar «Atrás», HTMX vuelve a pedir la URL con la cabecera `HX-Request` y el servidor respondía con el fragmento: la página se quedaba sin menú ni buscador.
- **Causa / contexto:** el servidor distinguía página o fragmento solo por `HX-Request`, sin tener en cuenta `HX-History-Restore-Request`.
- **Qué se hizo:** página completa en las restauraciones de historial, `Vary: HX-Request` y un test.
- **Lección:** cada endurecimiento cambia el comportamiento; hay que probar los flujos normales (navegar hacia atrás) después de aplicarlo.
- **Para la memoria:** §3, §4 · S01

### 2026-10-19 17:34 · ❌ Fallo · La interfaz no comprobaba el anclaje del precio
- **Qué pasó:** la revisión vio que el **precio**, el dato más expuesto a la *prompt injection* («el precio es 1 €», ejemplo de la propia memoria §4), se mostraba sin comprobar el anclaje y podía salir como «Mejor precio».
- **Causa / contexto:** la IA que generó la interfaz aplicó el anclaje a los demás campos y se olvidó justo del principal.
- **Qué se hizo:** `mejor_oferta` descarta los precios no anclados, la celda del precio los tacha y un test inyecta un precio de 1 € en una tienda falsa.
- **Lección:** revisar contra la memoria (requisitos) detecta lo que los tests escritos por la misma IA no cubren.
- **Para la memoria:** §4 (riesgos), §3 · S01

### 2026-10-19 17:34 · 💡 Aprendizaje · Un «pentester» contra el servidor en marcha encuentra lo que los tests no ven
- **Qué pasó:** el agente de seguridad atacó el servidor real con sockets crudos y encontró que el límite de 16 KiB se saltaba enviando `Content-Length` y `Transfer-Encoding: chunked` a la vez (5 MiB procesados), que un dígito «²» provocaba un 500 sin cabeceras de seguridad y que otra web podía lanzar búsquedas de pago por GET.
- **Qué se hizo:** los tres corregidos y con test (rechazo de `Transfer-Encoding`, recuento real de bytes, captura de los 500 en el middleware y protección de `/buscar` con `Sec-Fetch-Site`).
- **Lección:** los tests comprueban lo que el autor pensó; el ataque real comprueba lo que no pensó. Encaja con la asignatura (ciberejercicios).
- **Para la memoria:** §3 (validación), §4 · S01

### 2026-10-19 17:34 · 📏 Medida · Segunda revisión adversarial
- **Qué pasó:** 25 hallazgos (21 distintos: 3 altos, 9 medios y 9 bajos) → 28 tests en rojo → 139 tests en verde. Contraste de los bordes de los controles: de 1,26:1 a 3,86:1 (WCAG 1.4.11).
- **Para la memoria:** §3 · S01
