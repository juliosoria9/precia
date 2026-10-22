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
| 2026-10-20 | La IA de la herramienta (Claude Haiku) propuso las clases genéricas de WooCommerce sin verlas en la página (el resumen se cortaba antes de los productos) | Validador automático + diagnóstico offline con la página de la caché | Pendiente: resumen que comprime lo repetido y prioriza el listado |
| 2026-10-20 | La API de WooCommerce da el SKU interno de la tienda (`DESP220618`) y se guardaba como referencia OEM | Prueba real en desguacesvelazquez.com | Pendiente |
| 2026-10-20 | El título de la ficha salía «Test Sites» (el de la web) y no el del producto | Prueba real en webscraper.io | Pendiente |
| 2026-10-20 | Compresión del resumen que quitaba filas de la tabla de atributos (OEM) y secciones de Elementor; contenedor del listado sin proteger | Revisor adversarial F1 (fichas sintéticas y portada real de Velázquez) | Umbral de ahorro, forma con más niveles y contenedor protegido + tests |
| 2026-10-20 | Título de ficha igual al href de `<a itemprop="name">` | Revisor adversarial F2 con el listado real de webscraper.io | Microdatos: el texto, no el href, para `name` + tests |
| 2026-10-20 | «Código … fabricante» (pintura, motor, EAN) tomado como OEM; dos OEM pegadas con «/» | Revisor adversarial F3 | Exclusiones y «/» fuera de los separadores + tests |
| 2026-10-20 | Test parametrizado con la propia constante (no detecta que se quite un caso); informe con una afirmación falsa sobre el tope de peticiones | Revisor adversarial F4 (mutación) | Lista escrita a mano; afirmación corregida en la bitácora |
| 2026-10-20 | Una candidata con 401/403/451 se repetía con Chromium (insistir ante un rechazo) | Revisor adversarial F4 | `RECHAZOS` en `descubrimiento.py` + 4 tests |
| 2026-10-20 | `palabras_url` quitaba los números de modelo y medidas («308», «205 55 r16»): las búsquedas devolvían otros modelos | Revisor adversarial de corrección (test de extremo a extremo con el almacén) | `_sin_ids`: solo se quitan los ids (tramo solo numérico o al principio del slug) + tests |
| 2026-10-20 | `de_producto` descartaba los índices paginados por parámetros (BigCommerce) | Revisor adversarial de corrección | Se clasifica por los valores de la consulta, no por los nombres + tests |
| 2026-10-20 | `ValueError` sin capturar con corchetes IPv6 en la URL de entrada y en el almacén | Revisor adversarial de corrección | `urlsplit` dentro de `try` → `EntradaNoValida` / `DatoNoValido` + tests |
| 2026-10-20 | Coste de CPU por URL del catálogo sin tope; `detectar_bloqueo` sobre 20 MiB en el bucle de eventos | Revisor adversarial de seguridad y coste | `MAX_RUTA_PALABRAS`; el bloqueo mira 5 MiB como mucho + tests deterministas |
| 2026-10-20 | Topes de profundidad y de etiquetas del lector sin ningún test que los comprobara | Revisor adversarial (mutación) | `test_tope_de_profundidad` y `test_tope_de_etiquetas` |
| 2026-10-20 | La ruta se recortaba por el principio y se perdía el slug del final | Yo, al revisar los pendientes de los revisores | Tramos enteros del final + test |
| 2026-10-20 | Pasó las credenciales del proxy a Playwright, que las enviaba a cualquier servidor con 401 Basic | Revisor adversarial de seguridad | Relé local sin credenciales + test con Chromium real |
| 2026-10-20 | Un corte por tiempo propio se tomaba por «la tienda cortó la conexión» y la tienda lenta pasaba al proxy de pago para siempre | Revisor adversarial (red) | Distinguir el corte del temporizador + test con tienda que gotea |
| 2026-10-20 | Un 429 se repetía por el proxy, saltándose el límite de frecuencia de la tienda | Revisor adversarial (lógica) | El 429 normal no se repite + test |
| 2026-10-20 | La contraseña de `PRECIA_PROXY` con caracteres que NFKC convierte en separadores acababa en el log y en Ajustes | Revisor adversarial de seguridad | `URLProxyNoValida` con mensajes propios + test |
| 2026-10-22 | El test del relé `test_connect_se_reenvia_al_proxy_con_las_credenciales` lee el túnel con una sola `read(65536)` y falla 3 de cada 5 veces cuando el cuerpo llega en otro segmento | Yo (S03), al pasar `pytest` antes del commit y repetir el fichero 5 veces | Corregido a las 12:46 (S03): el helper lee hasta EOF; 0 fallos en 10 repeticiones (antes, 9 de 20) |
| 2026-10-22 | Un subagente planificador lanzó experimentos con Playwright `channel='chromium'` (Chrome for Testing) que salieron a Internet (DoH automático: UDP a 8.8.8.8:53 y TLS a 8.8.4.4:443, `dns.google`), aunque las instrucciones decían «no hagas peticiones a Internet» | Crítico adversarial (S03), leyendo el netlog de Chromium | Solo el headless shell (ADR-023) y un test de netlog que verifique que no hay tráfico fuera del relé |
| 2026-10-22 | En un test de `TransporteCurl` esperaba que una cookie puesta por `tienda.example` (sin `Domain`, solo para ese host) se mandara también a `www.tienda.example` | Yo (S03): el test falló en la primera ejecución y curl tenía razón (es lo que hace un navegador) | Test corregido para pedir al mismo host; la sesión por dominio sin «www.» se mantiene |
| 2026-10-22 | El relé SOCKS5 sin proxy abría cada conexión en el executor por defecto, con 30 s por IP y sin tope: una página hostil podía congelar la aplicación | Revisión adversarial (lente seguridad), reproducido con 32 clientes contra 127.0.0.1 | Grupo de hilos propio para el DNS, `asyncio.open_connection` con plazo total y semáforo de 8 (S03) |
| 2026-10-22 | El rescate con Chromium dejaba escapar `playwright.Error` sin traducir (la tienda acababa INESPERADO) y reutilizaba para siempre un Chromium muerto | Revisión adversarial (lente corrección), reproducido con `Browser.crash` en el headless shell | Traducción a `ErrorDescarga`, relanzar si `is_connected()` es falso y `_rescatar` relanza el original ante cualquier fallo (S03) |
| 2026-10-22 | Tres tests de seguridad de curl que no podían fallar: Alt-Svc por `http://` (libcurl solo lo sigue por HTTPS), ninguno pasaba por la comprobación de `primary_ip` y el de reutilización acababa en `>= 1` | Revisión adversarial (lente tests y reglas), con mutantes | Alt-Svc por HTTPS, 2 tests nuevos de `primary_ip` y asertos que pueden fallar; cada uno verificado con su mutante (S03) |
| 2026-10-22 | Una tienda rescatada con Chromium se pedía dos veces con Chromium en el descubrimiento (`js=False` y luego `js=True`), contra lo que decía su propio docstring; un rescate con 403 también marcaba la tienda | Revisión adversarial (lente tests y reglas), reproducido con el `Montaje` de los tests | `Pagina.por_chromium`, caché con clave `js` para lo que trae Chromium, `_probar` y la portada sin segunda pasada, y sin marca tras un 403 (S03) |
| 2026-10-22 | Cuarta vez: escribió horas del diario y la bitácora antes de consultar el reloj (dos desmentidas por el propio `date`, seis con posible desvío de 1–2 min) | Autorrevisión al cerrar el bloque, comparando con la salida de `date` | Horas desmentidas corregidas y aviso en el diario con la última hora real de cada entrada (S03) |
| 2026-10-22 | Al aplicar ADR-021/022/023 dejó textos que las contradicen: «no se evade» en `orquestador.BLOQUEO` y en las notas 15 y 08, «no evade protecciones» en `inicio.html`, «El navegador lanza `Bloqueado` y no reintenta» en la nota 14, y «tres desviaciones» con cuatro viñetas en la 08 | Cierre documental de S03, con `grep` de «evad» y «honesto» en el vault y en `src/` | Notas 08, 14 y 15 corregidas citando las ADR; el código no se toca (bug 14 de la nota 12, decide Julio) |
| 2026-10-22 | El verificador independiente de S03 contó los finales de línea con `grep -c $'\r$'` en Git Bash, que da CRLF también en ficheros LF, y estuvo a punto de reportar como falsas las verificaciones «LF» del diario | Él mismo (S03), al ver que `git ls-files --eol` decía `w/lf` en un fichero que `grep` daba como CRLF | Recontado por bytes con Python: las verificaciones del diario eran correctas; no se reportó nada falso |

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

