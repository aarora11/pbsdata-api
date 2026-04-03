# PBSdata.io API

A commercial REST API serving Australian PBS (Pharmaceutical Benefits Scheme) drug schedule data. Built with FastAPI, asyncpg, and PostgreSQL. Designed for tiered API access with per-key rate limiting, webhook notifications, and monthly schedule ingestion from the Australian Government PBS API.

## What this is

The Australian Government publishes the PBS schedule monthly — a list of all subsidised medicines, their PBS codes, prices, benefit types, and prescribing restrictions. This project ingests that data and exposes it via a clean, authenticated REST API with:

- Monthly schedule history with diff/change tracking
- Full-text search by ingredient name or brand name
- Per-PBS-code item lookup with restriction text
- Tiered access plans (sandbox, starter, growth, enterprise)
- Per-key rate limiting with `X-RateLimit-*` headers
- Webhook subscriptions for schedule change events (growth+ plans)
- HMAC-signed webhook delivery with retry logic

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- PostgreSQL 16+ running locally with the `pg_trgm` extension available

## Setup

```bash
# 1. Clone the repository
git clone <repo-url>
cd pbsdata-api

# 2. Copy the example env file and fill in your values
cp .env.example .env
# Edit .env — at minimum set PBS_API_SUBSCRIPTION_KEY, APP_SECRET_KEY,
# INTERNAL_INGEST_TOKEN, and WEBHOOK_SIGNING_SECRET_SALT

# 3. Create the database and user in PostgreSQL
psql postgres -c "CREATE USER pbsapi WITH PASSWORD 'pbsapi';"
psql postgres -c "CREATE DATABASE pbsapi OWNER pbsapi;"
psql pbsapi -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

# 4. Run migrations (creates all tables and indexes)
uv run python scripts/migrate.py

# 5. Create your first API key
uv run python scripts/create_api_key.py "Dev Key" "dev@example.com" sandbox

# 6. Start the development server
uv run uvicorn api.main:app --reload
```

API docs are available at http://localhost:8000/docs

## Running tests

```bash
uv run pytest tests/ -v
```

To run with coverage:

```bash
uv run pytest tests/ -v --cov=api --cov=ingest --cov-report=term-missing
```

67 tests cover all five phases: database schema, ingest pipeline, auth and rate limiting, API endpoints, and the webhook system.

## Triggering a manual ingest

The ingest endpoint is internal and requires a bearer token matching `INTERNAL_INGEST_TOKEN` from your `.env`.

```bash
curl -X POST http://localhost:8000/internal/ingest \
  -H "Authorization: Bearer <INTERNAL_INGEST_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"month": "2026-04"}'
```

In production, this is called by a scheduled job (e.g. cron or Fly.io scheduled machines) shortly after the PBS schedule is published each month.

## Quick-start curl example

```bash
# Replace with your API key from the create_api_key script
API_KEY="pbslive_xxxxxxxxxxxx..."

# List the most recent schedule
curl -H "X-API-Key: $API_KEY" https://api.pbsdata.io/v1/schedules

# Search for medicines by ingredient
curl -H "X-API-Key: $API_KEY" "https://api.pbsdata.io/v1/medicines?q=metformin"

# Get a specific PBS item by code
curl -H "X-API-Key: $API_KEY" https://api.pbsdata.io/v1/items/02647H

# Get changes since a date
curl -H "X-API-Key: $API_KEY" "https://api.pbsdata.io/v1/changes?since=2026-01-01"
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://pbsapi:pbsapi@localhost:5432/pbsapi` | PostgreSQL connection string |
| `DATABASE_POOL_MIN` | `2` | Minimum asyncpg connection pool size |
| `DATABASE_POOL_MAX` | `10` | Maximum asyncpg connection pool size |
| `PBS_API_BASE_URL` | `https://api.pbs.gov.au/api/v3` | Australian Government PBS API base URL |
| `PBS_API_SUBSCRIPTION_KEY` | — | Your PBS API subscription key (required for ingest) |
| `PBS_API_EMBARGO_KEY` | `""` | Embargo key for early access to next month's schedule |
| `PBS_REQUEST_DELAY_SECONDS` | `21.0` | Delay between PBS API requests (rate limit compliance) |
| `APP_ENV` | `development` | Environment name (`development`, `production`) |
| `APP_SECRET_KEY` | — | Secret key for internal signing (required) |
| `INTERNAL_INGEST_TOKEN` | — | Bearer token to authenticate the `/internal/ingest` endpoint |
| `STRIPE_SECRET_KEY` | `""` | Stripe secret key for billing (optional) |
| `STRIPE_WEBHOOK_SECRET` | `""` | Stripe webhook signing secret (optional) |
| `WEBHOOK_SIGNING_SECRET_SALT` | — | Salt used to derive per-webhook HMAC signing secrets |
| `CACHE_TTL_SCHEDULE_SECONDS` | `86400` | Cache TTL for schedule list responses (seconds) |
| `CACHE_TTL_META_SECONDS` | `300` | Cache TTL for metadata responses (seconds) |

## API key tiers

| Plan | Rate limit | Webhook support | History access |
|---|---|---|---|
| `sandbox` | 60 req/min | No | 1 schedule only |
| `starter` | 120 req/min | No | Full |
| `growth` | 600 req/min | Yes | Full |
| `enterprise` | Custom | Yes | Full |

## Tech stack

- **Python 3.12** — runtime
- **FastAPI 0.115.6** — web framework
- **asyncpg 0.30.0** — async PostgreSQL driver
- **PostgreSQL 16** — primary datastore with `pg_trgm` for fuzzy search
- **pydantic-settings 2.7.0** — settings management
- **structlog** — structured JSON logging
- **httpx** — async HTTP client for PBS API and webhook delivery
- **stripe** — billing integration
- **uv** — dependency and environment management

## Project structure

```
api/
  config.py          — pydantic-settings configuration
  database.py        — asyncpg pool management
  main.py            — FastAPI app, lifespan, internal endpoints
  middleware/
    auth.py          — API key extraction and validation
    rate_limit.py    — per-key sliding window rate limiter
  models/            — Pydantic response models
  routers/
    medicines.py     — GET /v1/medicines, /v1/medicines/{id}
    items.py         — GET /v1/items/{pbs_code}
    changes.py       — GET /v1/changes
    schedules.py     — GET /v1/schedules
    webhooks.py      — POST/GET/DELETE /v1/webhooks
  services/
    search.py        — trigram search helpers
    webhook_sender.py — async webhook delivery with HMAC signing
    cache.py         — aiocache wrappers
ingest/
  pbs_client.py      — PBS Government API client
  normaliser.py      — raw XML/JSON → domain model
  differ.py          — schedule-to-schedule change detection
  loader.py          — upsert normalised data into PostgreSQL
  runner.py          — orchestrates full ingest pipeline
migrations/          — SQL migration files
scripts/
  migrate.py         — run all migrations
  create_api_key.py  — create API keys from the command line
tests/               — pytest test suite (67 tests)
```
