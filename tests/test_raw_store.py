"""Tests for the raw landing layer.

The raw layer's job is to be lossless: every row of every PBS feed, stored
verbatim. These tests pin the two properties that matter — full endpoint
coverage, and idempotent replacement on re-ingest.
"""
import json

import pytest

from ingest import runner
from ingest.raw_store import FEED_ENDPOINTS, endpoint_sources, store_raw_feeds


def test_every_fetched_feed_has_an_endpoint_mapping():
    """A feed fetched but absent from FEED_ENDPOINTS would never land in raw."""
    missing = set(runner.RAW_FEEDS) - set(FEED_ENDPOINTS)
    assert not missing, f"fetched feeds with no raw endpoint mapping: {missing}"


def test_no_endpoint_mapping_without_a_fetched_feed():
    stale = set(FEED_ENDPOINTS) - set(runner.RAW_FEEDS)
    assert not stale, f"raw mappings for feeds that are never fetched: {stale}"


def test_endpoint_sources_collapses_aliases():
    """
    Some cache entries are second copies of the same endpoint: item_pricing and
    item_dispensing_rules are both /item-dispensing-rule-relationships, and
    item_amt is a second copy of /amt-items. Raw stores one copy per endpoint.
    """
    sources = endpoint_sources()
    assert len(sources) < len(FEED_ENDPOINTS), "expected aliases to collapse"
    assert set(sources) == set(FEED_ENDPOINTS.values())
    # each chosen cache name really maps to the endpoint it was chosen for
    for endpoint, cache_name in sources.items():
        assert FEED_ENDPOINTS[cache_name] == endpoint


def test_new_v378_endpoints_are_mapped():
    """PBS added these in API v3.7.8; they were previously never fetched."""
    assert FEED_ENDPOINTS["medication_charts"] == "/medication-charts"
    assert FEED_ENDPOINTS["item_chart_eligibilities"] == "/item-chart-eligibilities"


@pytest.mark.asyncio
async def test_store_raw_feeds_is_idempotent_and_lossless(db):
    """Re-running must replace rows, not append, and must preserve payloads."""
    class FakePool:
        def acquire(self): return FakeAcquire(db)

    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    rows = [
        {"pbs_code": "1234A", "nested": {"name": "Acme", "abn": None}, "n": 1},
        {"pbs_code": "5678B", "nested": {"name": "Beta", "abn": "123"}, "n": 2},
    ]

    def loader(cache_name):
        return rows if cache_name == "items" else None

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-01'")

    first = await store_raw_feeds(FakePool(), "1999-01", "9999", loader)
    assert first["/items"] == 2

    second = await store_raw_feeds(FakePool(), "1999-01", "9999", loader)
    assert second["/items"] == 2

    total = await db.fetchval(
        "SELECT count(*) FROM raw_feed_rows WHERE month = '1999-01' AND endpoint = '/items'"
    )
    assert total == 2, f"re-run appended instead of replacing: {total} rows"

    # Payload must survive verbatim, including nested objects and nulls.
    stored = await db.fetch(
        "SELECT row_index, payload FROM raw_feed_rows "
        "WHERE month = '1999-01' AND endpoint = '/items' ORDER BY row_index"
    )
    got = [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
           for r in stored]
    assert got == rows

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-01'")


# ── Raw reconciliation in the quality gate ────────────────────────────────────

@pytest.mark.asyncio
async def test_quality_gate_passes_when_raw_matches_source(db):
    """A faithful raw layer produces no raw errors."""
    from ingest.quality import check_raw_layer

    rows = [{"pbs_code": "1234A", "n": 1}, {"pbs_code": "5678B", "n": 2}]

    class FakePool:
        def acquire(self): return FakeAcquire(db)

    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-02'")
    await store_raw_feeds(FakePool(), "1999-02", "9999",
                          lambda n: rows if n == "items" else None)

    errors, _ = await check_raw_layer(db, "1999-02", lambda n: rows if n == "items" else None)
    assert [e for e in errors if "/items" in e] == []

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-02'")


@pytest.mark.asyncio
async def test_quality_gate_errors_when_raw_rows_are_missing(db):
    """
    Dropped source rows must be an error, not a warning — the runner marks the
    schedule failed on any error, so a lossy ingest cannot be marked complete.
    """
    from ingest.quality import check_raw_layer

    rows = [{"pbs_code": "1234A"}, {"pbs_code": "5678B"}, {"pbs_code": "9999Z"}]

    class FakePool:
        def acquire(self): return FakeAcquire(db)

    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-03'")
    # Store only the first two rows, simulating a lossy load.
    await store_raw_feeds(FakePool(), "1999-03", "9999",
                          lambda n: rows[:2] if n == "items" else None)

    # Reconcile against the full source: the gate must notice the shortfall.
    errors, _ = await check_raw_layer(db, "1999-03", lambda n: rows if n == "items" else None)
    items_errors = [e for e in errors if "/items" in e]
    assert items_errors, "lossy raw layer was not reported as an error"
    assert "fetched 3 rows but stored 2" in items_errors[0]

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-03'")


@pytest.mark.asyncio
async def test_quality_gate_errors_when_a_payload_is_corrupted(db):
    """Row counts alone would not catch a mangled payload."""
    from ingest.quality import check_raw_layer

    rows = [{"pbs_code": "1234A", "price": 10.5}, {"pbs_code": "5678B", "price": 20.0}]

    class FakePool:
        def acquire(self): return FakeAcquire(db)

    class FakeAcquire:
        def __init__(self, c): self._c = c
        async def __aenter__(self): return self._c
        async def __aexit__(self, *a): pass

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-04'")
    await store_raw_feeds(FakePool(), "1999-04", "9999",
                          lambda n: rows if n == "items" else None)

    # Same row count, different content.
    tampered = [{"pbs_code": "1234A", "price": 99.9}, rows[1]]
    errors, _ = await check_raw_layer(db, "1999-04", lambda n: tampered if n == "items" else None)
    assert any("payload differs" in e for e in errors), \
        f"payload corruption not detected: {errors}"

    await db.execute("DELETE FROM raw_feed_rows WHERE month = '1999-04'")