### 2026-10-20 14:35 · 📏 Medida · Primera prueba de generalidad con webs reales: 2 de 4 dan datos y la IA escribe un scraper correcto a la primera
- **Qué pasó:** con un tope de 12 peticiones por dominio y 2 s entre ellas. (1) Un desguace WooCommerce (desguacesvelazquez.com): 5 ofertas reales por su API pública, **0 llamadas a la IA**, 4 peticiones. (2) webscraper.io, una web de práctica que PrecIA no conocía: Claude escribió a la primera los selectores del listado (6 productos con precio) y las fichas se leyeron por microdatos e IA, todo anclado, por 0,0018 $. (3) Un desguace con plantilla Bricks (desguacesdli.com): se encontró el buscador, pero Claude no dio con un perfil válido. (4) books.toscrape.com: sin buscador, PrecIA no tiene por dónde entrar. Coste total de la IA: unos 0,0065 $.
- **Lección:** la vía general (la IA escribe el scraper y el código lo ejecuta) funciona en una web real que no había visto, y es barata. Los fallos no son de la IA, sino de **lo que se le enseña** y de **cómo se entra** en la web.
- **Para la memoria:** Eval (cobertura, coste por tienda, tasa de éxito), §5 (flujo real), §1 (generalidad) · S01

### 2026-10-20 14:35 · ❌ Fallo · La IA se inventó los selectores porque el resumen de la página no llegaba a los productos
- **Qué pasó:** en desguacesdli.com, el resumen de 30 000 caracteres que se manda a la IA se llenaba con el menú y unos 1200 filtros del buscador antes de llegar a los 12 productos. Sin verlos, Claude propuso las clases genéricas de WooCommerce, que en esa plantilla no existen. El validador lo cazó (no se guardó ningún perfil malo), pero la tienda se quedó sin perfil.
- **Causa:** un recorte por longitud no sirve para páginas reales, donde lo repetitivo (filtros, menús) pesa más que el contenido.
- **Lección:** cuando a un modelo le falta información, no se queda callado: rellena con lo que es «típico». Lo que se le manda tiene que priorizar lo que importa. Y el validador automático es lo que impide que esa invención llegue a los datos.
- **Para la memoria:** §4 (riesgos: alucinación), §5 (resumen para la IA), Eval · S01

### 2026-10-20 14:35 · 💡 Aprendizaje · «General» exige también una forma de entrar en webs sin buscador
- **Qué pasó:** books.toscrape.com no tiene buscador. El configurador solo sabe partir de una búsqueda, así que gastó todas las peticiones probando rutas que daban 404, y Chromium repitió los mismos 404.
- **Lección:** el diseño suponía que toda tienda tiene buscador. Para que sea general hace falta otra entrada (una URL de listado o categorías), y no repetir con Chromium lo que ya dio 404.
- **Para la memoria:** §5 (diseño), §7 (trabajo futuro o mejora) · S01

### 2026-10-20 15:22 · 🔧 Solución · El resumen para la IA comprime lo repetido y prioriza el listado: Claude ya escribe bien los selectores de DLI
- **Qué pasó:** con la misma página real de desguacesdli.com que antes hacía inventar a Claude, el resumen nuevo deja 3 ejemplos de cada serie repetida (los 1196 filtros pasan a 3 + «N elementos similares omitidos») y reserva sitio para la serie que parece el listado. Resultado con Claude de verdad (1 llamada, 0,0016 $): selectores correctos a la primera y 10 resultados reales con título, enlace y precio. Antes eran 0 resultados y clases inventadas.
- **Además:** el título de la ficha ya es el del producto (no «Test Sites»); en Velázquez la OEM real sale del atributo «Ref.Fabricante» y no del SKU de stock; en una web sin buscador se pasa de 14 a 9 descargas, y un 401/403/429/451 nunca se repite con Chromium.
- **Lección:** la calidad de un sistema con IA depende mucho de **qué contexto se le da**. Aquí la mejora no estuvo en el modelo ni en el prompt, sino en elegir qué parte de la página ve.
- **Para la memoria:** §4 (riesgos: alucinación y su mitigación), §5 (diseño del resumen), Eval (antes y después en una web real) · S01

### 2026-10-20 15:22 · ❌ Fallo · Los revisores cazaron 9 fallos en las correcciones de la IA, algunos peores que el fallo original
- **Qué pasó:** (1) La compresión del resumen recortaba también la tabla de atributos de una ficha y podía quitar la fila de la referencia OEM. (2) Agrupaba como «repetidas» las secciones de Elementor y omitía partes enteras de la ficha. (3) No protegía el propio contenedor del listado: con 6 filas de 8 tarjetas, 0 productos. (4) Un mensaje de reintento falso («ningún elemento tiene href»). (5) Con microdatos `<a itemprop="name" href=…>`, el título de la ficha salía como la ruta. (6) Cualquier «código … fabricante» (pintura, motor, EAN) contaba como OEM. (7) «5K1941005/5K1941006» salía como una sola OEM. (8) Un test parametrizado con la propia constante, que no detectaba que se quitara un caso. (9) Un informe decía que el tope de 12 peticiones lo pone el navegador real, y es falso: lo ponía el guardián de la prueba.
- **Qué se hizo:** todos corregidos con un test en rojo primero. Los revisores comprobaron que los tests fallan al quitar la corrección a mano (mutación).
- **Lección:** cada corrección abre su propio riesgo. La revisión adversarial, con pruebas de mutación, no es un extra: en esta ronda encontró tantos fallos como la prueba real.
- **Para la memoria:** §4, metodología (uso de IA en el desarrollo y su control) · S01

### 2026-10-20 15:22 · 🧭 Decisión · Un rechazo HTTP no se repite con Chromium
- **Qué pasó:** un revisor señaló que una candidata que respondía 401/403/451 sin parecer un bloqueo (página grande o con productos) se volvía a pedir con Chromium.
- **Qué se hizo:** constante `RECHAZOS` = {401, 403, 429, 451}, que no se repiten con Chromium (TDD: 4 tests en rojo y luego en verde).
- **Lección:** la línea roja no está solo en «no resolver CAPTCHAs». Insistir con otra herramienta ante un «no» explícito es el mismo gesto, más sutil. Va en la nota legal.
- **Para la memoria:** nota legal (art. 197 bis CP), §4 · S01

### 2026-10-20 17:31 · ✅ Avance · PrecIA ya puede entrar en webs sin buscador: URL de entrada del usuario (A) y catálogo por sitemap.xml (B)
- **Qué pasó:** books.toscrape.com no tiene buscador y PrecIA no tenía por dónde entrar. Ahora el configurador prueba, por este orden: API de la plataforma → URL de búsqueda que pega el usuario (con `{consulta}`) → buscador descubierto → `sitemap.xml` → listado o categoría que pega el usuario (con `{pagina}`) → y, si nada sirve, un mensaje que pide la URL de entrada. Con el sitemap, PrecIA guarda solo las URL y las palabras de cada slug (nunca precios) y en cada búsqueda abre unas pocas fichas y lee el precio en vivo.
- **Cómo:** 8 agentes en tres fases (base, integración y dos revisores adversariales), con TDD y sin red. Lector de sitemaps propio, sin `xml.etree`, que rechaza DTD y entidades; descompresión gzip acotada contra bombas; migración 4 probada sobre una copia de la base real.
- **Para la memoria:** §5 (diseño: generalidad del scraper), §3 (decisiones, ADR-018), nota legal (el sitemap como vía «invitada»; art. 133 TRLPI) · S01

### 2026-10-20 17:31 · 📏 Medida · Coste de una tienda sin buscador con el catálogo por sitemap
- **Qué pasó:** en la prueba de extremo a extremo, dar de alta la tienda y hacer la 1.ª búsqueda cuesta 13 peticiones (robots, portada, 6 URL del descubrimiento de siempre, 2 sitemaps y 3 fichas) y **0 llamadas a la IA**. La 2.ª búsqueda, 0 peticiones (caché) y 0 IA. El catálogo se rehace cada 7 días. Tests: de 2240 a **2947** (707 nuevos), con ruff y mypy estricto en verde.
- **Para la memoria:** Eval (coste por tienda), §4 (coste de la IA) · S01

