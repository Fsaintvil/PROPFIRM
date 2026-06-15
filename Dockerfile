# NOTE: MetaTrader5 ne fonctionne que sous Windows.
# Ce Dockerfile est fourni pour la structure et les dépendances logicielles,
# mais le robot de trading ne peut PAS être exécuté dans ce conteneur Linux.
# Utilisez ce Docker uniquement pour les étapes offline (calibration, retraining).
# Multi-stage Docker build for MT5 FTMO Robot
# Stage 1: Base — Python 3.10 slim
FROM python:3.10-slim AS base

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt \
    && pip install mlflow

# Stage 2: Runtime — lean
FROM python:3.10-slim AS runtime

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=base /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=base /usr/local/bin /usr/local/bin

COPY . .

RUN mkdir -p runtime models logs config

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:9090/health', timeout=5)" || exit 1

ENTRYPOINT ["python", "main.py"]
