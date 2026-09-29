"""60-day prescriptions: the flags PBS uses for them, and how 30-day and 60-day listings pair.

sixty_day_eligible was read from continued_dispensing_flag, the Continued Dispensing
emergency-supply measure. On 2026-08 that marked 726 items across 149 medicines as
60-day eligible; PBS's own 60-day flags mark 806 increased-quantity items across 283
medicines, in line with the "about 300 drugs" Grattan counts. Nothing checked the
mapping against a source outside the code, so it survived the audit.
"""
from ingest.normaliser import normalise_schedule
from ingest.sixty_day import pair_sixty_day


def _raw(code, **flags):
    return {"pbs_code": code, "drug_name": "Testmed", "brand_name": "Brand", "benefit_type_code": "U",
            "number_of_repeats": 5, "maximum_quantity_units": 30, "li_item_id": f"{code}_1_2_3_4",
            "program_code": "GE", "li_form": "Tablet 10 mg", **flags}


def test_continued_dispensing_is_not_sixty_day():
    items = {i["pbs_code"]: i for i in normalise_schedule("2026-04", [
        _raw("0010A", continued_dispensing_flag="Y",
             policy_applied_imdq60_flag="N", policy_applied_imdq60_base_flag="N"),
    ], [])["items"]}
    assert items["0010A"]["sixty_day_eligible"] is False
    assert items["0010A"]["imdq60"] is False


def test_both_sides_of_the_policy_are_flagged():
    items = {i["pbs_code"]: i for i in normalise_schedule("2026-04", [
        _raw("0011B", policy_applied_imdq60_base_flag="Y"),
        _raw("0012C", policy_applied_imdq60_flag="Y", maximum_quantity_units=60),
    ], [])["items"]}
    assert items["0011B"]["imdq60_base"] and not items["0011B"]["imdq60"]
    assert items["0012C"]["imdq60"] and not items["0012C"]["imdq60_base"]
    assert items["0011B"]["sixty_day_eligible"] and items["0012C"]["sixty_day_eligible"]
    # Unrestricted, same form and program, double the quantity: a pair.
    assert items["0011B"]["sixty_day_pair"] == "0012C"
    assert items["0012C"]["sixty_day_pair"] == "0011B"


def _item(code, qty, *, base=False, sixty=False, conditions=(), form="Tablet 25 mg", ingredient="lamotrigine"):
    restrictions = [{"restriction_kind": "restriction",
                     "restriction_text": f"<h1>Listing of Pharmaceutical Benefits (NHL) - Schedule 4 part 1</h1><p>{c}</p><p>Criteria.</p>"}
                    for c in conditions]
    return {"pbs_code": code, "ingredient_lower": ingredient, "form": form, "program_code": "GE",
            "max_quantity": qty, "imdq60_base": base, "imdq60": sixty, "restrictions": restrictions}


def test_pairs_on_condition_not_restriction_code():
    """Lamotrigine 25 mg: one 30/60 pair for epilepsy and one for bipolar disorder.

    The 60-day listing carries its own restriction ("the condition must be stable")
    under a different code, so codes never match; the condition does.
    """
    items = [
        _item("2848X", 56, base=True, conditions=["Epileptic seizures"]),
        _item("13842M", 112, sixty=True, conditions=["Epileptic seizures"]),
        _item("15338G", 56, base=True, conditions=["Bipolar disorder"]),
        _item("15346Q", 112, sixty=True, conditions=["Bipolar disorder"]),
    ]
    stats = pair_sixty_day(items)
    pairs = {i["pbs_code"]: i["sixty_day_pair"] for i in items}
    assert pairs == {"2848X": "13842M", "13842M": "2848X", "15338G": "15346Q", "15346Q": "15338G"}
    assert stats["bases_linked"] == 2 and stats["ambiguous"] == 0


def test_quantity_must_double():
    items = [_item("0020A", 56, base=True), _item("0021B", 100, sixty=True)]
    pair_sixty_day(items)
    assert all(i["sixty_day_pair"] is None for i in items)


def test_an_ambiguous_match_is_left_unpaired():
    """Two 60-day candidates for one base: guessing could show a saving that doesn't exist."""
    items = [_item("0030A", 30, base=True), _item("0031B", 60, sixty=True), _item("0032C", 60, sixty=True)]
    stats = pair_sixty_day(items)
    assert all(i["sixty_day_pair"] is None for i in items)
    assert stats["ambiguous"] == 1


def test_different_forms_do_not_pair():
    items = [_item("0040A", 30, base=True, form="Tablet 5 mg"), _item("0041B", 60, sixty=True, form="Tablet 25 mg")]
    pair_sixty_day(items)
    assert all(i["sixty_day_pair"] is None for i in items)


def test_every_brand_row_of_a_code_gets_the_pair():
    items = [_item("0050A", 30, base=True), _item("0050A", 30, base=True), _item("0051B", 60, sixty=True)]
    pair_sixty_day(items)
    assert [i["sixty_day_pair"] for i in items] == ["0051B", "0051B", "0050A"]


def test_administrative_lines_are_not_conditions():
    """Metformin 850 mg, 2026-06: 13952H (60-day, 120) and 1801T (30-day, 60) are twins.

    The 60-day listing opens with the stability clause PBS adds to every 60-day item;
    the 30-day one with the prescriber-type heading. Neither is a condition, so both
    have none and they pair. Read as conditions, they never matched.
    """
    head = "<h1>Listing of Pharmaceutical Benefits (NHL) - Schedule 4 part 1</h1>"
    base = {"pbs_code": "1801T", "ingredient_lower": "metformin", "form": "Tablet 850 mg", "program_code": "GE",
            "max_quantity": 60, "imdq60_base": True, "imdq60": False,
            "restrictions": [{"restriction_kind": "restriction", "restriction_text":
                head + "<p>For prescribing by certain health practitioners</p><p>Must be treated by a health practitioner who is any of: ...</p>"}]}
    sixty = {"pbs_code": "13952H", "ingredient_lower": "metformin", "form": "Tablet 850 mg", "program_code": "GE",
             "max_quantity": 120, "imdq60_base": False, "imdq60": True,
             "restrictions": [{"restriction_kind": "restriction", "restriction_text":
                head + "<p>The condition must be stable for the prescriber to consider the listed maximum quantity of this medicine suitable for this patient.</p>"}]}
    pair_sixty_day([base, sixty])
    assert base["sixty_day_pair"] == "13952H" and sixty["sixty_day_pair"] == "1801T"


def test_one_sixty_day_item_can_replace_several_bases():
    """Furosemide 13501N (200) replaces 1810G (2 x 50) and 2414C (1 x 100).

    Each base links to it; the 60-day item can't name "the" 30-day version, so it has
    no back-link.
    """
    items = [_item("1810G", 100, base=True, form="Tablet 20 mg", ingredient="furosemide"),
             _item("2414C", 100, base=True, form="Tablet 20 mg", ingredient="furosemide"),
             _item("13501N", 200, sixty=True, form="Tablet 20 mg", ingredient="furosemide")]
    stats = pair_sixty_day(items)
    pairs = {i["pbs_code"]: i["sixty_day_pair"] for i in items}
    assert pairs == {"1810G": "13501N", "2414C": "13501N", "13501N": None}
    assert stats["bases_linked"] == 2 and stats["sixty_day_reached"] == 1