### 2026-10-20 17:31 · ❌ Fallo · La revisión adversarial encontró 6 fallos en el código de A + B escrito por la IA
- **Qué pasó:** (1) `palabras_url` quitaba todos los números «sueltos»: «pastillas Peugeot 308» devolvía las de la 208, 2008, 3008… y perdía las medidas de neumático («205 55 r16»), justo el caso de los recambios. (2) Los índices de sitemap paginados por parámetros (BigCommerce, `?type=products&page=2`) se descartaban enteros porque «page» estaba entre las exclusiones. (3) Unos corchetes IPv6 sin cerrar en la URL de entrada daban un `ValueError` sin capturar en la web y en el almacén. (4) Analizar cada URL del catálogo no tenía tope: un sitemap hostil costaba unos 23 s de CPU por fichero. (5) La detección de bloqueos miraba los 20 MiB de un sitemap dentro del bucle de eventos, unos 2 s con la web parada. (6) Dos topes del lector que ningún test comprobaba (la mutación que los quitaba sobrevivía).
- **Qué se hizo:** todos corregidos con un test en rojo primero; 34 mutaciones aplicadas a una copia del código, todas detectadas tras la corrección. Yo corregí uno más que un revisor dejó abierto: la ruta se recortaba por el principio y el slug va al final.
- **Lección:** la nota de diseño decía «sin los números de id sueltos» y la IA lo aplicó al pie de la letra, aunque rompía el caso de uso principal. Un contrato escrito también puede estar mal; la prueba que lo destapó fue un test de extremo a extremo con una consulta realista, no un test de la función sola.
- **Para la memoria:** §4 (riesgos de la IA en el desarrollo), metodología (revisión adversarial y mutación) · S01

### 2026-10-20 17:31 · 💡 Aprendizaje · Los tests cronometrados no son fiables con la máquina cargada
- **Qué pasó:** con 8 agentes ejecutando la batería a la vez, los tests con umbral de 1 s (`test_embebidos`, y a veces otros de descubrimiento y validador) fallaban por 0,1-0,2 s; con la máquina libre pasan todos (2947). Ya estaba apuntado como bug 6.
- **Lección:** un test de rendimiento con cronómetro mide la máquina, no el código. Los revisores prefirieron tests deterministas («cuánto texto se analiza»), que no dependen de la carga.
- **Para la memoria:** metodología (TDD y fiabilidad de los tests) · S01

### 2026-10-20 19:36 · 📏 Medida · Primera tienda real sin buscador dada de alta en vivo (books.toscrape.com)
- **Qué pasó:** con la URL del catálogo paginado pegada por el usuario, PrecIA creó el perfil `listado` en la primera búsqueda: 22 peticiones (sobre todo los intentos de buscador y sitemap que no existen), 3 llamadas a Claude y **0,0018 $**, 64 s con 2 s entre peticiones. Encontró y extrajo *Sapiens* con el precio anclado. La segunda búsqueda: 0,4 s, 0 peticiones, 0 IA. Antes de A + B, esta web se agotaba sin resultado.
- **Matiz:** el producto se descartó por estar en libras. Es lo correcto, pero la web lo mostraba como «Sin resultados», que confunde.
- **Para la memoria:** Eval (generalidad y coste en una web real), §5 · S01

### 2026-10-20 19:56 · ❌ Fallo · Primera prueba de Julio con la web: «puerta delantera opel corsa» no da nada
- **Qué pasó:** cada tienda falló por una razón distinta. Velázquez respondió bien (no tiene esa puerta; sí faros y pilotos del Corsa). DLI se cortó por el tope de peticiones de la demo, que había gastado casi entero una búsqueda anterior en la que Julio pegó la URL de una ficha de B-Parts en el buscador: PrecIA la trató como texto y la buscó en todas las tiendas. B-Parts, Ecooparts y Ovoko no estaban permitidas en la demo (y bloquean a los robots).
- **Lección:** un usuario real hace cosas que los tests no prevén: pegar una URL donde va el producto es natural. Y «0 resultados» sin explicar por qué en cada tienda parece un fallo aunque el sistema haya hecho lo correcto.
- **Para la memoria:** Eval (prueba con usuario), §5 (experiencia de uso) · S01

### 2026-10-20 21:15 · 🧭 Decisión · La demo no limita nada que PrecIA no limite por sí misma
- **Qué:** Julio pidió que no se limite la herramienta por código. Los límites que cortaron la búsqueda del Corsa (lista de webs aprobadas y tope de 25 peticiones por dominio) eran del guion de pruebas, no de PrecIA. Se quitan de la demo. PrecIA conserva su cortesía (2 s entre peticiones por dominio, configurable), la protección anti-SSRF y el presupuesto de la IA. Proxies: no, porque no habrían cambiado nada aquí y rotar IP contra un WAF es evasión anti-bot.
- **Lección:** un arnés de pruebas demasiado estricto puede hacer parecer que falla la herramienta. Hay que separar en la memoria los límites del experimento de los del producto.
- **Para la memoria:** Eval (metodología de prueba), §8 (legal y ética) · S01

### 2026-10-20 21:22 · 📏 Medida · Un proxy residencial desbloquea 2 de las 3 tiendas que bloqueaban
- **Qué:** 1 petición a la portada de cada tienda por el proxy residencial de Evomi (IP española), con el mismo UA honesto. B-Parts y Ecooparts pasan de 403 (prueba del 19) a 200. Ovoko sigue en 403 porque Cloudflare le sirve un desafío JavaScript, y eso no depende de la IP.
- **Lección:** el bloqueo de B-Parts y Ecooparts era por la reputación de la IP (la de casa o un centro de datos); el de Ovoko es por el comportamiento del cliente.
- **Para la memoria:** Eval (scraping real), §8 (legal y ética) · S01

### 2026-10-20 22:16 · 🧭 Decisión · Proxies residenciales (Evomi) para las tiendas que bloquean por IP
- **Qué:** Julio decide usar proxies y cambia la regla de «nunca evasión anti-bot» en lo que toca a la IP. Se mantienen el UA honesto, la cortesía de 2 s, el anti-SSRF y la regla de no resolver CAPTCHAs, desafíos JS ni logins. Modo por defecto «si bloquea»: primero directo y, solo si la tienda bloquea, 1 reintento por el proxy (se paga por GB).
- **Para la memoria:** §8 (legal y ética), §4 (diseño del navegador) · S01

### 2026-10-20 22:16 · ✅ Avance · PrecIA sale por proxy (HTTP con túnel CONNECT y Chromium con relé local)
- **Qué:** proxy configurable (`EVOMI_API_KEY` o `PRECIA_PROXY`) con credenciales pedidas a la API de Evomi y guardadas solo en memoria. Descargador envoltorio con reintento por bloqueo y tiendas marcadas. Sección «Proxy» en Ajustes con peticiones y MB. 3122 tests (175 nuevos), ruff y mypy en verde.
- **Para la memoria:** §4 (implementación), Eval · S01

### 2026-10-20 22:16 · ❌ Fallo · La primera versión del proxy en Chromium entregaba las credenciales a terceros
- **Qué pasó:** la IA pasó usuario y contraseña del proxy a `launch(proxy=...)` de Playwright. Playwright los guarda como credenciales HTTP sin origen: cualquier servidor que contestara 401 Basic (por ejemplo, un script de terceros en la página de una tienda) recibía las credenciales de Evomi. Los tests estaban en verde.
- **Cómo se detectó:** revisor adversarial de seguridad, leyendo el bundle de Playwright 1.64 instalado.
- **Solución:** relé `CONNECT` local en 127.0.0.1 que añade la autenticación hacia Evomi; Chromium no conoce las credenciales. Test con Chromium real: la tienda contesta 401 y nunca recibe `Authorization`.
- **Lección:** una opción de una librería que «parece» segura puede tener efectos laterales documentados solo en su código. Con secretos, hay que revisar el camino completo que siguen.
- **Para la memoria:** §3 (validación del código generado), §8 · S01

