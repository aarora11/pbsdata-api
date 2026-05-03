"""Phase 2 tests: full ingest pipeline with fixture data."""
import json
import pytest
from pathlib import Path
from decimal import Decimal

from ingest.normaliser import normalise_schedule, normalise_ingredient_name, BENEFIT_TYPE_MAP
from ingest.differ import compute_changes, price_changed


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> list:
    return json.loads((FIXTURES / filename).read_text())


# ── Normaliser tests ──────────────────────────────────────────────────────────

def test_normaliser_returns_required_keys():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    assert "month" in result
    assert "medicines" in result
    assert "items" in result
    assert result["month"] == "2026-04"


def test_normaliser_deduplicates_medicines():
    """Two items with the same ingredient produce one medicine."""
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    metformin_medicines = [
        m for m in result["medicines"] if "metformin" in m["ingredient_lower"]
    ]
    assert len(metformin_medicines) == 1, \
        f"Expected 1 metformin medicine, got {len(metformin_medicines)}"


def test_normaliser_maps_all_benefit_types():
    """All benefit type codes are mapped to their string equivalents."""
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    for item in result["items"]:
        assert item["benefit_type"] in BENEFIT_TYPE_MAP.values(), \
            f"Unexpected benefit type '{item['benefit_type']}' on {item['pbs_code']}"


def test_normaliser_U_maps_to_unrestricted():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    lipitor = next(i for i in result["items"] if i["pbs_code"] == "8591B")
    assert lipitor["benefit_type"] == "unrestricted"


def test_normaliser_S_maps_to_authority_streamlined():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    humira = next(i for i in result["items"] if i["pbs_code"] == "1234A")
    assert humira["benefit_type"] == "authority_streamlined"


def test_normaliser_A_maps_to_authority_required():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    clozaril = next(i for i in result["items"] if i["pbs_code"] == "5678B")
    assert clozaril["benefit_type"] == "authority_required"


def test_brand_premium_never_counts_to_safety_net():
    """brand_premium_counts_to_safety_net is ALWAYS False — PBS rule, not data."""
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    for item in result["items"]:
        assert item["brand_premium_counts_to_safety_net"] is False, \
            f"Item {item['pbs_code']} has brand_premium_counts_to_safety_net=True"


def test_normaliser_attaches_restrictions_to_items():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    metformin = next(i for i in result["items"] if i["pbs_code"] == "2622M")
    assert len(metformin["restrictions"]) >= 1
    assert metformin["restrictions"][0]["streamlined_code"] == "4236"


def test_adalimumab_has_two_restrictions():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    humira = next(i for i in result["items"] if i["pbs_code"] == "1234A")
    assert len(humira["restrictions"]) == 2
    has_continuation = any(r["continuation_only"] for r in humira["restrictions"])
    assert has_continuation, "Adalimumab should have a continuation-only restriction"


def test_ingredient_name_title_cased():
    assert normalise_ingredient_name("METFORMIN HYDROCHLORIDE") == "Metformin Hydrochloride"
    assert normalise_ingredient_name("metformin hydrochloride") == "Metformin Hydrochloride"
    assert normalise_ingredient_name("Metformin Hydrochloride") == "Metformin Hydrochloride"


def test_normaliser_handles_missing_fields():
    """Normaliser does not crash on items with missing optional fields."""
    minimal = [{"pbs_code": "0001T", "drug_name": "Test Drug", "brand_name": "TestBrand", "benefit_type": "U"}]
    result = normalise_schedule("2026-04", minimal, [])
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item.get("general_charge") is None


# ── Differ tests ──────────────────────────────────────────────────────────────

def test_price_changed_detects_real_change():
    assert price_changed(Decimal("21.43"), Decimal("22.15")) is True


def test_price_changed_ignores_sub_cent_noise():
    assert price_changed(Decimal("21.43"), Decimal("21.430001")) is False


def test_price_changed_handles_none():
    assert price_changed(None, Decimal("21.43")) is True
    assert price_changed(Decimal("21.43"), None) is True
    assert price_changed(None, None) is False


@pytest.mark.asyncio
async def test_differ_all_new_when_no_previous_schedule(db):
    """When no prior schedule exists, all items are detected as new."""
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    normalised = normalise_schedule("2026-04", items, restrictions)

    class FakePool:
        def acquire(self): return FakeAcquire(db)
    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    changes = await compute_changes(FakePool(), normalised, "2026-03")
    assert all(c["change_type"] == "new" for c in changes)
    assert len(changes) == len(normalised["items"])


# ── Full pipeline integration test ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_pipeline_loads_data(db):
    """The full normalise → load pipeline inserts data without errors."""
    from ingest.loader import load_to_database

    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    normalised = normalise_schedule("2026-04", items, restrictions)

    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2026-04', NOW(), 'running') ON CONFLICT (month) DO UPDATE SET ingest_status = 'running'"
    )

    class FakePool:
        def acquire(self): return FakeAcquire(db)
    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    await load_to_database(FakePool(), "2026-04", normalised, [])

    item_count = await db.fetchval(
        "SELECT COUNT(*) FROM items WHERE schedule_id = (SELECT id FROM schedules WHERE month = '2026-04')"
    )
    medicine_count = await db.fetchval("SELECT COUNT(*) FROM medicines")

    assert item_count == len(items)
    assert medicine_count > 0


@pytest.mark.asyncio
async def test_pipeline_idempotent(db):
    """Running ingest twice for the same month does not create duplicate medicines."""
    from ingest.loader import load_to_database

    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    normalised = normalise_schedule("2026-04", items, restrictions)

    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2026-04', NOW(), 'running') ON CONFLICT (month) DO UPDATE SET ingest_status = 'running'"
    )

    class FakePool:
        def acquire(self): return FakeAcquire(db)
    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    pool = FakePool()
    await load_to_database(pool, "2026-04", normalised, [])
    count_after_first = await db.fetchval("SELECT COUNT(*) FROM medicines")

    await load_to_database(pool, "2026-04", normalised, [])
    count_after_second = await db.fetchval("SELECT COUNT(*) FROM medicines")

    assert count_after_first == count_after_second, \
        "Idempotency violated: duplicate medicines created on second run"
