"""Phase 3 tests: API key generation, validation, and rate limiting."""
import pytest
import hashlib
from api.middleware.auth import generate_api_key, hash_api_key


def test_generate_returns_three_values():
    assert len(generate_api_key("sandbox")) == 3


def test_sandbox_key_prefix():
    full_key, _, _ = generate_api_key("sandbox")
    assert full_key.startswith("sk_test_")


def test_live_key_prefix():
    full_key, _, _ = generate_api_key("starter")
    assert full_key.startswith("sk_live_")


def test_key_prefix_is_12_chars():
    _, key_prefix, _ = generate_api_key("sandbox")
    assert len(key_prefix) == 12


def test_key_hash_is_sha256():
    full_key, _, key_hash = generate_api_key("sandbox")
    expected = hashlib.sha256(full_key.encode()).hexdigest()
    assert key_hash == expected
    assert len(key_hash) == 64


def test_two_keys_are_unique():
    k1, _, _ = generate_api_key("sandbox")
    k2, _, _ = generate_api_key("sandbox")
    assert k1 != k2


def test_hash_is_deterministic():
    full_key, _, key_hash = generate_api_key("sandbox")
    assert hash_api_key(full_key) == key_hash


@pytest.fixture
async def valid_key(db_pool):
    """Insert a committed starter key — visible to app requests."""
    full_key, key_prefix, key_hash = generate_api_key("starter")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit) VALUES ($1, $2, 'Test', 'test@test.com', 'starter', 50000, 12)",
            key_prefix, key_hash,
        )
    yield full_key
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", key_hash)


@pytest.fixture
async def limited_key(db_pool):
    """Sandbox key with a limit of 3 requests."""
    full_key, key_prefix, key_hash = generate_api_key("sandbox")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit, requests_this_month) VALUES ($1, $2, 'Limited', 'ltd@test.com', 'sandbox', 3, 3, 0)",
            key_prefix, key_hash,
        )
    yield full_key
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", key_hash)


@pytest.mark.asyncio
async def test_health_no_auth(app_client):
    r = await app_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_missing_key_rejected(app_client):
    r = await app_client.get("/v1/medicines")
    assert r.status_code in (401, 422)


@pytest.mark.asyncio
async def test_invalid_key_rejected(app_client):
    r = await app_client.get("/v1/medicines", headers={"X-API-Key": "bad_key_value"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_API_KEY"


@pytest.mark.asyncio
async def test_valid_key_accepted(app_client, valid_key):
    r = await app_client.get("/v1/medicines", headers={"X-API-Key": valid_key})
    assert r.status_code != 401


@pytest.mark.asyncio
async def test_rate_limit_enforced(app_client, limited_key):
    """After 3 requests the 4th returns 429."""
    headers = {"X-API-Key": limited_key}
    for _ in range(3):
        await app_client.get("/v1/medicines", headers=headers)
    r = await app_client.get("/v1/medicines", headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_limit_headers_present(app_client, valid_key):
    r = await app_client.get("/v1/medicines", headers={"X-API-Key": valid_key})
    assert "x-ratelimit-limit" in r.headers
    assert "x-ratelimit-remaining" in r.headers
    assert "x-ratelimit-reset" in r.headers


@pytest.mark.asyncio
async def test_revoked_key_rejected(app_client, db_pool, valid_key):
    key_hash = hash_api_key(valid_key)
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE api_keys SET is_active = FALSE WHERE key_hash = $1", key_hash)
    r = await app_client.get("/v1/medicines", headers={"X-API-Key": valid_key})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_internal_ingest_requires_token(app_client):
    r = await app_client.post("/internal/ingest", json={"month": "2026-04"})
    assert r.status_code in (401, 403)
