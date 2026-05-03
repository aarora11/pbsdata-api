"""Phase 7 tests: expanded API endpoints for all new PBS data."""
import json
import pytest
from pathlib import Path
from api.middleware.auth import generate_api_key
from ingest.normaliser import normalise_schedule
from ingest.loader import load_to_database

FIXTURES = Path(__file__).parent / "fixtures"


def load(filename: str):
    return json.loads((FIXTURES / filename).read_text())


async def seed_expanded(db):
    """Seed all expanded PBS data for integration tests."""
    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2099-02', NOW(), 'complete') ON CONFLICT (month) DO UPDATE SET ingest_status = 'complete'"
    )
    normalised = normalise_schedule(
        "2099-02",
        load("pbs_sample_items.json"),
        load("pbs_sample_restrictions.json"),
        raw_fees=load("pbs_sample_fees.json"),
        raw_prescribing_texts=load("pbs_sample_prescribing_texts.json"),
        raw_indications=load("pbs_sample_indications.json"),
        raw_amt_items=load("pbs_sample_amt_items.json"),
        raw_item_overviews=load("pbs_sample_item_overviews.json"),
        raw_item_amt=load("pbs_sample_item_amt.json"),
        raw_item_dispensing_rules=load("pbs_sample_item_dispensing_rules.json"),
        raw_program_dispensing_rules=load("pbs_sample_program_dispensing_rules.json"),
        raw_item_restriction_relationships=load("pbs_sample_item_restriction_relationships.json"),
        raw_restriction_prescribing_text_relationships=load("pbs_sample_restriction_prescribing_text_relationships.json"),
        raw_item_prescribing_texts=load("pbs_sample_item_prescribing_texts.json"),
        raw_summary_of_changes=load("pbs_sample_summary_of_changes.json"),
    )

    class P:
        def acquire(self): return A(db)
    class A:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    await load_to_database(P(), "2099-02", normalised, [])


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
async def client_expanded(app_client, db_pool):
    async with db_pool.acquire() as conn:
        await seed_expanded(conn)
    yield app_client
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM summary_of_changes WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM item_prescribing_text_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM restriction_prescribing_text_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM item_restriction_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM item_dispensing_rules WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM program_dispensing_rules WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM item_amt_relationships WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM amt_items WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM indications WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM prescribing_texts WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM fees WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM restrictions WHERE item_id IN (SELECT id FROM items WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02'))")
        await conn.execute("DELETE FROM items WHERE schedule_id IN (SELECT id FROM schedules WHERE month = '2099-02')")
        await conn.execute("DELETE FROM schedules WHERE month = '2099-02'")


# ── Fees ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fees_200(client_expanded, headers):
    r = await client_expanded.get("/v1/fees", headers=headers)
    assert r.status_code == 200
    assert "data" in r.json()


@pytest.mark.asyncio
async def test_fees_fields(client_expanded, headers):
    r = await client_expanded.get("/v1/fees", headers=headers)
    data = r.json()["data"]
    assert len(data) == 3
    for f in data:
        assert "fee_code" in f
        assert "fee_type" in f
        assert "amount" in f


@pytest.mark.asyncio
async def test_fees_by_code_200(client_expanded, headers):
    r = await client_expanded.get("/v1/fees/FEE001", headers=headers)
    assert r.status_code == 200
    assert r.json()["fee_code"] == "FEE001"


@pytest.mark.asyncio
async def test_fees_by_code_404(client_expanded, headers):
    r = await client_expanded.get("/v1/fees/MISSING", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_fees_filter_by_type(client_expanded, headers):
    r = await client_expanded.get("/v1/fees?fee_type=dispensing_fee", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["fee_type"] == "dispensing_fee"


# ── Prescribing texts ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prescribing_texts_200(client_expanded, headers):
    r = await client_expanded.get("/v1/prescribing-texts", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert body["meta"]["total"] == 2


@pytest.mark.asyncio
async def test_prescribing_texts_filter_by_pbs_code(client_expanded, headers):
    r = await client_expanded.get("/v1/prescribing-texts?pbs_code=1234A", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) >= 1
    ids = [d["prescribing_text_id"] for d in data]
    assert "PT001" in ids


@pytest.mark.asyncio
async def test_prescribing_texts_by_id_200(client_expanded, headers):
    r = await client_expanded.get("/v1/prescribing-texts/PT001", headers=headers)
    assert r.status_code == 200
    assert r.json()["prescribing_text_id"] == "PT001"


@pytest.mark.asyncio
async def test_prescribing_texts_by_id_404(client_expanded, headers):
    r = await client_expanded.get("/v1/prescribing-texts/MISSING", headers=headers)
    assert r.status_code == 404


# ── Indications ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_indications_200(client_expanded, headers):
    r = await client_expanded.get("/v1/indications", headers=headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 2


@pytest.mark.asyncio
async def test_indications_filter_by_pbs_code(client_expanded, headers):
    r = await client_expanded.get("/v1/indications?pbs_code=1234A", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["indication_id"] == "IND001"


@pytest.mark.asyncio
async def test_indications_by_id_200(client_expanded, headers):
    r = await client_expanded.get("/v1/indications/IND001", headers=headers)
    assert r.status_code == 200
    assert r.json()["indication_id"] == "IND001"


# ── AMT ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_amt_200(client_expanded, headers):
    r = await client_expanded.get("/v1/amt", headers=headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 2


@pytest.mark.asyncio
async def test_amt_filter_by_atc_code(client_expanded, headers):
    r = await client_expanded.get("/v1/amt?atc_code=A10BA02", headers=headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 2


@pytest.mark.asyncio
async def test_amt_by_id_200(client_expanded, headers):
    r = await client_expanded.get("/v1/amt/AMT001", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["amt_id"] == "AMT001"
    assert "linked_items" in body


@pytest.mark.asyncio
async def test_amt_by_id_includes_linked_items(client_expanded, headers):
    r = await client_expanded.get("/v1/amt/AMT002", headers=headers)
    assert r.status_code == 200
    linked = r.json()["linked_items"]
    pbs_codes = [i["pbs_code"] for i in linked]
    assert "2622M" in pbs_codes


# ── Dispensing rules ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispensing_rules_200(client_expanded, headers):
    r = await client_expanded.get("/v1/dispensing-rules", headers=headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 2


@pytest.mark.asyncio
async def test_dispensing_rules_filter_by_program(client_expanded, headers):
    r = await client_expanded.get("/v1/dispensing-rules?program_code=GE", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["program_code"] == "GE"


@pytest.mark.asyncio
async def test_dispensing_rule_by_code_200(client_expanded, headers):
    r = await client_expanded.get("/v1/dispensing-rules/RULE001", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["rule_code"] == "RULE001"
    assert "linked_items" in body
    assert "2622M" in body["linked_items"]


# ── Summary of changes ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_of_changes_200(client_expanded, headers):
    r = await client_expanded.get("/v1/summary-of-changes", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert body["meta"]["total"] == 5


@pytest.mark.asyncio
async def test_summary_of_changes_filter_by_pbs_code(client_expanded, headers):
    r = await client_expanded.get("/v1/summary-of-changes?pbs_code=2622M", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 2
    assert all(d["pbs_code"] == "2622M" for d in data)
    assert all(d["change_type"] == "DELETE" for d in data)


@pytest.mark.asyncio
async def test_summary_of_changes_filter_by_schedule(client_expanded, headers):
    r = await client_expanded.get("/v1/summary-of-changes?schedule=2099-02", headers=headers)
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 5


# ── Extended item detail ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_item_detail_includes_artg_id(client_expanded, headers):
    r = await client_expanded.get("/v1/items/2622M", headers=headers)
    assert r.status_code == 200
    body = r.json()
    # Growth (T2) key returns enriched envelope — data fields are under "data"
    data = body["data"]
    assert "artg_id" in data
    assert data["artg_id"] == "ARTG12345"
    assert data["sponsor"] == "Merck Serono Pty Ltd"


@pytest.mark.asyncio
async def test_item_prescribing_texts_200(client_expanded, headers):
    r = await client_expanded.get("/v1/items/1234A/prescribing-texts", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) >= 1
    assert data[0]["prescribing_text_id"] == "PT001"


@pytest.mark.asyncio
async def test_item_dispensing_rules_200(client_expanded, headers):
    r = await client_expanded.get("/v1/items/2622M/dispensing-rules", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["rule_code"] == "RULE001"
    assert data[0]["program_code"] == "GE"
