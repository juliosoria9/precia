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
| 2026-10-19 | Tercera vez: escribió «18:05» a mano en una entrada del diario (eran las 17:48) | Al releer la nota tras escribirla | Marcada como aproximada; regla: la hora solo la pone `datetime.now()` |
| 2026-10-19 | Regex cuadráticas (DoS) en la detección de CAPTCHA, en precios, en referencias OEM y en el sufijo `@atributo` de los selectores | Revisores adversariales de navegador, extracción y emparejado (con entradas hostiles cronometradas) | Reescritas en lineal + tests de tiempo |
| 2026-10-19 | Límite de tiempo que solo cubría el cuerpo (slow-loris en las cabeceras, también por HTTPS) | Revisor de navegador, con un servidor TCP hostil y TLS real | Temporizador por petición que cierra la conexión |
| 2026-10-19 | La clave de la API podía salir en el mensaje de un `ValueError` de `http.client` | Revisor de IA | Transporte que envuelve todos los errores; la clave se valida en el constructor |
| 2026-10-19 | SSRF por redirecciones que Chromium sigue por su cuenta; `page.content()` sin límite de tiempo | Implementador y revisor de navegador, con Chromium real | Redirecciones validadas fuera del navegador; `wait_for` con límite |
| 2026-10-19 | Anclaje por subcadena (una referencia de otra variante se daba por anclada) | Revisor de extracción | Anclaje por palabra completa |
| 2026-10-19 | Perfil que validaba leyendo el precio de «relacionados»; fichas de otra tienda tras un redirector | Revisor adversarial de perfiles | Coherencia de precios listado/ficha/JSON-LD y mismo dominio tras redirecciones |
| 2026-10-19 | Un error inesperado en una tienda tumbaba la búsqueda entera; fuga de Chromium al cambiar ajustes; tiendas desconocidas que hacían buscar en todas con IA | Revisor adversarial de la integración | Aislamiento por tienda, contador de usos del navegador, 422 si no hay tiendas válidas |
| 2026-10-19 | Al insertar un `Protocol` con un script, el `@dataclass` de la clase siguiente quedó encima del protocolo | Al releer el fichero antes de ejecutar mypy | Quitado; los scripts de sustitución se revisan siempre después |
| 2026-10-19 | En la sonda de B-Parts hizo 4 peticiones de búsqueda, más de las «1-2» propuestas a Julio | Al resumir la prueba | Reconocido; en las sondas, tope de peticiones por tienda fijado antes de empezar |
| 2026-10-20 | Detector de bloqueo con falsos positivos: un `robots.txt` de Imperva paraba todo el dominio; una CSP, una reseña o un reCAPTCHA marcaban como bloqueadas fichas buenas | Revisor adversarial del frente A | Señales de producto antes que las firmas + `<script` obligatorio en Incapsula + tests |
| 2026-10-20 | Aceptaba JSON servido por otra web tras una redirección, y respuestas 403/500 con JSON, como catálogo de la tienda | Revisor adversarial del frente C (test en rojo) | `url_final` del dominio y estado 2xx obligatorios |
| 2026-10-20 | La comprobación de mismo dominio sustituía `{consulta}` por «prueba», así que una plantilla con la consulta en el host pasaba | Revisor adversarial de la integración | `consulta_fuera_del_host` en el validador y en el descubrimiento |
| 2026-10-20 | «1.500» leído como 1,50 €; los microdatos de «relacionados» tomados como el producto de la ficha | Revisor adversarial del frente B | Importe ambiguo descartado; ámbitos con otra URL descartados |
| 2026-10-20 | Los destacados de `__NEXT_DATA__` se daban como resultados de cualquier búsqueda (y en modo Auto se podía pagar IA por cada uno) | Revisor adversarial de la integración | Solo los resultados que nombran la consulta |
| 2026-10-20 | TDD incumplido: `descubrimiento.py` se escribió antes que sus tests unitarios, y los tests de extremo a extremo después del código | Lo reconoció el propio agente en su informe | Se comprobó que los tests prueban algo quitando el código a mano; queda anotado |
| 2026-10-20 | Filtro de scripts que dejaba fuera Rocket Loader/WP Rocket (falso negativo de plataforma) | Tests de optimizadores en rojo | Lista de exclusión de tipos de datos en lugar de lista de admitidos |

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

