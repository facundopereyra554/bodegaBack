# syntax=docker/dockerfile:1.6
ARG PYTHON_VERSION=3.12

# --- ETAPA 1: Base ---
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# Instalamos curl para el healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*

# --- ETAPA 2: Dependencias ---
FROM base AS deps
COPY requirements.txt ./
RUN python -m venv /venv && . /venv/bin/activate && \
    pip install --upgrade pip && \
    # IMPORTANTE: Asegúrate de que gunicorn esté en tu requirements.txt
    pip install -r requirements.txt

# --- ETAPA 3: Runtime (Final) ---
FROM base AS runtime

# Creamos usuario no-root por seguridad
RUN useradd -u 1000 -ms /bin/bash fastapi

# Copiamos el entorno virtual de la etapa anterior
COPY --from=deps /venv /venv
ENV PATH="/venv/bin:$PATH"

# Copiamos el código
COPY . .

# Permisos: Damos control de la carpeta al usuario fastapi
# Esto es CRÍTICO para que SQLite pueda escribir en tienda.db
RUN chown -R fastapi:fastapi /app

# Variable de entorno para la DB
ENV DATABASE_URL=sqlite:///tienda.db

EXPOSE 8000

# CORRECCIÓN HEALTHCHECK:
# Usamos /api/products porque sabemos que existe. Si falla, la app está caída.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -fsS http://127.0.0.1:8000/api/products || exit 1

USER fastapi

# Ejecutamos Gunicorn
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "main:app", "--bind", "0.0.0.0:8000", "--workers", "3"]