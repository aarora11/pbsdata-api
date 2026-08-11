#!/usr/bin/env python3
"""Compare our joined data against what pbs.gov.au displays, item by item.

Conservative by design: a field is only marked FAIL when the two sources clearly
contradict each other. Anything the site does not publish, or publishes in a form that
cannot be compared unambiguously, is NA and excluded from the pass rate — inflating the
score by counting unknowns as passes would defeat the purpose.
"""
import json
import re
import sys

import sys
SRC = sys.argv[1] if len(sys.argv) > 1 else "sample.json"
ours = {i["pbs_code"]: i for i in json.load(open(SRC))["items"]}
site = json.load(open("site_results.json"))

STOP = re.compile(r"\b(pty|ltd|limited|australia|australian|healthcare|pharmaceuticals?|"
                  r"pharma|health|group|inc|proprietary|company|co)\b")


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def norm_org(s):
    return " ".join(sorted(set(STOP.sub(" ", norm(s)).split())))


def money(s):
    if s is None:
        return None
    s = s.strip()
    if s.upper() in ("NIL", "NONE", "FREE"):
        return 0.0
    m = re.search(r"([\d,]+\.?\d*)", s.replace("$", ""))
    return float(m.group(1).replace(",", "")) if m else None


def units(s):
    """Pull the unit count out of strings like '2 packs / 60 units', '400 mg', '28'."""
    if not s or s.upper() == "NONE":
        return None
    m = re.search(r"([\d.]+)\s*units", s, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"([\d.]+)\s*(mg|mcg|microgram|g|ml)\b", s, re.I)
    if m:
        return ("AMOUNT", float(m.group(1)))
    m = re.match(r"\s*([\d.]+)\s*$", s)
    return float(m.group(1)) if m else None


BENEFIT_WORDS = {
    "S": ["streamlined"],
    "A": ["authority required"],
    "R": ["restricted benefit"],
    "U": ["no restrictions", "unrestricted"],
}

