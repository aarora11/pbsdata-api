"""Phase 6 tests: expanded ingest pipeline — all 13 PBS endpoints."""
import json
import pytest
from pathlib import Path
from ingest.normaliser import normalise_schedule
from ingest.loader import load_to_database
from ingest.pbs_client import PBSClient

FIXTURES = Path(__file__).parent / "fixtures"


def load(filename: str):
    return json.loads((FIXTURES / filename).read_text())


# ── Normaliser: backward compatibility ────────────────────────────────────────

def test_normaliser_old_signature_still_works():
    """Existing two-param call still returns same structure."""
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    assert "month" in result
    assert "medicines" in result
    assert "items" in result
    assert result["month"] == "2026-04"


def test_normaliser_new_keys_present_with_empty_data():
    """Calling with just items+restrictions produces empty lists for all new keys."""
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)
    for key in [
        "fees", "prescribing_texts", "indications", "amt_items",
        "item_amt_relationships", "item_dispensing_rules", "program_dispensing_rules",
        "item_restriction_relationships", "restriction_prescribing_text_relationships",
        "item_prescribing_text_relationships", "summary_of_changes",
    ]:
        assert key in result, f"Missing key '{key}' in normalised output"
        assert result[key] == [], f"Expected empty list for '{key}', got {result[key]}"


def test_normaliser_returns_all_new_keys_when_data_provided():
    """All new keys are populated when full fixture data is passed."""
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    result = normalise_schedule(
        "2026-04", items, restrictions,
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
    assert len(result["fees"]) == 3
    assert len(result["prescribing_texts"]) == 2
    assert len(result["indications"]) == 2
    assert len(result["amt_items"]) == 2
    assert len(result["item_amt_relationships"]) == 2
    assert len(result["item_dispensing_rules"]) == 3
    assert len(result["program_dispensing_rules"]) == 2
    assert len(result["item_restriction_relationships"]) == 3
    assert len(result["restriction_prescribing_text_relationships"]) == 2
    assert len(result["item_prescribing_text_relationships"]) == 2
    assert len(result["summary_of_changes"]) == 2


def test_normaliser_item_overview_augments_item():
    """Item overview data (artg_id, sponsor, caution) is merged onto the item."""
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    overviews = load("pbs_sample_item_overviews.json")
    result = normalise_schedule("2026-04", items, restrictions, raw_item_overviews=overviews)

    metformin = next(i for i in result["items"] if i["pbs_code"] == "2622M")
    assert metformin["artg_id"] == "ARTG12345"
    assert metformin["sponsor"] == "Merck Serono Pty Ltd"
    assert metformin["caution"] is None

    humira = next(i for i in result["items"] if i["pbs_code"] == "1234A")
    assert humira["artg_id"] == "ARTG67890"
    assert humira["caution"] == "Biosimilar products may be available"


def test_normaliser_item_without_overview_has_none_fields():
    """Items without matching overview have None for new fields."""
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions)

    lipitor = next(i for i in result["items"] if i["pbs_code"] == "8591B")
    assert lipitor["artg_id"] is None
    assert lipitor["sponsor"] is None


def test_normaliser_fees_decimal_parsing():
    """Fee amounts are parsed to Decimal."""
    from decimal import Decimal
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    result = normalise_schedule("2026-04", items, restrictions, raw_fees=load("pbs_sample_fees.json"))

    fee = next(f for f in result["fees"] if f["fee_code"] == "FEE001")
    assert fee["amount"] == Decimal("6.42")


def test_normaliser_summary_of_changes_date_parsing():
    """Effective dates in summary_of_changes are parsed to datetime.date."""
    import datetime
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    result = normalise_schedule(
        "2026-04", items, restrictions,
        raw_summary_of_changes=load("pbs_sample_summary_of_changes.json"),
    )
    chg = result["summary_of_changes"][0]
    assert isinstance(chg["effective_date"], datetime.date)
    assert chg["effective_date"] == datetime.date(2026, 4, 1)


# ── Loader: new tables ────────────────────────────────────────────────────────

class FakePool:
    def __init__(self, conn):
        self._conn = conn
    def acquire(self):
        return FakeAcquire(self._conn)

class FakeAcquire:
    def __init__(self, c): self._c = c
    async def __aenter__(self): return self._c
    async def __aexit__(self, *a): pass


async def seed_schedule(db, month: str = "2026-04"):
    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ($1, NOW(), 'running') ON CONFLICT (month) DO NOTHING",
        month,
    )


