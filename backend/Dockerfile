FROM python:3.12-slim AS base

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    curl \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Install Reconlify CLI
# RUN curl -sSL https://install.reconlify.io | bash
# Uncomment ↑ once you have a valid Reconlify distribution URL.

WORKDIR /app

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e "."

COPY . .

# ── API image ──────────────────────────────────────────────────────────────────
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--loop", "uvloop"]

# ── Worker image ──────────────────────────────────────────────────────────────
FROM base AS worker
CMD ["python", "-m", "arq", "app.workers.worker_settings.WorkerSettings"]
