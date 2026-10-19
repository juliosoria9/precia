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
