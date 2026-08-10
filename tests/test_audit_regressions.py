"""Regression tests for the 2026-07 data & join audit.

Each test pins a specific finding from docs/AUDIT_FINDINGS.md so it cannot silently
return. The suite is organised the same way the audit is: blocking defects, then
correctness, then the guardrails that would have caught them earlier.

Why these are needed: the pre-audit suite had 221 passing tests while the system was
badly broken, because each layer was tested against its own assumptions. The ingest
tests asserted the ingest's own output; the API tests asserted only that response keys
existed, never their values; nothing tested the seam between them.
"""
import pytest
import pytest_asyncio
from pathlib import Path

from ingest.reference import (
    BENEFIT_TYPE_LABELS,
    LEGACY_BENEFIT_TYPE_TO_CODE,
    canonical_benefit_type,
    canonical_section,
    benefit_type_label,
    formulary_label,
)
from tests.test_comprehensive import (  # reuse the established harness
    seed_schedule, _insert_key, _delete_key, TEST_MONTH,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest_asyncio.fixture
async def seeded_audit(db_pool):
    """Seed the fixture schedule for audit regression tests."""
    async with db_pool.acquire() as conn:
        await seed_schedule(conn, TEST_MONTH)
    yield
    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month=$1", TEST_MONTH)
        if not sid:
            return
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
            "standard_formula_preparations", "extemporaneous_ingredients",
            "extemporaneous_tariffs", "extemporaneous_preparations", "changes",
        ]:
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE schedule_id=$1", sid)
            except Exception:
                pass
        await conn.execute("DELETE FROM schedules WHERE id=$1", sid)


@pytest_asyncio.fixture
async def ent_key(db_pool):
    k, h = await _insert_key(db_pool, "enterprise")
    yield k
    await _delete_key(db_pool, h)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3.1 — Contract tests across the ingest/API seam
#
# The single highest-value guardrail. Every value the API matches on literally must
# be a value the ingest is capable of producing.
# ══════════════════════════════════════════════════════════════════════════════

def test_benefit_type_vocabulary_is_shared():
    """[B3] Ingest output and API expectations come from one table, not two.

    The ingest wrote 'authority_required'; the API matched on 'A'. Because the
    mismatch produced empty aggregates rather than errors, 221 tests passed.
    """
    # Every legacy long-form value must resolve to a code the API knows.
    for legacy, code in LEGACY_BENEFIT_TYPE_TO_CODE.items():
        assert code in BENEFIT_TYPE_LABELS, f"{legacy} maps to unknown code {code}"
        assert canonical_benefit_type(legacy) == code

    # Codes are single letters — the form every router SQL literal compares against.
    for code in BENEFIT_TYPE_LABELS:
        assert len(code) == 1, f"benefit_type code {code!r} is not a single letter"
        assert canonical_benefit_type(code) == code


@pytest.mark.parametrize("literal", ["A", "S", "U", "R"])
def test_benefit_type_literals_used_in_sql_are_producible(literal):
    """[B3] Every benefit_type literal hardcoded in router SQL is reachable.

    market.py filters on 'A' and 'S'; drugs.py exposes U/R/A/S as query values. If
    the ingest cannot produce one of them, the corresponding metric is silently
    always zero — which is exactly what happened.
    """
    assert literal in BENEFIT_TYPE_LABELS
    assert canonical_benefit_type(literal) == literal
    assert benefit_type_label(literal) != literal, "label should differ from the raw code"


def test_section_vocabulary_is_shared():
    """[B4] PBS sends '/items'; equality comparisons expect 'items'."""
    assert canonical_section("/items") == "items"
    assert canonical_section("items") == "items"
    assert canonical_section("/item-restriction-relationships") == "item-restriction-relationships"
    assert canonical_section(None) is None


def test_formulary_labels_do_not_claim_cost_effectiveness():
    """[F6] F1/F2 is a brand-count classification, not a cost-effectiveness ranking."""
    for code in ("F1", "F2"):
        label = formulary_label(code).lower()
        assert "cost-effectiveness" not in label, (
            f"{code} label reintroduces the cost-effectiveness framing, which is wrong"
        )
    # CDL is a real value in PBS data and must not render as "Unknown".
    assert formulary_label("CDL") is not None
    assert "unknown" not in formulary_label("CDL").lower()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3.2 — Schema conformance
#
# [B1] referenced a column that had never been created; [B2] referenced a table
# alias that did not exist in the query. Both only triggered on optional query
# parameters, so they passed every smoke test.
# ══════════════════════════════════════════════════════════════════════════════

REQUIRED_COLUMNS = [
    ("item_atc_relationships", "is_primary"),      # [B1]
    ("item_atc_relationships", "atc_priority_pct"),
    ("items", "benefit_type"),
    ("items", "formulary"),
    ("items", "li_item_id"),
    ("item_pricing", "commonwealth_price"),
    ("item_pricing", "li_item_id"),
    ("restrictions", "restriction_code"),
    ("summary_of_changes", "section"),
]


@pytest.mark.parametrize("table,column", REQUIRED_COLUMNS)
async def test_required_column_exists(db, table, column):
    """Columns that router SQL depends on must exist in the schema."""
    found = await db.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = $1 AND column_name = $2",
        table, column,
    )
    assert found, f"{table}.{column} is referenced by router SQL but does not exist"