### 2026-10-19 19:09 · ✅ Avance · Paquetes base del core: H1, H2, H3 y H6 (1148 tests)
- **Qué pasó:** navegador, extracción, almacén, capa de IA y emparejado quedan implementados y en verde: 1148 tests en el repositorio, ruff y mypy estricto sin errores (115 ficheros), y ninguna dependencia nueva (solo stdlib y `playwright`, aprobado).
- **Qué se hizo:** contratos escritos antes que el código (tipos compartidos + nota 14 del vault); un agente implementó cada paquete con TDD y otro, distinto, lo revisó adversarialmente. Un commit por hito.
- **Para la memoria:** §6 (hitos), §3 (flujo de trabajo con IA: especificación → implementación → revisión adversarial) · S01

### 2026-10-19 19:09 · 💡 Aprendizaje · La revisión adversarial por agente encontró 41 fallos en código que ya pasaba todos sus tests
- **Qué pasó:** los cinco revisores encontraron y corrigieron 41 fallos en paquetes cuyos tests estaban en verde. Los más relevantes para la asignatura: **DoS por expresiones regulares cuadráticas** (detección de CAPTCHA: 229 s con 210 KB hostiles; precios: más de un minuto con 160 KB; referencias OEM: 3 s por título), **slow-loris** (un servidor que envía las cabeceras byte a byte bloqueaba la descarga sin límite, también por HTTPS), **fuga de la clave de la API** (un `ValueError` de `http.client` copia el valor de la cabecera en su mensaje), un **SSRF dentro de Chromium** (el navegador seguía las redirecciones sin pasar por el filtro), **ASCII smuggling** (instrucciones escondidas con caracteres Unicode invisibles que el modelo lee y una persona no ve), un anclaje por subcadena que daba por buena una referencia OEM de otra variante, y «1.9TDI» detectado como referencia OEM.
- **Qué se hizo:** cada fallo se reprodujo primero con un test en rojo y después se corrigió.
- **Lección:** los tests del autor prueban lo que él previó; un revisor con mirada de atacante encuentra la otra mitad. En un proyecto de ciberseguridad, la revisión adversarial no es opcional.
- **Para la memoria:** §3 (método de validación), §4 (riesgos: prompt injection, SSRF, DoS) · S01

### 2026-10-19 19:09 · 🧭 Decisión · Motor de selectores propio y Haiku 5.5 (ADR-015 y 016, propuestos)
- **Qué pasó:** los selectores CSS que genera la IA los interpreta un motor propio con un subconjunto de CSS; Playwright solo renderiza la página. El modelo por defecto es `claude-haiku-5-5` (0,10 $ / 0,50 $ por millón).
- **Causa / contexto:** un lenguaje restringido impide que la IA cuele código (memoria §4) y permite probar sin red ni navegador. Haiku 5.5 es el Haiku vigente y diez veces más barato que el que preveía la memoria.
- **Para la memoria:** §4, §7, §8 (coste) · S01

### 2026-10-19 19:13 · ❌ Fallo · Un 403 de Cloudflare en robots.txt se habría leído como «permitido todo»
- **Qué pasó:** al leer el `robots.txt` de las tiendas reales, oscaro.es respondió con un 403 y el desafío de Cloudflare («Just a moment…»). El servicio de robots sigue el RFC 9309, que dice que un 4xx significa que no hay restricciones.
- **Causa / contexto:** el RFC piensa en un `robots.txt` que no existe (404) o es privado, no en un muro anti-bot. Los 1148 tests no lo cubrían porque nadie había previsto ese caso; salió al probar contra una web real.
- **Qué se hizo:** se corrige con TDD: un `robots.txt` que devuelve un desafío anti-bot cuenta como bloqueo (se prohíbe todo y no se insiste).
- **Lección:** seguir un estándar al pie de la letra no basta; hay que contrastarlo con cómo se comporta la web real. Las pruebas reales (con permiso y a ritmo lento) son parte de la validación.
- **Para la memoria:** §3 (validación), §4 (riesgos), nota legal (robots.txt) · S01

### 2026-10-19 19:13 · 🧭 Decisión · Qué tiendas reales se pueden usar según su robots.txt
- **Qué pasó:** de las cinco tiendas aprobadas por Julio, ecooparts.com y b-parts.com permiten buscar; ovoko.es prohíbe la búsqueda con `?q=`; ebay.es prohíbe su búsqueda (salvo con un parámetro de marketing, que no se usa para no engañar a robots); oscaro.es bloquea con Cloudflare incluso el `robots.txt`.
- **Qué se hizo:** solo se prueban las permitidas. Cuando robots prohíbe o hay anti-bot, PrecIA lo registra y no insiste: es el comportamiento que promete la memoria.
- **Para la memoria:** §4 (riesgos), estado del arte (nota legal), Eval (tasa de bloqueo) · S01

