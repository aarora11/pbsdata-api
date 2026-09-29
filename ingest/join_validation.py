"""Tier A join validation against PBS's own pre-joined view.

`/item-overview` nests the same relationships we rebuild ourselves from separate feeds
(ATC, restrictions, prescribing texts, AMT, dispensing rules, prescribers, sponsor,
pricing events). That makes it an *independent* source: comparing our tables against it
tests whether a join is right, not merely whether it is self-consistent — the dimension
docs/JOIN_CONFIDENCE.md calls E7.

Note the grain: /item-overview is brand grain (one row per li_item_id), so
listing-grain relationships repeat once per brand and are deduped to distinct
(pbs_code, key) pairs before comparing.

This module is the shared implementation. `ingest.quality` runs it as part of the
post-ingest gate so join drift fails an ingest, and `scripts/validate_tier_a_joins.py`
is a CLI over the same code.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ingest.raw_archive import load_raw_endpoint

CACHE = Path(os.environ.get("PBS_CACHE_DIR", "/tmp/pbs_raw_cache"))

OVERVIEW_ENDPOINT = "/item-overview"


@dataclass
class JoinResult:
    name: str
    expected: int
    actual: int
    missing: int
    extra: int
    sample_missing: list = field(default_factory=list)
    sample_extra: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.missing == 0 and self.extra == 0


async def load_overview(conn, month: str) -> list[dict]:
    """PBS's own pre-joined rows for a month, preferring the raw landing layer.

    raw_feed_rows is the better source: it is reconciled against the fetch on every
    ingest (ingest.quality.check_raw_layer), so it is guaranteed to equal what PBS
    served, and it survives the on-disk fetch cache being cleared — /tmp/pbs_raw_cache
    is not durable. Older months are read from the raw archive (ingest.raw_archive).
    The cache is kept as a fallback so this still works against a database predating
    the raw layer.
    """
    rows = await load_raw_endpoint(conn, month, OVERVIEW_ENDPOINT)
    if rows:
        return rows
    cached = CACHE / month / "item_overviews.json"
    return json.loads(cached.read_text()) if cached.exists() else []


def _pairs(rows, field_name, extract):
    """Distinct (pbs_code, value) pairs from a nested overview field."""
    out = set()
    for r in rows:
        for entry in (r.get(field_name) or []):
            v = extract(entry)
            if v is not None and v != "":
                out.add((r["pbs_code"], str(v)))
    return out


async def _db_pairs(conn, month, table, col, code_col="pbs_code", where=""):
    rows = await conn.fetch(
        f"SELECT DISTINCT t.{code_col} AS c, t.{col} AS v FROM {table} t "
        f"JOIN schedules s ON s.id = t.schedule_id "
        f"WHERE s.month = $1 AND t.{col} IS NOT NULL {where}",
        month,
    )
    return {(r["c"], str(r["v"])) for r in rows}


async def validate_month(conn, month: str, overview: list[dict] | None = None) -> list[JoinResult]:
    """Compare every Tier A join for a month against /item-overview.

    Pass `overview` to reuse rows already loaded; otherwise they are read here.
    """
    ov = overview if overview is not None else await load_overview(conn, month)

    checks = [
        ("item_atc_relationships",
         _pairs(ov, "item_atcs", lambda e: (e.get("atc") or {}).get("atc_code")),
         await _db_pairs(conn, month, "item_atc_relationships", "atc_code")),

        # PBS nests only true restrictions under item_restrictions, while our
        # relationship table also holds the notes and cautions that arrive on the same
        # feed. Compare like with like by taking only the restriction-kind rows; the
        # notes and cautions are still stored, and /drugs/{code}/restrictions returns
        # them tagged. Before the split this reported +12,904 extra, which is exactly
        # the 11,939 notes + 965 cautions.
        ("item_restriction_relationships",
         _pairs(ov, "item_restrictions", lambda e: e.get("res_code")),
         await _db_pairs(conn, month, "item_restriction_relationships", "restriction_code",
                         where="AND t.restriction_code LIKE '%\\_R'")),

        ("item_prescribing_text_relationships",
         _pairs(ov, "item_prescribing_texts", lambda e: e.get("prescribing_txt_id")),
         await _db_pairs(conn, month, "item_prescribing_text_relationships", "prescribing_text_id")),

        ("item_amt_relationships",
         _pairs(ov, "item_amts", lambda e: e.get("amt_code")),
         await _db_pairs(conn, month, "item_amt_relationships", "amt_id")),

        # Keyed on dispensing_rule_reference, the identifier PBS documents for this
        # table. The mnemonic names a group of rules and disagreed on 100% of rows.
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

    results = []
    for name, expected, actual in checks:
        missing = expected - actual      # PBS says it exists, we do not have it
        extra = actual - expected        # we assert a link PBS does not
        results.append(JoinResult(
            name=name, expected=len(expected), actual=len(actual),
            missing=len(missing), extra=len(extra),
            sample_missing=sorted(missing)[:3], sample_extra=sorted(extra)[:3],
        ))
    return results
