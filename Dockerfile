FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev deps, no editable install)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY api/ ./api/
COPY ingest/ ./ingest/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
