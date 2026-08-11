#!/usr/bin/env python3
"""Export our canonical facts for a reproducible sample of PBS codes.

Companion to a manual/agent sweep of pbs.gov.au/medicine/item/{code}. This side of the
comparison is generated here so the check is against a fixed, auditable snapshot rather
than ad-hoc queries.

Sampling is stratified and seeded: the bulk comes from the General Schedule because that
is what the catalogue mostly is, with deliberate coverage of the programs whose shapes
differ — Section 100 hospital, chemotherapy, Prescriber Bag, RPBS, palliative care.

Excluded by design: Extemporaneous (EP) and Paraplegic/Quadriplegic (PQ). pbs.gov.au
publishes no medicine page for those, verified on 11025G, 3250C and 100G, so they cannot
be compared and would only pollute the failure count.

Usage:
    python scripts/verify_against_pbs_site.py --month 2026-08 --n 100 --out sample.json
"""
import argparse
import asyncio
import json
import os
import random
import re

import asyncpg

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")
NO_PAGE = ("EP", "PQ")
SEED = 20260811


def strip_html(html):
    if not html:
        return None
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&apos;", "'").replace("&quot;", '"')
    return " ".join(text.split())[:160]


async def pick(conn, month, n):
    """Stratified, seeded sample of PBS codes that have a public page."""
    rows = await conn.fetch(
        """
        SELECT i.pbs_code, i.program_code, i.benefit_type
        FROM items_listing i JOIN schedules s ON s.id = i.schedule_id
        WHERE s.month = $1 AND i.program_code <> ALL($2::text[])
        ORDER BY i.pbs_code
        """,
        month, list(NO_PAGE),
    )
    rnd = random.Random(SEED)
    by_prog = {}
    for r in rows:
        by_prog.setdefault(r["program_code"], []).append(r["pbs_code"])

    picked, seen = [], set()

    # Guarantee coverage of the shapes that differ, before filling with the bulk.
    quota = {"HB": 6, "HS": 6, "IN": 5, "IP": 4, "R1": 6, "PL": 4,
             "CA": 3, "GH": 3, "CT": 3, "DB": 3, "TY": 2, "TZ": 2, "IF": 2, "MF": 2}
    for prog, want in quota.items():
        pool = by_prog.get(prog, [])
        for code in rnd.sample(pool, min(want, len(pool))):
            if code not in seen:
                picked.append(code); seen.add(code)

    # Fill the rest from the General Schedule, spread across benefit types so the
    # sample is not accidentally all unrestricted items.
    ge = [r for r in rows if r["program_code"] == "GE"]
    by_benefit = {}
    for r in ge:
        by_benefit.setdefault(r["benefit_type"], []).append(r["pbs_code"])
    remaining = n - len(picked)
    per_type = max(1, remaining // max(1, len(by_benefit)))
    for bt, pool in sorted(by_benefit.items()):
        for code in rnd.sample(pool, min(per_type, len(pool))):
            if code not in seen and len(picked) < n:
                picked.append(code); seen.add(code)
    all_ge = [r["pbs_code"] for r in ge]
    while len(picked) < n and len(seen) < len(all_ge):
        code = rnd.choice(all_ge)
        if code not in seen:
            picked.append(code); seen.add(code)
    return sorted(picked)


async def facts(conn, month, code):
    it = await conn.fetchrow(
        """
        SELECT i.pbs_code, m.ingredient, i.brand_name, i.sponsor, i.form, i.strength,
               i.max_quantity, i.max_repeats, i.maximum_amount, i.unit_of_measure, i.pack_size,
               i.benefit_type, i.program_code, i.brand_count
        FROM items_listing i
        JOIN schedules s ON s.id = i.schedule_id
        JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND s.month = $2
        """,
        code, month,
    )
    if not it:
        return None

    price = await conn.fetchrow(
        """
        SELECT ip.dispensed_price_max_qty, ip.max_general_patient_charge,
               ip.max_record_val_for_safety_net, ip.brand_premium
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

    kinds = await conn.fetch(
        """
        SELECT r.restriction_kind AS kind, count(DISTINCT r.restriction_code) AS n
        FROM restrictions r JOIN items i ON i.id = r.item_id
        JOIN schedules s ON s.id = i.schedule_id
        WHERE i.pbs_code = $1 AND s.month = $2 GROUP BY 1
        """,
        code, month,
    )
    indication = await conn.fetchval(
        """
        SELECT r.restriction_text FROM restrictions r
        JOIN items i ON i.id = r.item_id JOIN schedules s ON s.id = i.schedule_id
        WHERE i.pbs_code = $1 AND s.month = $2 AND r.restriction_kind = 'restriction'
        ORDER BY r.restriction_code LIMIT 1
        """,
        code, month,
    )
    sponsors = await conn.fetch(
        "SELECT DISTINCT i.brand_name, i.sponsor FROM items i "
        "JOIN schedules s ON s.id = i.schedule_id "
        "WHERE i.pbs_code = $1 AND s.month = $2 ORDER BY i.brand_name",
        code, month,
    )

    def fl(v):
        return float(v) if v is not None else None

    return {
        "pbs_code": it["pbs_code"],
        "ingredient": it["ingredient"],
        "brand_name": it["brand_name"],
        "sponsor": it["sponsor"],
        "form": it["form"],
        "max_quantity": it["max_quantity"],
        "pack_size": it["pack_size"],
        "max_repeats": it["max_repeats"],
        "maximum_amount": fl(it["maximum_amount"]),
        "unit_of_measure": it["unit_of_measure"],
        "benefit_type": it["benefit_type"],
        "program": it["program_code"],
        "brand_count": it["brand_count"],
        "dpmq": fl(price["dispensed_price_max_qty"]) if price else None,
        "charge": fl(price["max_general_patient_charge"]) if price else None,
        "safety_net": fl(price["max_record_val_for_safety_net"]) if price else None,
        "premium": fl(price["brand_premium"]) if price else None,
        "restrictions": next((r["n"] for r in kinds if r["kind"] == "restriction"), 0),
        "notes": next((r["n"] for r in kinds if r["kind"] == "note"), 0),
        "cautions": next((r["n"] for r in kinds if r["kind"] == "caution"), 0),
        "indication_text": strip_html(indication),
        "brands": [{"brand": b["brand_name"], "sponsor": b["sponsor"]} for b in sponsors],
    }


async def main(month, n, out):
    conn = await asyncpg.connect(DSN)
    try:
        if not month:
            month = await conn.fetchval(
                "SELECT month FROM schedules WHERE ingest_status = 'complete' "
                "ORDER BY month DESC LIMIT 1")
        codes = await pick(conn, month, n)
        data = []
        for c in codes:
            f = await conn.fetchval("SELECT 1")  # keep-alive on slow loops
            payload = await facts(conn, month, c)
            if payload:
                data.append(payload)
    finally:
        await conn.close()

    doc = {"month": month, "seed": SEED, "count": len(data),
           "excluded_programs": list(NO_PAGE), "items": data}
    with open(out, "w") as fh:
        json.dump(doc, fh, indent=1)

    progs = {}
    for d in data:
        progs[d["program"]] = progs.get(d["program"], 0) + 1
    print(f"{len(data)} items for {month} -> {out}")
    print("  programs:", dict(sorted(progs.items(), key=lambda x: -x[1])))
    bt = {}
    for d in data:
        bt[d["benefit_type"]] = bt.get(d["benefit_type"], 0) + 1
    print("  benefit types:", dict(sorted(bt.items())))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=None)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--out", default="sample.json")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.month, a.n, a.out)))
