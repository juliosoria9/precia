# PrecIA

Extractor comparativo de datos de producto con IA adaptativa. Proyecto universitario.

Dado un producto o una referencia de fabricante y una lista de tiendas (o descubriéndolas con Google), PrecIA extrae de cada web los campos que pide el usuario (precio, referencia OEM, marca, estado...) y los compara, con histórico y alertas. La IA genera y repara la configuración de scraping de las tiendas nuevas; después esa configuración se reutiliza sin IA.

La documentación del desarrollo está en el vault de Obsidian `../precia-vault/` (empieza por `00 - ÍNDICE.md`) y la memoria del proyecto en `../idea-tarea-1/`.

## Desarrollo

Requiere Python 3.13.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m precia.web                  # interfaz web en http://127.0.0.1:8000 (datos de ejemplo)
pytest                                # tests
ruff check . ; ruff format --check .  # lint y formato
mypy                                  # tipos
```

## Uso de IA

Todo uso de IA en el desarrollo se documenta en [`USO-IA.md`](USO-IA.md). Los avances, fallos y decisiones del desarrollo, de cara a la memoria final, están en el cuaderno de bitácora [`BITACORA.md`](BITACORA.md).
