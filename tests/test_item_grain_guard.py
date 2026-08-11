"""Guards on the item grain, so the brand-grain rekey cannot silently fan out.

`items` is keyed on (li_item_id, schedule_id) to match PBS's ITEM_T, so a PBS
code appears once per brand — roughly 2.1 on average, up to 16. Any query that
joins a listing-grain table to `items` on pbs_code therefore multiplies rows:
measured on 2026-06, item_pricing goes 38,318 -> 159,520 (4.2x).

`items_listing` is the listing-grain view that keeps those joins 1:1, and the
API reads through it. These tests pin the two things that keep that true.
"""
import re
from pathlib import Path

import pytest

API_DIR = Path(__file__).parent.parent / "api"

# The one read of raw `items` left in the API. It is an EXISTS semi-join, which
# returns a boolean rather than rows and so cannot fan out; it is on brand grain
# deliberately, to search all brand names rather than one per PBS code.
ALLOWED_RAW_ITEMS_READS = {
    "api/services/search.py",
}

# Joining `items` on pbs_code multiplies rows by the item's brand count. Joining on
# `id` or `li_item_id` is 1:1 and safe, so only pbs_code joins are checked here.
#
# `FROM items` alone missed this: /market/price-reduction-events used
# `LEFT JOIN items i ON i.pbs_code = ...` and returned 32 rows for 8 events.
#
# Each entry below is a pbs_code join that is safe for a stated reason.
ALLOWED_PBS_CODE_JOINS = {
    # Deduplicated or aggregated with DISTINCT, so the fan-out cannot reach the output.
    "api/routers/market.py",
    "api/routers/atc_codes.py",
    # Brand grain by design: a sponsor sponsors brands, and the count query fans out
    # identically to the data query, so pagination stays coherent.
    "api/routers/organisations.py",
}


def _api_files():
    return sorted(p for p in API_DIR.rglob("*.py"))


def test_api_reads_items_through_the_listing_view():
    """
    New `FROM items` in the API is almost always a fan-out bug.

    Use `items_listing` for one row per PBS code. If you genuinely want brand
    grain, add the file to ALLOWED_RAW_ITEMS_READS with a note saying why.
    """
    offenders = []
    for path in _api_files():
        rel = str(path.relative_to(API_DIR.parent))
        if rel in ALLOWED_RAW_ITEMS_READS:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            # `FROM items` but not `FROM items_listing`
            if re.search(r"\bFROM\s+items\b(?!_listing)", line):
                offenders.append(f"{rel}:{i}: {line.strip()[:90]}")

    assert not offenders, (
        "API reads raw `items` (brand grain) — joining this on pbs_code fans out.\n"
        "Use items_listing, or allow-list the file with a reason:\n  "
        + "\n  ".join(offenders)
    )


def test_allowed_raw_items_reads_are_semi_joins_only():
    """An allow-listed file may only reference items inside EXISTS/IN."""
    for rel in ALLOWED_RAW_ITEMS_READS:
        text = (API_DIR.parent / rel).read_text()
        for m in re.finditer(r"\bFROM\s+items\b(?!_listing)", text):
            prefix = text[max(0, m.start() - 200):m.start()].upper()
            assert "EXISTS" in prefix or " IN (" in prefix, (
                f"{rel} reads raw items outside a semi-join — this can fan out"
            )


@pytest.mark.asyncio
async def test_items_listing_is_one_row_per_pbs_code(db):
    """The property every rewritten API query depends on."""
    dupes = await db.fetchval(
        "SELECT count(*) FROM ("
        "  SELECT pbs_code, schedule_id FROM items_listing"
        "  GROUP BY pbs_code, schedule_id HAVING count(*) > 1"
        ") d"
    )
    assert dupes == 0, f"{dupes} (pbs_code, schedule_id) pairs appear more than once"


