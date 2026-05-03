"""Comprehensive integration tests covering tier gating, rate limiting, drug endpoints,
schedule-changes, market endpoints, extemporaneous, and webhooks."""
import json
import pytest
import asyncpg
from pathlib import Path
from api.middleware.auth import generate_api_key, hash_api_key
from ingest.normaliser import normalise_schedule
from ingest.loader import load_to_database

FIXTURES = Path(__file__).parent / "fixtures"

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _insert_key(pool, tier: str, monthly_limit: int = 50000,
                      requests_this_month: int = 0) -> tuple[str, str]:
    """Insert an API key and return (full_key, key_hash). Caller must clean up."""
    full_key, key_prefix, key_hash = generate_api_key(tier)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO api_keys
               (key_prefix, key_hash, name, customer_email, tier, monthly_limit,
                history_months_limit, requests_this_month)
               VALUES ($1,$2,$3,$4,$5,$6,999,$7)""",
            key_prefix, key_hash, f"test-{tier}", f"{tier}@test.com",
            tier, monthly_limit, requests_this_month,
        )
    return full_key, key_hash


async def _delete_key(pool, key_hash: str):
    async with pool.acquire() as conn:
        key_id = await conn.fetchval("SELECT id FROM api_keys WHERE key_hash = $1", key_hash)
        if key_id:
            await conn.execute("DELETE FROM webhooks WHERE api_key_id = $1", key_id)
        await conn.execute("DELETE FROM api_keys WHERE key_hash = $1", key_hash)


class _Pool:
    """Minimal pool shim so load_to_database works with a single connection."""
    def __init__(self, conn): self._conn = conn
    def acquire(self): return _Ctx(self._conn)

class _Ctx:
    def __init__(self, c): self._c = c
    async def __aenter__(self): return self._c
    async def __aexit__(self, *a): pass


async def seed_schedule(db, month: str = "2026-04"):
    """Seed a minimal but representative schedule from fixture files."""
    items        = json.loads((FIXTURES / "pbs_sample_items.json").read_text())
    restrictions = json.loads((FIXTURES / "pbs_sample_restrictions.json").read_text())
    organisations= json.loads((FIXTURES / "pbs_sample_organisations.json").read_text())
    programs     = json.loads((FIXTURES / "pbs_sample_programs.json").read_text())
    atc_codes    = json.loads((FIXTURES / "pbs_sample_atc_codes.json").read_text())
    copayments   = json.loads((FIXTURES / "pbs_sample_copayments.json").read_text())
    fees         = json.loads((FIXTURES / "pbs_sample_fees.json").read_text())
    soc          = json.loads((FIXTURES / "pbs_sample_summary_of_changes.json").read_text())
    pt           = json.loads((FIXTURES / "pbs_sample_prescribing_texts.json").read_text())
    indications  = json.loads((FIXTURES / "pbs_sample_indications.json").read_text())
    prog_dr      = json.loads((FIXTURES / "pbs_sample_program_dispensing_rules.json").read_text())
    item_dr      = json.loads((FIXTURES / "pbs_sample_item_dispensing_rules.json").read_text())
    item_org     = json.loads((FIXTURES / "pbs_sample_item_organisation_relationships.json").read_text())
    item_atc     = json.loads((FIXTURES / "pbs_sample_item_atc_relationships.json").read_text())
    item_res     = json.loads((FIXTURES / "pbs_sample_item_restriction_relationships.json").read_text())
    item_pt      = json.loads((FIXTURES / "pbs_sample_item_prescribing_texts.json").read_text())

    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ($1, NOW(), 'complete') "
        "ON CONFLICT (month) DO UPDATE SET ingest_status = 'complete'",
        month,
    )

    normalised = normalise_schedule(
        month, items, restrictions,
        raw_organisations=organisations,
        raw_programs=programs,
        raw_atc_codes=atc_codes,
        raw_copayments=copayments,
        raw_fees=fees,
        raw_summary_of_changes=soc,
        raw_prescribing_texts=pt,
        raw_indications=indications,
        raw_program_dispensing_rules=prog_dr,
        raw_item_dispensing_rules=item_dr,
        raw_item_organisation_relationships=item_org,
        raw_item_atc_relationships=item_atc,
        raw_item_restriction_relationships=item_res,
        raw_item_prescribing_text_relationships=item_pt,
    )
    await load_to_database(_Pool(db), month, normalised, [])


# ── Fixtures ──────────────────────────────────────────────────────────────────

TEST_MONTH = "2099-01"

@pytest.fixture
async def seeded(db_pool):
    """Seed fixture data with auto-committed transactions so app_client can see it."""
    async with db_pool.acquire() as conn:
        await seed_schedule(conn, TEST_MONTH)
    yield
    # Cleanup: remove fixture schedule so it doesn't become the permanent "latest"
    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month=$1", TEST_MONTH)
        if not sid:
            return
        # restrictions FK → items, must delete before items
        await conn.execute(
            "DELETE FROM restrictions WHERE item_id IN (SELECT id FROM items WHERE schedule_id=$1)", sid
        )
        for tbl in [
            "summary_of_changes", "item_atc_relationships", "item_restriction_relationships",
            "item_prescribing_text_relationships", "item_organisation_relationships",
            "item_dispensing_rules", "item_pricing_events", "item_pricing", "item_prescribers",
            "item_amt_relationships", "amt_items", "items", "prescribing_texts",
            "restriction_prescribing_text_relationships", "organisations", "programs",
            "program_dispensing_rules", "atc_codes", "copayments", "fees", "markup_bands",
            "indications", "criteria_parameter_relationships", "criteria", "parameters",
            "standard_formula_preparations", "extemporaneous_ingredients", "extemporaneous_tariffs",
            "extemporaneous_preparations", "changes",
        ]:
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE schedule_id=$1", sid)
            except Exception:
                pass
        await conn.execute("DELETE FROM schedules WHERE id=$1", sid)


@pytest.fixture
async def free_key(db_pool):
    k, h = await _insert_key(db_pool, "free", monthly_limit=1000)
    yield k
    await _delete_key(db_pool, h)


@pytest.fixture
async def starter_key(db_pool):
    k, h = await _insert_key(db_pool, "starter")
    yield k
    await _delete_key(db_pool, h)


@pytest.fixture
async def growth_key(db_pool):
    k, h = await _insert_key(db_pool, "growth")
    yield k
    await _delete_key(db_pool, h)


@pytest.fixture
async def scale_key(db_pool):
    k, h = await _insert_key(db_pool, "scale")
    yield k
    await _delete_key(db_pool, h)


@pytest.fixture
async def enterprise_key(db_pool):
    k, h = await _insert_key(db_pool, "enterprise", monthly_limit=999_999_999)
    yield k
    await _delete_key(db_pool, h)


@pytest.fixture
async def exhausted_key(db_pool):
    k, h = await _insert_key(db_pool, "starter", monthly_limit=10, requests_this_month=10)
    yield k
    await _delete_key(db_pool, h)


def h(key): return {"X-API-Key": key}


# ══════════════════════════════════════════════════════════════════════════════
# 1. TIER GATING — full boundary matrix
# ══════════════════════════════════════════════════════════════════════════════

# T1 endpoint: /v1/atc-codes/{code}/hierarchy (starter+)
# T2 endpoint: /v1/drugs/{code}  (growth+)
# T3 endpoint: /v1/schedule-changes (scale+) | /v1/drugs/search (scale+)
# T4 endpoint: /v1/market/atc-summary (enterprise)

@pytest.mark.asyncio
async def test_free_blocked_from_t1(app_client, seeded, free_key):
    r = await app_client.get("/v1/atc-codes/A10BA02/hierarchy", headers=h(free_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_free_blocked_from_t2(app_client, seeded, free_key):
    r = await app_client.get("/v1/drugs/2622M", headers=h(free_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_free_blocked_from_t3(app_client, seeded, free_key):
    r = await app_client.get("/v1/schedule-changes", headers=h(free_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_free_blocked_from_t4(app_client, seeded, free_key):
    r = await app_client.get("/v1/market/atc-summary?atc_code=A10", headers=h(free_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_starter_allowed_on_t1(app_client, seeded, starter_key):
    r = await app_client.get("/v1/atc-codes/A10BA02/hierarchy", headers=h(starter_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_starter_blocked_from_t2(app_client, seeded, starter_key):
    r = await app_client.get("/v1/drugs/2622M", headers=h(starter_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_starter_blocked_from_t3(app_client, seeded, starter_key):
    r = await app_client.get("/v1/schedule-changes", headers=h(starter_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_starter_blocked_from_t4(app_client, seeded, starter_key):
    r = await app_client.get("/v1/market/atc-summary?atc_code=A10", headers=h(starter_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_growth_allowed_on_t2(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M", headers=h(growth_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_growth_blocked_from_t3(app_client, seeded, growth_key):
    r = await app_client.get("/v1/schedule-changes", headers=h(growth_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_growth_blocked_from_t4(app_client, seeded, growth_key):
    r = await app_client.get("/v1/market/atc-summary?atc_code=A10", headers=h(growth_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_scale_allowed_on_t3(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes", headers=h(scale_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_scale_allowed_on_drug_search(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/search?q=metformin", headers=h(scale_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_growth_blocked_from_drug_search(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/search?q=metformin", headers=h(growth_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_scale_blocked_from_t4(app_client, seeded, scale_key):
    r = await app_client.get("/v1/market/atc-summary?atc_code=A10", headers=h(scale_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_enterprise_allowed_on_t4(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/atc-summary?atc_code=A10", headers=h(enterprise_key))
    assert r.status_code in (200, 404)  # 404 acceptable if ATC not in fixture

@pytest.mark.asyncio
async def test_enterprise_allowed_on_t3(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/schedule-changes", headers=h(enterprise_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_enterprise_allowed_on_t1(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/atc-codes/A10BA02/hierarchy", headers=h(enterprise_key))
    assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 2. RATE LIMITING
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_monthly_limit_enforced(app_client, seeded, exhausted_key):
    r = await app_client.get("/v1/medicines", headers=h(exhausted_key))
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"

@pytest.mark.asyncio
async def test_monthly_limit_headers_present(app_client, seeded, starter_key):
    r = await app_client.get("/v1/medicines", headers=h(starter_key))
    assert "x-ratelimit-limit" in r.headers
    assert "x-ratelimit-remaining" in r.headers
    assert "x-ratelimit-reset" in r.headers

@pytest.mark.asyncio
async def test_burst_limit_headers_present(app_client, seeded, starter_key):
    r = await app_client.get("/v1/medicines", headers=h(starter_key))
    assert "x-ratelimit-burst-limit" in r.headers
    assert "x-ratelimit-burst-remaining" in r.headers

@pytest.mark.asyncio
async def test_burst_limit_header_value_numeric(app_client, seeded, starter_key):
    r = await app_client.get("/v1/medicines", headers=h(starter_key))
    burst = r.headers.get("x-ratelimit-burst-limit", "")
    assert burst.isdigit() or burst == "unlimited"

@pytest.mark.asyncio
async def test_enterprise_burst_is_unlimited(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/medicines", headers=h(enterprise_key))
    assert r.headers.get("x-ratelimit-burst-limit") == "unlimited"
    assert r.headers.get("x-ratelimit-burst-remaining") == "unlimited"

@pytest.mark.asyncio
async def test_ratelimit_remaining_decrements(app_client, seeded, starter_key):
    r1 = await app_client.get("/v1/medicines", headers=h(starter_key))
    r2 = await app_client.get("/v1/medicines", headers=h(starter_key))
    rem1 = int(r1.headers["x-ratelimit-remaining"])
    rem2 = int(r2.headers["x-ratelimit-remaining"])
    assert rem2 == rem1 - 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. WEBHOOKS — starter tier gate (changed from growth)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_free_cannot_create_webhook(app_client, seeded, free_key):
    r = await app_client.post("/v1/webhooks", headers=h(free_key), json={
        "endpoint_url": "https://example.com/hook",
        "event_types": ["pbs.schedule.released"],
    })
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_starter_can_create_webhook(app_client, seeded, starter_key):
    r = await app_client.post("/v1/webhooks", headers=h(starter_key), json={
        "endpoint_url": "https://example.com/hook",
        "event_types": ["pbs.schedule.released"],
    })
    assert r.status_code == 201
    data = r.json()
    assert "id" in data
    # Clean up
    await app_client.delete(f"/v1/webhooks/{data['id']}", headers=h(starter_key))

@pytest.mark.asyncio
async def test_growth_can_create_webhook(app_client, seeded, growth_key):
    r = await app_client.post("/v1/webhooks", headers=h(growth_key), json={
        "endpoint_url": "https://example.com/hook",
        "event_types": ["pbs.schedule.released"],
    })
    assert r.status_code == 201
    data = r.json()
    await app_client.delete(f"/v1/webhooks/{data['id']}", headers=h(growth_key))

@pytest.mark.asyncio
async def test_webhook_requires_https(app_client, seeded, starter_key):
    r = await app_client.post("/v1/webhooks", headers=h(starter_key), json={
        "endpoint_url": "http://example.com/hook",
        "event_types": ["pbs.schedule.released"],
    })
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_webhook_invalid_event_type(app_client, seeded, starter_key):
    r = await app_client.post("/v1/webhooks", headers=h(starter_key), json={
        "endpoint_url": "https://example.com/hook",
        "event_types": ["not.a.real.event"],
    })
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_webhook_list_and_delete(app_client, seeded, starter_key):
    create = await app_client.post("/v1/webhooks", headers=h(starter_key), json={
        "endpoint_url": "https://example.com/hook",
        "event_types": ["pbs.schedule.released"],
    })
    assert create.status_code == 201
    wh_id = create.json()["id"]

    lst = await app_client.get("/v1/webhooks", headers=h(starter_key))
    assert lst.status_code == 200
    ids = [w["id"] for w in lst.json()["data"]]
    assert wh_id in ids

    delete = await app_client.delete(f"/v1/webhooks/{wh_id}", headers=h(starter_key))
    assert delete.status_code == 204


# ══════════════════════════════════════════════════════════════════════════════
# 4. DRUG ENDPOINTS — T2 (growth tier)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_drugs_search_returns_results(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/search?q=metformin", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["total"] > 0
    assert len(data["data"]) > 0

@pytest.mark.asyncio
async def test_drugs_search_fields_present(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/search?q=metformin", headers=h(scale_key))
    item = r.json()["data"][0]
    for field in ("pbs_code", "ingredient", "brand_name", "formulary", "benefit_type_code"):
        assert field in item, f"Missing field: {field}"

@pytest.mark.asyncio
async def test_drug_detail_structure(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M", headers=h(growth_key))
    assert r.status_code == 200
    data = r.json()["data"]
    for block in ("drug", "dispensing", "program", "classification", "restriction", "pricing_summary"):
        assert block in data, f"Missing block: {block}"

@pytest.mark.asyncio
async def test_drug_detail_government_price_populated(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M", headers=h(growth_key))
    pricing = r.json()["data"]["pricing_summary"]
    assert pricing["government_price"] is not None

@pytest.mark.asyncio
async def test_drug_detail_include_brands(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M?include_brands=true", headers=h(growth_key))
    assert r.status_code == 200
    assert "brands" in r.json()["data"]
    assert isinstance(r.json()["data"]["brands"], list)

@pytest.mark.asyncio
async def test_drug_detail_brands_not_present_by_default(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M", headers=h(growth_key))
    assert "brands" not in r.json()["data"]

@pytest.mark.asyncio
async def test_drug_brands_endpoint(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M/brands", headers=h(growth_key))
    assert r.status_code == 200
    assert "data" in r.json()

@pytest.mark.asyncio
async def test_drug_prescribers_endpoint(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M/prescribers", headers=h(growth_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_drug_atc_endpoint(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M/atc", headers=h(growth_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_drug_restrictions_endpoint(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M/restrictions", headers=h(growth_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_drug_full_profile_blocks(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/full-profile", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    for block in ("identity", "dispensing", "program", "restriction", "pricing"):
        assert block in data, f"Missing block: {block}"

@pytest.mark.asyncio
async def test_drug_404_unknown_code(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/ZZZZZZ", headers=h(growth_key))
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# 5. DRUG ENDPOINTS — T3 (scale tier)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_restriction_full_returns_restrictions(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/restriction-full", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "restriction_count" in data
    assert "restrictions" in data

@pytest.mark.asyncio
async def test_restriction_full_has_prescribing_components(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/restriction-full", headers=h(scale_key))
    restrictions = r.json()["data"]["restrictions"]
    if restrictions:
        assert "prescribing_components" in restrictions[0]

@pytest.mark.asyncio
async def test_authority_workflow_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/authority-workflow", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "workflows" in data
    assert "requires_any_authority" in data
    assert "authorised_prescribers" in data

@pytest.mark.asyncio
async def test_authority_workflow_clinical_fields(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/authority-workflow", headers=h(scale_key))
    workflows = r.json()["data"]["workflows"]
    if workflows:
        w = workflows[0]
        for field in ("restriction_text", "indication", "clinical_criteria", "checklist"):
            assert field in w, f"Missing field in workflow: {field}"
        assert isinstance(w["checklist"], list)
        assert len(w["checklist"]) > 0

@pytest.mark.asyncio
async def test_substitution_returns_ingredient(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/substitution", headers=h(scale_key))
    assert r.status_code == 200
    assert r.json()["data"]["ingredient"] is not None

@pytest.mark.asyncio
async def test_price_history_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/price-history", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "snapshot_count" in data
    assert "history" in data
    assert "trend" in data
    assert data["snapshot_count"] >= 1

@pytest.mark.asyncio
async def test_price_history_snapshot_has_government_price(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/price-history", headers=h(scale_key))
    history = r.json()["data"]["history"]
    assert len(history) > 0
    assert history[0]["government_price"] is not None

@pytest.mark.asyncio
async def test_price_history_trend_fields(app_client, seeded, scale_key):
    # Seed a second schedule month so trend can compute
    r = await app_client.get("/v1/drugs/2622M/price-history", headers=h(scale_key))
    data = r.json()["data"]
    if data["snapshot_count"] >= 2 and data["trend"] is not None:
        trend = data["trend"]
        for field in ("oldest_month", "newest_month", "oldest_price", "newest_price",
                      "delta", "delta_pct", "direction"):
            assert field in trend
        assert trend["direction"] in ("up", "down", "stable")

@pytest.mark.asyncio
async def test_pricing_events_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/pricing-events", headers=h(scale_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_safety_net_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/safety-net", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "patient_cost" in data
    assert "safety_net" in data
    assert "estimated_scripts_to_safety_net" in data

@pytest.mark.asyncio
async def test_safety_net_patient_cost_fields(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/safety-net", headers=h(scale_key))
    pc = r.json()["data"]["patient_cost"]
    assert "general_copayment" in pc
    assert "concessional_copayment" in pc

@pytest.mark.asyncio
async def test_sixty_day_pair_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/60-day-pair", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "sixty_day_eligible" in data
    assert "same_ingredient_60_day_eligible" in data

@pytest.mark.asyncio
async def test_formulary_status_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/formulary-status", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "formulary" in data
    assert "formulary_label" in data
    assert "benefit_type_label" in data

@pytest.mark.asyncio
async def test_t3_drug_blocked_for_growth(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M/restriction-full", headers=h(growth_key))
    assert r.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 6. SCHEDULE-CHANGES — classifier and sub-route enrichment
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_schedule_changes_summary_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "total_changes" in data
    assert "summary_by_type" in data
    assert "summary_by_severity" in data
    assert "changes" in data

@pytest.mark.asyncio
async def test_schedule_changes_classifier_new_listing(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04", headers=h(scale_key))
    by_type = r.json()["data"]["summary_by_type"]
    assert by_type.get("NEW_LISTING", 0) > 0

@pytest.mark.asyncio
async def test_schedule_changes_classifier_price_change(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04", headers=h(scale_key))
    by_type = r.json()["data"]["summary_by_type"]
    assert by_type.get("PRICE_CHANGE", 0) > 0

@pytest.mark.asyncio
async def test_schedule_changes_classifier_delisting(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04", headers=h(scale_key))
    by_type = r.json()["data"]["summary_by_type"]
    assert by_type.get("DELISTING", 0) > 0

@pytest.mark.asyncio
async def test_schedule_changes_classifier_restriction(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04", headers=h(scale_key))
    by_type = r.json()["data"]["summary_by_type"]
    assert by_type.get("RESTRICTION_CHANGE", 0) > 0

@pytest.mark.asyncio
async def test_schedule_changes_no_other_dominance(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04", headers=h(scale_key))
    data = r.json()["data"]
    other = data["summary_by_type"].get("OTHER_MODIFICATION", 0)
    total = data["total_changes"]
    assert other < total * 0.5, f"OTHER_MODIFICATION dominates: {other}/{total}"

@pytest.mark.asyncio
async def test_schedule_changes_new_listings_enrichment(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04/new-listings", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["total"] > 0
    item = data["data"][0]
    assert "is_first_in_atc_class" in item
    assert "primary_atc_code" in item
    assert item["change_type_code"] == "NEW_LISTING"

@pytest.mark.asyncio
async def test_schedule_changes_delistings_enrichment(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04/delistings", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["total"] > 0
    item = data["data"][0]
    assert "therapeutic_alternatives" in item
    assert item["change_type_code"] == "DELISTING"

@pytest.mark.asyncio
async def test_schedule_changes_price_changes_enrichment(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04/price-changes", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["total"] > 0
    item = data["data"][0]
    for field in ("price_delta", "price_delta_pct", "current_price", "previous_price"):
        assert field in item, f"Missing field: {field}"
    assert item["change_type_code"] == "PRICE_CHANGE"

@pytest.mark.asyncio
async def test_schedule_changes_restriction_enrichment(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/2026-04/restriction-changes", headers=h(scale_key))
    assert r.status_code == 200
    data = r.json()
    assert data["meta"]["total"] > 0
    item = data["data"][0]
    assert "current_restriction_codes" in item
    assert item["change_type_code"] == "RESTRICTION_CHANGE"

@pytest.mark.asyncio
async def test_schedule_changes_404_unknown_month(app_client, seeded, scale_key):
    r = await app_client.get("/v1/schedule-changes/1999-01", headers=h(scale_key))
    assert r.status_code == 404

@pytest.mark.asyncio
async def test_schedule_changes_blocked_for_growth(app_client, seeded, growth_key):
    r = await app_client.get("/v1/schedule-changes/2026-04", headers=h(growth_key))
    assert r.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 7. MARKET ENDPOINTS — T4 (enterprise tier)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_market_requires_enterprise(app_client, seeded, scale_key):
    r = await app_client.get("/v1/market/atc-summary?atc_code=A10", headers=h(scale_key))
    assert r.status_code == 403

@pytest.mark.asyncio
async def test_market_atc_summary_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/atc-summary?atc_code=A10", headers=h(enterprise_key))
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        data = r.json()["data"]
        for field in ("atc_code", "item_count", "brand_count"):
            assert field in data

@pytest.mark.asyncio
async def test_market_formulary_landscape_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/formulary-landscape", headers=h(enterprise_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "total_items" in data
    assert "by_formulary" in data

@pytest.mark.asyncio
async def test_market_authority_landscape_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/authority-landscape", headers=h(enterprise_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "total_items" in data

@pytest.mark.asyncio
async def test_market_biosimilar_landscape_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/biosimilar-landscape", headers=h(enterprise_key))
    assert r.status_code == 200
    assert "data" in r.json()

@pytest.mark.asyncio
async def test_market_manufacturer_landscape_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/manufacturer-landscape", headers=h(enterprise_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_market_safety_net_burden_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/safety-net-burden", headers=h(enterprise_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_market_listings_pipeline_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/listings-pipeline", headers=h(enterprise_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_market_price_pressure_index_structure(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/price-pressure-index", headers=h(enterprise_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_market_schedule_comparison_requires_two_params(app_client, seeded, enterprise_key):
    r = await app_client.get("/v1/market/schedule-comparison", headers=h(enterprise_key))
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_market_price_reduction_events_structure(app_client, seeded, enterprise_key):
    r = await app_client.get(
        "/v1/market/price-reduction-events?start_schedule=2026-01&end_schedule=2026-05",
        headers=h(enterprise_key),
    )
    assert r.status_code in (200, 404)


# ══════════════════════════════════════════════════════════════════════════════
# 8. EXTEMPORANEOUS ENDPOINTS — T3 (scale tier)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_extemporaneous_ingredients_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/extemporaneous/ingredients", headers=h(scale_key))
    assert r.status_code == 200
    assert "data" in r.json()

@pytest.mark.asyncio
async def test_extemporaneous_tariffs_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/extemporaneous/tariffs", headers=h(scale_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_extemporaneous_preparations_structure(app_client, seeded, scale_key):
    r = await app_client.get("/v1/extemporaneous/preparations", headers=h(scale_key))
    assert r.status_code == 200

@pytest.mark.asyncio
async def test_extemporaneous_blocked_for_growth(app_client, seeded, growth_key):
    r = await app_client.get("/v1/extemporaneous/ingredients", headers=h(growth_key))
    assert r.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 9. SCHEDULE VERSIONING
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_schedule_param_returns_correct_month(app_client, seeded, growth_key):
    r = await app_client.get(f"/v1/drugs/2622M?schedule={TEST_MONTH}", headers=h(growth_key))
    assert r.status_code == 200
    assert r.json()["meta"]["schedule_month"] == TEST_MONTH

@pytest.mark.asyncio
async def test_unknown_schedule_returns_404(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M?schedule=1990-01", headers=h(growth_key))
    assert r.status_code == 404

@pytest.mark.asyncio
async def test_default_schedule_is_latest(app_client, seeded, growth_key):
    r = await app_client.get("/v1/drugs/2622M", headers=h(growth_key))
    assert r.status_code == 200
    assert r.json()["meta"]["schedule_month"] == TEST_MONTH


# ══════════════════════════════════════════════════════════════════════════════
# 10. PRICE FIELDS — normaliser fix validation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_government_price_populated_from_commonwealth_price(app_client, seeded, growth_key):
    """Fixtures use commonwealth_price (legacy field) — must still populate government_price."""
    r = await app_client.get("/v1/drugs/2622M", headers=h(growth_key))
    pricing = r.json()["data"]["pricing_summary"]
    assert pricing["government_price"] is not None
    assert float(pricing["government_price"]) > 0

@pytest.mark.asyncio
async def test_price_history_government_price_in_snapshot(app_client, seeded, scale_key):
    r = await app_client.get("/v1/drugs/2622M/price-history", headers=h(scale_key))
    history = r.json()["data"]["history"]
    assert len(history) > 0
    assert history[0]["government_price"] is not None

@pytest.mark.asyncio
async def test_items_patient_cost_uses_copayment(app_client, seeded, growth_key):
    r = await app_client.get("/v1/items/2622M/patient-cost", headers=h(growth_key))
    assert r.status_code == 200
    data = r.json()["data"]
    assert "general_patient" in data
    assert "dispensed_price" in data
