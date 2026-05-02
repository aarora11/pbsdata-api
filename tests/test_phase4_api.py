"""Phase 4 tests: all API endpoints."""
import json
import pytest
from pathlib import Path
from api.middleware.auth import generate_api_key
from ingest.normaliser import normalise_schedule
from ingest.loader import load_to_database

FIXTURES = Path(__file__).parent / "fixtures"


async def seed(db):
    items = json.loads((FIXTURES / "pbs_sample_items.json").read_text())
    restrictions = json.loads((FIXTURES / "pbs_sample_restrictions.json").read_text())
    normalised = normalise_schedule("2026-04", items, restrictions)
    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2026-04', NOW(), 'complete')"
    )
    class P:
        def acquire(self): return A(db)
    class A:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass
    await load_to_database(P(), "2026-04", normalised, [])


@pytest.fixture
async def headers(db_pool):
    k, p, h = generate_api_key("growth")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit) VALUES ($1,$2,'T','t@t.com','growth',500000,120)",
            p, h,
        )
    yield {"X-API-Key": k}
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", h)


@pytest.fixture
async def client_with_data(app_client, db_pool):
    """Seed data directly (committed) and return the test client."""
    # Insert schedule and items via committed connection
    async with db_pool.acquire() as conn:
        items = json.loads((FIXTURES / "pbs_sample_items.json").read_text())
        restrictions = json.loads((FIXTURES / "pbs_sample_restrictions.json").read_text())
        normalised = normalise_schedule("2026-04", items, restrictions)
        await conn.execute(
            "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2026-04', NOW(), 'complete') ON CONFLICT (month) DO NOTHING"
        )
        class P:
            def acquire(self): return A(conn)
        class A:
            def __init__(self, c): self._c = c
            async def __aenter__(self): return self._c
            async def __aexit__(self, *a): pass
        await load_to_database(P(), "2026-04", normalised, [])
    yield app_client
    # Cleanup
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM item_restriction_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2026-04')")
        await conn.execute("DELETE FROM restrictions WHERE item_id IN (SELECT id FROM items WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2026-04'))")
        await conn.execute("DELETE FROM items WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2026-04')")
        await conn.execute("DELETE FROM medicines")
        await conn.execute("DELETE FROM schedules WHERE month = '2026-04'")


# Schedules
@pytest.mark.asyncio
async def test_schedules_200(client_with_data, headers):
    r = await client_with_data.get("/v1/schedules", headers=headers)
    assert r.status_code == 200
    assert "data" in r.json()


@pytest.mark.asyncio
async def test_schedules_fields(client_with_data, headers):
    r = await client_with_data.get("/v1/schedules", headers=headers)
    data = r.json()["data"]
    assert len(data) >= 1
    s = data[0]
    for f in ["month", "released_at", "is_embargo", "item_count", "change_count"]:
        assert f in s, f"Missing field '{f}' from schedules response"


@pytest.mark.asyncio
async def test_schedules_most_recent_first(client_with_data, headers):
    r = await client_with_data.get("/v1/schedules", headers=headers)
    months = [s["month"] for s in r.json()["data"]]
    assert months == sorted(months, reverse=True)


# Medicines list
@pytest.mark.asyncio
async def test_medicines_200(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines", headers=headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_medicines_structure(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines", headers=headers)
    body = r.json()
    assert "data" in body
    assert "meta" in body
    for f in ["total", "page", "limit"]:
        assert f in body["meta"]


@pytest.mark.asyncio
async def test_medicines_fields(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines", headers=headers)
    assert r.json()["meta"]["total"] >= 1
    for m in r.json()["data"]:
        for f in ["id", "ingredient", "item_count", "sixty_day_eligible", "has_generic"]:
            assert f in m, f"Missing field '{f}'"


@pytest.mark.asyncio
async def test_search_by_ingredient(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines?q=metformin", headers=headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] >= 1
    ingredients = [m["ingredient"].lower() for m in r.json()["data"]]
    assert any("metformin" in i for i in ingredients)


@pytest.mark.asyncio
async def test_search_by_brand_name(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines?q=glucophage", headers=headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] >= 1


@pytest.mark.asyncio
async def test_filter_sixty_day(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines?sixty_day=true", headers=headers)
    assert r.status_code == 200
    for m in r.json()["data"]:
        assert m["sixty_day_eligible"] is True


@pytest.mark.asyncio
async def test_pagination_no_overlap(client_with_data, headers):
    p1 = await client_with_data.get("/v1/medicines?limit=2&page=1", headers=headers)
    p2 = await client_with_data.get("/v1/medicines?limit=2&page=2", headers=headers)
    ids1 = {m["id"] for m in p1.json()["data"]}
    ids2 = {m["id"] for m in p2.json()["data"]}
    assert not ids1 & ids2


# Medicine detail
@pytest.mark.asyncio
async def test_medicine_detail_200(client_with_data, headers):
    list_r = await client_with_data.get("/v1/medicines", headers=headers)
    med_id = list_r.json()["data"][0]["id"]
    r = await client_with_data.get(f"/v1/medicines/{med_id}", headers=headers)
    assert r.status_code == 200
    assert "items" in r.json()
    assert len(r.json()["items"]) >= 1


@pytest.mark.asyncio
async def test_medicine_detail_404(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines/00000000-0000-0000-0000-000000000000", headers=headers)
    assert r.status_code == 404


# Items
@pytest.mark.asyncio
async def test_item_by_pbs_code(client_with_data, headers):
    r = await client_with_data.get("/v1/items/2622M", headers=headers)
    assert r.status_code == 200
    body = r.json()
    # Growth (T2) key returns enriched envelope — data fields are under "data"
    data = body["data"]
    assert data["pbs_code"] == "2622M"
    assert "restrictions" in data
    assert data["brand_premium_counts_to_safety_net"] is False


@pytest.mark.asyncio
async def test_item_404(client_with_data, headers):
    r = await client_with_data.get("/v1/items/XXXXX", headers=headers)
    assert r.status_code == 404


# Changes
@pytest.mark.asyncio
async def test_changes_requires_since(client_with_data, headers):
    r = await client_with_data.get("/v1/changes", headers=headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_changes_200(client_with_data, headers):
    r = await client_with_data.get("/v1/changes?since=2026-01", headers=headers)
    assert r.status_code == 200
    assert "data" in r.json()


# Sandbox history limit
@pytest.mark.asyncio
async def test_sandbox_history_limit(app_client, db_pool):
    k, p, h = generate_api_key("sandbox")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit) VALUES ($1,$2,'S','s@t.com','sandbox',500,3)",
            p, h,
        )
    try:
        r = await app_client.get("/v1/items/2622M?schedule=2020-01", headers={"X-API-Key": k})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "HISTORY_LIMIT_EXCEEDED"
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", h)


# Rate limit headers
@pytest.mark.asyncio
async def test_rate_limit_headers(client_with_data, headers):
    r = await client_with_data.get("/v1/medicines", headers=headers)
    assert "x-ratelimit-limit" in r.headers
    assert "x-ratelimit-remaining" in r.headers
    assert "x-ratelimit-reset" in r.headers