@pytest.mark.asyncio
async def test_listing_view_keeps_joins_one_to_one_when_items_would_fan_out(db):
    """
    A live demonstration that the view is what prevents the fan-out.

    Two brands of one PBS code, plus one listing-grain relationship row: joining
    through `items` doubles it, joining through `items_listing` does not.
    """
    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) "
        "VALUES ('1997-01', NOW(), 'complete') ON CONFLICT (month) DO UPDATE SET ingest_status='complete'"
    )
    sched = await db.fetchval("SELECT id FROM schedules WHERE month='1997-01'")
    med = await db.fetchval(
        "INSERT INTO medicines (ingredient, ingredient_lower) VALUES ('Guardium','guardium') "
        "ON CONFLICT (ingredient_lower) DO UPDATE SET ingredient=EXCLUDED.ingredient RETURNING id"
    )
    await db.execute("DELETE FROM item_atc_relationships WHERE schedule_id = $1", sched)
    await db.execute("DELETE FROM items WHERE schedule_id = $1", sched)

    for li in ("6000B_1_1_1_1", "6000B_2_2_2_2"):
        await db.execute(
            "INSERT INTO items (pbs_code, li_item_id, schedule_id, medicine_id, brand_name, "
            "brand_name_lower, benefit_type) VALUES ('6000B',$1,$2,$3,'B','b','U')",
            li, sched, med,
        )
    await db.execute(
        "INSERT INTO atc_codes (atc_code, schedule_id, atc_description) VALUES ('Z01ZZ01',$1,'test') "
        "ON CONFLICT (atc_code, schedule_id) DO NOTHING", sched,
    )
    await db.execute(
        "INSERT INTO item_atc_relationships (pbs_code, atc_code, schedule_id) "
        "VALUES ('6000B','Z01ZZ01',$1)", sched,
    )

    via_items = await db.fetchval(
        "SELECT count(*) FROM item_atc_relationships x "
        "JOIN items i ON i.pbs_code=x.pbs_code AND i.schedule_id=x.schedule_id "
        "WHERE x.schedule_id=$1", sched,
    )
    via_listing = await db.fetchval(
        "SELECT count(*) FROM item_atc_relationships x "
        "JOIN items_listing i ON i.pbs_code=x.pbs_code AND i.schedule_id=x.schedule_id "
        "WHERE x.schedule_id=$1", sched,
    )

    assert via_items == 2, "expected raw items to fan out across both brands"
    assert via_listing == 1, "items_listing must keep listing-grain joins 1:1"

    await db.execute("DELETE FROM item_atc_relationships WHERE schedule_id = $1", sched)
    await db.execute("DELETE FROM items WHERE schedule_id = $1", sched)


def test_items_joins_on_pbs_code_are_reviewed():
    """A pbs_code join onto brand-grain `items` multiplies rows.

    The `FROM items` check above does not see `JOIN items`, which is how
    /market/price-reduction-events came to return 32 rows for 8 pricing events. Joins on
    `id` or `li_item_id` are 1:1 and ignored; only pbs_code joins need a reason.
    """
    offenders = []
    for path in _api_files():
        rel = str(path.relative_to(API_DIR.parent))
        if rel in ALLOWED_PBS_CODE_JOINS:
            continue
        text = path.read_text()
        for m in re.finditer(r"\b(?:LEFT\s+|INNER\s+)?JOIN\s+items\b(?!_listing)", text):
            on_clause = " ".join(text[m.end():m.end() + 220].split())
            joins_on_pbs_code = (
                "pbs_code" in on_clause
                and "li_item_id" not in on_clause.split("WHERE")[0]
                and ".id =" not in on_clause.split("WHERE")[0]
            )
            if joins_on_pbs_code:
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}: {on_clause[:80]}")

    assert not offenders, (
        "items is joined on pbs_code, which fans out by brand count.\n"
        "Use items_listing, or add the file to ALLOWED_PBS_CODE_JOINS with a reason:\n  "
        + "\n  ".join(offenders)
    )