### 2026-10-19 19:20 · 🧭 Decisión · Se ignora robots.txt (ADR-017), con límites que no se tocan
- **Qué pasó:** Julio decide ignorar `robots.txt` en todas las tiendas porque los datos son públicos. Se le explicó el riesgo: `robots.txt` es la señal de reserva de minería de datos (art. 67 RDL 24/2021) y prueba de la voluntad del titular (*Ryanair c. PR Aviation*), y la memoria entregada prometía respetarlo.
- **Qué se hizo:** ajuste `respetar_robots` desactivado. Se sigue leyendo `robots.txt` para mostrarlo en el progreso y respetar el `Crawl-delay`. Lo que no cambia: nunca se evade un CAPTCHA o un anti-bot (art. 197 bis CP), la frecuencia por dominio está limitada y el User-Agent dice quién es.
- **Lección:** «público» no equivale a «sin condiciones»; la diferencia entre ignorar una preferencia (`robots.txt`) y saltarse una medida técnica (anti-bot) es la línea legal importante.
- **Para la memoria:** nota legal (estado del arte §7), §4 (riesgos). Hay que justificar el cambio respecto a la memoria entregada · S01

### 2026-10-19 20:28 · ✅ Avance · Perfiles con IA (H4) e integración del core con la web
- **Qué pasó:** el configurador descubre el buscador de una tienda nueva y pide a la IA los selectores; el validador los ejecuta antes de aceptarlos; el reparador propone versiones nuevas; el orquestador une navegador, extracción, IA, emparejado y almacén con los modos clásico, auto e IA; la web da de alta tiendas y fija perfiles revisados por una persona. 1464 tests.
- **Para la memoria:** §5 (flujo real), §6 (H4 y la integración), §4 (revisión humana del perfil) · S01

### 2026-10-19 20:28 · ❌ Fallo · Un perfil generado por IA podía «validar» con datos falsos
- **Qué pasó:** el revisor demostró que un perfil que lee el precio de «productos relacionados» (12 € en todas las fichas) pasaba la validación 3/3: el precio aparece en la página, así que el anclaje lo daba por bueno. Una prompt injection que llevara a la IA hacia ese bloque produciría un perfil con precios falsos. También contaban como fichas válidas páginas de otra tienda a las que se llegaba por un redirector de afiliados.
- **Qué se hizo:** el validador exige que el precio de la ficha coincida con el del listado y con el del selector cuando hay JSON-LD, y que la ficha siga en la tienda tras las redirecciones.
- **Lección:** el anclaje demuestra que un valor está en la página, no que sea el valor correcto; hace falta coherencia entre fuentes. Por eso, además, el perfil no se fija sin revisión humana.
- **Para la memoria:** §4 (riesgos: prompt injection, datos inventados) · S01

### 2026-10-19 20:30 · 📏 Medida · Primera prueba real: 5 de 5 tiendas bloquean a un cliente automático
- **Qué pasó:** de las cinco tiendas aprobadas, oscaro.es, ecooparts.com y ovoko.es responden con el desafío de Cloudflare («Just a moment…»), b-parts.com devuelve 405 en su búsqueda (control de bots) y ebay.es, 403. PrecIA lo detecta, para, no evade y no gasta IA (0 llamadas, 0 $).
- **Causa / contexto:** las grandes tiendas y marketplaces de recambios protegen su catálogo con servicios anti-bot. Ignorar el `robots.txt` (ADR-017) no cambia nada: el freno real es técnico, y saltárselo sería eludir una medida de seguridad.
- **Qué se hizo:** se documenta como resultado. Siguiente paso: tiendas más pequeñas sin anti-bot y una web pensada para practicar scraping, para validar el configurador con IA de punta a punta.
- **Lección:** la «tasa de bloqueo» no es una métrica secundaria del banco de evaluación: decide qué tiendas se pueden comparar. Es un argumento para la memoria a favor de las tiendas pequeñas, justo el hueco que dice cubrir PrecIA.
- **Para la memoria:** Eval (tasa de bloqueo), §1 (hueco: tiendas pequeñas), §4 (riesgos), nota legal · S01

