#!/usr/bin/env python3
"""Tier A join validation: check our materialised joins against PBS's own answer.

`/item-overview` is PBS's pre-joined view of an item — it nests the same
relationships we rebuild ourselves from separate feeds (ATC, restrictions,
prescribing texts, AMT, dispensing rules, prescribers, sponsor, pricing events).
That makes it an *independent* source: comparing our tables against it tests
whether the join is right, not merely whether it is self-consistent.

This is the evidence dimension docs/JOIN_CONFIDENCE.md calls E7 and rates as
"nothing passes today". Internal checks — orphan counts, coverage, invariants —
cannot catch a join that is coherently wrong. This can.

Note the grain: /item-overview is brand grain (one row per li_item_id), so
listing-grain relationships repeat once per brand and are deduped to distinct
(pbs_code, key) pairs before comparing.

Usage:
    python scripts/validate_tier_a_joins.py [month ...]
Exits non-zero if any join mismatches.
"""
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import asyncpg

CACHE = Path(os.environ.get("PBS_CACHE_DIR", "/tmp/pbs_raw_cache"))
DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5433/pbsapi")


def _overview(month: str) -> list[dict]:
    return json.loads((CACHE / month / "item_overviews.json").read_text())


def _pairs(rows, field, extract):
    """Distinct (pbs_code, value) pairs from a nested overview field."""
    out = set()
    for r in rows:
        for entry in (r.get(field) or []):
            v = extract(entry)
            if v is not None and v != "":
                out.add((r["pbs_code"], str(v)))
    return out


async def _db_pairs(conn, month, table, col, code_col="pbs_code"):
    rows = await conn.fetch(
        f"SELECT DISTINCT t.{code_col} AS c, t.{col} AS v FROM {table} t "
        f"JOIN schedules s ON s.id = t.schedule_id "
        f"WHERE s.month = $1 AND t.{col} IS NOT NULL",
        month,
    )
    return {(r["c"], str(r["v"])) for r in rows}


async def validate_month(conn, month: str) -> list[tuple]:
    ov = _overview(month)
    results = []

    checks = [
        ("item_atc_relationships",
         _pairs(ov, "item_atcs", lambda e: (e.get("atc") or {}).get("atc_code")),
         await _db_pairs(conn, month, "item_atc_relationships", "atc_code")),

        ("item_restriction_relationships",
         _pairs(ov, "item_restrictions", lambda e: e.get("res_code")),
         await _db_pairs(conn, month, "item_restriction_relationships", "restriction_code")),

        ("item_prescribing_text_relationships",
         _pairs(ov, "item_prescribing_texts", lambda e: e.get("prescribing_txt_id")),
         await _db_pairs(conn, month, "item_prescribing_text_relationships", "prescribing_text_id")),

        ("item_amt_relationships",
         _pairs(ov, "item_amts", lambda e: e.get("amt_code")),
         await _db_pairs(conn, month, "item_amt_relationships", "amt_id")),

        ("item_dispensing_rules",
         _pairs(ov, "item_dispensing_rules",
                lambda e: (e.get("dispensing_rule") or {}).get("dispensing_rule_reference")),
         await _db_pairs(conn, month, "item_dispensing_rules", "rule_code")),

        ("item_prescribers",
         _pairs(ov, "prescribers", lambda e: e.get("prescriber_type")),
         await _db_pairs(conn, month, "item_prescribers", "prescriber_type")),

        ("item_pricing_events",
         _pairs(ov, "item_pricing_events", lambda e: e.get("event_type_code")),
         await _db_pairs(conn, month, "item_pricing_events", "event_type")),
    ]

    # items.sponsor comes from the nested manufacturer object, not a list.
    ov_sponsor = {(r["pbs_code"], (r.get("manufacturer") or {}).get("name"))
                  for r in ov if (r.get("manufacturer") or {}).get("name")}
    db_sponsor = {(r["c"], r["v"]) for r in await conn.fetch(
        "SELECT DISTINCT i.pbs_code AS c, i.sponsor AS v FROM items i "
        "JOIN schedules s ON s.id = i.schedule_id WHERE s.month = $1 AND i.sponsor IS NOT NULL",
        month)}
    checks.append(("items.sponsor", ov_sponsor, db_sponsor))

    for name, expected, actual in checks:
        missing = expected - actual      # PBS says it exists, we do not have it
        extra = actual - expected        # we assert a link PBS does not
        results.append((name, len(expected), len(actual), len(missing), len(extra),
                        sorted(missing)[:3], sorted(extra)[:3]))
    return results


async def main(months):
    conn = await asyncpg.connect(DSN)
    failed = False
    for month in months:
        if not (CACHE / month / "item_overviews.json").exists():
            print(f"{month}: no cached item_overviews — skipping")
            continue
        print(f"\n=== {month} " + "=" * 62)
        print(f"{'join':<38}{'PBS':>9}{'ours':>9}{'missing':>9}{'extra':>8}   verdict")
        print("-" * 82)
        for name, exp, act, miss, extra, ex_miss, ex_extra in await validate_month(conn, month):
            ok = miss == 0 and extra == 0
            failed |= not ok
            print(f"{name:<38}{exp:>9,}{act:>9,}{miss:>9,}{extra:>8,}   {'MATCH' if ok else '*** MISMATCH ***'}")
            if ex_miss:
                print(f"    missing e.g. {ex_miss}")
            if ex_extra:
                print(f"    extra   e.g. {ex_extra}")
    await conn.close()
    print("\n" + ("SOME JOINS DISAGREE WITH PBS" if failed else
                  "ALL TIER A JOINS MATCH PBS'S OWN PRE-JOINED VIEW"))
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:] or ["2026-05", "2026-06"]
    sys.exit(asyncio.run(main(args)))
