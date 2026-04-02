"""Normalise raw PBS API data into our internal format."""
from decimal import Decimal, InvalidOperation
from typing import Optional


BENEFIT_TYPE_MAP = {
    "U": "unrestricted",
    "R": "restricted",
    "S": "authority_streamlined",
    "A": "authority_required",
    "O": "hospital",
    "B": "brand_equivalent",
    "C": "combination",
    "D": "doctors_bag",
    "F": "mnemonics",
    "G": "general_schedule",
    "H": "highly_specialised",
    "N": "nursing_home",
    "P": "private",
    "T": "in_hospital",
    "W": "in_hospital_only",
    "X": "not_benefit",
}


def normalise_ingredient_name(name: str) -> str:
    """Title-case an ingredient name."""
    return name.title()


def parse_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def normalise_schedule(
    month: str,
    raw_items: list[dict],
    raw_restrictions: list[dict],
) -> dict:
    """
    Normalise raw PBS API data into internal format.
    Returns {"month": str, "medicines": [...], "items": [...]}.
    """
    # Build restriction lookup by pbs_code
    restriction_map: dict[str, list[dict]] = {}
    for r in raw_restrictions:
        code = r.get("pbs_code", "")
        if code not in restriction_map:
            restriction_map[code] = []
        restriction_map[code].append({
            "streamlined_code": r.get("streamlined_authority_code"),
            "indication": r.get("indication"),
            "restriction_text": r.get("restriction_text"),
            "prescriber_type": r.get("prescriber_type"),
            "authority_required": bool(r.get("authority_required", False)),
            "continuation_only": bool(r.get("continuation_only", False)),
        })

    # Deduplicate medicines by ingredient_lower
    medicines_map: dict[str, dict] = {}

    normalised_items = []
    for raw in raw_items:
        pbs_code = raw.get("pbs_code", "")
        drug_name = raw.get("drug_name", "")
        ingredient = normalise_ingredient_name(drug_name) if drug_name else ""
        ingredient_lower = ingredient.lower()

        if ingredient_lower and ingredient_lower not in medicines_map:
            medicines_map[ingredient_lower] = {
                "ingredient": ingredient,
                "ingredient_lower": ingredient_lower,
                "atc_code": raw.get("atc_code"),
                "therapeutic_group": raw.get("therapeutic_group"),
                "therapeutic_subgroup": raw.get("therapeutic_subgroup"),
            }

        raw_benefit = raw.get("benefit_type", "U")
        benefit_type = BENEFIT_TYPE_MAP.get(raw_benefit, raw_benefit)

        general_charge = parse_decimal(raw.get("general_patient_charge"))
        concessional_charge = parse_decimal(raw.get("concession_charge"))
        government_price = parse_decimal(raw.get("commonwealth_price"))
        brand_premium = parse_decimal(raw.get("brand_premium")) or Decimal("0.00")

        item = {
            "pbs_code": pbs_code,
            "ingredient_lower": ingredient_lower,
            "brand_name": raw.get("brand_name", ""),
            "brand_name_lower": raw.get("brand_name", "").lower(),
            "form": raw.get("form"),
            "strength": raw.get("strength"),
            "pack_size": raw.get("pack_size"),
            "pack_unit": raw.get("pack_unit"),
            "benefit_type": benefit_type,
            "formulary": raw.get("formulary"),
            "section": raw.get("section"),
            "program_code": raw.get("program_code"),
            "general_charge": general_charge,
            "concessional_charge": concessional_charge,
            "government_price": government_price,
            "brand_premium": brand_premium,
            "brand_premium_counts_to_safety_net": False,  # PBS rule: never counts
            "sixty_day_eligible": bool(raw.get("sixty_day_prescribing", False)),
            "max_quantity": raw.get("max_quantity"),
            "max_repeats": raw.get("max_repeats"),
            "dangerous_drug": bool(raw.get("dangerous_drug", False)),
            "restrictions": restriction_map.get(pbs_code, []),
        }
        normalised_items.append(item)

    return {
        "month": month,
        "medicines": list(medicines_map.values()),
        "items": normalised_items,
    }