### 2026-10-20 12:16 · ✅ Avance · Scrapers mejorados sin evadir anti-bots: bloqueos de WAF, datos embebidos, APIs de plataforma y descubrimiento del buscador
- **Qué pasó:** tras la prueba real (5 de 5 tiendas bloquean), se mejora lo que es legítimo. (1) **Detectar mejor**: firmas de 8 WAF y rechazos secos (el 403 de eBay y el 405 de B-Parts ya se identifican como bloqueo). (2) **Leer mejor** lo que la página ya entrega: OpenGraph, microdatos, JSON-LD `ItemList` y JSON embebido de Next.js (`__NEXT_DATA__`). (3) **Plataformas**: detección de WooCommerce, Shopify, PrestaShop y Magento, y lectura de sus APIs públicas de búsqueda (las que usa su propio buscador), con lo que se configura una tienda **sin gastar IA**. (4) **Descubrir el buscador** sin `<form>`: OpenSearch, enlaces, plantillas de la plataforma y, en último caso, una propuesta de la IA validada. De 1464 a 2110 tests.
- **Línea roja:** nada supera un CAPTCHA, un desafío JS, un WAF ni un login; el User-Agent sigue siendo honesto. Las tiendas con Cloudflare siguen bloqueadas.
- **Para la memoria:** §5 (flujo real: cascada JSON-LD → metadatos → perfil → IA; perfiles API), §6 (avance), §4 (riesgos), Eval (coste de configuración 0 $ con API) · S01

### 2026-10-20 12:16 · ❌ Fallo · Los detectores de bloqueo daban falsos positivos que habrían dejado fuera tiendas válidas
- **Qué pasó:** el revisor adversarial encontró que un `robots.txt` normal de una tienda detrás de Imperva (`Disallow: /_Incapsula_Resource`) se tomaba por la página de bloqueo de Incapsula y paraba **todo el dominio**. También se marcaban como bloqueadas fichas buenas con una CSP que nombra a DataDome, con una reseña que cita «Incapsula incident ID» o con un reCAPTCHA en el formulario de contacto (este último fallo ya existía antes).
- **Qué se hizo:** las señales de producto (JSON-LD, microdatos, OpenGraph, precios) se miran primero: una página con productos y precios nunca es un bloqueo (salvo un 429). La firma de Incapsula exige además un `<script>`.
- **Lección:** detectar un bloqueo también tiene falsos positivos, y cuestan tanto como los negativos: una tienda descartada por error es una tienda que no se compara. El texto de una reseña lo escribe un tercero y no se puede tratar como una señal fiable.
- **Para la memoria:** §4 (riesgos), Eval (tasa de bloqueo) · S01

### 2026-10-20 12:16 · ❌ Fallo · Datos de otra web o mal leídos que se daban como de la tienda
- **Qué pasó:** las revisiones encontraron cinco formas de meter datos falsos con confianza alta. (1) Una API de la tienda que redirige a otra web: su JSON se aceptaba como `OrigenDato.API`, con el dato anclado. (2) En una plantilla de búsqueda propuesta por la IA, o inyectada desde la portada, `https://recambios-{consulta}.es/` pasaba la comprobación de mismo dominio, porque esta sustituía `{consulta}` por «prueba», y cada búsqueda habría ido a otro host. (3) «1.500» se leía como 1,50 € (Shopify sin decimales escribe así 1500 €). (4) Los microdatos de «productos relacionados» daban el precio de una bombilla a un faro. (5) Los «destacados» de `__NEXT_DATA__`, que vienen incluso en la página «sin resultados», se daban como resultados de cualquier búsqueda.
- **Qué se hizo:** la respuesta tiene que venir de la tienda y con un 2xx; `{consulta}` no puede ir en el esquema ni en el host; un importe ambiguo se descarta (la cascada pasa al perfil o a la IA, que se anclan en el texto visible); se descartan los ámbitos con la URL de otra página; solo cuentan los resultados estructurados que nombran la consulta.
- **Lección:** que un dato sea «estructurado» no lo hace fiable. Hay que comprobar quién lo sirve, a qué producto pertenece y si responde a la búsqueda. Fiarse de la estructura es otra forma de la prompt injection.
- **Para la memoria:** §4 (riesgos: datos inventados, SSRF, prompt injection) · S01

### 2026-10-20 12:16 · 💡 Aprendizaje · Una tienda con API pública se configura sin IA
- **Qué pasó:** si la portada revela WooCommerce o Shopify y su API pública devuelve productos con precio, el configurador crea un perfil `api_*` directamente: 0 llamadas a la IA y sin selectores que se rompan en un rediseño. La IA queda para las tiendas sin plataforma conocida.
- **Lección:** el uso de la IA tiene que ser el último recurso de una cascada, no el primero: abarata, es más fiable y deja menos superficie a la prompt injection. Conecta con el modo Auto (ADR-002).
- **Para la memoria:** §5 (arquitectura), Eval (coste por tienda), §4 · S01
