"""Phase 2 tests: full ingest pipeline with fixture data."""
import json
import pytest
from pathlib import Path
from decimal import Decimal

from ingest.normaliser import normalise_schedule, normalise_ingredient_name, BENEFIT_TYPE_MAP
from ingest.reference import benefit_type_label, canonical_benefit_type
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


def test_normaliser_stores_pbs_benefit_type_codes():
    """Benefit type is stored as the PBS letter code, which is what the API matches on.

    The ingest previously expanded these to "authority_required" while every API
    query compared against 'A'. See docs/AUDIT_FINDINGS.md [B3].
    """
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    for item in result["items"]:
        assert item["benefit_type"] in BENEFIT_TYPE_MAP, \
            f"Unexpected benefit type '{item['benefit_type']}' on {item['pbs_code']}"
        assert len(item["benefit_type"]) == 1, \
            f"Benefit type must be a single PBS letter, got '{item['benefit_type']}'"


def test_normaliser_U_stays_U():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    lipitor = next(i for i in result["items"] if i["pbs_code"] == "8591B")
    assert lipitor["benefit_type"] == "U"
    assert benefit_type_label(lipitor["benefit_type"]) == "Unrestricted"


def test_normaliser_S_stays_S():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    humira = next(i for i in result["items"] if i["pbs_code"] == "1234A")
    assert humira["benefit_type"] == "S"
    assert benefit_type_label(humira["benefit_type"]) == "Streamlined Authority"


def test_normaliser_A_stays_A():
    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    clozaril = next(i for i in result["items"] if i["pbs_code"] == "5678B")
    assert clozaril["benefit_type"] == "A"
    assert benefit_type_label(clozaril["benefit_type"]) == "Authority Required"


def test_normaliser_accepts_legacy_long_form_benefit_type():
    """Long-form values written by older ingest runs normalise back to PBS codes."""
    assert canonical_benefit_type("authority_required") == "A"
    assert canonical_benefit_type("authority_streamlined") == "S"
    assert canonical_benefit_type("unrestricted") == "U"
    assert canonical_benefit_type("restricted") == "R"


def test_normaliser_strips_leading_slash_from_section():
    """PBS supplies changed_endpoint as '/items'; stored without the slash.

    Equality against 'items' previously never matched, so new listings and
    delistings were never classified. See docs/AUDIT_FINDINGS.md [B4].
    """
    result = normalise_schedule(
        "2026-04", [], [],
        raw_summary_of_changes=[
            {"table_keys": {"pbs_code": "1234A"}, "change_type": "INSERT",
             "changed_endpoint": "/items", "change_detail": "New listing"},
            {"table_keys": {"pbs_code": "5678B"}, "change_type": "UPDATE",
             "changed_endpoint": "/item-restriction-relationships", "change_detail": "x"},
        ],
    )
    sections = [c["section"] for c in result["summary_of_changes"]]
    assert sections == ["items", "item-restriction-relationships"]
    assert not any(s.startswith("/") for s in sections)


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


@pytest.mark.asyncio
async def test_pipeline_idempotent_for_changes(db):
    """
    Re-running an ingest must not append a second set of change rows.

    test_pipeline_idempotent above passes an empty changes list, so it never
    exercised insert_changes — which was the one loader with no delete-first,
    and duplicated its rows on every re-run.
    """
    from ingest.loader import load_to_database

    items = load_fixture("pbs_sample_items.json")
    restrictions = load_fixture("pbs_sample_restrictions.json")
    normalised = normalise_schedule("2026-04", items, restrictions)

    changes = [
        {"pbs_code": "10001J", "change_type": "price_change",
         "field_name": "government_price", "old_value": "1.00", "new_value": "2.00"},
        {"pbs_code": "10003L", "change_type": "new_listing",
         "field_name": None, "old_value": None, "new_value": None},
    ]

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
    count_sql = (
        "SELECT COUNT(*) FROM changes c JOIN schedules s ON s.id = c.schedule_id "
        "WHERE s.month = '2026-04'"
    )

    await load_to_database(pool, "2026-04", normalised, changes)
    after_first = await db.fetchval(count_sql)

    await load_to_database(pool, "2026-04", normalised, changes)
    after_second = await db.fetchval(count_sql)

    assert after_first == len(changes)
    assert after_first == after_second, \
        f"Idempotency violated: changes grew {after_first} -> {after_second} on re-run"


@pytest.mark.asyncio
async def test_differ_compares_brands_not_collapsed_codes(db):
    """
    Price changes are compared brand-to-brand on li_item_id.

    `items` is keyed on (li_item_id, schedule_id) to match PBS's ITEM_T, so a
    PBS code appears once per brand. The differ previously keyed the previous
    schedule by pbs_code, keeping one arbitrary brand and comparing every
    current brand against it — which reported a price change whenever the
    winning brand differed between months, though nothing had moved. Measured
    on 2026-06: 246 of 295 government_price changes were that artifact.
    """
    from ingest.differ import compute_changes

    class FakePool:
        def acquire(self): return FakeAcquire(db)
    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) "
        "VALUES ('1998-01', NOW(), 'complete') ON CONFLICT (month) DO UPDATE SET ingest_status='complete'"
    )
    sched = await db.fetchval("SELECT id FROM schedules WHERE month='1998-01'")
    med = await db.fetchval(
        "INSERT INTO medicines (ingredient, ingredient_lower) VALUES ('Testium','testium') "
        "ON CONFLICT (ingredient_lower) DO UPDATE SET ingredient=EXCLUDED.ingredient RETURNING id"
    )
    await db.execute("DELETE FROM items WHERE schedule_id = $1", sched)
    # Two brands of one PBS code, different prices, neither of which changes.
    for li, price in (("5000A_1_1_1_1", 10), ("5000A_2_2_2_2", 90)):
        await db.execute(
            "INSERT INTO items (pbs_code, li_item_id, schedule_id, medicine_id, brand_name, "
            "brand_name_lower, benefit_type, government_price) "
            "VALUES ('5000A',$1,$2,$3,'B','b','U',$4)", li, sched, med, price,
        )

    normalised = {"items": [
        {"pbs_code": "5000A", "li_item_id": "5000A_1_1_1_1",
         "government_price": 10, "brand_premium": 0},
        {"pbs_code": "5000A", "li_item_id": "5000A_2_2_2_2",
         "government_price": 90, "brand_premium": 0},
    ]}

    changes = await compute_changes(FakePool(), normalised, "1998-02")
    price_changes = [c for c in changes if c["change_type"] == "price_changed"]

    assert price_changes == [], \
        f"reported price movement where each brand held its price: {price_changes}"

    await db.execute("DELETE FROM items WHERE schedule_id = $1", sched)
