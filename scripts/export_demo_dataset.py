#!/usr/bin/env python3
"""Export the curated demo dataset the public showcase reads.

A fixed, hand-picked set of medicines — not a searchable corpus. Two reasons:

  - It demos better. Each item is chosen to show something different: many brands,
    an authority listing, a hospital-only supply, a price reduction. Random search
    results are mostly unremarkable.
  - It is safe to publish. Someone who can browse all 6,921 PBS codes can rebuild the
    corpus; someone who can browse nine cannot.

Run after an ingest. The output is a single static JSON file, so the showcase needs no
database, no API key and no rate limiting, and it refreshes itself each schedule.

Usage:
    python scripts/export_demo_dataset.py [--out demo-data.json] [--month 2026-06]
"""
import argparse
import asyncio
import json
import os
import re
from decimal import Decimal

import asyncpg

from ingest.raw_archive import load_raw_endpoint

# The showcase must never recompute a patient charge in JavaScript — that would be a
# second implementation of the model, which is the defect class this project keeps
# finding. Figures come from the same module the API serves.
from api.services.pricing import (
    brand_dispensed_price, concessional_charge, government_contribution,
    additional_patient_fees,
)

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")

# Each entry is chosen for the one thing it demonstrates. The blurb is shown in the
# showcase's medicine list so a visitor knows why they'd click it.
CURATED = [
    ("10104T", "Price cut 25% last schedule — and PBS's own event feed agrees"),
    ("13842M", "Eight brands, one carries a premium the patient pays"),
    ("10001J", "Authority Required — with notes that are not restrictions"),
    ("10091D", "Carries a caution, three streamlined codes, and notes"),
    ("12219D", "Sixteen brands, prescribed by amount rather than packs"),
    ("10056G", "Hospital supply only — no community pharmacy price exists"),
    ("10178Q", "Prescriber Bag — the patient pays nothing"),
    ("10005N", "Priced under the copayment, so the fees change what you pay"),
    ("1512N",  "Repeats increased from 1 to 3 this schedule"),
]

TAGS = "<[^>]+>"