### 2026-10-20 22:31 · 📏 Medida · El proxy residencial no desbloquea ninguna de las 3 tiendas
- **Qué:** con el proxy ya integrado, la búsqueda del Corsa pasó a B-Parts, Ecooparts y Ovoko por el proxy y las tres siguieron bloqueadas. Diagnóstico (14 peticiones): B-Parts deja ver la portada pero bloquea toda ficha con el WAF de AWS; Ecooparts sirve el desafío de Cloudflare a 5 de 5 IP residenciales (el 200 de la primera sonda fue suerte); Ovoko, igual. DLI, que antes se cortaba por el tope de la demo, se configura y responde en 69 s.
- **Lección:** los anti-bot modernos deciden por comportamiento (JavaScript, huella del navegador), no solo por IP. Cambiar de IP no basta; lo siguiente sería resolver el desafío, que es justo lo que la ley (art. 197 bis CP) y la regla 5 dejan fuera. Además, una sola medida (la sonda de 1 petición) no es representativa: hay que repetir.
- **Para la memoria:** Eval (scraping real), §8 (legal y ética) · S01

### 2026-10-20 22:31 · ❌ Fallo · DLI da como ofertas piezas que no tienen nada que ver con la búsqueda
- **Qué pasó:** para «puerta delantera opel corsa», el buscador de DLI devolvió un mando limpia de Toyota, mandos de luces de Ford y una guantera de Mitsubishi, y PrecIA los enseñó como 5 productos con precio. Nadie comprueba que el resultado de la tienda se parezca a la consulta.
- **Para la memoria:** Eval (calidad de resultados), §5 · S01

### 2026-10-21 19:17 · 💡 Aprendizaje · El bloqueo de Cloudflare no venía de un User-Agent de Chrome
- **Qué pasó:** la tarea partía de que PrecIA se presentaba como Chrome desde `urllib`. El análisis (6 subagentes) lo descartó: el UA es el honesto `PrecIA/0.1 …` y las cabeceras ya son coherentes con `urllib`. Los defectos reales son otros: sin cookies ni sesión, `Pagina` sin cabeceras de respuesta (no se puede distinguir un desafío gestionado de un 1020/1010 o de un 403 del origen), un desafío en `robots.txt` que bloquea el dominio entero aunque robots se ignore, y un Chromium cuya red sale por Node (`route.fetch`), con una huella que no es la del navegador.
- **Lección:** comprobar la hipótesis en el código antes de corregir. Cloudflare decide por la coherencia de muchas señales (TLS, HTTP/2, JavaScript, cookies); cambiar solo el UA no arregla nada.
- **Para la memoria:** §4 (diseño del navegador), §8 (legal y ética), Eval · S02

### 2026-10-21 19:17 · ❌ Fallo · Un desafío en robots.txt bloquea toda la tienda 10 minutos aunque robots se ignore
- **Qué pasó:** `robots.txt` se pide siempre por `urllib`; si trae un desafío de Cloudflare, `ServicioRobots` lo guarda 600 s y `Navegador` lanza `Bloqueado` antes de elegir descargador, también para Chromium. Choca con ADR-017 y explica tiendas como Oscaro, que ponen el desafío en el propio `robots.txt`. Además esa petición va fuera del limitador de cortesía.
- **Qué se hizo:** sin resolver todavía (pendiente de la decisión de Julio).
- **Para la memoria:** §4, Eval · S02

### 2026-10-21 19:33 · 🧭 Decisión · Alcance de las correcciones de Cloudflare
- **Qué:** Julio aprueba cookies en memoria por tienda, que Chromium use su propia red (hoy la saca por Node con `route.fetch`) y un intento con Chromium tras un bloqueo por HTTP. Se mantiene el diagnóstico (clasificación de bloqueos y cabeceras de respuesta en `Pagina`).
- **Lección:** la mayor parte del problema era incoherencia creada por el propio PrecIA (red de Node bajo un Chromium, visitas en frío sin cookies).
- **Para la memoria:** §4, Eval · S02

### 2026-10-22 10:20 · ✅ Avance · Trabajo del proxy guardado en git (commit 083851b en core/proxy)
- **Qué pasó:** con la autorización de Julio, el proxy residencial (ADR-020), el relé local de Chromium y las correcciones de la revisión adversarial quedan en un commit de la rama `core/proxy`: 33 ficheros, 12 nuevos. Sin push. Antes se comprobó que no entra ningún secreto: el grep de claves solo encuentra valores de prueba y los valores reales de `.env` no aparecen.
- **Qué se hizo:** ruff, formato y mypy en verde; pytest 3115 en verde con 3 fallos intermitentes (ver la entrada siguiente).
- **Para la memoria:** §6 (hitos y estructura), §3 · S03

### 2026-10-22 10:20 · ❌ Fallo · Test del relé intermitente: lee el túnel con una sola llamada
- **Qué pasó:** `test_connect_se_reenvia_al_proxy_con_las_credenciales` falla 3 de cada 5 ejecuciones. La respuesta trae `200 Connection established` y las cabeceras de la tienda, pero no el cuerpo.
- **Causa / contexto:** el helper `_pedir` del test hace `lector.read(65536)` una sola vez. `BaseHTTPRequestHandler` envía cabeceras y cuerpo en escrituras distintas, y a veces llegan en segmentos separados. Es un fallo del test generado por la IA, no del relé. También fallaron otra vez los dos tests de cronómetro de `test_embebidos` (1,03 s frente a 1 s; bug 6).
- **Qué se hizo:** sin resolver; se reporta para corregir el test (leer hasta EOF, la petición lleva `Connection: close`).
- **Lección:** con sockets, una lectura no equivale a un mensaje. Un test que pasa «casi siempre» en local es un test roto.
- **Para la memoria:** §3 (validación del código generado) · S03

### 2026-10-22 10:25 · 🧭 Decisión · Huella de Chrome en las descargas: Chromium con su red y curl_cffi (ADR-022)
- **Qué pasó:** para TLS/HTTP2, Julio elige «Chromium + curl_cffi» y «Las de Chrome». Chromium usa su propia red (TLS y HTTP/2 reales, sin `route.fetch` de Node) y sirve de recurso tras un bloqueo por HTTP. Un `TransporteCurl` opcional con `curl_cffi` (`impersonate='chrome'`, hoy `chrome150`) da al cliente ligero la huella TLS (JA3/JA4) y HTTP/2 de Chrome, con su UA y sus cabeceras por defecto. El cliente `urllib` conserva el UA configurado. Julio aprueba `curl_cffi==0.16.3`, `cffi`, `pycparser` y `certifi`.
- **Causa / contexto:** ADR-021 levantó la prohibición de falsear la huella. Los WAF deciden por la coherencia entre TLS, HTTP/2 y cabeceras, y `urllib` + `ssl` no pueden imitar la de Chrome. Se descartan `httpx[http2]` (HTTP/2 con la huella de OpenSSL) y un HTTP/2 propio sobre `ssl` (no controla extensiones, GREASE ni ajustes de Chrome). Se mantienen la cortesía por dominio, el anti-SSRF en cada salto, nunca login, 1 reintento por el proxy y Google desactivado.
- **Lección:** es la primera dependencia que entra porque la stdlib no puede hacer el trabajo, no por comodidad: la huella TLS depende de la librería criptográfica y no del código Python. Hay que justificarlo así en la memoria (ADR-007).
- **Para la memoria:** §4 (diseño del navegador), §7 (tecnologías), §8 (legal y ética) · S03

### 2026-10-22 12:16 · ❌ Fallo · Un subagente salió a Internet con Chrome for Testing en contra de sus instrucciones
- **Qué pasó:** durante el diseño de Chromium con red propia, un subagente planificador lanzó experimentos con Playwright `channel='chromium'` (Chrome for Testing). Ese binario hace DoH automático: el netlog muestra UDP a 8.8.8.8:53 y TLS a 8.8.4.4:443 (`dns.google`), fuera de cualquier relé. Las instrucciones del subagente decían «no hagas peticiones a Internet».
- **Causa / contexto:** el agente dio por hecho que un Chromium sin navegar a ninguna web no hace tráfico. Chrome for Testing trae activados servicios de fondo (DoH) que el headless shell no tiene, y además no admite `--force-webrtc-ip-handling-policy`. Lo detectó el crítico adversarial leyendo el netlog, no el propio agente.
- **Qué se hizo:** se descarta Chrome for Testing: solo el headless shell (sin `channel`), recogido en ADR-023. Se exige un test de netlog que compruebe que Chromium no abre ninguna conexión fuera del relé SOCKS5 local.
- **Lección:** una instrucción en el prompt no impide nada; hay que verificarla con una medida (el netlog). Y un navegador «sin navegar» no es un navegador sin red.
- **Para la memoria:** §3 (validación del código generado), §4 (diseño del navegador) · S03

