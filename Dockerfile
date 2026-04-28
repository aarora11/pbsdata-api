# ── Build stage: install dependencies into a venv ────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY api/ ./api/
COPY ingest/ ./ingest/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

RUN uv sync --frozen --no-dev

# ── Runtime stage: no uv, no build tools ─────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/api ./api
COPY --from=builder /app/ingest ./ingest
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/scripts ./scripts

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Override via env: WEB_CONCURRENCY=4
ENV WEB_CONCURRENCY=2

EXPOSE 8000

CMD gunicorn -w ${WEB_CONCURRENCY} -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --access-logfile - \
    --error-logfile - \
    api.main:app
