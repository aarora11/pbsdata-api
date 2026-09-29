"""60-day prescriptions: which listings they apply to, and how 30-day and 60-day listings pair.

PBS implements 60-day prescribing as a second item code with double the maximum
quantity, not as a flag on the original item. The data dictionary names both sides:

  policy_applied_imdq60_flag       the increased-quantity item (the 60-day script)
  policy_applied_imdq60_base_flag  the single-quantity item it was created from

PBS publishes no link between the two codes, so they are paired here. A base and a
60-day item pair when they share medicine, form, program and the conditions their
restrictions cover, and the 60-day item's maximum is exactly double. Conditions are
compared by name, not restriction code: the 60-day listing carries its own restriction
("the condition must be stable") with a different code for the same condition.

One 60-day item can replace several 30-day items: furosemide 13501N (200 tablets)
replaces both 1810G (2 packs of 50) and 2414C (1 pack of 100), and brimonidine 14497B
replaces separate listings for doctors and for optometrists. So a base links to its
60-day item whenever that is its only candidate, and the 60-day item links back only
when exactly one base points at it. A base with several candidate 60-day items is left
unlinked rather than guessed: a wrong link would show a patient a saving that doesn't
exist.

continued_dispensing_flag, which this project previously read as "60-day eligible", is
the unrelated Continued Dispensing (emergency supply) measure.
"""
from collections import defaultdict

from ingest.restriction_text import condition_of, paragraphs


def _conditions(item) -> frozenset:
    out = set()
    for r in item.get("restrictions") or []:
        if r.get("restriction_kind", "restriction") != "restriction":
            continue
        c = condition_of(paragraphs(r.get("restriction_text")))
        if c:
            out.add(c.lower())
    return frozenset(out)


def _key(item):
    return (item.get("ingredient_lower"), item.get("form"), item.get("program_code"), _conditions(item))


def _doubles(base, sixty) -> bool:
    for field in ("max_quantity", "maximum_amount"):
        b, s = base.get(field), sixty.get(field)
        if b is not None and s is not None:
            return float(s) == 2 * float(b)
    # Neither side states a maximum: the flags alone say which is which.
    return True


def pair_sixty_day(items: list[dict]) -> dict:
    """Set sixty_day_pair on every brand row of each paired listing. Returns counts.

    `items` are normalised brand-grain rows; the flags and pairing are per PBS code.
    """
    by_code = {}
    for it in items:
        by_code.setdefault(it["pbs_code"], it)

    bases = [it for it in by_code.values() if it.get("imdq60_base")]
    sixties = [it for it in by_code.values() if it.get("imdq60")]
    sixty_by_key = defaultdict(list)
    for s in sixties:
        sixty_by_key[_key(s)].append(s)

    candidates = {b["pbs_code"]: [s for s in sixty_by_key.get(_key(b), []) if _doubles(b, s)] for b in bases}
    claimed = defaultdict(list)
    for base_code, cands in candidates.items():
        for s in cands:
            claimed[s["pbs_code"]].append(base_code)

    links = {}
    for base_code, cands in candidates.items():
        if len(cands) == 1:
            links[base_code] = cands[0]["pbs_code"]
    for sixty_code, base_codes in claimed.items():
        linked = [b for b in base_codes if links.get(b) == sixty_code]
        if len(linked) == 1 and len(base_codes) == 1:
            links[sixty_code] = linked[0]

    for it in items:
        it["sixty_day_pair"] = links.get(it["pbs_code"])

    bases_linked = sum(1 for b in bases if b["pbs_code"] in links)
    sixties_reached = len({links[b["pbs_code"]] for b in bases if b["pbs_code"] in links})
    return {
        "base_items": len(bases),
        "sixty_day_items": len(sixties),
        "bases_linked": bases_linked,
        "sixty_day_reached": sixties_reached,
        "sixty_day_unreached": len(sixties) - sixties_reached,
        "ambiguous": sum(1 for c in candidates.values() if len(c) > 1),
    }
