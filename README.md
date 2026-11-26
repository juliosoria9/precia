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
| `PRECIA_ALERTAS_CADA_H` | Cada cuántas horas se comprueban las alertas (mínimo 1; `0` lo desactiva) | `12` |
| `PRECIA_SMTP_SERVIDOR`, `PRECIA_SMTP_USUARIO`, `PRECIA_SMTP_CLAVE` | Servidor de correo saliente y su cuenta, para los avisos por email | Sin avisos por email |
| `PRECIA_ALERTAS_EMAIL` | Dirección a la que se envían los avisos | Sin avisos por email |
| `PRECIA_SMTP_PUERTO`, `PRECIA_SMTP_SEGURIDAD`, `PRECIA_SMTP_REMITENTE` | Puerto, `starttls` o `ssl`, y dirección del remitente | `587`, `starttls` y el usuario |
| `PRECIA_TELEGRAM_TOKEN`, `PRECIA_TELEGRAM_CHAT` | Token del bot de Telegram y número del chat (o `@canal`) | Sin avisos por Telegram |

Si están `PRECIA_PROXY` y `EVOMI_API_KEY` a la vez, manda `PRECIA_PROXY`. Las variables se leen en `src/precia/nucleo/config.py`.

Alertas: PrecIA comprueba los precios de los productos con alerta cada `PRECIA_ALERTAS_CADA_H` horas mientras está abierto (o al pulsar «Comprobar ahora» en Alertas). Repite la búsqueda en modo clásico, sin IA, y avisa una sola vez por cada bajada. El correo va siempre cifrado (STARTTLS o SSL) y el aviso es texto plano. La pantalla de Alertas dice qué canal está configurado y qué variable falta, sin enseñar ningún secreto. Para Telegram: crea un bot con @BotFather, escríbele un mensaje y usa el número de tu chat.

Ollama (IA local, opcional): instala Ollama, descarga un modelo y elige el proveedor «ollama» en Ajustes. La URL por defecto es `http://127.0.0.1:11434`. Este proveedor aún no se ha probado con un modelo real.

## Ejecución

```bash
python -m precia.web           # interfaz en http://127.0.0.1:8000 con datos reales
python -m precia.web --demo    # la misma interfaz con datos de ejemplo, sin red ni IA
```

El servidor solo escucha en `127.0.0.1`. La base de datos SQLite se crea en `datos/` y la caché de páginas en `cache/`; las dos carpetas están ignoradas por git.

## Tests y comprobaciones

```bash
pytest                                # unos 5000 tests, sin acceso a la red (2-3 minutos)
pytest -m chromium                    # solo los que abren un Chromium real contra 127.0.0.1
ruff check . ; ruff format --check .  # lint y formato
mypy                                  # tipos (modo estricto)
```

La CI (`.github/workflows/ci.yml`) ejecuta lo mismo en Ubuntu con Python 3.13. No instala Chromium, así que allí se saltan los tests marcados `chromium`.

## Banco de evaluación

```bash
python -m precia.evaluacion                          # modo clásico: gratis y sin red
python -m precia.evaluacion --modos clasico api      # además, la API de Claude (gasta de la clave)
python -m precia.evaluacion --modos local            # el Ollama del equipo
```

Resuelve los mismos casos etiquetados (`src/precia/evaluacion/casos/recambios.json`: fichas reales guardadas en `tests/fixtures/` y lo que una persona lee en ellas) en cada modo y mide cobertura, exactitud, valores inventados, coste y tiempo. Deja `datos/evaluacion.json`, que es lo que enseña la pantalla de Evaluación, y `datos/evaluacion.md`, el informe para la memoria. El modo `api` tiene su propio tope de gasto (`--presupuesto`, 0,50 $ por defecto). La línea base con Crawl4AI no está medida: `crawl4ai` sigue pendiente de aprobar.

## Despliegue en un servidor con Docker

```bash
cp .env.example .env            # y rellena las claves que vayas a usar
docker compose up -d --build    # construye la imagen (unos 1,4 GB, con Chromium) y arranca
docker compose logs -f precia   # ver el registro
```

PrecIA **no tiene usuarios ni contraseña**: `docker-compose.yml` publica el puerto solo en el `127.0.0.1` del servidor y la web solo responde a `127.0.0.1` y `localhost`. Para usarla desde tu equipo abre un túnel SSH y entra en http://127.0.0.1:8000:

```bash
ssh -L 8000:127.0.0.1:8000 usuario@servidor
```

No cambies el puerto a `0.0.0.0:8000:8000` ni la pongas detrás de un proxy público sin añadir antes autenticación. Los datos (base de datos y caché) viven en los volúmenes `precia-datos` y `precia-cache`; el contenedor corre con un usuario sin privilegios, sin *capabilities* y sin el fichero `.env` dentro de la imagen. Copia de seguridad de la base de datos: `docker compose cp precia:/app/datos ./copia-datos`. El VPS no tiene GPU: allí se usa la API de Claude, no Ollama.

## Uso de IA

Todo uso de IA en el desarrollo se documenta en [`USO-IA.md`](USO-IA.md). Los avances, fallos y decisiones del desarrollo, de cara a la memoria final, están en el cuaderno de bitácora [`BITACORA.md`](BITACORA.md).