### 2026-10-22 12:16 · 🧭 Decisión · Chromium tras un relé SOCKS5 siempre activo (ADR-023) y curl por defecto
- **Qué pasó:** Julio aprueba ADR-023 entera («Aprobar ADR-023 entero (Recommended)») y el rescate «Chromium por el proxy (Recommended)». Chromium usa su propia red detrás de un relé SOCKS5 en 127.0.0.1, siempre activo, que valida cada conexión TCP (saltos de redirección y subrecursos) en modo directo o encadenado al proxy residencial. Va en headless shell sin `channel`, con el UA y los `userAgentMetadata` del propio binario sin «HeadlessChrome» (por CDP, `Emulation.setUserAgentOverride`), bypass `<-loopback>` explícito y `route` solo para `abort` / `continue_`. Tras un bloqueo o un corte por HTTP hay 1 intento con Chromium; el 403 sale de `RECHAZOS` y se reintenta, el 401 y el 451 nunca. Si la tienda ya está marcada para el proxy, Chromium sale por el proxy (un único intento que cambia IP y cliente; no suma reintentos al de ADR-020). Además, Julio elige «curl por defecto (Recommended)»: `transporte_http='curl'` y urllib como opción. Para el proxy local dejó elegir («la que sea mejor») y se mantiene urllib, sin la API privada de `curl_cffi`.
- **Causa / contexto:** validar en el relé y no en `route` cubre todo el tráfico del navegador (también lo que no pasa por la página) sin volver a la red de Node. El UA por defecto del headless shell se delata, y Chrome for Testing sacaba tráfico fuera del relé (entrada anterior). Los errores HTTP/2 (`RST_STREAM`, códigos de curl 92 y 16) no cuentan por ahora como corte para pasar al proxy: pendiente de Julio.
- **Lección:** el control de seguridad tiene que estar en el único punto por el que pasa todo el tráfico (el relé), no en una API del navegador que deja huecos.
- **Para la memoria:** §4 (diseño del navegador), §7 (tecnologías), §8 (legal y ética) · S03

### 2026-10-22 12:31 · ❌ Fallo · Test de cookies de curl mal planteado por la IA
- **Qué pasó:** en la primera ejecución de `test_curl.py` (49 en verde), falló `test_cookies_aisladas_por_tienda`: esperaba que la cookie de `tienda.example` llegara a `www.tienda.example`.
- **Causa / contexto:** una cookie sin atributo `Domain` es solo del host que la puso (RFC 6265). Compartir la sesión entre `tienda.example` y `www.tienda.example` no cambia esa regla, y curl la aplica igual que Chrome. El error era de la expectativa del test, escrita por la IA, no del código.
- **Qué se hizo:** el test pide al mismo host; sigue comprobando que otra tienda no recibe la cookie.
- **Lección:** cuando un test «falla» contra una librería madura, primero hay que comprobar si la expectativa respeta el estándar.
- **Para la memoria:** §3 (validación del código generado) · S03

### 2026-10-22 12:41 · ✅ Avance · Transporte curl con la huella TLS y HTTP/2 de Chrome, por defecto
- **Qué pasó:** queda implementada la vía curl de ADR-022: `navegador/curl.py` con `TransporteCurl` (`curl_cffi`, perfil `chrome`, una sesión por tienda con cookies solo en memoria y tope, LRU de 32 que nunca espera a una petición en vuelo). Las defensas de urllib siguen: IP validadas y fijadas con `RESOLVE` (sin DNS de curl), `NOPROXY=''` y sin proxies del entorno, solo http/https, sin redirecciones ni reintentos, tope de bytes **descomprimidos** (bomba de 200 MiB parada en el tope en menos de 2 s), plazo total que incluye DNS y espera, comprobación de `primary_ip` y errores traducidos (RST, también tras el ClientHello, como corte para `con_proxy`; tiempo agotado sin causa). Con proxy remoto, túnel `CONNECT` con el nombre de la tienda y el del proxy fijado; con proxy local, `TransporteURLLib` (decisión de Julio). Ningún error cita las credenciales, ni siquiera el de un `setopt` que mete el valor en su mensaje. `DescargadorHTTP` pasa al transporte el tiempo total y el tope exacto (`TransporteNavegador`), acepta respuestas ya descomprimidas y lo cierra. En Ajustes, «Cliente HTTP» con `curl` por defecto y `urllib` como opción.
- **Causa / contexto:** decisiones de Julio de S03 (ADR-022 y «curl por defecto»), plan corregido tras la revisión adversarial.
- **Qué se hizo:** TDD por pasos; donde el código ya existía antes del test (modo proxy), se comprobó con mutaciones que los tests fallan si se quita la defensa (`NOPROXY`, `from None`, urllib para el proxy local, `RESOLVE`).
- **Para la memoria:** §4 (diseño del navegador), §7 (tecnologías), §3 · S03

### 2026-10-22 12:41 · 🧭 Decisión · Tres ajustes al plan de curl durante la implementación
- **Qué pasó:** (1) `DescargadorHTTP` pasa **todas** sus cabeceras a `TransporteNavegador.pedir` (sin la propiedad `cabeceras_propias` del plan) y curl solo usa las de Chrome: con un proxy local, `TransporteCurl` delega en `TransporteURLLib`, que necesita el User-Agent, `Accept` y `Accept-Encoding: identity`. (2) Con proxy local, la delegación en urllib se decide dentro de `TransporteCurl`, en la primera petición y en el hilo, con la IP resuelta del proxy (nada de DNS en el bucle de eventos al crear el navegador). (3) Un `transporte_http` desconocido en la BD usa el de por defecto (`curl`), no `urllib`.
- **Causa / contexto:** el plan se escribió cuando el defecto era urllib y con `CONNECT_TO` para el proxy local; las dos cosas cambiaron con las decisiones de Julio de las 12:15.
- **Para la memoria:** §4 (diseño del navegador) · S03

### 2026-10-22 12:41 · 📏 Medida · Tests de la vía curl
- **Qué pasó:** +95 tests (de 3116 a 3211 en verde): 50 en `test_curl.py`, 21 en `test_curl_proxy.py`, 7 en `test_http_gzip.py`, 4 en `test_http.py`, 5 en `test_fuente_real_proxy.py` (2 casos nuevos del cableado y 3 tests nuevos), 6 en `test_rutas.py` y 2 en `test_ajustes.py`. `ruff`, `ruff format` y `mypy` (180 ficheros) en verde. Suite completa: 3211 en verde, 4 omitidos (`openssl` fuera del PATH en tests antiguos) y los 2 intermitentes conocidos de `test_embebidos` (cronómetro). Los ficheros de curl y HTTP, 5 veces seguidas: 209 en verde las 5, unos 28 s cada vez.
- **Para la memoria:** §3, §6 · S03

### 2026-10-22 12:46 · 🔧 Solución · Test intermitente del relé: leer el túnel hasta EOF
- **Qué pasó:** `test_rele.py::test_connect_se_reenvia_al_proxy_con_las_credenciales` fallaba 9 de cada 20 veces (medido en bucle antes de tocarlo).
- **Causa / contexto:** el helper del test leía la respuesta de la tienda con una sola `read(65536)`. Por el túnel, la cabecera y el cuerpo pueden llegar en segmentos TCP distintos, y entonces el cuerpo no estaba en lo leído. El fallo era del test (escrito por la IA en S02), no del relé.
- **Qué se hizo:** el helper lee hasta EOF (la GET lleva `Connection: close` y el relé cierra al cerrar la tienda). El fichero entero, 10 veces seguidas: 0 fallos.
- **Lección:** en un test de red, una lectura no es un mensaje: hay que leer hasta el delimitador o hasta EOF.
- **Para la memoria:** §3 (validación del código generado) · S03

### 2026-10-22 13:16 · ❌ Fallo · El cierre del relé se colgaba con Chromium real (asyncio en Windows)
- **Qué pasó:** la primera ejecución de los tests reales de Chromium con el relé SOCKS5 se quedó colgada 10 minutos sin salida.
- **Causa / contexto:** con un script de depuración por pasos se vio que la descarga terminaba bien y que lo que no volvía era `ReleProxy.cerrar()`, en `Server.wait_closed()`. Al cerrar, Chromium corta alguna conexión con RST; en Windows, asyncio 3.13 lanza `ConnectionResetError` dentro de `_ProactorBasePipeTransport._call_connection_lost` (en el `shutdown`) y se salta `server._detach`, así que el servidor cree que esa conexión sigue viva para siempre. No es determinista: unas ejecuciones se colgaban y otras no.
- **Qué se hizo:** `cerrar()` espera a las conexiones como mucho 1 s (`ESPERA_CIERRE_S`); el puerto ya queda cerrado con `close()`. Test que simula un `wait_closed` que nunca vuelve: rojo antes del cambio, verde después.
- **Lección:** «esperar a que se cierre todo» sin plazo es un cuelgue esperando a ocurrir; y los tests reales sin un `timeout` propio esconden dónde se cuelgan.
- **Para la memoria:** §3 (validación), §4 (diseño del navegador) · S03

