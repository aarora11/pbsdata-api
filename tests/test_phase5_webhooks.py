"""Phase 5 tests: webhook system."""
import hmac
import hashlib
import json
import pytest
from api.middleware.auth import generate_api_key


@pytest.fixture
async def growth_key(db_pool):
    k, p, h = generate_api_key("growth")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit) VALUES ($1,$2,'G','g@t.com','growth',500000,120)",
            p, h,
        )
    yield k
    async with db_pool.acquire() as conn:
        # Clean up webhooks first
        api_key_id = await conn.fetchval("SELECT id FROM api_keys WHERE key_hash = $1", h)
        if api_key_id:
            await conn.execute("DELETE FROM webhook_delivery_log WHERE webhook_id IN (SELECT id FROM webhooks WHERE api_key_id = $1)", api_key_id)
            await conn.execute("DELETE FROM webhooks WHERE api_key_id = $1", api_key_id)
        await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", h)


@pytest.fixture
async def starter_key(db_pool):
    k, p, h = generate_api_key("starter")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit) VALUES ($1,$2,'S','s@t.com','starter',50000,12)",
            p, h,
        )
    yield k
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", h)


@pytest.mark.asyncio
async def test_create_webhook_growth(app_client, growth_key):
    r = await app_client.post(
        "/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["pbs.schedule.released"]},
        headers={"X-API-Key": growth_key},
    )
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["endpoint_url"] == "https://example.com/hook"
    assert "pbs.schedule.released" in body["event_types"]


@pytest.mark.asyncio
async def test_create_webhook_starter_rejected(app_client, starter_key):
    r = await app_client.post(
        "/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["pbs.schedule.released"]},
        headers={"X-API-Key": starter_key},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_http_url_rejected(app_client, growth_key):
    r = await app_client.post(
        "/v1/webhooks",
        json={"url": "http://example.com/hook", "events": ["pbs.schedule.released"]},
        headers={"X-API-Key": growth_key},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_event_rejected(app_client, growth_key):
    r = await app_client.post(
        "/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["not.a.real.event"]},
        headers={"X-API-Key": growth_key},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_webhooks(app_client, growth_key):
    await app_client.post(
        "/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["pbs.schedule.released"]},
        headers={"X-API-Key": growth_key},
    )
    r = await app_client.get("/v1/webhooks", headers={"X-API-Key": growth_key})
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1


@pytest.mark.asyncio
async def test_delete_webhook(app_client, growth_key):
    create = await app_client.post(
        "/v1/webhooks",
        json={"url": "https://example.com/del-me", "events": ["pbs.schedule.released"]},
        headers={"X-API-Key": growth_key},
    )
    wid = create.json()["id"]
    r = await app_client.delete(f"/v1/webhooks/{wid}", headers={"X-API-Key": growth_key})
    assert r.status_code in (200, 204)


def test_hmac_signature_correct():
    from api.services.webhook_sender import compute_signature
    secret = "test_secret"
    body = json.dumps({"event": "pbs.schedule.released"}).encode()
    sig = compute_signature(secret, body)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


@pytest.mark.asyncio
async def test_delivery_failure_increments_count(db_pool, growth_key):
    from api.services.webhook_sender import deliver_webhook
    from api.middleware.auth import hash_api_key

    key_hash = hash_api_key(growth_key)
    async with db_pool.acquire() as conn:
        api_key_id = await conn.fetchval("SELECT id FROM api_keys WHERE key_hash = $1", key_hash)
        webhook_id = await conn.fetchval(
            "INSERT INTO webhooks (api_key_id, endpoint_url, event_types, failure_count) VALUES ($1, 'https://nonexistent.invalid/hook', ARRAY['pbs.schedule.released'], 0) RETURNING id",
            api_key_id,
        )

    async with db_pool.acquire() as conn:
        webhook = {
            "id": webhook_id,
            "endpoint_url": "https://nonexistent.invalid/hook",
            "signing_secret": None,
            "secret_hash": None,
        }
        result = await deliver_webhook(webhook, "pbs.schedule.released", {"test": True}, db=conn, retry_delays=[0, 0, 0])

    assert result is False

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT failure_count FROM webhooks WHERE id = $1", webhook_id)
    assert count >= 1
