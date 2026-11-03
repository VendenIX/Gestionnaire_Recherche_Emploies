# syntax=docker/dockerfile:1
# Image unique du suivi d'offres : backend FastAPI + SQLite, front React servi en statique.
# Build : docker buildx build --platform linux/amd64 -t ghcr.io/<utilisateur>/jobs-tracker:latest .

# --- Étape 0 : front React (Vite) compilé en fichiers statiques -----------------
FROM --platform=linux/amd64 node:22-slim AS frontend

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Étape 1 : dépendances Python dans un venv --------------------------------
FROM --platform=linux/amd64 python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml ./
# On installe seulement les dépendances déclarées dans pyproject.toml (pas le paquet) :
# le code est copié tel quel dans l'étape finale, avec data/ à côté de backend/ (D-001).
RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); \
print('\n'.join(d['project']['dependencies']))" > requirements.txt \
    && pip install -r requirements.txt

# --- Étape 2 : image finale, utilisateur non root 568:568 ----------------------
FROM --platform=linux/amd64 python:3.12-slim AS final

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

RUN groupadd --system --gid 568 apps \
    && useradd --system --uid 568 --gid 568 --no-create-home --home-dir /app --shell /usr/sbin/nologin apps \
    && mkdir -p /data \
    && chown 568:568 /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Disposition imposée : /app/backend et /app/data (référentiel des départements).
COPY backend/app ./backend/app
COPY data ./data
# Front compilé, servi sur / par le backend (StaticFiles html=True ; les routes /api restent prioritaires).
COPY --from=frontend /src/dist ./backend/app/static

USER 568:568
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "backend.app.main:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