async def test_dead_market_tables_are_gone(db):
    """[D4] Migration 008's pre-computed tables were never written or read."""
    for table in ("market_atc_summary", "market_manufacturer_landscape"):
        exists = await db.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_name = $1", table
        )
        assert not exists, (
            f"{table} is back. It was never populated by the ingest nor read by any "
            f"router; reintroduce it only alongside a job that fills it."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3.4 — Aggregate invariants
#
# The fan-out bug [C1] violates a trivially checkable property: a breakdown of a
# population cannot exceed the population.
# ══════════════════════════════════════════════════════════════════════════════

async def test_atc_summary_breakdowns_do_not_exceed_total(app_client, seeded_audit, ent_key):
    """[C1] f1 + f2 and the authority counts must fit inside the item total.

    Previously items were joined to item_pricing and item_organisation_relationships
    in one pass and counted with COUNT(*), so an item with 3 sponsors and 4 pricing
    rows contributed 12. Measured inflation on live data was 3.12x.
    """
    r = await app_client.get(
        f"/v1/market/atc-summary?atc_code=A10&schedule={TEST_MONTH}",
        headers={"X-API-Key": ent_key},
    )
    if r.status_code == 404:
        pytest.skip("fixture data has no items under A10")
    assert r.status_code == 200, r.text
    d = r.json()["data"]

    total = d["unique_prescribing_rules"]
    assert d["f1_count"] + d["f2_count"] <= total, (
        f"formulary breakdown ({d['f1_count']}+{d['f2_count']}) exceeds item total "
        f"({total}) — join fan-out has returned"
    )
    assert d["authority_required_count"] <= total
    assert d["streamlined_count"] <= total
    assert d["sixty_day_items"] <= total
    assert d["biosimilar_items"] <= total


async def test_atc_summary_primary_atc_only_does_not_error(app_client, seeded_audit, ent_key):
    """[B1] This parameter referenced a non-existent column and returned HTTP 500."""
    r = await app_client.get(
        f"/v1/market/atc-summary?atc_code=A10&primary_atc_only=true&schedule={TEST_MONTH}",
        headers={"X-API-Key": ent_key},
    )
    assert r.status_code != 500, f"primary_atc_only regressed to a 500: {r.text}"
    assert r.status_code in (200, 404)


async def test_schedule_comparison_atc_prefix_does_not_error(app_client, seeded_audit, ent_key):
    """[B2] The ATC filter referenced alias 'i' in a query aliased b/t → HTTP 500."""
    r = await app_client.get(
        f"/v1/market/schedule-comparison?base_schedule={TEST_MONTH}"
        f"&target_schedule={TEST_MONTH}&atc_prefix=A10",
        headers={"X-API-Key": ent_key},
    )
    assert r.status_code != 500, f"atc_prefix regressed to a 500: {r.text}"
    assert r.status_code in (200, 404)


async def test_only_one_primary_atc_per_item(db, seeded_audit):
    """[B1]/[B6] is_primary must select exactly one ATC per item."""
    rows = await db.fetch(
        """
        SELECT pbs_code, schedule_id, COUNT(*) FILTER (WHERE is_primary) AS primaries
        FROM item_atc_relationships
        GROUP BY pbs_code, schedule_id
        HAVING COUNT(*) FILTER (WHERE is_primary) > 1
        """
    )
    assert not rows, f"{len(rows)} items have more than one primary ATC"


# ══════════════════════════════════════════════════════════════════════════════
# Correctness regressions
# ══════════════════════════════════════════════════════════════════════════════

def test_patient_charge_honours_a_zero_cap():
    """[C5] A $0 patient charge is a real PBS value, not a missing one.

    `min(dpmq, cap or dpmq)` treated 0.00 as absent and charged the patient the full
    dispensed price — the exact opposite of the correct answer for fully subsidised
    and CTG items.
    """
    from api.routers.items import patient_charge, government_contribution

    assert patient_charge(42.50, 0.0) == 0.0, "zero cap must mean the patient pays nothing"
    assert government_contribution(42.50, 0.0) == 42.50

    # Normal capping still applies.
    assert patient_charge(42.50, 31.60) == 31.60
    assert patient_charge(12.40, 31.60) == 12.40, "cheap items cost less than the cap"

    # A missing cap is distinct from a zero cap.
    assert patient_charge(42.50, None) == 42.50


def test_patient_and_government_shares_recompose():
    """[C5] Tier B derivations must reverse: patient + government == dispensed price."""
    from api.routers.items import patient_charge, government_contribution

    for dpmq, cap in [(42.50, 31.60), (12.40, 0.0), (1204.75, 31.60), (5.00, None)]:
        patient = patient_charge(dpmq, cap)
        government = government_contribution(dpmq, patient)
        assert round(patient + government, 2) == round(dpmq, 2), (
            f"dpmq={dpmq} cap={cap}: {patient} + {government} != {dpmq}"
        )


async def test_price_and_patient_cost_agree_on_concessional(app_client, seeded_audit, db_pool):
    """[C6] The same quantity was computed two different ways in two endpoints.

    /items/{code}/price returned the raw copayment uncapped; /patient-cost capped it
    at the dispensed price. For a drug cheaper than the copayment they disagreed.
    """
    k, h = await _insert_key(db_pool, "growth")
    try:
        headers = {"X-API-Key": k}
        price = await app_client.get(
            f"/v1/items/8591B/price?schedule={TEST_MONTH}", headers=headers)
        cost = await app_client.get(
            f"/v1/items/8591B/patient-cost?schedule={TEST_MONTH}", headers=headers)
        if price.status_code != 200 or cost.status_code != 200:
            pytest.skip("fixture item 8591B unavailable")

        contexts = price.json()["data"]["pricing_contexts"]
        if not contexts:
            pytest.skip("no pricing rows for 8591B")

        con_from_price = contexts[0]["patient_outcome"]["concessional_patient_charge"]
        con_from_cost = cost.json()["data"]["concessional_patient"]["you_pay"]
        dpmq = contexts[0]["commonwealth_price"]

        # Both must be capped by the dispensed price.
        if dpmq is not None:
            assert con_from_price <= dpmq + 0.001
            assert con_from_cost <= dpmq + 0.001
    finally:
        await _delete_key(db_pool, h)


async def test_patient_cost_discloses_its_price_basis(app_client, seeded_audit, db_pool):
    """[H1]/[C2] The endpoint had no meta block at all.

    It selects a single representative pricing row (highest price) and falls back to
    a different price column when pricing is unavailable. Both are Tier C choices the
    audit spec requires be disclosed.
    """
    k, h = await _insert_key(db_pool, "growth")
    try:
        r = await app_client.get(
            f"/v1/items/2622M/patient-cost?schedule={TEST_MONTH}",
            headers={"X-API-Key": k},
        )
        if r.status_code != 200:
            pytest.skip("fixture item 2622M unavailable")
        meta = r.json().get("meta")
        assert meta, "patient-cost must return a meta block"
        assert "price_basis" in meta, "caller cannot tell which price column was used"
        assert "tier" in meta and "schedule_code" in meta
        assert "caveat" in meta, "safety-net estimate must be labelled as an estimate"
    finally:
        await _delete_key(db_pool, h)


async def test_restriction_lookup_is_deterministic_and_lists_all_items(
    app_client, seeded_audit, db_pool
):
    """[C4] restriction_code is not unique — one code mapped to up to 2,124 items.

    fetchrow with no ORDER BY returned an arbitrary copy and presented that copy's
    pbs_code as though it were the item for the restriction.
    """
    k, h = await _insert_key(db_pool, "growth")
    try:
        headers = {"X-API-Key": k}
        async with db_pool.acquire() as conn:
            code = await conn.fetchval(
                """
                SELECT r.restriction_code
                FROM restrictions r JOIN items i ON i.id = r.item_id
                JOIN schedules s ON s.id = i.schedule_id
                WHERE s.month = $1 AND r.restriction_code IS NOT NULL
                GROUP BY r.restriction_code ORDER BY COUNT(*) DESC LIMIT 1
                """,
                TEST_MONTH,
            )
        if not code:
            pytest.skip("no restrictions in fixture data")

        first = await app_client.get(
            f"/v1/restrictions/{code}?schedule={TEST_MONTH}", headers=headers)
        second = await app_client.get(
            f"/v1/restrictions/{code}?schedule={TEST_MONTH}", headers=headers)
        assert first.status_code == 200 and second.status_code == 200

        d1 = first.json().get("data", first.json())
        d2 = second.json().get("data", second.json())
        assert d1["pbs_code"] == d2["pbs_code"], "repeated lookups returned different items"
        assert "applies_to_pbs_codes" in d1, "full item list must be exposed"
        assert d1["applies_to_item_count"] >= 1
        assert d1["pbs_code"] in d1["applies_to_pbs_codes"]
    finally:
        await _delete_key(db_pool, h)


async def test_safety_net_burden_stats_cover_whole_population(app_client, seeded_audit, ent_key):
    """[C7] min/max/mean were computed over the LIMIT 500 slice, not all items."""
    r = await app_client.get(
        f"/v1/market/safety-net-burden?schedule={TEST_MONTH}",
        headers={"X-API-Key": ent_key},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    if not d or not d.get("items"):
        pytest.skip("no priced items in fixture data")
    stats = d["scripts_to_safety_net"]
    assert "population_item_count" in stats, "stats must state their population size"
    assert stats["population_item_count"] >= len(d["items"])
    if stats["min"] is not None:
        assert stats["min"] <= stats["mean"] <= stats["max"]


async def test_price_pressure_index_publishes_signals_and_weights(
    app_client, seeded_audit, ent_key
):
    """[C8] A composite score with undocumented weights is not auditable."""
    r = await app_client.get(
        f"/v1/market/price-pressure-index?schedule={TEST_MONTH}",
        headers={"X-API-Key": ent_key},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert "signals" in d and "weights" in d, "raw signals and weights must be published"
    assert abs(sum(d["weights"].values()) - 1.0) < 1e-9, "weights must sum to 1"
    assert set(d["signals"]) == set(d["weights"]), "every signal needs a stated weight"
    assert "caveat" in r.json()["meta"]


# ══════════════════════════════════════════════════════════════════════════════
# Tier C disclosure — audit spec §0.1 requires heuristics be labelled
# ══════════════════════════════════════════════════════════════════════════════

async def test_authority_workflow_declares_itself_an_interpretation(
    app_client, seeded_audit, db_pool
):
    """[H2] The generated checklist is ours, not a PBS-issued procedure."""
    k, h = await _insert_key(db_pool, "scale")
    try:
        r = await app_client.get(
            f"/v1/drugs/2622M/authority-workflow?schedule={TEST_MONTH}",
            headers={"X-API-Key": k},
        )
        if r.status_code != 200:
            pytest.skip("fixture item unavailable")
        assert "workflow_note" in r.json()["data"], (
            "authority workflow must state it is an interpretation"
        )
    finally:
        await _delete_key(db_pool, h)


async def test_substitution_warns_strength_and_form_are_not_matched(
    app_client, seeded_audit, db_pool
):
    """[H3] Same-ingredient matching ignores strength and form.

    A 500 mg IR tablet is returned as a substitute for a 1 g MR tablet. This is the
    finding with genuine patient-safety exposure, so the caveat is asserted verbatim.
    """
    k, h = await _insert_key(db_pool, "scale")
    try:
        for path in ("substitution", "60-day-pair"):
            r = await app_client.get(
                f"/v1/drugs/2622M/{path}?schedule={TEST_MONTH}",
                headers={"X-API-Key": k},
            )
            if r.status_code != 200:
                continue
            note = r.json()["data"].get("note", "").upper()
            assert "STRENGTH" in note and "FORM" in note, (
                f"/{path} must warn that strength and form are not matched"
            )
    finally:
        await _delete_key(db_pool, h)


async def test_brand_lists_carry_real_per_brand_names_and_manufacturers(
    app_client, seeded_audit, db_pool
):
    """[F8] superseded: brand lists now carry the real per-brand name and sponsor.

    Every li_item_id used to be returned with the same item-level brand name, disclosed
    by brand_name_note. Migration 011 rekeyed items to brand grain, so the real values
    are a 1:1 join on li_item_id away. pbs.gov.au confirms the shape: it lists a
    manufacturer per brand — all 8 brands of 13842M matched items.sponsor exactly.
    The note now appears only when there is genuinely no per-brand row.
    """
    k, h = await _insert_key(db_pool, "scale")
    try:
        headers = {"X-API-Key": k}
        inline = await app_client.get(
            f"/v1/drugs/2622M?include_brands=true&schedule={TEST_MONTH}", headers=headers)
        assert inline.status_code == 200, inline.text
        brands = inline.json()["data"]["brands"]
        assert brands, "expected per-brand rows"
        assert all("manufacturer" in b for b in brands)
        # Real per-brand rows must not be accompanied by the "no per-brand data" note.
        if brands[0]["li_item_id"] is not None:
            assert "brand_name_note" not in inline.json()["data"]

        profile = await app_client.get(
            f"/v1/drugs/2622M/full-profile?schedule={TEST_MONTH}", headers=headers)
        assert profile.status_code == 200, profile.text
        assert all("manufacturer" in b for b in profile.json()["data"]["brands"]["items"])
    finally:
        await _delete_key(db_pool, h)


async def test_brand_count_is_not_fabricated(app_client, seeded_audit, db_pool):
    """[F8] brand_count used `or 1`, asserting a brand for items with no pricing."""
    k, h = await _insert_key(db_pool, "growth")
    try:
        r = await app_client.get(
            f"/v1/drugs/9999X?schedule={TEST_MONTH}", headers={"X-API-Key": k})
        if r.status_code != 200:
            pytest.skip("no unpriced fixture item available")
        async with db_pool.acquire() as conn:
            actual = await conn.fetchval(
                """SELECT COUNT(DISTINCT ip.li_item_id) FROM item_pricing ip
                   JOIN schedules s ON s.id = ip.schedule_id
                   WHERE ip.pbs_code = '9999X' AND s.month = $1""",
                TEST_MONTH,
            ) or 0
        assert r.json()["data"]["pricing_summary"]["brand_count"] == actual
    finally:
        await _delete_key(db_pool, h)


# ══════════════════════════════════════════════════════════════════════════════
# Schedule-changes classification [B4] [B5] [B6]
# ══════════════════════════════════════════════════════════════════════════════

def test_item_changes_classify_as_listings_not_other():
    """[B4] section == 'items' never matched '/items', so delistings — graded HIGH —
    were reported as OTHER_MODIFICATION / LOW."""
    from api.routers.schedule_changes import _classify_change

    for section in ("items", "/items"):
        assert _classify_change("INSERT", section) == ("NEW_LISTING", "MEDIUM")
        assert _classify_change("DELETE", section) == ("DELISTING", "HIGH")
        assert _classify_change("UPDATE", section) == ("FORMULARY_CHANGE", "MEDIUM")

    # Substring-matched sections were unaffected and must stay that way.
    assert _classify_change("UPDATE", "/item-pricing-events")[0] == "PRICE_CHANGE"
    assert _classify_change("INSERT", "/item-restriction-relationships")[0] == "RESTRICTION_CHANGE"


async def test_new_listings_and_delistings_are_not_empty(app_client, seeded_audit, db_pool):
    """[B4] Both endpoints returned zero rows for every schedule."""
    k, h = await _insert_key(db_pool, "scale")
    try:
        headers = {"X-API-Key": k}
        async with db_pool.acquire() as conn:
            expected_new = await conn.fetchval(
                """SELECT COUNT(*) FROM summary_of_changes sc
                   JOIN schedules s ON s.id = sc.schedule_id
                   WHERE s.month = $1 AND sc.change_type = 'INSERT'
                     AND LTRIM(sc.section, '/') = 'items'""",
                TEST_MONTH,
            )
        if not expected_new:
            pytest.skip("fixture has no item INSERT changes")

        r = await app_client.get(
            f"/v1/schedule-changes/{TEST_MONTH}/new-listings", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["meta"]["total"] == expected_new, (
            "new-listings total does not match the underlying change records"
        )
    finally:
        await _delete_key(db_pool, h)


async def test_delistings_resolve_names_from_previous_schedule(
    app_client, seeded_audit, db_pool
):
    """[B5] Delisted items were looked up in the schedule they were removed from,
    so drug_name was always null and therapeutic_alternatives always []."""
    k, h = await _insert_key(db_pool, "scale")
    try:
        r = await app_client.get(
            f"/v1/schedule-changes/{TEST_MONTH}/delistings", headers={"X-API-Key": k})
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        if not rows:
            pytest.skip("fixture has no delistings")
        # The heuristic caveat must be present regardless of whether alternatives
        # were found for this particular fixture.
        assert "therapeutic_alternatives_note" in rows[0]
        # Any item still present in this schedule must resolve a name.
        for row in rows:
            if row["pbs_code"] == "2622M":
                assert row["drug_name"] is not None, (
                    "delisting name resolution regressed to null"
                )
    finally:
        await _delete_key(db_pool, h)


async def test_new_listings_primary_atc_is_highest_priority(db, seeded_audit):
    """[B6] A dict comprehension over a priority-ordered result kept the LAST row,
    returning the lowest-priority ATC as 'primary'."""
    rows = await db.fetch(
        """
        SELECT DISTINCT ON (iar.pbs_code) iar.pbs_code, iar.atc_code, iar.atc_priority_pct
        FROM item_atc_relationships iar
        JOIN schedules s ON s.id = iar.schedule_id
        WHERE s.month = $1
        ORDER BY iar.pbs_code, iar.is_primary DESC, iar.atc_priority_pct DESC NULLS LAST
        """,
        TEST_MONTH,
    )
    for row in rows:
        best = await db.fetchval(
            """
            SELECT MAX(atc_priority_pct) FROM item_atc_relationships iar
            JOIN schedules s ON s.id = iar.schedule_id
            WHERE s.month = $1 AND iar.pbs_code = $2
            """,
            TEST_MONTH, row["pbs_code"],
        )
        if best is not None and row["atc_priority_pct"] is not None:
            assert row["atc_priority_pct"] == best, (
                f"{row['pbs_code']} selected a non-primary ATC as primary"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Phase 0 — the ingest wiring that caused a third of the audit findings
# ══════════════════════════════════════════════════════════════════════════════

def test_runner_fetches_the_pricing_overview_and_amt_feeds():
    """§0.1 Three client methods existed but the runner never called them, leaving
    item_pricing, item_amt_relationships and the overview fields permanently empty.

    get_all_item_dispensing_rule_relationships (pricing) sits next to the
    similarly-named get_all_item_dispensing_rules (rule links); calling only the
    latter is what produced 'no price data anywhere in the system'.
    """
    source = (Path(__file__).parent.parent / "ingest" / "runner.py").read_text()
    for method in (
        "get_all_item_dispensing_rule_relationships",
        "get_all_item_overviews",
        "get_all_item_amt",
    ):
        assert method in source, f"runner.py no longer calls {method}"

    assert "raw_item_pricing=raw_item_pricing" in source, (
        "the pricing feed is fetched but not passed to the normaliser"
    )
    # The hardcoded empties that caused the original bug must not come back.
    assert "raw_item_amt = []" not in source
    assert "raw_item_overviews = []" not in source


def test_loader_runs_in_a_transaction():
    """§0.2 A partial load left a schedule holding 6,883 items while flagged failed,
    which every downstream query then treated as complete data."""
    source = (Path(__file__).parent.parent / "ingest" / "loader.py").read_text()
    assert "async with conn.transaction():" in source, (
        "load_to_database must be atomic — a schedule loads fully or not at all"
    )


async def test_item_pricing_fixture_reaches_the_database(db, seeded_audit):
    """The pricing fixture must actually land, or every pricing test is vacuous."""
    count = await db.fetchval(
        """SELECT COUNT(*) FROM item_pricing ip
           JOIN schedules s ON s.id = ip.schedule_id WHERE s.month = $1""",
        TEST_MONTH,
    )
    assert count > 0, "item_pricing is empty — pricing endpoints are untested"


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3.3 — Post-ingest data quality gate
#
# The audit's most damning operational finding: a schedule could be marked
# `complete` while holding zero items, and nothing anywhere reported it.
# ══════════════════════════════════════════════════════════════════════════════

async def test_quality_gate_flags_an_empty_schedule(db):
    """[D1] A completed schedule with no items must not pass the quality gate."""
    from ingest.quality import check_schedule

    sid = await db.fetchval(
        "INSERT INTO schedules (month, released_at, ingest_status) "
        "VALUES ('2098-01', NOW(), 'running') RETURNING id"
    )
    report = await check_schedule(db, "2098-01", str(sid))
    assert not report.ok, "an empty schedule was reported as usable"
    assert any("items" in e for e in report.errors)


async def test_quality_gate_accepts_a_seeded_schedule(db_pool, seeded_audit):
    """The gate must not cry wolf on a schedule that is genuinely fine."""
    from ingest.quality import check_schedule

    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month = $1", TEST_MONTH)
        report = await check_schedule(conn, TEST_MONTH, str(sid))
    assert report.ok, f"seeded schedule rejected: {report.errors}"
    assert report.table_counts["items"] > 0
    assert report.table_counts["item_pricing"] > 0


async def test_quality_gate_catches_a_never_populated_column(db_pool, seeded_audit):
    """[F3] A column that is 100% null is a mapping failure, not drift.

    program_code was chosen because it is nullable and blocking. authority_method
    and li_item_id are also 100% null in production but are deliberately non-blocking
    until the domain review confirms whether PBS supplies them at all.
    """
    from ingest.quality import check_schedule

    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month = $1", TEST_MONTH)
        await conn.execute(
            "UPDATE items SET program_code = NULL WHERE schedule_id = $1", sid
        )
        try:
            report = await check_schedule(conn, TEST_MONTH, str(sid))
        finally:
            await conn.execute(
                "UPDATE items SET program_code = 'GE' "
                "WHERE schedule_id = $1 AND program_code IS NULL",
                sid,
            )
    assert not report.ok
    assert any("program_code" in e and "never populated" in e for e in report.errors)


async def test_quality_gate_blocks_on_null_authority_method(db_pool, seeded_audit):
    """authority_method is no longer an open question, so a null one must block.

    It was 100% null because the loader's INSERT omitted the column, not because PBS
    withheld it — the feed populates it on every restriction. The gate previously only
    warned; now that the source field is confirmed, a regression to all-null is an
    error. Notes and cautions legitimately carry no authority method, so the threshold
    sits above their share rather than at zero.
    """
    from ingest.quality import check_schedule, NULL_RATE_LIMITS

    limit = next(l for l in NULL_RATE_LIMITS if l[0] == "restrictions" and l[1] == "authority_method")
    assert limit[3] is True, "authority_method must block, not warn"

    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month = $1", TEST_MONTH)
        report = await check_schedule(conn, TEST_MONTH, str(sid))
        assert report.ok, f"seeded data should pass the gate: {report.errors}"

        # Mutation: blank every authority_method, exactly what the dropped column did.
        await conn.execute(
            "UPDATE restrictions SET authority_method = NULL WHERE item_id IN "
            "(SELECT id FROM items WHERE schedule_id = $1)", sid,
        )
        regressed = await check_schedule(conn, TEST_MONTH, str(sid))

    assert not regressed.ok, "an all-null authority_method must fail the gate"
    assert any("authority_method" in e for e in regressed.errors)


def test_runner_gates_completion_on_quality():
    """§0.5 A schedule must not be marked complete without passing the gate."""
    source = (Path(__file__).parent.parent / "ingest" / "runner.py").read_text()
    assert "assert_schedule_usable" in source
    gate = source.index("assert_schedule_usable")
    complete = source.index("ingest_status = 'complete'")
    assert gate < complete, "quality gate must run before the schedule is marked complete"


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5.3 — Response cache
# ══════════════════════════════════════════════════════════════════════════════

def test_cache_honours_ttl_and_bounds():
    """[D5] The stored timestamp was read and discarded — entries never expired,
    and nothing bounded the dict."""
    import api.cache as cache_mod

    cache_mod.cache_clear()
    cache_mod.cache_set("k", {"v": 1}, "sched-1")
    assert cache_mod.cache_get("k") == {"v": 1}

    # Expired entries are dropped rather than served indefinitely.
    original = cache_mod._ttl_seconds
    cache_mod._ttl_seconds = lambda: -1.0
    try:
        assert cache_mod.cache_get("k") is None, "TTL is not enforced"
    finally:
        cache_mod._ttl_seconds = original

    # Size is bounded.
    cache_mod.cache_clear()
    for i in range(cache_mod.MAX_ENTRIES + 50):
        cache_mod.cache_set(f"key-{i}", i, "sched-1")
    assert cache_mod.cache_stats()["entries"] <= cache_mod.MAX_ENTRIES
    cache_mod.cache_clear()


def test_cache_invalidation_still_works():
    import api.cache as cache_mod

    cache_mod.cache_clear()
    cache_mod.cache_set("a", 1, "sched-1")
    cache_mod.cache_set("b", 2, "sched-2")
    cache_mod.cache_invalidate_schedule("sched-1")
    assert cache_mod.cache_get("a") is None
    assert cache_mod.cache_get("b") == 2
    cache_mod.cache_clear()


# ══════════════════════════════════════════════════════════════════════════════
# Restrictions, notes and cautions — validated against pbs.gov.au 2026-08-10
#
# /restrictions returns three different things and we served all three as
# restrictions. pbs.gov.au shows one restriction for item 10001J (Rifaximin) where
# this API returned three; the two extras were notes. On 2026-06 that was 10,148
# restrictions against 11,939 notes and 965 cautions — a 2.3x overstatement on a
# clinical endpoint.
#
# PBS marks the difference three independent ways and all three agree exactly, so
# any one of them alone would do; the code prefers the explicit flags.
# ══════════════════════════════════════════════════════════════════════════════

def test_restriction_kind_from_explicit_flags():
    from ingest.reference import restriction_kind

    assert restriction_kind({"res_code": "7606_N", "note_indicator": "Y"}) == "note"
    assert restriction_kind({"res_code": "9001_C", "caution_indicator": "Y"}) == "caution"
    assert restriction_kind(
        {"res_code": "17266_4306_R", "note_indicator": "N", "caution_indicator": "N"}
    ) == "restriction"


def test_restriction_kind_falls_back_to_res_code_suffix():
    """The data dictionary ties the flag to the suffix: note_indicator "Relates to N
    suffix in res_code". Either signal alone must classify correctly."""
    from ingest.reference import restriction_kind

    assert restriction_kind({"res_code": "7606_N"}) == "note"
    assert restriction_kind({"res_code": "9001_C"}) == "caution"
    assert restriction_kind({"res_code": "17266_4306_R"}) == "restriction"
    # Unknown shape defaults to restriction, matching the feed's own default.
    assert restriction_kind({"res_code": "12345"}) == "restriction"
    assert restriction_kind({}) == "restriction"


def test_authority_method_accepts_both_published_forms():
    """The dictionary (v3.6.5) documents R/A/S; the live API (v3.7.8) sends the long
    form. Storage is the PBS letter, matching how benefit_type is handled."""
    from ingest.reference import canonical_authority_method, is_authority_required

    assert canonical_authority_method("AUTHORITY_REQUIRED") == "A"
    assert canonical_authority_method("STREAMLINED") == "S"
    assert canonical_authority_method("RESTRICTED") == "R"
    assert canonical_authority_method("A") == "A"
    assert canonical_authority_method(None) is None
    # An unrecognised value must surface as itself, never as a guess.
    assert canonical_authority_method("NEW_PBS_VALUE") == "NEW_PBS_VALUE"

    # A Restricted Benefit carries criteria but needs no authority approval.
    assert is_authority_required("AUTHORITY_REQUIRED") is True
    assert is_authority_required("STREAMLINED") is True
    assert is_authority_required("RESTRICTED") is False
    assert is_authority_required(None) is False


def test_normaliser_tags_kind_and_keeps_note_text():
    """li_html_text is null for notes and cautions by design — the dictionary says
    they "are not legal". Reading only that field left 56% of rows with no text."""
    from ingest.normaliser import normalise_schedule
    import json

    items = json.loads((FIXTURES / "pbs_sample_items.json").read_text())
    restrictions = json.loads((FIXTURES / "pbs_sample_restrictions.json").read_text())
    rels = json.loads((FIXTURES / "pbs_sample_item_restriction_relationships.json").read_text())

    result = normalise_schedule(
        "2026-04", items, restrictions, raw_item_restriction_relationships=rels
    )
    metformin = next(i for i in result["items"] if i["pbs_code"] == "2622M")
    by_code = {r["restriction_code"]: r for r in metformin["restrictions"]}

    assert by_code["4236_4236_R"]["restriction_kind"] == "restriction"
    assert by_code["7606_N"]["restriction_kind"] == "note"
    assert by_code["9001_C"]["restriction_kind"] == "caution"

    # Every kind carries text, and the note's comes from schedule_html_text.
    assert all(r["restriction_text"] for r in metformin["restrictions"])
    assert "maximum quantity" in by_code["7606_N"]["restriction_text"]

    # authority_method is stored as the PBS letter and only restrictions have one.
    assert by_code["4236_4236_R"]["authority_method"] == "S"
    assert by_code["7606_N"]["authority_method"] is None
    # Streamlined code is treatment_of_code, not a copy of res_code.
    assert by_code["4236_4236_R"]["streamlined_code"] == "4236"
    assert by_code["7606_N"]["streamlined_code"] is None


async def test_loader_persists_authority_method(db_pool, seeded_audit):
    """The normaliser always computed authority_method; the loader's INSERT column
    list omitted it, so it measured 100% null while the feed populated every row."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.restriction_code, r.restriction_kind, r.authority_method,
                   r.authority_required, r.restriction_text, r.streamlined_code
            FROM restrictions r JOIN items i ON i.id = r.item_id
            JOIN schedules s ON s.id = i.schedule_id
            WHERE s.month = $1
            """,
            TEST_MONTH,
        )

    assert rows, "fixture schedule should have restrictions"
    by_code = {r["restriction_code"]: r for r in rows}

    assert by_code["4236_4236_R"]["authority_method"] == "S"
    assert by_code["4236_4236_R"]["authority_required"] is True
    assert by_code["4236_4236_R"]["streamlined_code"] == "4236"
    assert by_code["8176_8176_R"]["authority_method"] == "A"

    # Every row is classified and every row has text.
    assert all(r["restriction_kind"] in ("restriction", "note", "caution") for r in rows)
    assert all(r["restriction_text"] for r in rows)


async def test_restrictions_endpoint_tags_type_and_counts_restrictions_only(
    app_client, seeded_audit, ent_key
):
    """The endpoint returns all three kinds tagged, but restriction_count counts only
    restrictions — it previously counted notes and cautions as restrictions too."""
    r = await app_client.get(
        f"/v1/drugs/2622M/restrictions?schedule={TEST_MONTH}",
        headers={"X-API-Key": ent_key},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    kinds = {e["restriction_code"]: e["type"] for e in data["restrictions"]}
    assert kinds["4236_4236_R"] == "restriction"
    assert kinds["7606_N"] == "note"
    assert kinds["9001_C"] == "caution"

    assert data["restriction_count"] == 1, "notes and cautions are not restrictions"
    assert data["note_count"] == 1
    assert data["caution_count"] == 1
    assert len(data["restrictions"]) == 3, "all three kinds are still returned"

    # The clinical fields the endpoint used to return as null/false for an authority
    # listing, plus the text it never returned at all.
    entry = next(e for e in data["restrictions"] if e["type"] == "restriction")
    assert entry["authority_method"] == "S"
    assert entry["authority_method_label"] == "Authority Required (Streamlined)"
    assert entry["authority_required"] is True
    assert entry["restriction_text"]


async def test_authority_workflow_excludes_notes_and_cautions(
    app_client, seeded_audit, ent_key
):
    """A note carries no authority, so a workflow built over one emitted "No authority
    required" for advisory text that was never a restriction."""
    r = await app_client.get(
        f"/v1/drugs/2622M/authority-workflow?schedule={TEST_MONTH}",
        headers={"X-API-Key": ent_key},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    codes = [w["restriction_code"] for w in data["workflows"]]
    assert "4236_4236_R" in codes
    assert "7606_N" not in codes
    assert "9001_C" not in codes


# ══════════════════════════════════════════════════════════════════════════════
# Dispensing rules and manufacturers — validated against pbs.gov.au 2026-08-10
#
# The mnemonic is not a key. The data dictionary calls dispensing_rule_reference the
# "Unique identifier for a Dispensing Rule" and names it the primary key of both
# /dispensing-rules and /item-dispensing-rule-relationships, while the mnemonic only
# identifies a "GROUP of Dispensing Rules". Keying on it collapsed 17 rules to 10.
#
# pbs.gov.au lists a manufacturer per brand — all 8 brands of 13842M matched
# items.sponsor exactly — so there is no "primary" manufacturer in PBS's model.
# ══════════════════════════════════════════════════════════════════════════════

def test_yn_accepts_both_pbs_boolean_spellings():
    """PBS spells booleans Y/N in most feeds but true/false in /dispensing-rules.

    A Y/N-only test read "true" as False, so all 17 dispensing rules came out
    non-community-pharmacy — including the five carrying the DPMQ pbs.gov.au publishes.
    """
    from ingest.normaliser import yn

    for truthy in ("Y", "y", "Yes", "true", "TRUE", "True", "1", True):
        assert yn(truthy) is True, f"{truthy!r} should be truthy"
    for falsy in ("N", "n", "false", "FALSE", "No", "0", "", None, False):
        assert yn(falsy) is False, f"{falsy!r} should be falsy"


def test_dispensing_rules_keyed_on_reference_not_mnemonic():
    from ingest.normaliser import normalise_schedule
    import json

    items = json.loads((FIXTURES / "pbs_sample_items.json").read_text())
    restrictions = json.loads((FIXTURES / "pbs_sample_restrictions.json").read_text())
    result = normalise_schedule(
        "2026-04", items, restrictions,
        raw_program_dispensing_rules=json.loads(
            (FIXTURES / "pbs_sample_program_dispensing_rules.json").read_text()),
        raw_item_dispensing_rules=json.loads(
            (FIXTURES / "pbs_sample_item_dispensing_rules.json").read_text()),
    )

    rules = {r["rule_code"]: r for r in result["program_dispensing_rules"]}
    # Two references share the s90-cp mnemonic; keying on the mnemonic lost one.
    assert set(rules) == {"rp-s90-cp", "in-s90-cp", "rp-s94-public"}
    assert rules["rp-s90-cp"]["dispensing_rule_mnem"] == "s90-cp"
    assert rules["in-s90-cp"]["dispensing_rule_mnem"] == "s90-cp"
    assert rules["rp-s90-cp"]["dispensing_rule_title"] == "Community Pharmacy"
    assert rules["rp-s90-cp"]["community_pharmacy_indicator"] is True
    assert rules["rp-s94-public"]["community_pharmacy_indicator"] is False

    links = result["item_dispensing_rules"]
    assert {l["rule_code"] for l in links} == {"rp-s90-cp", "in-s90-cp", "rp-s94-public"}
    # PBS keys this feed on li_item_id and never sends pbs_code.
    assert {l["pbs_code"] for l in links} == {"2622M", "1234A"}


async def test_dispensing_rule_endpoint_resolves_reference_and_mnemonic(
    app_client, seeded_audit, ent_key
):
    headers = {"X-API-Key": ent_key}

    exact = await app_client.get(
        f"/v1/dispensing-rules/rp-s90-cp?schedule={TEST_MONTH}", headers=headers)
    assert exact.status_code == 200, exact.text
    assert exact.json()["matched_on"] == "dispensing_rule_reference"
    assert len(exact.json()["matched_rules"]) == 1

    # The mnemonic names a group, so it resolves to every rule in it. This is what
    # keeps callers written against the pre-014 vocabulary working.
    group = await app_client.get(
        f"/v1/dispensing-rules/s90-cp?schedule={TEST_MONTH}", headers=headers)
    assert group.status_code == 200, group.text
    assert group.json()["matched_on"] == "dispensing_rule_mnem"
    assert {m["rule_code"] for m in group.json()["matched_rules"]} == {"rp-s90-cp", "in-s90-cp"}


async def test_drug_endpoints_report_manufacturer_per_brand(
    app_client, seeded_audit, ent_key
):
    """PBS lists a manufacturer per brand; primary_manufacturer was always null.

    It joined items.organisation_id, which is 0% populated, while items.sponsor is
    98.9% populated and is one of the joins validated against PBS's item-overview.
    """
    headers = {"X-API-Key": ent_key}

    r = await app_client.get(
        f"/v1/drugs/2622M/brands?schedule={TEST_MONTH}", headers=headers)
    assert r.status_code == 200, r.text
    brands = r.json()["data"]["brands"]
    assert brands
    assert all("manufacturer" in b for b in brands)

    detail = await app_client.get(f"/v1/drugs/2622M?schedule={TEST_MONTH}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["primary_manufacturer"] is not None
    assert "per brand" in data["primary_manufacturer_note"]


async def test_primary_manufacturer_falls_back_to_sponsor(
    app_client, seeded_audit, ent_key, db_pool
):
    """The organisations join covers 1.5% of items, so it misses almost always.

    The fixture happens to have an organisations row, which is exactly why this needs
    its own test: with the row present the fallback never runs, and primary_manufacturer
    was null across production while items.sponsor sat populated at 98.9%.
    """
    headers = {"X-API-Key": ent_key}
    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month = $1", TEST_MONTH)
        await conn.execute(
            "DELETE FROM item_organisation_relationships "
            "WHERE pbs_code = '2622M' AND schedule_id = $1", sid,
        )

    r = await app_client.get(f"/v1/drugs/2622M?schedule={TEST_MONTH}", headers=headers)
    assert r.status_code == 200, r.text
    manufacturer = r.json()["data"]["primary_manufacturer"]
    assert manufacturer is not None, "must fall back to items.sponsor, not return null"
    assert manufacturer["name"] == "Merck Serono Pty Ltd"


# ══════════════════════════════════════════════════════════════════════════════
# Tier A join validation runs inside the quality gate
#
# Every other check in ingest/quality.py is internal consistency — orphan counts,
# null rates, row volumes — and none of them can catch a join that is coherently
# wrong. This one compares against PBS's own pre-joined /item-overview, and running
# it in the gate means join drift fails an ingest instead of waiting for someone to
# run the script.
# ══════════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def seeded_with_raw_overview(db_pool, seeded_audit):
    """Seed the fixture schedule *and* the raw /item-overview rows it implies.

    A real ingest writes both: normalised tables via the loader, and raw_feed_rows via
    ingest/raw_store. seed_schedule only does the first, so the join validation would
    find no overview and warn instead of comparing. The fixture mirrors exactly what
    the other fixtures load, so a clean seed must validate clean.
    """
    import json
    overviews = json.loads((FIXTURES / "pbs_sample_item_overviews.json").read_text())
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM raw_feed_rows WHERE month = $1", TEST_MONTH)
        for i, row in enumerate(overviews):
            await conn.execute(
                "INSERT INTO raw_feed_rows (month, schedule_code, endpoint, row_index, payload) "
                "VALUES ($1, $2, '/item-overview', $3, $4)",
                TEST_MONTH, "9999", i, json.dumps(row),
            )
    yield
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM raw_feed_rows WHERE month = $1", TEST_MONTH)


def test_quality_gate_runs_tier_a_join_validation():
    """The gate must call the validator; a validator nobody runs is worth nothing."""
    import inspect
    from ingest import quality

    source = inspect.getsource(quality.check_schedule)
    assert "check_tier_a_joins" in source, (
        "check_schedule no longer runs Tier A join validation"
    )


async def test_clean_seed_agrees_with_pbs_on_every_tier_a_join(
    db_pool, seeded_with_raw_overview
):
    """The baseline: a correct load must report zero disagreement on all 8 joins."""
    from ingest.quality import check_tier_a_joins

    async with db_pool.acquire() as conn:
        errors, warnings = await check_tier_a_joins(conn, TEST_MONTH)

    assert not errors, f"a clean seed must match its own overview: {errors}"
    assert not any("not validated" in w for w in warnings), (
        f"the check must actually run, not skip: {warnings}"
    )


async def test_join_mismatch_is_a_blocking_error(db_pool, seeded_with_raw_overview):
    """A join that disagrees with PBS must be an error, so a blocking gate refuses.

    Mutation-verified end to end against real data too: dropping five
    item_atc_relationships rows made run_ingest exit 1 and left the schedule 'failed'.
    """
    from ingest.quality import check_tier_a_joins

    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month = $1", TEST_MONTH)

        # Assert a link PBS's overview does not carry.
        await conn.execute(
            "INSERT INTO item_atc_relationships (schedule_id, pbs_code, atc_code, is_primary) "
            "VALUES ($1, '2622M', 'Z99ZZ99', false)", sid,
        )
        extra_errors, _ = await check_tier_a_joins(conn, TEST_MONTH)

        # And drop one PBS does carry.
        await conn.execute(
            "DELETE FROM item_atc_relationships WHERE schedule_id = $1 AND atc_code = 'Z99ZZ99'", sid)
        await conn.execute(
            "DELETE FROM item_restriction_relationships "
            "WHERE schedule_id = $1 AND restriction_code = '8176_8176_R'", sid)
        missing_errors, _ = await check_tier_a_joins(conn, TEST_MONTH)

    assert any("item_atc_relationships" in e and "1 extra" in e for e in extra_errors), extra_errors
    assert any("item_restriction_relationships" in e and "1 missing" in e
               for e in missing_errors), missing_errors


async def test_missing_overview_warns_rather_than_blocking(db_pool):
    """No overview rows means the check could not run — report it, never pass silently.

    It warns rather than errors because check_raw_layer already owns feed completeness;
    erroring here would report a single missing feed twice.
    """
    from ingest.quality import check_tier_a_joins

    async with db_pool.acquire() as conn:
        errors, warnings = await check_tier_a_joins(conn, "1999-01")

    assert not errors
    assert any("not validated" in w for w in warnings), warnings


async def test_join_mismatch_makes_the_whole_report_fail(db_pool, seeded_with_raw_overview):
    """A join disagreement must reach report.errors, so a blocking gate refuses.

    check_tier_a_joins returning errors is not enough on its own — check_schedule has
    to propagate them into report.errors rather than report.warnings, because only
    errors make report.ok False and only that stops assert_schedule_usable marking the
    schedule complete.
    """
    from ingest.quality import check_schedule

    async with db_pool.acquire() as conn:
        sid = await conn.fetchval("SELECT id FROM schedules WHERE month = $1", TEST_MONTH)

        clean = await check_schedule(conn, TEST_MONTH, str(sid))
        assert clean.ok, f"a clean seed must pass the gate: {clean.errors}"

        await conn.execute(
            "INSERT INTO item_atc_relationships (schedule_id, pbs_code, atc_code, is_primary) "
            "VALUES ($1, '2622M', 'Z99ZZ99', false)", sid,
        )
        broken = await check_schedule(conn, TEST_MONTH, str(sid))

    assert not broken.ok, "a join that disagrees with PBS must fail the gate, not warn"
    assert any("item_atc_relationships" in e for e in broken.errors), broken.errors


# ══════════════════════════════════════════════════════════════════════════════
# Item quantity fields — spot-checked against pbs.gov.au 2026-08-10
#
# Neither of these is a join, which is why the Tier A gate is green and still missed
# them. Both were found by comparing individual medicine pages field by field.
# ══════════════════════════════════════════════════════════════════════════════

def test_first_present_keeps_a_legitimate_zero():
    """`a or b` treats 0 as absent. For PBS numerics, 0 is an answer."""
    from ingest.normaliser import first_present

    assert first_present(0, 5) == 0
    assert first_present(None, 5) == 5
    assert first_present(None, None) is None
    assert first_present(0) == 0
    # Falsy-but-present values must survive too.
    assert first_present("", "fallback") == ""


def test_zero_repeats_is_stored_as_zero_not_null():
    """PBS sends number_of_repeats 0 for 2,608 of 14,782 items on 2026-06.

    Every null in max_repeats came from `or` falling through to a legacy field PBS
    does not send. pbs.gov.au shows "Number of Repeats: 0" for 10017F and 10091D,
    where this API returned null — "no repeats allowed" is not "unknown".
    """
    from ingest.normaliser import normalise_schedule

    raw = [
        {"pbs_code": "0001T", "drug_name": "Zero Repeats", "brand_name": "ZeroBrand",
         "benefit_type_code": "U", "number_of_repeats": 0, "maximum_quantity_units": 1,
         "li_item_id": "0001T_1_2_3_4", "program_code": "GE"},
        {"pbs_code": "0002W", "drug_name": "Five Repeats", "brand_name": "FiveBrand",
         "benefit_type_code": "U", "number_of_repeats": 5, "maximum_quantity_units": 120,
         "li_item_id": "0002W_1_2_3_4", "program_code": "GE"},
    ]
    items = {i["pbs_code"]: i for i in normalise_schedule("2026-04", raw, [])["items"]}

    assert items["0001T"]["max_repeats"] == 0, "zero repeats must survive as 0"
    assert items["0001T"]["max_repeats"] is not None
    assert items["0002W"]["max_repeats"] == 5


def test_infusible_items_carry_maximum_amount_not_max_quantity():
    """Infusibles are prescribed by amount in unit_of_measure, not by units.

    The dictionary: maximum_amount is "based on the maximum amount in terms of the
    unit_of_measure. Defined only for infusible items, i.e. program_code = IN, IP, TY,
    TZ". Measured on 2026-06 the two fields partition exactly — 730 infusible rows have
    an amount and no units, every other program the reverse — so they are kept in
    separate columns rather than conflated. pbs.gov.au shows "3000 mcg" for bortezomib,
    where this API returned null.
    """
    from ingest.normaliser import normalise_schedule
    from decimal import Decimal

    raw = [
        {"pbs_code": "0003X", "drug_name": "Infusible", "brand_name": "InfBrand",
         "benefit_type_code": "R", "number_of_repeats": 15, "maximum_amount": 3000,
         "unit_of_measure": "mcg", "li_item_id": "0003X_1_2_3_4", "program_code": "IN"},
        {"pbs_code": "0004Y", "drug_name": "Tablet", "brand_name": "TabBrand",
         "benefit_type_code": "U", "number_of_repeats": 5, "maximum_quantity_units": 120,
         "li_item_id": "0004Y_1_2_3_4", "program_code": "GE"},
    ]
    items = {i["pbs_code"]: i for i in normalise_schedule("2026-04", raw, [])["items"]}

    inf = items["0003X"]
    assert inf["maximum_amount"] == Decimal("3000")
    assert inf["unit_of_measure"] == "mcg"
    assert inf["max_quantity"] is None, "an amount must not be written into max_quantity"

    tab = items["0004Y"]
    assert tab["max_quantity"] == 120
    assert tab["maximum_amount"] is None
    assert tab["unit_of_measure"] is None


async def test_drug_endpoint_exposes_maximum_amount(app_client, seeded_audit, ent_key):
    """An amount is unusable without its unit, so both are surfaced together."""
    r = await app_client.get(
        f"/v1/drugs/2622M?schedule={TEST_MONTH}", headers={"X-API-Key": ent_key})
    assert r.status_code == 200, r.text
    dispensing = r.json()["data"]["dispensing"]
    assert "maximum_amount" in dispensing
    assert "unit_of_measure" in dispensing
    assert "number_of_repeats" in dispensing