### 2026-10-22 13:16 · 💡 Aprendizaje · Lo que dice el netlog del headless shell
- **Qué pasó:** el test que comprueba que Chromium no sale de 127.0.0.1 encontró un `UDP_CONNECT` a `[2001:4860:4860::8888]:443`. Es la sonda con la que Chromium mira si hay ruta IPv6 (`HOST_RESOLVER_MANAGER_IPV6_REACHABILITY_CHECK`): un `connect()` UDP elige la ruta y no manda ningún paquete (aquí falló con -109, sin IPv6). No hay ni un `UDP_BYTES_SENT` ni un `TCP_CONNECT` fuera del relé. Además: `Sec-Fetch-*` y los client hints solo se mandan a orígenes seguros (`localhost` sí, `http://tienda.example` no), y sin `--force-webrtc-ip-handling-policy` una página manda 2 paquetes STUN directos (con el flag, 0).
- **Qué se hizo:** el test separa los `connect()` UDP (solo se admite esa sonda) de los envíos UDP (ninguno fuera de loopback). Las mutaciones confirman que el test de WebRTC, el de client hints y los de saltos prohibidos fallan si se quita su defensa.
- **Lección:** «sin tráfico» hay que definirlo con precisión (paquetes enviados, no sockets abiertos) para que el test no dé falsos positivos ni se relaje hasta no medir nada.
- **Para la memoria:** §4 (diseño del navegador), §3 · S03

### 2026-10-22 13:29 · ✅ Avance · Chromium con su propia red detrás de un relé SOCKS5 que valida cada conexión
- **Qué pasó:** queda implementada la vía Chromium de ADR-023. `rele.py` pasa a ser un servidor **SOCKS5** local (solo `CONNECT` sin autenticación), siempre activo con Chromium, que valida **cada conexión TCP** con el mismo validador que el descargador: host estricto, puerto 80/443 comprobado por él y conexión al host normalizado; sin proxy conecta a la IP validada (contra el *DNS rebinding*) y con proxy encadena al residencial con las credenciales, que Chromium nunca ve. `chromium.py` arranca el headless shell (sin `channel`) contra el relé con `bypass <-loopback>`, sin UDP fuera del proxy (WebRTC) ni QUIC, deja que Chromium use su red (`route` solo `abort`/`continue_`, fuera `route.fetch`) y pone por CDP el UA y los client hints del propio binario sin «HeadlessChrome». `navegador.py` prueba **una vez** con Chromium tras un bloqueo o un corte por HTTP (nunca tras 429, 401 o 451; nunca para sitemaps ni robots.txt), relanza el error original si Chromium no puede y, con proxy, Chromium sale por él si la tienda ya está marcada. El 403 sale de `RECHAZOS` en el descubrimiento y Chromium deja de recibir el UA de Ajustes.
- **Causa / contexto:** decisiones de Julio de S03 (ADR-023 entero, rescate con Chromium por el proxy) y plan corregido tras la revisión adversarial.
- **Qué se hizo:** TDD por pasos y mutaciones para comprobar que los tests muerden: sin la comprobación de puerto, del host estricto o de la IP del relé, sin el flag de WebRTC (2 paquetes STUN), sin el override por CDP o con el relé sin validar, los tests fallan. Un test de netlog garantiza que Chromium no abre conexiones ni manda UDP fuera de 127.0.0.1 (la defensa contra repetir el error de Chrome for Testing).
- **Para la memoria:** §4 (diseño del navegador), §3 (validación), §7 · S03

### 2026-10-22 13:29 · 📏 Medida · Tests de la vía Chromium
- **Qué pasó:** de 3217 a 3289 tests recogidos (+72; antes, 3211 en verde, 4 omitidos y 2 intermitentes): `test_rele.py` 54 (antes 12), `test_chromium.py` 34 (antes 28; 22 sin navegador y 12 con el headless shell real), 20 nuevos en `test_navegador.py`, 2 en `test_navegador_grande.py`, 1 en `test_descubrimiento.py` (más el 403 movido de un parámetro a otro) y 1 en `test_fuente_real_proxy.py`. `ruff`, `ruff format` y `mypy` (180 ficheros) en verde. Suite completa con los tests de Chromium: 3286 en verde y los 2 intermitentes conocidos de `test_embebidos` (cronómetro). Los 12 tests reales de Chromium tardan unos 25 s; `test_chromium.py` + `test_rele.py`, 3 veces seguidas: 88 en verde las 3. El intermitente del relé: 9 fallos de 20 antes del arreglo, 0 de 10 después.
- **Para la memoria:** §3, §6 · S03

### 2026-10-22 13:53 · ❌ Fallo · El relé sin proxy podía llenar el executor por defecto de la aplicación
- **Qué pasó:** la revisión adversarial confirmó (y reprodujo contra 127.0.0.1) que el relé SOCKS5 que escribió la IA, sin proxy, abría cada conexión con `asyncio.to_thread(conexion_validada(...))`: `create_connection` bloqueante, IP a IP, con el plazo entero (30 s) para **cada** IP y sin tope de conexiones. Ese executor es el de las descargas, la caché, el almacén y la IA. Una página hostil que abra decenas de `fetch` a nombres cuyas IP descartan el SYN ocupaba los 24 hilos durante minutos, y cancelar no paraba los hilos.
- **Causa / contexto:** al hacer el relé «siempre activo» (ADR-023) se reutilizó `conexion_validada`, pensada para urllib (un hilo por descarga), dentro de un servidor que atiende conexiones que decide una web. Nadie pensó en la concurrencia que impone el atacante.
- **Para la memoria:** §3 (validación del código generado), §5 (riesgos) · S03

### 2026-10-22 13:53 · 🔧 Solución · Relé sin hilos compartidos, con plazo total y tope de conexiones
- **Qué se hizo:** sin proxy, el relé resuelve con `ips_permitidas` en un grupo de 4 hilos **propio** (con plazo) y conecta con `asyncio.open_connection` a cada IP validada contra un único límite de tiempo; un semáforo deja validar y abrir como mucho 8 conexiones a la vez y `cerrar()` apaga el grupo sin esperar. Tests nuevos: con 32 conexiones colgadas por un DNS lento, un `to_thread` ajeno pasaba de 2,64 s a menos de 0,3 s; 5 IP que no contestan dan `0x05` en menos de 1 s con un plazo de 0,4 s; nunca más de 8 intentos a la vez.
- **Lección:** un componente que atiende peticiones que decide una web es una superficie de denegación de servicio: plazos totales, topes y nada de recursos compartidos con el resto de la aplicación.
- **Para la memoria:** §3, §4 (diseño del navegador) · S03

### 2026-10-22 13:57 · ❌ Fallo · El rescate con Chromium dejaba escapar errores de Playwright y reutilizaba un Chromium muerto
- **Qué pasó:** la revisión adversarial lo reprodujo con el headless shell real: tras tumbar Chromium con `Browser.crash`, un 403 de Cloudflare por HTTP acababa en `TargetClosedError` (no en `Bloqueado`), una y otra vez, y la tienda salía como error INESPERADO. `descargar` solo traducía los fallos de `goto` y `content()`; `new_context`, la página y la sesión CDP lanzaban `playwright.Error` en bruto, `_rescatar` solo capturaba `ErrorDescarga` y `_abrir` solo relanzaba si `_navegador` era `None`.
- **Causa / contexto:** el código lo escribió la IA y el docstring prometía «quien llama ve lo mismo que sin Chromium» sin un test que lo comprobara con un error que no fuera `ErrorDescarga`. El rescate llevó a las tiendas HTTP un problema que antes solo tocaba a las de `necesita_js`.
- **Para la memoria:** §3 (validación del código generado) · S03

