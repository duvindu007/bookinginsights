# --- Build stage: install dependencies into a venv-like layer -------------
FROM python:3.12-slim AS builder

WORKDIR /app

# psycopg2-binary needs libpq at build time on some slim images; installing
# build-essential keeps this resilient across base image changes.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# --- Runtime stage: copy only what's needed to run -------------------------
FROM python:3.12-slim

WORKDIR /app

# Run as a non-root user (container security best practice).
RUN useradd --create-home --shell /bin/bash appuser

COPY --from=builder /install /usr/local
COPY . .

# These are the default locations logging_config.py / error_logger.py /
# database.py write to; created here so the non-root user can write to them
# even before an external volume is mounted over one of them.
RUN mkdir -p logs upload_logs && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Basic liveness check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