@pytest.mark.asyncio
async def test_loader_inserts_fees(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule("2026-04", items, restrictions, raw_fees=load("pbs_sample_fees.json"))

    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM fees")
    assert count == 3


@pytest.mark.asyncio
async def test_loader_inserts_prescribing_texts(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_prescribing_texts=load("pbs_sample_prescribing_texts.json"),
    )
    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM prescribing_texts")
    assert count == 2


@pytest.mark.asyncio
async def test_loader_inserts_indications(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_indications=load("pbs_sample_indications.json"),
    )
    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM indications")
    assert count == 2


@pytest.mark.asyncio
async def test_loader_inserts_amt_items(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_amt_items=load("pbs_sample_amt_items.json"),
    )
    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM amt_items")
    assert count == 2


@pytest.mark.asyncio
async def test_loader_inserts_item_amt_relationships(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_amt_items=load("pbs_sample_amt_items.json"),
        raw_item_amt=load("pbs_sample_item_amt.json"),
    )
    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM item_amt_relationships")
    assert count == 2


@pytest.mark.asyncio
async def test_loader_inserts_program_dispensing_rules(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_program_dispensing_rules=load("pbs_sample_program_dispensing_rules.json"),
        raw_item_dispensing_rules=load("pbs_sample_item_dispensing_rules.json"),
    )
    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM program_dispensing_rules")
    assert count == 2
    link_count = await db.fetchval("SELECT COUNT(*) FROM item_dispensing_rules")
    assert link_count == 3


@pytest.mark.asyncio
async def test_loader_inserts_item_restriction_relationships(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_item_restriction_relationships=load("pbs_sample_item_restriction_relationships.json"),
    )
    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM item_restriction_relationships")
    assert count == 3


@pytest.mark.asyncio
async def test_loader_inserts_summary_of_changes(db):
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_summary_of_changes=load("pbs_sample_summary_of_changes.json"),
    )
    await load_to_database(FakePool(db), "2026-04", normalised, [])

    count = await db.fetchval("SELECT COUNT(*) FROM summary_of_changes")
    assert count == 2


@pytest.mark.asyncio
async def test_loader_expanded_idempotent(db):
    """Running the full expanded load twice produces consistent counts."""
    await seed_schedule(db)
    items = load("pbs_sample_items.json")
    restrictions = load("pbs_sample_restrictions.json")
    normalised = normalise_schedule(
        "2026-04", items, restrictions,
        raw_fees=load("pbs_sample_fees.json"),
        raw_prescribing_texts=load("pbs_sample_prescribing_texts.json"),
        raw_indications=load("pbs_sample_indications.json"),
        raw_amt_items=load("pbs_sample_amt_items.json"),
        raw_item_amt=load("pbs_sample_item_amt.json"),
        raw_item_dispensing_rules=load("pbs_sample_item_dispensing_rules.json"),
        raw_program_dispensing_rules=load("pbs_sample_program_dispensing_rules.json"),
        raw_item_restriction_relationships=load("pbs_sample_item_restriction_relationships.json"),
        raw_restriction_prescribing_text_relationships=load("pbs_sample_restriction_prescribing_text_relationships.json"),
        raw_item_prescribing_texts=load("pbs_sample_item_prescribing_texts.json"),
        raw_summary_of_changes=load("pbs_sample_summary_of_changes.json"),
    )

    pool = FakePool(db)
    await load_to_database(pool, "2026-04", normalised, [])
    fees_after_first = await db.fetchval("SELECT COUNT(*) FROM fees")
    prescribing_texts_after_first = await db.fetchval("SELECT COUNT(*) FROM prescribing_texts")

    await load_to_database(pool, "2026-04", normalised, [])
    fees_after_second = await db.fetchval("SELECT COUNT(*) FROM fees")
    prescribing_texts_after_second = await db.fetchval("SELECT COUNT(*) FROM prescribing_texts")

    assert fees_after_first == fees_after_second, "Idempotency violated: duplicate fees"
    assert prescribing_texts_after_first == prescribing_texts_after_second, "Idempotency violated: duplicate prescribing_texts"


# ── PBS Client: new methods exist ─────────────────────────────────────────────

def test_pbs_client_has_all_new_methods():
    """All new PBS client methods are present and callable."""
    client = PBSClient()
    expected_methods = [
        "get_available_schedules",
        "get_all_fees",
        "get_all_prescribing_texts",
        "get_all_indications",
        "get_all_amt_items",
        "get_all_item_overviews",
        "get_all_item_amt",
        "get_all_item_dispensing_rules",
        "get_all_program_dispensing_rules",
        "get_all_item_restriction_relationships",
        "get_all_restriction_prescribing_text_relationships",
        "get_all_item_prescribing_texts",
        "get_all_summary_of_changes",
    ]
    for method in expected_methods:
        assert hasattr(client, method), f"PBSClient missing method: {method}"
        assert callable(getattr(client, method)), f"PBSClient.{method} is not callable"