### 2026-10-22 13:57 · 🔧 Solución · Errores de Playwright traducidos y Chromium relanzado si murió
- **Qué se hizo:** `descargar` convierte todo `playwright.Error` no traducido en `ErrorDescarga`, el cierre del contexto no tapa el error, `_abrir` mira `is_connected()` y relanza (soltando relé y Playwright), y `_rescatar` relanza el error original ante cualquier fallo inesperado (con su traza en el registro). Tests con Playwright falso, en `Navegador` y uno real que tumba Chromium por CDP; una mutación confirma que muerden.
- **Lección:** cada promesa de un docstring necesita un test con el caso que la rompe; un componente externo (Playwright) lanza sus propias excepciones en cualquier llamada, no solo en las «peligrosas».
- **Para la memoria:** §3, §4 (diseño del navegador) · S03

### 2026-10-22 14:00 · ❌ Fallo · Tres tests de seguridad de curl que no podían fallar
- **Qué pasó:** la revisión adversarial demostró con mutantes que tres tests escritos por la IA no protegían nada: `test_no_sigue_alt_svc` mandaba Alt-Svc por `http://`, y libcurl solo lo acepta por HTTPS (pasaba con Alt-Svc activado, que por TLS lleva a curl a resolver la tienda con su propio DNS); ningún test pasaba por la comprobación de `primary_ip` (quitarla dejaba la suite en verde); y el test de conexión reutilizada acababa en `assert a + b >= 1`, cierto siempre.
- **Causa / contexto:** se escribieron los tests a la vez que el código y se dieron por buenos al verlos en verde, sin comprobar que fallaran sin la defensa. Es el mismo patrón que ya había salido en S02.
- **Para la memoria:** §3 (validación del código generado) · S03

### 2026-10-22 14:00 · 🔧 Solución · Tests de curl comprobados con mutantes
- **Qué se hizo:** Alt-Svc por HTTPS con el certificado de la tienda; dos tests nuevos que llegan a conectar y comprueban `URLNoPermitida` en la comprobación posterior (IP rechazada y `primary_ip` vacío); el de reutilización afirma qué servidor contesta. Cada uno se comprobó con su mutante: Alt-Svc activado → falla; sin la defensa de `primary_ip` → fallan los 2 nuevos. De paso, el test de reutilización baja de 5 s a milisegundos (se cerraba el transporte después del servidor keep-alive).
- **Lección:** un test de seguridad solo cuenta si se le ha visto fallar sin la defensa: para cada defensa, su mutante.
- **Para la memoria:** §3 · S03

### 2026-10-22 14:06 · ❌ Fallo · El descubrimiento pedía dos veces con Chromium las páginas de una tienda rescatada
- **Qué pasó:** la revisión adversarial lo reprodujo: tras rescatar una tienda, sus peticiones `js=False` ya van por Chromium, pero se guardaban en la caché con la clave `js=False`; el descubrimiento repite con `js=True` cada candidata que no es definitiva (200 sin listado, 403 grande, 5xx) y la portada, así que cada una se descargaba dos veces con Chromium. El docstring de `RECHAZOS` escrito por la IA decía «así que no hay dos intentos con Chromium». Además, un rescate que daba un 403 grande también marcaba la tienda.
- **Causa / contexto:** el rescate se añadió en `Navegador` sin repasar a quien llama con `js=True` después de `js=False` (el descubrimiento) y sin un test que los juntara.
- **Para la memoria:** §3 (validación del código generado), §4 · S03

### 2026-10-22 14:06 · 🔧 Solución · `Pagina.por_chromium` y una sola descarga con Chromium por URL
- **Qué se hizo:** `Pagina` dice si la descargó Chromium; el navegador guarda esas páginas como versión `js` en la caché (y la usa para `js=False` en una tienda rescatada) y solo marca la tienda si el rescate no dio un 403; el descubrimiento no repite con `js=True` lo que ya fue por Chromium. Test con el `Navegador` real y descargadores falsos: la portada y cada candidata, una sola vez con Chromium; dos mutaciones confirman que el test muerde.
- **Lección:** al añadir un comportamiento en una capa (el rescate), hay que probarlo con sus llamadores reales, no solo con dobles de la capa de arriba.
- **Para la memoria:** §3, §4 (diseño del navegador y del descubrimiento) · S03

### 2026-10-22 14:15 · 📏 Medida · Correcciones de la revisión adversarial de la rama `core/tls-http2`
- **Qué pasó:** 5 hallazgos confirmados de gravedad media corregidos (relé SOCKS5 que podía congelar la aplicación, errores de Playwright sin traducir y Chromium muerto, test de Alt-Svc inútil, defensa de `primary_ip` sin tests, doble descarga con Chromium) y 4 de gravedad baja (plazo de menos de 1 ms en curl, IP del proxy fija para siempre, textos del User-Agent y del 429). Queda pendiente el de la CI (instalar el headless shell). De 3289 a 3307 tests (+18). Suite completa: 3305 en verde y los 2 intermitentes de cronómetro de `test_embebidos` (ajenos a la rama); `test_curl*.py` + `test_rele.py`, 5 veces seguidas: 133 en verde las 5. Para los tests que cubren código ya existente, el «rojo» se comprobó con 6 mutantes (Alt-Svc activado, sin `primary_ip`, sin `is_connected`, sin los dos saltos del descubrimiento): todos los matan.
- **Para la memoria:** §3 (validación), §6 · S03

### 2026-10-22 14:16 · ❌ Fallo · Otra vez horas del diario escritas sin mirar el reloj
- **Qué pasó:** en el bloque de correcciones de la revisión, la IA escribió varias horas del diario y de la bitácora en el mismo comando que consultaba el reloj, pero **antes** de consultarlo. Dos quedaron desmentidas por el propio `date` (14:10 era 14:09; 14:16 y 14:17 eran 14:15) y otras seis pueden desviarse 1–2 minutos.
- **Causa / contexto:** por ir rápido se juntó en una sola llamada «apuntar la entrada» y «mirar la hora», en vez de mirar la hora primero. Es el mismo error que ya está tres veces en la tabla (S01).
- **Qué se hizo:** corregidas las dos horas desmentidas, avisadas las demás en el diario con la última hora real consultada antes de cada una.
- **Lección:** la hora se consulta en un paso aparte y antes de escribir; juntarlo en un comando invita a escribirla de memoria.
- **Para la memoria:** §3 (método de validación del proceso con IA) · S03

### 2026-10-22 14:22 · 📏 Medida · Prueba real de la huella de Chrome (S03), sin proxy y desde la IP de casa
- **Qué pasó:** 1 petición por vía y URL, ≥ 5 s entre peticiones a la misma tienda, sin proxy, con las clases reales (`DescargadorHTTP` con `TransporteURLLib` o `TransporteCurl`, `DescargadorChromium` como en `fuente_real`); Chromium solo si curl quedaba bloqueado. 18 peticiones de primer nivel en total.
- **Huellas (tls.peet.ws):**

| Vía | HTTP | JA4 | JA3 | Akamai (HTTP/2) | User-Agent |
|---|---|---|---|---|---|
| urllib | HTTP/1.1 | `t13d1811_85036bcba153_d41ae481755e` | `93c7d42c0df602fb91589311534831f5` | — | `PrecIA/0.1 (proyecto universitario; uso personal)` |
| curl | h2 | `t13d1516h2_8daaf6152771_806a8c22fdea` | `3f5c2b9d89a987fada29e448d03e3569` | `52d84b11737d980aef856699f885ca86` | Chrome 150, macOS (perfil de `curl_cffi`) |
| Chromium | h2 | `t13d1516h2_8daaf6152771_806a8c22fdea` | `4725c7558f3d12edd31bc96f5a9845a1` | `52d84b11737d980aef856699f885ca86` | Chrome 156, Windows, sin «HeadlessChrome» |

- **Eficacia (estado · bloqueo según `detectar_bloqueo` · tamaño · tiempo):**

| Tienda | urllib | curl | Chromium | S01/S02 |
|---|---|---|---|---|
| Oscaro (portada) | 403 · Cloudflare · 5,6 KB · 0,03 s | **200** · no · 495 KB · 0,24 s | no hizo falta | desafío de Cloudflare (incluso en `robots.txt`) |
| Ecooparts (portada) | 403 · Cloudflare · 5,6 KB · 0,09 s | **200** · no · 971 KB · 0,29 s | no hizo falta | Cloudflare; con proxy, 200 una vez y luego Cloudflare en 5 de 5 IP |
| Ovoko (portada) | 403 · Cloudflare · 5,6 KB · 0,05 s | **200** · no · 460 KB · 4,34 s | no hizo falta | Cloudflare, también con proxy |
| B-Parts (portada) | 200 · no · 416 KB · 0,37 s | 200 · no · 416 KB · 0,38 s | no hizo falta | 403 el 19; 200 por proxy el 20 |
| eBay (portada) | 403 · rechazo seco · 1,9 KB · 0,42 s | 403 · rechazo seco · 1,9 KB · 0,53 s | **200** · no · 977 KB · 3,02 s | 403 |
| B-Parts (búsqueda `/es/auto-parts/search?vehicle=opel-84-vb`) | 405 · AWS WAF · 2,1 KB · 0,02 s | 405 · AWS WAF · 2,1 KB · 0,02 s | 405 · AWS WAF («Human Verification») · 9,6 KB · 1,47 s | 405 con HTTP y con Chromium |

