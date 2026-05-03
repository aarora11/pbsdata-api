"""Phase 8 tests: reference data API endpoints — organisations, programs, atc-codes, copayments."""
import json
import pytest
from pathlib import Path
from api.middleware.auth import generate_api_key
from ingest.normaliser import normalise_schedule
from ingest.loader import load_to_database

FIXTURES = Path(__file__).parent / "fixtures"


def load(filename: str):
    return json.loads((FIXTURES / filename).read_text())


async def seed_reference(db):
    """Seed all reference data for integration tests."""
    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2099-03', NOW(), 'complete') ON CONFLICT (month) DO UPDATE SET ingest_status = 'complete'"
    )
    normalised = normalise_schedule(
        "2099-03",
        load("pbs_sample_items.json"),
        load("pbs_sample_restrictions.json"),
        raw_organisations=load("pbs_sample_organisations.json"),
        raw_programs=load("pbs_sample_programs.json"),
        raw_atc_codes=load("pbs_sample_atc_codes.json"),
        raw_copayments=load("pbs_sample_copayments.json"),
        raw_item_atc_relationships=load("pbs_sample_item_atc_relationships.json"),
        raw_item_organisation_relationships=load("pbs_sample_item_organisation_relationships.json"),
    )

    class P:
        def acquire(self): return A(db)
    class A:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    await load_to_database(P(), "2099-03", normalised, [])


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
async def client_ref(app_client, db_pool):
    async with db_pool.acquire() as conn:
        await seed_reference(conn)
    yield app_client
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM item_organisation_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM item_atc_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM copayments WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM atc_codes WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM programs WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM organisations WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM item_restriction_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM restrictions WHERE item_id IN (SELECT id FROM items WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03'))")
        await conn.execute("DELETE FROM items WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-03')")
        await conn.execute("DELETE FROM schedules WHERE month = '2099-03'")


# ── Normaliser: reference data output ─────────────────────────────────────────

def test_normaliser_produces_organisations():
    result = normalise_schedule(
        "2099-03",
        load("pbs_sample_items.json"),
        load("pbs_sample_restrictions.json"),
        raw_organisations=load("pbs_sample_organisations.json"),
    )
    assert len(result["organisations"]) == 3
    org = next(o for o in result["organisations"] if o["organisation_id"] == 1001)
    assert org["name"] == "Merck Serono Pty Ltd"
    assert org["state"] == "NSW"


def test_normaliser_produces_programs():
    result = normalise_schedule(
        "2099-03",
        load("pbs_sample_items.json"),
        load("pbs_sample_restrictions.json"),
        raw_programs=load("pbs_sample_programs.json"),
    )
    assert len(result["programs"]) == 3
    ge = next(p for p in result["programs"] if p["program_code"] == "GE")
    assert ge["program_title"] == "General Schedule"


def test_normaliser_produces_atc_codes():
    result = normalise_schedule(
        "2099-03",
        load("pbs_sample_items.json"),
        load("pbs_sample_restrictions.json"),
        raw_atc_codes=load("pbs_sample_atc_codes.json"),
    )
    assert len(result["atc_codes"]) == 5
    leaf = next(a for a in result["atc_codes"] if a["atc_code"] == "A10BA02")
    assert leaf["atc_description"] == "Metformin"
    assert leaf["atc_level"] == 5
    assert leaf["atc_parent_code"] == "A10BA"


def test_normaliser_produces_copayments():
    result = normalise_schedule(
        "2099-03",
        load("pbs_sample_items.json"),
        load("pbs_sample_restrictions.json"),
        raw_copayments=load("pbs_sample_copayments.json"),
    )
    cp = result["copayments"]
    assert cp is not None
    from decimal import Decimal
    assert cp["general"] == Decimal("31.60")
    assert cp["concessional"] == Decimal("7.70")


def test_normaliser_produces_item_atc_relationships():
    result = normalise_schedule(
        "2099-03",
        load("pbs_sample_items.json"),
        load("pbs_sample_restrictions.json"),
        raw_item_atc_relationships=load("pbs_sample_item_atc_relationships.json"),
    )
    assert len(result["item_atc_relationships"]) == 3
    rel = next(r for r in result["item_atc_relationships"] if r["pbs_code"] == "2622M")
    assert rel["atc_code"] == "A10BA02"


# ── Organisations API ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_organisations_200(client_ref, headers):
    r = await client_ref.get("/v1/organisations", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert body["meta"]["total"] == 3


@pytest.mark.asyncio
async def test_organisations_fields(client_ref, headers):
    r = await client_ref.get("/v1/organisations", headers=headers)
    org = r.json()["data"][0]
    for field in ["organisation_id", "name", "abn", "street_address", "city", "state", "postcode"]:
        assert field in org


@pytest.mark.asyncio
async def test_organisations_filter_by_state(client_ref, headers):
    r = await client_ref.get("/v1/organisations?state=NSW", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["name"] == "Merck Serono Pty Ltd"


@pytest.mark.asyncio
async def test_organisation_by_id_200(client_ref, headers):
    r = await client_ref.get("/v1/organisations/1001", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["organisation_id"] == 1001
    assert body["name"] == "Merck Serono Pty Ltd"
    assert "linked_items" in body
    assert "2622M" in body["linked_items"]


@pytest.mark.asyncio
async def test_organisation_by_id_404(client_ref, headers):
    r = await client_ref.get("/v1/organisations/9999", headers=headers)
    assert r.status_code == 404


# ── Programs API ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_programs_200(client_ref, headers):
    r = await client_ref.get("/v1/programs", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] == 3


@pytest.mark.asyncio
async def test_programs_fields(client_ref, headers):
    r = await client_ref.get("/v1/programs", headers=headers)
    prog = r.json()["data"][0]
    assert "program_code" in prog
    assert "program_title" in prog


@pytest.mark.asyncio
async def test_program_by_code_200(client_ref, headers):
    r = await client_ref.get("/v1/programs/GE", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["program_code"] == "GE"
    assert body["program_title"] == "General Schedule"
    assert "dispensing_rules" in body


@pytest.mark.asyncio
async def test_program_by_code_404(client_ref, headers):
    r = await client_ref.get("/v1/programs/MISSING", headers=headers)
    assert r.status_code == 404


# ── ATC Codes API ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_atc_codes_200(client_ref, headers):
    r = await client_ref.get("/v1/atc-codes", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] == 5


@pytest.mark.asyncio
async def test_atc_codes_filter_by_level(client_ref, headers):
    r = await client_ref.get("/v1/atc-codes?level=1", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["atc_code"] == "A"


@pytest.mark.asyncio
async def test_atc_codes_filter_by_parent(client_ref, headers):
    r = await client_ref.get("/v1/atc-codes?parent_code=A10", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["atc_code"] == "A10BA"


@pytest.mark.asyncio
async def test_atc_code_by_code_200(client_ref, headers):
    r = await client_ref.get("/v1/atc-codes/A10BA02", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["atc_code"] == "A10BA02"
    assert body["atc_description"] == "Metformin"
    assert "linked_items" in body
    pbs_codes = [i["pbs_code"] for i in body["linked_items"]]
    assert "2622M" in pbs_codes
    assert "children" in body


@pytest.mark.asyncio
async def test_atc_code_detail_includes_children(client_ref, headers):
    r = await client_ref.get("/v1/atc-codes/A10", headers=headers)
    assert r.status_code == 200
    children = r.json()["children"]
    assert len(children) == 1
    assert children[0]["atc_code"] == "A10BA"


@pytest.mark.asyncio
async def test_atc_code_by_code_404(client_ref, headers):
    r = await client_ref.get("/v1/atc-codes/MISSING", headers=headers)
    assert r.status_code == 404


# ── Copayments API ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_copayments_200(client_ref, headers):
    r = await client_ref.get("/v1/copayments", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "general" in body
    assert "concessional" in body
    assert "safety_net_general" in body
    assert "safety_net_concessional" in body
    assert body["month"] == "2099-03"


@pytest.mark.asyncio
async def test_copayments_values(client_ref, headers):
    r = await client_ref.get("/v1/copayments", headers=headers)
    body = r.json()
    assert float(body["general"]) == 31.60
    assert float(body["concessional"]) == 7.70


@pytest.mark.asyncio
async def test_copayments_filter_by_schedule(client_ref, headers):
    r = await client_ref.get("/v1/copayments?schedule=2099-03", headers=headers)
    assert r.status_code == 200
    assert r.json()["month"] == "2099-03"
