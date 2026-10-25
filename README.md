# PrecIA

Extractor comparativo de datos de producto con IA adaptativa. Proyecto universitario.

Dado un producto o una referencia de fabricante y una lista de tiendas (o descubriéndolas con Google), PrecIA extrae de cada web los campos que pide el usuario (precio, referencia OEM, marca, estado...) y los compara, con histórico y alertas. La IA genera y repara la configuración de scraping de las tiendas nuevas; después esa configuración se reutiliza sin IA.

La documentación del desarrollo está en el vault de Obsidian `../precia-vault/` (empieza por `00 - ÍNDICE.md`) y la memoria del proyecto en `../idea-tarea-1/`.

## Requisitos

- Python 3.13.
- Git.
- Opcional: el Chromium de Playwright (*headless shell*). Sin él la aplicación funciona por HTTP (`curl_cffi`), pero no puede rescatar con Chromium las tiendas que bloquean ni las que solo pintan con JavaScript, y se saltan los tests marcados `chromium`.
- Opcional: `openssl` en el PATH (en Windows vale el que trae Git for Windows). Sin él se saltan 4 tests que generan un certificado de prueba.
- Opcional: una clave de la API de Anthropic para los modos IA y Auto. Sin clave se puede usar el modo clásico o la demostración.

## Instalación

Windows (PowerShell):

```powershell
git clone https://github.com/juliosoria9/precia.git
cd precia
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m playwright install --only-shell chromium
```

Linux o macOS:

```bash
git clone https://github.com/juliosoria9/precia.git
cd precia
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install --only-shell chromium
# En Linux, si faltan librerías del sistema para Chromium:
# python -m playwright install --with-deps --only-shell chromium
```

Solo se usa el *headless shell* de Chromium: Chrome for Testing está descartado porque resuelve DNS por su cuenta y se salta el relé que valida cada conexión (ADR-023).

## Configuración

La configuración secreta va en variables de entorno o en un fichero `.env` en la raíz del repositorio, que está en `.gitignore` y nunca se sube. Copia la plantilla y rellena solo lo que necesites:

```bash
cp .env.example .env
```

| Variable | Para qué | Si no está |
|---|---|---|
| `ANTHROPIC_API_KEY` | Clave de la API de Claude para los modos IA y Auto | Solo funciona el modo clásico (o Ollama, si se elige en Ajustes) |
| `PRECIA_PROXY` | Proxy propio, como URL `http://usuario:contraseña@servidor:puerto` | Sin proxy |
| `EVOMI_API_KEY` | Clave de Evomi: las credenciales del proxy residencial se piden a su API | Sin proxy |
| `EVOMI_PRODUCTO` | Producto de Evomi | `rpc` |
| `PRECIA_PROXY_PAIS` | País de salida del proxy | `ES` |
| `PRECIA_PROXY_MODO` | `bloqueo` (directo y 1 reintento por el proxy si la tienda bloquea), `siempre` o `nunca` | `bloqueo` |
| `PRECIA_DEMO` | `1` arranca con datos de ejemplo, igual que `--demo` | Datos reales |

Si están `PRECIA_PROXY` y `EVOMI_API_KEY` a la vez, manda `PRECIA_PROXY`. Las variables se leen en `src/precia/nucleo/config.py`.

Ollama (IA local, opcional): instala Ollama, descarga un modelo y elige el proveedor «ollama» en Ajustes. La URL por defecto es `http://127.0.0.1:11434`. Este proveedor aún no se ha probado con un modelo real.

## Ejecución

```bash
python -m precia.web           # interfaz en http://127.0.0.1:8000 con datos reales
python -m precia.web --demo    # la misma interfaz con datos de ejemplo, sin red ni IA
```

El servidor solo escucha en `127.0.0.1`. La base de datos SQLite se crea en `datos/` y la caché de páginas en `cache/`; las dos carpetas están ignoradas por git.

## Tests y comprobaciones

```bash
pytest                                # unos 4750 tests, sin acceso a la red (2-3 minutos)
pytest -m chromium                    # solo los que abren un Chromium real contra 127.0.0.1
ruff check . ; ruff format --check .  # lint y formato
mypy                                  # tipos (modo estricto)
```

La CI (`.github/workflows/ci.yml`) ejecuta lo mismo en Ubuntu con Python 3.13. No instala Chromium, así que allí se saltan los tests marcados `chromium`.

## Uso de IA

Todo uso de IA en el desarrollo se documenta en [`USO-IA.md`](USO-IA.md). Los avances, fallos y decisiones del desarrollo, de cara a la memoria final, están en el cuaderno de bitácora [`BITACORA.md`](BITACORA.md).