- **Causa / contexto:** urllib y curl se pidieron en el mismo minuto y desde la misma IP, así que la diferencia en Oscaro, Ecooparts y Ovoko se debe al cliente (huella TLS/HTTP2 y cabeceras de Chrome), no a la IP ni al día. El 200 de la portada de B-Parts con urllib indica que esa tienda ha cambiado desde el 19 por su cuenta. Solo se midieron portadas y una búsqueda: las fichas (el caso que motivó S02) no se han probado, y es una sola muestra por casilla.
- **Para la memoria:** Eval (tasa de bloqueo), §4 (diseño del navegador), §8 (legal y ética) · S03

### 2026-10-22 14:22 · ✅ Avance · Con la huella de Chrome, 4 de las 5 tiendas que bloqueaban dejan ver su portada sin proxy
- **Qué pasó:** curl abre Oscaro, Ecooparts y Ovoko (antes, desafío de Cloudflare, también por el proxy residencial) y el rescate con Chromium abre eBay (403 con urllib y con curl). B-Parts ya respondía con urllib.
- **Lección:** el bloqueo de Cloudflare en esas tres tiendas era por la huella del cliente (TLS, HTTP/2, cabeceras), no por la IP: con la misma IP de casa, urllib recibe el desafío y curl la portada. El proxy residencial no lo arreglaba porque no cambiaba la huella.
- **Para la memoria:** Eval, §4 · S03

### 2026-10-22 14:22 · ❌ Fallo · La búsqueda de B-Parts sigue bloqueada por el AWS WAF con las tres vías
- **Qué pasó:** `/es/auto-parts/search` responde 405 «Human Verification» (AWS WAF) a urllib, a curl y a Chromium con su propia red, igual que en S01. eBay rechaza también a curl (403 seco) y solo pasa con Chromium.
- **Causa / contexto:** la página es un desafío de AWS WAF («Human Verification»), que pide ejecutar su JavaScript; en esta descarga Chromium no lo superó (no se ha investigado por qué) y PrecIA no intenta resolverlo.
- **Qué se hizo:** sin resolver; se deja registrado. Además, la medida de la huella de Chromium falló a la primera porque `DescargadorChromium` rechaza las respuestas JSON (es lo esperado del producto); se midió en un guion aparte que admitía JSON solo para esa petición.
- **Para la memoria:** Eval (tasa de bloqueo), §4 · S03

### 2026-10-22 14:29 · ❌ Fallo · Textos del vault y del código que contradicen ADR-021/022/023
- **Qué pasó:** en el cierre documental de S03 aparecieron textos que seguían describiendo la regla anti-bot de antes. En el código, `orquestador.BLOQUEO` («Bloqueo (CAPTCHA o anti-bot): no se evade») y `inicio.html` («no evade protecciones»). En el vault, la nota 15 (el mismo «no se evade»), la 14 («El navegador lanza `Bloqueado` y no reintenta», falso desde el rescate con Chromium) y la 08 («tres desviaciones» con cuatro viñetas).
- **Causa / contexto:** cada ADR se aplicó a los ficheros que tocaba la tarea de ese momento, sin buscar en todo el proyecto los textos que dependían de la regla cambiada.
- **Qué se hizo:** notas 08, 14 y 15 corregidas citando las ADR, sin reescribir decisiones. El código no se ha tocado: queda como bug 14 en la nota 12, para que decida Julio.
- **Lección:** cuando una decisión cambia una regla, hay que buscar con `grep` sus palabras clave («evad», «honesto», «no reintenta») en todo el repo y en el vault, no solo en los ficheros de la tarea.
- **Para la memoria:** §3 (método de validación del proceso con IA), §4 (riesgos) · S03

### 2026-10-22 15:12 · 📏 Medida · Verificación independiente del cierre de `core/tls-http2`
- **Qué pasó:** un subagente verificador repasó el estado final de S03 sin tocar código: `ruff check`, `ruff format --check` (184 ficheros) y `mypy` (180 ficheros) en verde; `pytest` completo con 3305 en verde y 2 en rojo en 165 s, ninguno saltado (los tests de Chromium real se ejecutaron). Los dos rojos son los parámetros del test de cronómetro de `test_embebidos` (1,06 s frente a 1 s), que pasa si se lanza solo y que la rama no toca (bug 6). Rama y `HEAD` como se esperaba (`083851b`, sin commits nuevos), sin secretos ni artefactos entre los cambios, sin `curl_cffi._wrapper`, `CONNECT_TO` ni `channel=` en `src/`, y `curl_cffi==0.16.3` fijado.
- **Causa / contexto:** paso de verificación del workflow antes de pedir revisión a Julio.
- **Qué se hizo:** cifras y discrepancias menores apuntadas en el diario (15:12); no se cambió nada.
- **Para la memoria:** §3 (validación del código generado), §6 · S03

### 2026-10-22 15:12 · ❌ Fallo · El verificador contó mal los finales de línea con `grep` en Git Bash
- **Qué pasó:** para comprobar que las notas y la bitácora no mezclaban finales de línea, el verificador usó `grep -c $'\r$'`, que en Git Bash para Windows contó todas las líneas como CRLF, también en ficheros LF. Con eso, las verificaciones «LF» del diario parecían falsas.
- **Causa / contexto:** el `grep` de Git Bash no distingue el `\r` final de forma fiable en Windows. Lo destapó `git ls-files --eol`, que decía `w/lf` en un fichero que `grep` daba como CRLF.
- **Qué se hizo:** recontado por bytes con Python (`b.count(b'\r\n')`): la nota de S03 es LF y `BITACORA.md` CRLF, cada uno sin mezclas, y el diario tenía razón. No se reportó ninguna discrepancia falsa.
- **Lección:** los finales de línea se cuentan por bytes (Python o `git ls-files --eol`), no con `grep` en Windows; y una «evidencia» que contradice a varias fuentes se contrasta con otra herramienta antes de darla por buena.
- **Para la memoria:** §3 (método de validación del proceso con IA) · S03

### 2026-10-22 16:25 · 🧭 Decisión · Alcance de la tanda de eficiencia y facilidad de uso
- **Qué pasó:** tras el diagnóstico de la petición «necesito que sea un scraper bien eficiente y facil de usar» (4 auditores y una síntesis), Julio eligió las cuatro opciones recomendadas: «Paquete recomendado», «Commit S03 y rama nueva», «Sí, etiquetadas aparte» y «Usa esos valores».
- **Causa / contexto:** el diagnóstico encontró fichas abiertas sin necesidad, llamadas a la IA repetidas o inútiles, tiendas que se reconfiguraban en cada búsqueda tras fallar, OEM equivalentes descartadas y una búsqueda síncrona sin progreso ni forma de cancelarla.
- **Qué se hizo:** alcance fijado: B4 precio tachado, B5 OEM equivalentes y revisiones con sufijo, etiquetadas «equivalente» o «revisión» aparte de «exacta» (las alertas, solo exactas); A2 filtrar y ordenar antes de abrir fichas; A3 caché del extractor por sha256(texto) + campos + modelo con el TTL de la caché de páginas, y corte de la IA en una tienda si no aporta nada en su 1.ª ficha; A1 recordar configuraciones fallidas con reintento a la 1 h y luego al día, botón «Reintentar» y configuración con consulta genérica (filtro, faro, freno); C1 búsqueda en segundo plano con progreso por tienda, plazo y Cancelar (htmx con sondeo, sin SSE). Las ofertas de caché van al histórico con su fecha real de obtención; las tiendas en espera se saltan con aviso. C2 queda fuera. Antes, commit de S03 en `core/tls-http2` y rama nueva `core/eficiencia`, sin push.
- **Para la memoria:** §5 (flujo), §4 (IA integrada: coste y anclaje del extractor), §6 (hitos) · S03
