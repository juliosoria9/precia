# PrecIA en un contenedor (H9). La web no tiene usuarios ni contraseña: el puerto se publica
# solo en el 127.0.0.1 del servidor (docker-compose.yml) y se entra por un túnel SSH.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Instalación editable: la aplicación busca `datos/`, `cache/` y `.env` en la raíz del código.
COPY pyproject.toml README.md ./
COPY src ./src
# `libatomic1`: lo necesita el Node que trae Playwright y la imagen «slim» no lo incluye.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && pip install -e . \
    && python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

# Sin privilegios de root. Los secretos no se copian a la imagen: llegan por `env_file`.
RUN useradd --create-home --uid 10001 precia \
    && mkdir -p /app/datos /app/cache \
    && chown -R precia:precia /app/datos /app/cache
USER precia

# Dentro del contenedor hay que escuchar en todas sus interfaces para que el puerto publicado
# llegue; fuera, solo se publica en 127.0.0.1.
ENV PRECIA_HOST=0.0.0.0
EXPOSE 8000
VOLUME ["/app/datos", "/app/cache"]

HEALTHCHECK --interval=1m --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/estaticos/icono.svg', timeout=5)"]

CMD ["python", "-m", "precia.web"]
