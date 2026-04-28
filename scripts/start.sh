#!/bin/bash
# Run on every deploy: migrate first, then start the server.
# Safe to run repeatedly — migrations are idempotent.
set -e

echo "Running migrations..."
python scripts/migrate.py

echo "Starting server..."
exec gunicorn -w "${WEB_CONCURRENCY:-2}" \
    -k uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:${PORT:-8000}" \
    --access-logfile - \
    --error-logfile - \
    api.main:app
