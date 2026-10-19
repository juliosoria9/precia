# Uso de IA en el desarrollo de PrecIA

El enunciado exige documentar en cada entrega todo uso de IA. Este fichero se genera a partir del **diario de sesiones** del vault (`../precia-vault/10 - Diario de Sesiones.md`): cada entrada del diario registra qué generó la IA, con qué petición y qué se decidió.

**Herramienta principal:** Claude Code (agente en terminal) sobre este repositorio.
**Flujo:** definición de la tarea con criterio de aceptación → generación (TDD: primero el test) → revisión humana → `ruff` + `mypy` + `pytest` en local y en GitHub Actions → commit marcado con `Co-Authored-By`.

---

## Entrega en curso (desde 2026-10-19)

**Sesiones cubiertas:** S01 (2026-10-19).

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