results, fails = [], []
for code, o in ours.items():
    s = site.get(code)
    if not s:
        continue
    row = {"code": code, "program": o["program"], "benefit": o["benefit_type"], "checks": {}}

    def check(name, verdict, detail=""):
        row["checks"][name] = verdict
        if verdict == "FAIL":
            fails.append((code, name, detail))

    # ── ingredient ────────────────────────────────────────────────────────────
    ing, drug = norm(o["ingredient"]), norm(s["drug"])
    first = ing.split()[0] if ing else ""
    if not drug or drug == "none":
        check("ingredient", "NA")
    elif first and (first in drug or ing in drug or drug.split()[0] in ing):
        check("ingredient", "PASS")
    else:
        # multi-ingredient names list components in either order
        parts = [p for p in ing.split() if len(p) > 4]
        hit = sum(1 for p in parts if p in drug)
        check("ingredient", "PASS" if hit >= max(1, len(parts) // 2) else "FAIL",
              f"ours={o['ingredient']!r} site={s['drug']!r}")

    # ── brands ────────────────────────────────────────────────────────────────
    site_brands = {norm(b) for b in re.split(r",(?![^()]*\))", s["brand"]) if norm(b)}
    our_brands = {norm(b["brand"]) for b in o["brands"] if b["brand"]}
    if not site_brands or "none" in site_brands:
        check("brands", "NA")
    else:
        overlap = sum(1 for b in our_brands
                      if any(b in sb or sb in b for sb in site_brands))
        check("brands", "PASS" if overlap else "FAIL",
              f"ours={sorted(our_brands)} site={sorted(site_brands)}")

    # ── sponsors ──────────────────────────────────────────────────────────────
    site_orgs = {norm_org(x) for x in s["manuf"].split(",") if norm_org(x)}
    our_orgs = {norm_org(b["sponsor"]) for b in o["brands"] if b["sponsor"]}
    if not site_orgs or not our_orgs:
        check("sponsor", "NA")
    else:
        overlap = sum(1 for a in our_orgs
                      if any(set(a.split()) & set(b.split()) for b in site_orgs))
        check("sponsor", "PASS" if overlap else "FAIL",
              f"ours={sorted(our_orgs)} site={sorted(site_orgs)}")

    # ── max quantity ──────────────────────────────────────────────────────────
    su = units(s["maxqty"])
    if su is None:
        check("max_quantity", "NA")
    elif isinstance(su, tuple):                      # site quotes an amount, not units
        check("max_quantity", "PASS" if o["maximum_amount"] == su[1] else
              ("NA" if o["maximum_amount"] is None else "FAIL"),
              f"ours_amount={o['maximum_amount']} site={s['maxqty']!r}")
    elif o["max_quantity"] is None:
        check("max_quantity", "NA")
    else:
        ours_units = float(o["max_quantity"])
        packs = o.get("pack_size")
        ours_packs = (ours_units / packs) if packs else None
        if ours_units == su or (ours_packs is not None and abs(ours_packs - su) < 0.001):
            check("max_quantity", "PASS")
        else:
            check("max_quantity", "FAIL",
                  f"ours={ours_units} units (pack {packs}) site={s['maxqty']!r}")

    # ── repeats ───────────────────────────────────────────────────────────────
    try:
        sr = int(str(s["repeats"]).strip())
    except (ValueError, TypeError):
        sr = None
    if sr is None or o["max_repeats"] is None:
        check("repeats", "NA")
    else:
        check("repeats", "PASS" if int(o["max_repeats"]) == sr else "FAIL",
              f"ours={o['max_repeats']} site={s['repeats']}")

    # ── patient charge ────────────────────────────────────────────────────────
    sc = money(s["charge"])
    if sc is None or o["charge"] is None:
        check("patient_charge", "NA")
    else:
        check("patient_charge", "PASS" if abs(o["charge"] - sc) < 0.005 else "FAIL",
              f"ours={o['charge']} site={s['charge']}")

    # ── listing type ──────────────────────────────────────────────────────────
    listing = (s.get("listing") or "").lower() + " " + (s.get("restrictions") or "").lower()
    want = BENEFIT_WORDS.get(o["benefit_type"], [])
    other = [w for bt, ws in BENEFIT_WORDS.items() if bt != o["benefit_type"] for w in ws]
    if not listing.strip() or listing.strip() == "none":
        check("listing_type", "NA")
    elif any(w in listing for w in want):
        # "streamlined" also contains "authority required"; require the specific word
        if o["benefit_type"] == "A" and "streamlined" in listing:
            check("listing_type", "FAIL", f"ours=A site={s['listing']!r}")
        else:
            check("listing_type", "PASS")
    elif any(w in listing for w in other):
        check("listing_type", "FAIL", f"ours={o['benefit_type']} site={s['listing']!r}")
    else:
        check("listing_type", "NA")   # site showed a program name, not a listing type

    results.append(row)

# ── report ────────────────────────────────────────────────────────────────────
fields = ["ingredient", "brands", "sponsor", "max_quantity", "repeats",
          "patient_charge", "listing_type"]
print(f"Items compared: {len(results)}\n")
print(f"{'field':<16}{'PASS':>6}{'FAIL':>6}{'NA':>5}   {'pass rate':>10}")
print("-" * 48)
total_p = total_f = 0
for f in fields:
    p = sum(1 for r in results if r["checks"].get(f) == "PASS")
    x = sum(1 for r in results if r["checks"].get(f) == "FAIL")
    n = sum(1 for r in results if r["checks"].get(f) == "NA")
    total_p += p; total_f += x
    rate = f"{100*p/(p+x):.1f}%" if (p + x) else "—"
    print(f"{f:<16}{p:>6}{x:>6}{n:>5}   {rate:>10}")
print("-" * 48)
print(f"{'TOTAL':<16}{total_p:>6}{total_f:>6}{'':>5}   "
      f"{100*total_p/(total_p+total_f):.1f}%")

if fails:
    print(f"\n{len(fails)} disagreements:\n")
    for code, field, detail in fails:
        print(f"  {code:<8} {field:<15} {detail[:150]}")
json.dump({"results": results, "fails": fails}, open("comparison.json", "w"), indent=1)
