#!/usr/bin/env python3
"""Build the public PBS site: read one schedule from Postgres, write static HTML.

The live site has no server and no database. Every page is rendered here from the
newest schedule that passed its ingest gate, and the whole site is regenerated each
month. Templates hold layout only; every PBS figure comes from the database, and every
patient charge from api/services/pricing — the same module the API serves.

Only fields that are checked against PBS reach a page (docs/PUBLIC_SITE_SPEC.md §3).
60-day eligibility, safety-net estimates and /market aggregates are deliberately absent.

Usage:
    python web/build.py [--month 2026-08] [--out web/dist]
    python -m http.server -d web/dist 8080
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import asyncpg
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from api.services.pricing import concessional_charge  # noqa: E402
from ingest.restriction_text import condition_of, paragraphs  # noqa: E402
from ingest.raw_archive import load_raw_endpoint  # noqa: E402

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")

PBS_ITEM_URL = "https://www.pbs.gov.au/medicine/item/{code}"
# pbs.gov.au publishes no medicine page for these programs; a link would 404.
NO_PBS_PAGE_PROGRAMS = {"EP", "PQ"}

ACCESS = {
    "U": ("Unrestricted", "Any PBS prescriber can prescribe it. No conditions apply."),
    "R": ("Restricted", "Only subsidised for the conditions PBS lists below."),
    "S": ("Authority required (streamlined)",
          "Only subsidised for the listed conditions. Your prescriber writes an approval "
          "code on the script; no call to Services Australia is needed."),
    "A": ("Authority required",
          "Only subsidised for the listed conditions, and your prescriber must get "
          "approval from Services Australia first."),
}

PRESCRIBERS = {
    "Medical Practitioners": "Doctors",
    "Nurse Practitioners": "Nurse practitioners",
    "Midwives": "Midwives",
    "Dental": "Dentists",
    "Optometrists": "Optometrists",
}
PRESCRIBER_ORDER = list(PRESCRIBERS.values())

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def month_label(m: str) -> str:
    y, mo = m.split("-")
    return f"{MONTHS[int(mo) - 1]} {y}"


def num(v):
    return float(v) if isinstance(v, (Decimal, int, float)) else None


def money(v) -> str:
    return "" if v is None else f"${v:,.2f}"


def display(name: str) -> str:
    """PBS writes some names in lower case ("dressing hydrofibre with silver")."""
    return name[:1].upper() + name[1:] if name else name


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def natural_key(text):
    """Sort "Tablet 25 mg" before "Tablet 100 mg"."""
    return [(0, float(t), "") if re.fullmatch(r"\d+(?:\.\d+)?", t) else (1, 0.0, t.lower())
            for t in re.findall(r"\d+(?:\.\d+)?|\D+", text or "")]


# ── Read ──────────────────────────────────────────────────────────────────────


async def read_schedule(conn, month):
    sid = await conn.fetchval(
        "SELECT id FROM schedules WHERE month = $1 AND ingest_status = 'complete'", month)
    if not sid:
        raise SystemExit(f"{month} is not a complete schedule; refusing to publish it")

    q = {}
    q["listing"] = await conn.fetch(
        """
        SELECT i.pbs_code, i.form, i.pack_size, i.benefit_type, i.program_code,
               i.max_quantity, i.max_repeats, i.maximum_amount, i.unit_of_measure,
               i.first_listed_date, i.imdq60, i.imdq60_base, i.sixty_day_pair, p.program_title
        FROM items_listing i
        LEFT JOIN programs p ON p.program_code = i.program_code AND p.schedule_id = i.schedule_id
        WHERE i.schedule_id = $1
        """, sid)
    q["brands"] = await conn.fetch(
        "SELECT pbs_code, li_item_id, brand_name, sponsor FROM items WHERE schedule_id = $1",
        sid)
    q["pricing"] = await conn.fetch(
        """
        SELECT ip.li_item_id, ip.pbs_code, ip.dispensed_price_max_qty,
               ip.max_general_patient_charge, ip.brand_premium, ip.special_patient_contribution,
               ip.fee_extra, ip.fee_safety_net_recording, ip.max_record_val_for_safety_net,
               ip.dispensing_rule_reference, pdr.dispensing_rule_title,
               COALESCE(pdr.community_pharmacy_indicator, FALSE) AS retail
        FROM item_pricing ip
        LEFT JOIN program_dispensing_rules pdr
               ON pdr.rule_code = ip.dispensing_rule_reference AND pdr.schedule_id = ip.schedule_id
        WHERE ip.schedule_id = $1
        """, sid)
    q["copay"] = await conn.fetchrow(
        "SELECT general, concessional, safety_net_general, safety_net_concessional "
        "FROM copayments WHERE schedule_id = $1", sid)
    q["restrictions"] = await conn.fetch(
        """
        SELECT DISTINCT i.pbs_code, r.restriction_kind, r.restriction_code,
               r.streamlined_code, r.restriction_text, r.treatment_phase
        FROM restrictions r JOIN items i ON i.id = r.item_id
        WHERE i.schedule_id = $1
        """, sid)
    q["prescribers"] = await conn.fetch(
        "SELECT DISTINCT pbs_code, prescriber_type FROM item_prescribers WHERE schedule_id = $1",
        sid)
    q["atc"] = await conn.fetch(
        "SELECT DISTINCT pbs_code, atc_code FROM item_atc_relationships WHERE schedule_id = $1",
        sid)
    q["atc_names"] = {r["atc_code"]: r["atc_description"] for r in await conn.fetch(
        "SELECT atc_code, atc_description FROM atc_codes WHERE schedule_id = $1", sid)}
    # The schedule drug name is what pbs.gov.au displays; the normalised tables hold the
    # legal name (li_drug_name), which differs on ~15% of items. Read it from source.
    q["raw_items"] = await load_raw_endpoint(conn, month, "/items")

    q["months"] = [r["month"] for r in await conn.fetch(
        "SELECT month FROM schedules WHERE ingest_status = 'complete' AND month <= $1 "
        "ORDER BY month", month)]
    q["changes"] = await conn.fetch(
        """
        SELECT s.month, c.pbs_code, c.change_type, c.field_name, c.old_value, c.new_value
        FROM changes c JOIN schedules s ON s.id = c.schedule_id
        WHERE s.ingest_status = 'complete' AND s.month <= $1
        ORDER BY s.month, c.pbs_code
        """, month)
    # Names for codes that have since left the schedule, so a delisting still reads.
    q["past_names"] = {r["pbs_code"]: (r["ingredient"], r["form"]) for r in await conn.fetch(
        """
        SELECT DISTINCT ON (i.pbs_code) i.pbs_code, m.ingredient, i.form
        FROM items_listing i JOIN medicines m ON m.id = i.medicine_id
        JOIN schedules s ON s.id = i.schedule_id
        ORDER BY i.pbs_code, s.month DESC
        """)}
    return q


# ── Model ─────────────────────────────────────────────────────────────────────


def pick_pricing(rows):
    """The price a patient at a community pharmacy sees, else whatever PBS publishes."""
    return min(rows, key=lambda r: (
        not r["retail"], r["dispensed_price_max_qty"] is None,
        num(r["brand_premium"]) or 0.0))


def build_model(q, month):
    copay_general = num(q["copay"]["general"]) if q["copay"] else None
    copay_conc = num(q["copay"]["concessional"]) if q["copay"] else None

    raw_by_item = {r["li_item_id"]: r for r in q["raw_items"]}
    drug_name = {}
    for r in q["raw_items"]:
        drug_name.setdefault(r["pbs_code"], r.get("drug_name") or r.get("li_drug_name"))

    pricing_by_item = defaultdict(list)
    settings_by_code = defaultdict(dict)
    for r in q["pricing"]:
        pricing_by_item[r["li_item_id"]].append(r)
        title = r["dispensing_rule_title"] or r["dispensing_rule_reference"]
        if r["dispensed_price_max_qty"] is not None and num(r["brand_premium"]) in (None, 0.0):
            settings_by_code[r["pbs_code"]][title] = {
                "title": title, "retail": r["retail"],
                "dpmq": num(r["dispensed_price_max_qty"]),
                "charge": num(r["max_general_patient_charge"]),
            }

    brands_by_code = defaultdict(list)
    for b in q["brands"]:
        rows = pricing_by_item.get(b["li_item_id"])
        p = pick_pricing(rows) if rows else None
        general = conc = premium = dpmq = snet = None
        retail = False
        if p and p["dispensed_price_max_qty"] is not None:
            base = num(p["dispensed_price_max_qty"])
            premium = num(p["brand_premium"]) or 0.0
            general = num(p["max_general_patient_charge"])
            fees = (num(p["fee_extra"]) or 0.0) + (num(p["fee_safety_net_recording"]) or 0.0)
            conc = concessional_charge(base, copay_conc, premium,
                                       num(p["special_patient_contribution"]), general, fees)
            dpmq = round(base + premium, 2)
            snet = num(p["max_record_val_for_safety_net"])
            retail = p["retail"]
        raw = raw_by_item.get(b["li_item_id"], {})
        brands_by_code[b["pbs_code"]].append({
            "name": b["brand_name"], "sponsor": b["sponsor"], "premium": premium,
            "general": general, "concession": conc, "dpmq": dpmq, "safety_net": snet,
            "retail": retail, "packs": raw.get("maximum_prescribable_pack"),
        })

    restrictions = defaultdict(lambda: {"restriction": [], "note": [], "caution": []})
    seen = set()
    for r in q["restrictions"]:
        key = (r["pbs_code"], r["restriction_code"])
        if key in seen:
            continue
        seen.add(key)
        restrictions[r["pbs_code"]][r["restriction_kind"] or "restriction"].append({
            "code": r["restriction_code"], "streamlined": r["streamlined_code"],
            "text": paragraphs(r["restriction_text"]), "phase": r["treatment_phase"],
        })

    prescribers = defaultdict(set)
    for r in q["prescribers"]:
        prescribers[r["pbs_code"]].add(PRESCRIBERS.get(r["prescriber_type"], r["prescriber_type"]))

    atc = defaultdict(list)
    for r in q["atc"]:
        atc[r["pbs_code"]].append(r["atc_code"])

    medicines = {}
    for li in q["listing"]:
        code = li["pbs_code"]
        name = display(drug_name.get(code) or q["past_names"].get(code, (code, None))[0])
        slug = slugify(name)
        med = medicines.setdefault(slug, {"name": name, "slug": slug, "items": [],
                                          "atc": set(), "history": []})
        brands = sorted(brands_by_code.get(code, []),
                        key=lambda b: (b["premium"] or 0.0, b["name"] or ""))
        access = ACCESS.get(li["benefit_type"], (li["benefit_type"], ""))
        retail = [b for b in brands if b["retail"] and b["general"] is not None]
        packs = next((b["packs"] for b in brands if b["packs"]), None)
        med["atc"].update(atc.get(code, []))
        med["items"].append({
            "code": code,
            "form": li["form"],
            "pack_size": li["pack_size"],
            "max_units": li["max_quantity"],
            "max_packs": packs,
            "max_amount": num(li["maximum_amount"]),
            "unit": li["unit_of_measure"],
            "repeats": li["max_repeats"],
            "program_code": li["program_code"],
            "program": li["program_title"] or li["program_code"],
            "general_program": li["program_code"] == "GE",
            "benefit": li["benefit_type"],
            "access": access[0], "access_help": access[1],
            "brands": brands,
            "retail": bool(retail),
            "general_min": min((b["general"] for b in retail), default=None),
            "general_max": max((b["general"] for b in retail), default=None),
            "conc_min": min((b["concession"] for b in retail if b["concession"] is not None),
                            default=None),
            "settings": sorted(settings_by_code.get(code, {}).values(),
                               key=lambda s: (not s["retail"], s["title"] or "")),
            "restrictions": restrictions[code]["restriction"],
            "conditions": sorted({c for c in (condition_of(r["text"])
                                  for r in restrictions[code]["restriction"]) if c}),
            "phases": sorted({r["phase"] for r in restrictions[code]["restriction"] if r["phase"]}),
            "notes": restrictions[code]["note"],
            "cautions": restrictions[code]["caution"],
            "prescribers": [p for p in PRESCRIBER_ORDER if p in prescribers[code]],
            "atc": sorted(atc.get(code, [])),
            "first_listed": li["first_listed_date"].isoformat() if li["first_listed_date"] else None,
            "pbs_url": None if li["program_code"] in NO_PBS_PAGE_PROGRAMS
                       else PBS_ITEM_URL.format(code=code),
            "imdq60": li["imdq60"], "imdq60_base": li["imdq60_base"], "pair": li["sixty_day_pair"],
        })

    code_to_slug = {it["code"]: m["slug"] for m in medicines.values() for it in m["items"]}
    link_sixty_day(medicines)

    for m in medicines.values():
        items = m["items"]
        items.sort(key=lambda it: (not it["general_program"], it["program"] or "",
                                   natural_key(it["form"]), -(it["max_packs"] or 0),
                                   it["conditions"], it["code"]))
        # One card per form and program; item codes sharing a form become variants.
        forms = {}
        for it in items:
            forms.setdefault((it["form"], it["program"]), []).append(it)
        m["forms"] = [{"form": f, "program": prog, "general_program": its[0]["general_program"],
                       "items": its} for (f, prog), its in forms.items()]
        for f in m["forms"]:
            label_variants(f["items"])
            # The collapsed row a patient scans: price range and access for the form.
            retail_items = [it for it in f["items"] if it["retail"]]
            f["general_min"] = min((it["general_min"] for it in retail_items), default=None)
            f["general_max"] = max((it["general_max"] for it in retail_items), default=None)
            benefits = {it["benefit"] for it in f["items"]}
            f["benefit"] = benefits.pop() if len(benefits) == 1 else None
            f["access"] = ACCESS[f["benefit"]][0] if f["benefit"] in ACCESS else None

        # What the prescription picker needs: one entry per form, one per listing within it.
        m["picker"] = {"forms": [{
            "label": f["form"] or f["items"][0]["code"],
            "program": None if f["general_program"] else f["program"],
            "items": [{"code": it["code"], "label": it.get("variant") or "", "sixty": it["imdq60"],
                       "price": money(it["general_min"]) if it["general_min"] is not None else "",
                       "conc": money(it["conc_min"]) if it["conc_min"] is not None else "",
                       "retail": it["retail"]} for it in f["items"]],
        } for f in m["forms"]]}
        retail = [it for it in items if it["retail"]]
        m["general_min"] = min((it["general_min"] for it in retail), default=None)
        m["general_max"] = max((it["general_max"] for it in retail), default=None)
        m["conc_min"] = min((it["conc_min"] for it in retail if it["conc_min"] is not None),
                            default=None)
        benefits = {it["benefit"] for it in items}
        m["access"] = ACCESS.get(benefits.pop()) if len(benefits) == 1 else None
        m["prescribers"] = [p for p in PRESCRIBER_ORDER
                            if any(p in it["prescribers"] for it in items)]
        m["programs"] = sorted({it["program"] for it in items if not it["general_program"]})
        m["class"] = class_name(m["atc"], q["atc_names"])
        m["atc"] = sorted(m["atc"])
        m["brand_names"] = sorted({b["name"] for it in items for b in it["brands"] if b["name"]})

    changes = build_changes(q, month, code_to_slug, drug_name, medicines)
    meds_sorted = sorted(medicines.values(), key=lambda m: m["name"].lower())
    return {
        "az": build_az(meds_sorted),
        "sixty_medicines": sum(1 for m in meds_sorted if m["sixty_day"]),
        "areas": build_areas(meds_sorted, q["atc_names"]),
        "month": month, "month_label": month_label(month),
        "copay_general": copay_general, "copay_conc": copay_conc,
        "threshold_general": num(q["copay"]["safety_net_general"]) if q["copay"] else None,
        "threshold_conc": num(q["copay"]["safety_net_concessional"]) if q["copay"] else None,
        "medicines": sorted(medicines.values(), key=lambda m: m["name"].lower()),
        "changes": changes,
        "counts": {"medicines": len(medicines), "items": len(q["listing"]),
                   "brands": len(q["brands"])},
    }


def short_phase(phase):
    """A heading-length treatment phase; the full wording stays in the criteria.

    "Initial treatment - Initial 1 (new patient)" -> "Initial treatment"
    """
    phase = re.sub(r"\s*\([^)]*\)", "", phase)
    return phase.split(" - ")[0].strip()


def link_sixty_day(medicines):
    """Attach each listing's 30/60-day counterpart and the yearly saving it offers.

    The pairing comes from the ingest (ingest/sixty_day.py). The saving assumes a
    medicine taken every month, so 12 thirty-day scripts against 6 sixty-day ones, at
    the no-premium price: the same PBS maximums shown everywhere else.
    """
    items = {it["code"]: it for m in medicines.values() for it in m["items"]}

    def saving(base, sixty, key):
        if not (base["retail"] and sixty["retail"]) or base[key] is None or sixty[key] is None:
            return None
        return round(12 * base[key] - 6 * sixty[key], 2)

    for it in items.values():
        other = items.get(it["pair"]) if it["pair"] else None
        it["pair_item"] = other
        it["saving"] = it["saving_conc"] = None
        if other:
            base, sixty = (it, other) if it["imdq60_base"] else (other, it)
            it["saving"] = saving(base, sixty, "general_min")
            it["saving_conc"] = saving(base, sixty, "conc_min")
    # One 60-day listing can replace several 30-day ones (and then links back to none).
    # Name them all, and quote the smallest of their savings so it is never overstated.
    for it in items.values():
        if not it["imdq60"]:
            continue
        bases = [b for b in items.values() if b["imdq60_base"] and b["pair"] == it["code"]]
        it["replaces"] = [b["code"] for b in bases]
        if it["saving"] is None and bases:
            found = [s for s in (saving(b, it, "general_min") for b in bases) if s is not None]
            it["saving"] = min(found) if found else None
    for m in medicines.values():
        m["sixty_day"] = any(it["imdq60"] for it in m["items"])
        savings = [it["saving"] for it in m["items"] if it["imdq60_base"] and it["saving"]]
        m["sixty_day_saving"] = max(savings) if savings else None


def label_variants(items):
    """Name each item code in a form by what sets it apart from its siblings.

    Codes sharing a form differ by condition, treatment phase, quantity or access. Only
    the attributes that actually vary within the group are named, so an unrestricted
    60-day listing reads "Up to 2 packs" rather than repeating what every sibling shares.
    """
    if len(items) < 2:
        return
    facets = [
        lambda it: " or ".join(it["conditions"]),
        lambda it: ", ".join(dict.fromkeys(short_phase(ph) for ph in it["phases"])),
        lambda it: ("60-day script" if it["imdq60"] else
                    f"Up to {it['max_packs']} pack{'s' if it['max_packs'] != 1 else ''}" if it["max_packs"] else ""),
        lambda it: it["access"],
    ]
    varying = [f for f in facets if len({f(it) for it in items}) > 1]
    for it in items:
        parts = [p for p in (f(it) for f in varying) if p]
        label = " · ".join(parts)
        if len(label) > 90:
            label = label[:88].rsplit(" ", 1)[0].rstrip(" ·,") + "…"
        it["variant"] = (label[:1].upper() + label[1:]) if label else f"Item {it['code']}"


def sentence(name):
    """ATC names arrive in capitals at the top levels."""
    return name[:1] + name[1:].lower() if name and name.isupper() else name


def build_az(meds):
    letters = {}
    for m in meds:
        first = m["name"][:1].upper()
        letters.setdefault(first if first.isalpha() else "#", []).append(m)
    return [{"letter": k, "meds": letters[k]} for k in sorted(letters, key=lambda k: (k == "#", k))]


def build_areas(meds, atc_names):
    """Medicines by body system (ATC level 1), grouped by therapeutic group (level 2).

    A medicine with codes in several systems appears in each, as in any drug index.
    """
    areas = {}
    for m in meds:
        for code in m["atc"]:
            top, sub = code[:1], code[:3]
            if top not in BODY_SYSTEMS:
                continue
            area = areas.setdefault(top, {"code": top, "name": BODY_SYSTEMS[top],
                                          "official": sentence(atc_names.get(top, "")), "groups": {}})
            group = area["groups"].setdefault(sub, {"code": sub, "name": sentence(atc_names.get(sub, sub)), "meds": {}})
            group["meds"][m["slug"]] = m
    out = []
    for top in sorted(areas):
        a = areas[top]
        groups = [{"code": g["code"], "name": g["name"], "meds": list(g["meds"].values())}
                  for g in sorted(a["groups"].values(), key=lambda g: g["name"].lower())]
        count = len({m["slug"] for g in groups for m in g["meds"]})
        out.append({**{k: a[k] for k in ("code", "name", "official")}, "groups": groups, "count": count})
    return out


def class_name(codes, names):
    """The most specific shared drug class: ATC level 4 (e.g. C10AA), else level 3."""
    for length in (5, 4, 3):
        prefixes = {c[:length] for c in codes if len(c) >= length}
        if len(prefixes) == 1:
            p = prefixes.pop()
            if names.get(p):
                name = names[p]
                # Some ATC levels arrive in capitals ("DRUGS FOR OBSTRUCTIVE AIRWAY DISEASES").
                return name[:1] + name[1:].lower() if name.isupper() else name
    return None


def describe_change(c, access_names):
    t, f, old, new = c["change_type"], c["field_name"], c["old_value"], c["new_value"]
    if t == "new":
        return "new", "Newly listed on the PBS", True
    if t == "delisted":
        return "delisted", "Removed from the PBS", True
    if t == "benefit_type_changed":
        return ("access", f"Access changed from {access_names.get(old, old)} to "
                          f"{access_names.get(new, new)}", True)
    if t == "max_repeats_changed":
        return "quantity", f"Repeats changed from {old} to {new}", True
    if t == "price_changed" and f == "brand_premium":
        return ("premium", f"Brand premium changed from {money(num_s(old))} to "
                           f"{money(num_s(new))}", True)
    if t == "price_changed":
        return ("price", f"PBS price changed from {money(num_s(old))} to {money(num_s(new))}",
                False)
    return None  # e.g. sixty_day_eligible_changed: the field is not validated


def num_s(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_changes(q, month, code_to_slug, drug_name, medicines):
    access_names = {k: v[0] for k, v in ACCESS.items()}
    months = q["months"]
    by_month = defaultdict(list)
    for c in q["changes"]:
        # The first month held has nothing before it; every item reads as "new".
        if c["month"] == months[0]:
            continue
        d = describe_change(c, access_names)
        if not d:
            continue
        kind, text, public = d
        code = c["pbs_code"]
        past = q["past_names"].get(code, (None, None))
        name = display(drug_name.get(code) or past[0] or code)
        entry = {"month": c["month"], "code": code, "kind": kind, "text": text,
                 "public": public, "name": name, "slug": code_to_slug.get(code),
                 "form": past[1]}
        by_month[c["month"]].append(entry)
        if entry["slug"]:
            medicines[entry["slug"]]["history"].append(entry)

    for m in medicines.values():
        m["history"].sort(key=lambda e: e["month"], reverse=True)

    out = []
    for mo in months[1:]:
        prev = months[months.index(mo) - 1]
        entries = sorted(by_month.get(mo, []), key=lambda e: (e["name"].lower(), e["code"]))
        groups = defaultdict(list)
        for e in entries:
            groups[e["kind"]].append(e)
        # One row per medicine and change: several item codes of one medicine often
        # change together, and a row per code reads as duplicates.
        for kind, rows in groups.items():
            merged = {}
            for e in rows:
                key = (e["name"], e["text"] if kind in ("premium", "price", "access", "quantity") else "")
                row = merged.setdefault(key, {**e, "forms": [], "codes": []})
                if e["form"] and e["form"] not in row["forms"]:
                    row["forms"].append(e["form"])
                row["codes"].append(e["code"])
            for row in merged.values():
                row["forms"].sort(key=natural_key)
            groups[kind] = list(merged.values())
        gap = _months_between(prev, mo) > 1
        out.append({"month": mo, "label": month_label(mo), "prev_label": month_label(prev),
                    "gap": gap, "groups": groups, "total": len(entries)})
    return list(reversed(out))


def _months_between(a, b):
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


# ── Write ─────────────────────────────────────────────────────────────────────


# ATC anatomical main groups, in the words a patient would use. The official WHO name is
# shown alongside, so nothing is renamed, only explained.
BODY_SYSTEMS = {
    "A": "Digestion, diabetes and metabolism",
    "B": "Blood and clotting",
    "C": "Heart and blood pressure",
    "D": "Skin",
    "G": "Urinary, sexual and reproductive health",
    "H": "Hormones",
    "J": "Infections",
    "L": "Cancer and the immune system",
    "M": "Muscles, bones and joints",
    "N": "Brain, nerves and mental health",
    "P": "Parasites",
    "R": "Lungs and breathing",
    "S": "Eyes and ears",
    "V": "Other medicines and products",
}

# Common medicines offered as search examples on the home page; missing ones are skipped.
EXAMPLES = ["atorvastatin", "metformin", "salbutamol", "amoxicillin", "sertraline",
            "rosuvastatin"]

# The medicine whose real listings table is shown on the pharmacist home page.
PRO_SHOWCASE = "lamotrigine"

# The real medicines shown as preview cards in the home page hero.
FEATURED = ("atorvastatin", "metformin")

CHANGE_SHORT = {"new": "new listings", "delisted": "removed from the PBS",
                "access": "access changes", "quantity": "quantity changes",
                "premium": "brand premium changes", "price": "PBS price changes"}

CHANGE_GROUPS = [
    ("new", "New listings"), ("delisted", "Removed from the PBS"),
    ("access", "Access changes"), ("quantity", "Quantity and repeat changes"),
    ("premium", "Brand premium changes"), ("price", "PBS price changes"),
]


def planner_data(model):
    """What the "My medicines" planner needs, for every item a community pharmacy prices.

    All from published PBS fields: the brand's general charge and premium, the
    concessional charge (derived by api/services/pricing, as elsewhere on the site), and
    max_record_val_for_safety_net, which is what counts towards the safety net. The
    planner subtracts each brand's premium to get the base charge, because premiums are
    paid on top and never count towards the safety net.
    """
    items = {}
    for m in model["medicines"]:
        for it in m["items"]:
            retail = [b for b in it["brands"] if b["retail"] and b["general"] is not None]
            if not retail:
                continue
            prem = [b["premium"] or 0.0 for b in retail]
            general = min(b["general"] - p for b, p in zip(retail, prem))
            conc = min((b["concession"] - p for b, p in zip(retail, prem) if b["concession"] is not None),
                       default=model["copay_conc"])
            record = max((b["safety_net"] for b in retail if b["safety_net"] is not None), default=general)
            items[it["code"]] = {
                "s": m["slug"], "n": m["name"], "f": it["form"], "v": it.get("variant", ""),
                "g": round(general, 2), "c": round(conc, 2), "r": round(record, 2),
                "b": [[b["name"], round(p, 2)] for b, p in zip(retail, prem) if p > 0.004],
            }
            # 60-day: "d" marks the 60-day listing, "p" is the other side of its pair.
            if it["imdq60"]:
                items[it["code"]]["d"] = 1
            if it["pair"] and it["pair_item"] and it["pair_item"]["retail"]:
                items[it["code"]]["p"] = it["pair"]
    return {
        "month": model["month"], "label": model["month_label"],
        "copay": [model["copay_general"], model["copay_conc"]],
        "threshold": [model["threshold_general"], model["threshold_conc"]],
        "items": items,
    }


def render(model, out: Path):
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    by_slug = {m["slug"]: m for m in model["medicines"]}
    examples = [by_slug[s] for s in EXAMPLES if s in by_slug]
    model["examples"] = [m["slug"] for m in examples]
    env.filters["money"] = money
    env.filters["month_label"] = month_label
    env.globals.update(site=model, change_groups=CHANGE_GROUPS, change_short=CHANGE_SHORT,
                       latest_changes=model["changes"][0] if model["changes"] else None)

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "static", out / "static")

    def page(path, template, **ctx):
        dest = out / path / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(env.get_template(template).render(**ctx))

    page("", "home.html", title=None, examples=examples, nav="home",
         featured=by_slug.get(FEATURED[0]), featured_back=by_slug.get(FEATURED[1]))
    # The pharmacist-first home page, drafted alongside the patient one for comparison.
    showcase = by_slug.get(PRO_SHOWCASE)
    page("pro", "pro.html", title="PBS lookup for pharmacists and prescribers", nav="pro", showcase=showcase)
    for m in model["medicines"]:
        page(f"medicine/{m['slug']}", "medicine.html", m=m, title=m["name"], nav="medicine")
        for it in m["items"]:
            page(f"item/{it['code']}", "item.html", m=m, it=it, title=f"{it['code']} · {m['name']}")
    for ch in model["changes"]:
        page(f"changes/{ch['month']}", "changes.html", ch=ch, title=f"PBS changes, {ch['label']}",
             nav="changes")
    page("changes", "changes_index.html", title="PBS changes by month", nav="changes")
    page("my-medicines", "planner.html", title="My medicines", nav="planner")
    page("medicines", "az.html", title="Medicines A to Z", nav="browse")
    sixty = [m for m in model["medicines"] if m["sixty_day"]]
    page("60-day", "sixty_day.html", title="60-day prescriptions", nav="sixty", meds=sixty,
         listings=sum(1 for m in sixty for it in m["items"] if it["imdq60"]))
    page("browse", "browse_index.html", title="Browse by body system", nav="browse")
    for area in model["areas"]:
        page(f"browse/{area['code'].lower()}", "browse_area.html", area=area, title=area["name"], nav="browse")
    page("about/data", "data.html", title="How we check this data", nav="about")
    page("about", "about.html", title="About", nav="about")
    # Static hosts (Cloudflare Pages, Netlify) serve /404.html for unknown paths.
    (out / "404.html").write_text(env.get_template("404.html").render(title="Page not found", nav=None))

    index = []
    for m in model["medicines"]:
        entry = {"n": m["name"], "s": m["slug"], "b": m["brand_names"],
                 "c": [it["code"] for it in m["items"]], "k": m["class"] or ""}
        if m["sixty_day"]:
            entry["d"] = 1
        # Streamlined authority code -> the first listing it applies to. Pharmacists
        # verify these codes against the PBS criteria, so they are searchable too.
        codes = {}
        for it in m["items"]:
            for r in it["restrictions"]:
                if r["streamlined"]:
                    codes.setdefault(r["streamlined"], it["code"])
        if codes:
            entry["a"] = codes
        index.append(entry)
    (out / "search-index.json").write_text(json.dumps(index, separators=(",", ":")))
    (out / "site.json").write_text(json.dumps({"month": model["month"]}))
    (out / "planner.json").write_text(json.dumps(planner_data(model), separators=(",", ":")))


async def main(month, out):
    conn = await asyncpg.connect(DSN)
    try:
        if not month:
            month = await conn.fetchval(
                "SELECT month FROM schedules WHERE ingest_status = 'complete' "
                "ORDER BY month DESC LIMIT 1")
        q = await read_schedule(conn, month)
    finally:
        await conn.close()
    model = build_model(q, month)
    render(model, out)
    files = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Built {model['month_label']}: {model['counts']['medicines']:,} medicines, "
          f"{model['counts']['items']:,} items, {files:,} files, {size / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    a = ap.parse_args()
    asyncio.run(main(a.month, a.out))
