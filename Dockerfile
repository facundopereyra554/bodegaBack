ARG PYTHON_VERSION=3.12

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

FROM base AS deps
COPY requirements.txt ./
RUN python -m venv /venv && . /venv/bin/activate && \
    pip install --upgrade pip && \
    pip install -r requirements.txt

FROM base AS runtime

RUN useradd -u 1000 -ms /bin/bash fastapi

COPY --from=deps /venv /venv
ENV PATH="/venv/bin:$PATH"

COPY . .

RUN printf '#!/bin/bash\n\
set -e\n\
cd /app\n\
if [ ! -f "tienda.db" ]; then\n\
  echo "[ENTRYPOINT] tienda.db no existe, ejecutando seed_database.py..."\n\
  python seed_database.py\n\
else\n\
  echo "[ENTRYPOINT] tienda.db ya existe, no se hace seed."\n\
fi\n\
echo "[ENTRYPOINT] Iniciando aplicaciÃ³n..."\n\
exec "$@"\n' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

RUN chown -R fastapi:fastapi /app

ENV DATABASE_URL=sqlite:///tienda.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD curl -fsS http://127.0.0.1:8000/api/products || exit 1

USER fastapi

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "main:app", "--bind", "0.0.0.0:8000", "--workers", "3"]