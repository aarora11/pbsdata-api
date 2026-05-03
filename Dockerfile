# ── Build stage: install dependencies into a venv ────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Test stage: includes dev deps (pytest, httpx, etc.) ───────────────────────
FROM python:3.12-slim AS tester

COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY api/ ./api/
COPY ingest/ ./ingest/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── Runtime stage: no uv, no build tools ─────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY api/ ./api/
COPY ingest/ ./ingest/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

RUN chmod +x scripts/start.sh

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Override via env: WEB_CONCURRENCY=4
ENV WEB_CONCURRENCY=2

EXPOSE 8000

CMD ["scripts/start.sh"]