def clean(html):
    """PBS restriction text is loose HTML; render it as readable paragraphs."""
    if not html:
        return None
    text = re.sub(r"</p>|<br\s*/?>", "\n", html)
    text = re.sub(TAGS, " ", text)
    text = (text.replace("&amp;", "&").replace("&apos;", "'")
                .replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    lines = [" ".join(l.split()) for l in text.split("\n")]
    return [l for l in lines if l] or None


def f(v):
    return float(v) if isinstance(v, (Decimal, int, float)) else None


_overview_cache: dict[str, dict[str, list]] = {}


async def _overview_by_code(conn, month):
    """/item-overview rows for a month grouped by pbs_code, from Postgres or the raw archive.

    Loaded once per month: an archived month has to be read from its file in full.
    """
    if month not in _overview_cache:
        grouped: dict[str, list] = {}
        for r in await load_raw_endpoint(conn, month, "/item-overview"):
            grouped.setdefault(r.get("pbs_code"), []).append(r)
        _overview_cache[month] = grouped
    return _overview_cache[month]


async def item_payload(conn, month, code, blurb):
    item = await conn.fetchrow(
        """
        SELECT i.pbs_code, m.ingredient, i.brand_name, i.form, i.strength,
               i.benefit_type, i.program_code, i.max_quantity, i.max_repeats,
               i.maximum_amount, i.unit_of_measure, i.brand_count, i.sponsor,
               p.program_title
        FROM items_listing i
        JOIN schedules s ON s.id = i.schedule_id
        JOIN medicines m ON m.id = i.medicine_id
        LEFT JOIN programs p ON p.program_code = i.program_code AND p.schedule_id = i.schedule_id
        WHERE i.pbs_code = $1 AND s.month = $2
        """,
        code, month,
    )
    if not item:
        return None

    brands = await conn.fetch(
        """
        SELECT i.brand_name, i.sponsor, ip.brand_premium,
               ip.dispensed_price_max_qty, ip.max_general_patient_charge
        FROM items i
        JOIN schedules s ON s.id = i.schedule_id
        LEFT JOIN item_pricing ip ON ip.li_item_id = i.li_item_id
             AND ip.schedule_id = i.schedule_id
             AND ip.dispensing_rule_reference = (
                 SELECT ip2.dispensing_rule_reference FROM item_pricing ip2
                 LEFT JOIN program_dispensing_rules pdr
                        ON pdr.rule_code = ip2.dispensing_rule_reference
                       AND pdr.schedule_id = ip2.schedule_id
                 WHERE ip2.li_item_id = i.li_item_id AND ip2.schedule_id = i.schedule_id
                 ORDER BY COALESCE(pdr.community_pharmacy_indicator, FALSE) DESC LIMIT 1)
        WHERE i.pbs_code = $1 AND s.month = $2
        ORDER BY ip.brand_premium NULLS FIRST, i.brand_name
        """,
        code, month,
    )

    pricing = await conn.fetchrow(
        """
        SELECT ip.dispensed_price_max_qty, ip.max_general_patient_charge,
               ip.max_record_val_for_safety_net, ip.brand_premium,
               ip.special_patient_contribution, ip.fee_extra, ip.fee_safety_net_recording,
               ip.dispensing_rule_reference, pdr.dispensing_rule_title,
               COALESCE(pdr.community_pharmacy_indicator, FALSE) AS retail
        FROM item_pricing ip
        JOIN schedules s ON s.id = ip.schedule_id
        LEFT JOIN program_dispensing_rules pdr
               ON pdr.rule_code = ip.dispensing_rule_reference AND pdr.schedule_id = ip.schedule_id
        WHERE ip.pbs_code = $1 AND s.month = $2 AND ip.dispensed_price_max_qty IS NOT NULL
        ORDER BY COALESCE(pdr.community_pharmacy_indicator, FALSE) DESC,
                 ip.brand_premium ASC NULLS LAST
        LIMIT 1
        """,
        code, month,
    )

    copay = await conn.fetchrow(
        "SELECT c.general, c.concessional FROM copayments c "
        "JOIN schedules s ON s.id = c.schedule_id WHERE s.month = $1", month)

    settings = await conn.fetch(
        """
        SELECT DISTINCT pdr.dispensing_rule_title AS title, ip.dispensing_rule_reference AS ref,
               ip.dispensed_price_max_qty AS price, ip.max_general_patient_charge AS charge
        FROM item_pricing ip
        JOIN schedules s ON s.id = ip.schedule_id
        LEFT JOIN program_dispensing_rules pdr
               ON pdr.rule_code = ip.dispensing_rule_reference AND pdr.schedule_id = ip.schedule_id
        WHERE ip.pbs_code = $1 AND s.month = $2
        ORDER BY ip.dispensing_rule_reference
        """,
        code, month,
    )

    clinical = await conn.fetch(
        """
        SELECT DISTINCT r.restriction_kind, r.restriction_code, r.authority_method,
               r.streamlined_code, r.restriction_text, r.treatment_phase
        FROM restrictions r
        JOIN items i ON i.id = r.item_id
        JOIN schedules s ON s.id = i.schedule_id
        WHERE i.pbs_code = $1 AND s.month = $2
        ORDER BY r.restriction_kind, r.restriction_code
        """,
        code, month,
    )

    atc = await conn.fetch(
        """
        SELECT iar.atc_code, a.atc_description
        FROM item_atc_relationships iar
        JOIN schedules s ON s.id = iar.schedule_id
        LEFT JOIN atc_codes a ON a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id
        WHERE iar.pbs_code = $1 AND s.month = $2 ORDER BY iar.atc_code
        """,
        code, month,
    )

    changes = await conn.fetch(
        """
        SELECT DISTINCT c.change_type, c.field_name, c.old_value, c.new_value
        FROM changes c JOIN schedules s ON s.id = c.schedule_id
        WHERE c.pbs_code = $1 AND s.month = $2
        """,
        code, month,
    )

    events = await conn.fetch(
        """
        SELECT DISTINCT e.event_type, e.percentage_applied
        FROM item_pricing_events e JOIN schedules s ON s.id = e.schedule_id
        WHERE e.pbs_code = $1 AND s.month = $2
        """,
        code, month,
    )

    # Per-item verification against PBS's own pre-joined view. This is the whole point
    # of the showcase, so it is computed per item rather than asserted globally.
    rows = (await _overview_by_code(conn, month)).get(code, [])
    checks = []
    if rows:

        def pbs_set(field, key):
            out = set()
            for r in rows:
                for e in (r.get(field) or []):
                    v = e.get(key) if not isinstance(e.get(key), dict) else None
                    if isinstance(e.get(key), dict):
                        v = e[key].get(key + "_code")
                    if v not in (None, ""):
                        out.add(str(v))
            return out

        pbs_atc = set()
        for r in rows:
            for e in (r.get("item_atcs") or []):
                c = (e.get("atc") or {}).get("atc_code")
                if c:
                    pbs_atc.add(str(c))
        pbs_res = pbs_set("item_restrictions", "res_code")
        pbs_rules = set()
        for r in rows:
            for e in (r.get("item_dispensing_rules") or []):
                v = (e.get("dispensing_rule") or {}).get("dispensing_rule_reference")
                if v:
                    pbs_rules.add(str(v))

        ours_atc = {r["atc_code"] for r in atc}
        ours_res = {r["restriction_code"] for r in clinical
                    if r["restriction_kind"] == "restriction"}
        ours_rules = {r["ref"] for r in settings if r["ref"]}

        for label, pbs, ours in (
            ("ATC classification", pbs_atc, ours_atc),
            ("Restrictions", pbs_res, ours_res),
            ("Dispensing rules", pbs_rules, ours_rules),
        ):
            if pbs or ours:
                checks.append({"label": label, "pbs": len(pbs), "ours": len(ours),
                               "match": pbs == ours})

    # Every figure below comes from api/services/pricing, the module the API uses.
    cost = {"dispensed": None, "general": None, "concessional": None,
            "government": None, "extra_fees": None, "safety_net_record": None,
            "general_copay": f(copay["general"]) if copay else None,
            "concessional_copay": f(copay["concessional"]) if copay else None,
            "setting": None, "retail": False}
    if pricing:
        base = f(pricing["dispensed_price_max_qty"])
        prem = f(pricing["brand_premium"])
        spc = f(pricing["special_patient_contribution"])
        fees = (f(pricing["fee_extra"]) or 0.0) + (f(pricing["fee_safety_net_recording"]) or 0.0)
        general = f(pricing["max_general_patient_charge"])
        dispensed = brand_dispensed_price(base, prem)
        cost.update({
            "dispensed": dispensed,
            "base": base,
            "premium": prem,
            "general": general,
            "concessional": concessional_charge(
                base, cost["concessional_copay"], prem, spc, general, fees),
            "government": government_contribution(dispensed, general),
            "extra_fees": additional_patient_fees(dispensed, general),
            "safety_net_record": f(pricing["max_record_val_for_safety_net"]),
            "setting": pricing["dispensing_rule_title"],
            "retail": bool(pricing["retail"]),
        })

    return {
        "pbs_code": item["pbs_code"],
        "blurb": blurb,
        "ingredient": item["ingredient"],
        "brand_name": item["brand_name"],
        "form": item["form"],
        "strength": item["strength"],
        "program": item["program_title"] or item["program_code"],
        "benefit_type": item["benefit_type"],
        "max_quantity": item["max_quantity"],
        "max_repeats": item["max_repeats"],
        "maximum_amount": f(item["maximum_amount"]),
        "unit_of_measure": item["unit_of_measure"],
        "brand_count": item["brand_count"],
        "cost": cost,
        "brands": [
            {"brand_name": b["brand_name"], "sponsor": b["sponsor"],
             "premium": f(b["brand_premium"]),
             "price": f(b["dispensed_price_max_qty"]),
             "charge": f(b["max_general_patient_charge"])}
            for b in brands
        ],
        "settings": [
            {"title": s["title"] or s["ref"], "ref": s["ref"],
             "price": f(s["price"]), "charge": f(s["charge"])}
            for s in settings
        ],
        "clinical": [
            {"kind": c["restriction_kind"], "code": c["restriction_code"],
             "authority_method": c["authority_method"],
             "streamlined_code": c["streamlined_code"],
             "treatment_phase": c["treatment_phase"],
             "text": clean(c["restriction_text"])}
            for c in clinical
        ],
        "atc": [{"code": a["atc_code"], "description": a["atc_description"]} for a in atc],
        "changes": [
            {"type": c["change_type"], "field": c["field_name"],
             "old": c["old_value"], "new": c["new_value"]}
            for c in changes
        ],
        "events": [
            {"type": e["event_type"], "percentage": f(e["percentage_applied"])}
            for e in events
        ],
        "checks": checks,
    }


async def main(out_path, month):
    conn = await asyncpg.connect(DSN)
    try:
        if not month:
            month = await conn.fetchval(
                "SELECT month FROM schedules WHERE ingest_status = 'complete' "
                "ORDER BY month DESC LIMIT 1")
        items = []
        for code, blurb in CURATED:
            payload = await item_payload(conn, month, code, blurb)
            if payload:
                items.append(payload)
                print(f"  {code:<8} {payload['ingredient'][:38]:<40} "
                      f"brands={payload['brand_count']} checks={len(payload['checks'])}")
            else:
                print(f"  {code:<8} NOT FOUND in {month}")

        totals = await conn.fetchrow(
            """
            SELECT (SELECT count(*) FROM items_listing i JOIN schedules s ON s.id = i.schedule_id
                    WHERE s.month = $1) AS codes,
                   (SELECT count(*) FROM items i JOIN schedules s ON s.id = i.schedule_id
                    WHERE s.month = $1) AS brands,
                   (SELECT count(*) FROM changes c JOIN schedules s ON s.id = c.schedule_id
                    WHERE s.month = $1) AS changes
            """,
            month,
        )
    finally:
        await conn.close()

    doc = {
        "schedule": month,
        "generated_note": "Curated sample. Not the full PBS schedule.",
        "totals": {"codes": totals["codes"], "brands": totals["brands"],
                   "changes": totals["changes"]},
        "items": items,
    }
    with open(out_path, "w") as fh:
        json.dump(doc, fh, indent=1)
    print(f"\nWrote {len(items)} items for {month} -> {out_path}")
    return 0 if items else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo-data.json")
    ap.add_argument("--month", default=None)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.out, a.month)))
