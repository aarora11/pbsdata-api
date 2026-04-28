"""Normalise raw PBS API data into our internal format.

Field names here reflect the real PBS API v3 at data-api.health.gov.au/pbs/api/v3.
"""
import datetime
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


def parse_date(value) -> Optional[datetime.date]:
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def yn(value) -> bool:
    """Convert Y/N string to bool."""
    return str(value).upper() == "Y" if value else False


def normalise_schedule(
    month: str,
    raw_items: list[dict],
    raw_restrictions: list[dict],
    # Pricing
    raw_item_pricing: list[dict] = None,          # /item-dispensing-rule-relationships
    raw_copayments: list[dict] = None,             # /copayments
    # Reference data
    raw_organisations: list[dict] = None,          # /organisations
    raw_programs: list[dict] = None,               # /programs
    raw_prescribers: list[dict] = None,            # /prescribers
    raw_atc_codes: list[dict] = None,              # /atc-codes
    raw_dispensing_rules: list[dict] = None,       # /dispensing-rules
    raw_fees: list[dict] = None,                   # /fees
    raw_prescribing_texts: list[dict] = None,      # /prescribing-texts
    raw_indications: list[dict] = None,            # /indications
    raw_summary_of_changes: list[dict] = None,     # /summary-of-changes
    # Relationship tables
    raw_item_restriction_relationships: list[dict] = None,              # /item-restriction-relationships
    raw_item_atc_relationships: list[dict] = None,                      # /item-atc-relationships
    raw_item_prescribing_text_relationships: list[dict] = None,         # /item-prescribing-text-relationships
    raw_restriction_prescribing_text_relationships: list[dict] = None,  # /restriction-prescribing-text-relationships
    raw_item_dispensing_rule_relationships: list[dict] = None,          # /item-dispensing-rule-relationships (alias)
    raw_item_organisation_relationships: list[dict] = None,             # /item-organisation-relationships
    # Kept for backward compat with old call signatures (ignored, use above)
    raw_fees_old: list[dict] = None,
    raw_amt_items: list[dict] = None,
    raw_item_overviews: list[dict] = None,
    raw_item_amt: list[dict] = None,
    raw_item_dispensing_rules: list[dict] = None,
    raw_program_dispensing_rules: list[dict] = None,
    raw_restriction_prescribing_text_relationships_old: list[dict] = None,
    raw_item_prescribing_texts: list[dict] = None,
    # New endpoints (migration 007)
    raw_containers: list[dict] = None,
    raw_container_organisation_relationships: list[dict] = None,
    raw_criteria: list[dict] = None,
    raw_criteria_parameter_relationships: list[dict] = None,
    raw_parameters: list[dict] = None,
    raw_markup_bands: list[dict] = None,
    raw_item_pricing_events: list[dict] = None,
    raw_extemporaneous_ingredients: list[dict] = None,
    raw_extemporaneous_preparations: list[dict] = None,
    raw_extemporaneous_prep_sfp_relationships: list[dict] = None,
    raw_extemporaneous_tariffs: list[dict] = None,
    raw_standard_formula_preparations: list[dict] = None,
) -> dict:
    """
    Normalise raw PBS API data into internal format.

    All parameters are optional and default to None (treated as empty list).
    Existing callers using (month, raw_items, raw_restrictions) continue to work.
    """
    # Use alias if new names not provided (for runner compat during transition)
    if raw_item_pricing is None:
        raw_item_pricing = raw_item_dispensing_rule_relationships or []

    # ── Build lookup maps ─────────────────────────────────────────────────────

    # Item overviews: {pbs_code: overview_data}
    item_overviews_map: dict[str, dict] = {}
    for ov in (raw_item_overviews or []):
        pbs = ov.get("pbs_code", "")
        if pbs:
            item_overviews_map[pbs] = ov

    # Pricing: {li_item_id: {pricing fields}}
    pricing_map: dict[str, dict] = {}
    for p in (raw_item_pricing or []):
        key = p.get("li_item_id", "")
        if key and key not in pricing_map:
            pricing_map[key] = p

    # Restrictions: {res_code: restriction_data}
    restriction_lookup: dict[str, dict] = {}
    for r in (raw_restrictions or []):
        code = r.get("res_code") or r.get("streamlined_authority_code", "")
        if code:
            restriction_lookup[code] = r

    # Item -> restriction codes (from explicit relationship table)
    item_restriction_map: dict[str, list[str]] = {}
    for rel in (raw_item_restriction_relationships or []):
        pbs = rel.get("pbs_code", "")
        res = rel.get("res_code", "")
        if pbs and res:
            item_restriction_map.setdefault(pbs, []).append(res)

    # Fallback: if no relationship table, build from restrictions that have pbs_code
    # (old-style fixtures/test data where res_code was stored as pbs_code-keyed)
    if not item_restriction_map:
        for r in (raw_restrictions or []):
            pbs = r.get("pbs_code", "")
            res = r.get("res_code") or r.get("streamlined_authority_code", "")
            if pbs and res:
                item_restriction_map.setdefault(pbs, []).append(res)
            # Legacy: old fixture format used pbs_code on restriction rows
            if pbs and not res:
                item_restriction_map.setdefault(pbs, []).append(r.get("res_code", ""))

    # ATC: {pbs_code: atc_code} (primary ATC for each item)
    item_atc_map: dict[str, str] = {}
    for rel in (raw_item_atc_relationships or []):
        pbs = rel.get("pbs_code", "")
        atc = rel.get("atc_code", "")
        if pbs and atc and pbs not in item_atc_map:
            item_atc_map[pbs] = atc



    # ── Medicines deduplication ───────────────────────────────────────────────
    medicines_map: dict[str, dict] = {}
    normalised_items = []

    for raw in raw_items:
        pbs_code = raw.get("pbs_code", "")

        # Drug name: prefer li_drug_name, fall back to drug_name
        drug_name = raw.get("li_drug_name") or raw.get("drug_name", "")
        ingredient = normalise_ingredient_name(drug_name) if drug_name else ""
        ingredient_lower = ingredient.lower()

        # ATC code for this item
        atc_code = item_atc_map.get(pbs_code) or raw.get("atc_code")

        if ingredient_lower and ingredient_lower not in medicines_map:
            medicines_map[ingredient_lower] = {
                "ingredient": ingredient,
                "ingredient_lower": ingredient_lower,
                "atc_code": atc_code,
                "therapeutic_group": raw.get("therapeutic_group_title") or raw.get("therapeutic_group"),
                "therapeutic_subgroup": raw.get("therapeutic_subgroup"),
            }

        # Benefit type
        raw_benefit = raw.get("benefit_type_code") or raw.get("benefit_type", "U")
        benefit_type = BENEFIT_TYPE_MAP.get(raw_benefit, raw_benefit)

        # Pricing: from item-dispensing-rule-relationships or legacy fields on item
        li_item_id = raw.get("li_item_id", "")
        pricing = pricing_map.get(li_item_id, {})

        general_charge = (
            parse_decimal(pricing.get("max_general_patient_charge"))
            or parse_decimal(raw.get("general_patient_charge"))  # legacy fixture field
        )
        government_price = (
            parse_decimal(pricing.get("cmnwlth_price_to_pharmacist"))
            or parse_decimal(raw.get("commonwealth_price"))  # legacy fixture field
        )
        brand_premium = (
            parse_decimal(pricing.get("brand_premium"))
            or parse_decimal(raw.get("brand_premium"))
            or Decimal("0.00")
        )
        # concessional_charge: schedule-level, not per-item in real API
        # Kept for backward compat, populated from copayments if available
        concessional_charge = parse_decimal(raw.get("concession_charge"))  # legacy only

        # Sixty day: real field is continued_dispensing_flag (Y/N)
        sixty_day = (
            yn(raw.get("continued_dispensing_flag"))
            or bool(raw.get("sixty_day_prescribing", False))  # legacy
        )

        # Restrictions for this item
        res_codes = item_restriction_map.get(pbs_code, [])
        item_restrictions = []
        for res_code in res_codes:
            r = restriction_lookup.get(res_code, {})
            item_restrictions.append({
                "res_code": res_code,
                "restriction_code": res_code,
                "streamlined_code": res_code,  # backward compat alias
                "treatment_phase": r.get("treatment_phase"),
                "authority_method": r.get("authority_method"),
                "treatment_of_code": r.get("treatment_of_code"),
                "restriction_number": r.get("restriction_number"),
                "li_html_text": r.get("li_html_text"),
                "restriction_text": r.get("li_html_text") or r.get("restriction_text"),  # compat
                "indication": r.get("indication"),  # legacy compat
                "written_authority_required": yn(r.get("written_authority_required")),
                "complex_authority_required": yn(r.get("complex_authority_rqrd_ind")),
                "authority_required": yn(r.get("complex_authority_rqrd_ind")) or bool(r.get("authority_required", False)),
                "prescriber_type": r.get("prescriber_type"),
                "continuation_only": "continu" in str(r.get("treatment_phase", "")).lower() or bool(r.get("continuation_only", False)),
                "clinical_criteria": r.get("clinical_criteria"),
                "restriction_type": r.get("restriction_type"),
            })

        item = {
            "pbs_code": pbs_code,
            "li_item_id": li_item_id,
            "ingredient_lower": ingredient_lower,
            "brand_name": raw.get("brand_name") or "",
            "brand_name_lower": (raw.get("brand_name") or "").lower(),
            "form": raw.get("li_form") or raw.get("form"),
            "schedule_form": raw.get("schedule_form"),
            "manner_of_administration": raw.get("manner_of_administration"),
            "strength": raw.get("strength"),  # legacy — not in real API items
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
            "sixty_day_eligible": sixty_day,
            "max_quantity": raw.get("maximum_quantity_units") or raw.get("max_quantity"),
            "max_repeats": raw.get("number_of_repeats") or raw.get("max_repeats"),
            "dangerous_drug": bool(raw.get("dangerous_drug", False)),
            "caution_indicator": yn(raw.get("caution_indicator")),
            "note_indicator": yn(raw.get("note_indicator")),
            "first_listed_date": parse_date(raw.get("first_listed_date")),
            "organisation_id": raw.get("organisation_id"),
            "therapeutic_group_title": raw.get("therapeutic_group_title") or raw.get("therapeutic_group"),
            "section100_only_indicator": yn(raw.get("section100_only_indicator")),
            "extemporaneous_indicator": yn(raw.get("extemporaneous_indicator")),
            "infusible_indicator": yn(raw.get("infusible_indicator")),
            "originator_brand_indicator": yn(raw.get("originator_brand_indicator")),
            "innovator_indicator": yn(raw.get("innovator_indicator")),
            # From item-overview (optional enrichment)
            "artg_id": item_overviews_map.get(pbs_code, {}).get("artg_id") or raw.get("artg_id"),
            "sponsor": item_overviews_map.get(pbs_code, {}).get("sponsor") or raw.get("sponsor"),
            "caution": item_overviews_map.get(pbs_code, {}).get("caution") or raw.get("caution"),
            "biosimilar": bool(item_overviews_map.get(pbs_code, {}).get("biosimilar") or raw.get("biosimilar", False)),
            "restrictions": item_restrictions,
        }
        normalised_items.append(item)

    # ── Organisations ─────────────────────────────────────────────────────────
    normalised_organisations = []
    for org in (raw_organisations or []):
        normalised_organisations.append({
            "organisation_id": org.get("organisation_id"),
            "name": org.get("name"),
            "abn": org.get("abn"),
            "street_address": org.get("street_address"),
            "city": org.get("city"),
            "state": org.get("state"),
            "postcode": org.get("postcode"),
        })

    # ── Programs ──────────────────────────────────────────────────────────────
    normalised_programs = []
    for prog in (raw_programs or []):
        normalised_programs.append({
            "program_code": prog.get("program_code", ""),
            "program_title": prog.get("program_title"),
        })

    # ── ATC codes ─────────────────────────────────────────────────────────────
    normalised_atc_codes = []
    for atc in (raw_atc_codes or []):
        normalised_atc_codes.append({
            "atc_code": atc.get("atc_code", ""),
            "atc_description": atc.get("atc_description"),
            "atc_level": atc.get("atc_level"),
            "atc_parent_code": atc.get("atc_parent_code"),
        })

    # ── Item <-> ATC relationships ────────────────────────────────────────────
    normalised_item_atc_relationships = []
    for rel in (raw_item_atc_relationships or []):
        normalised_item_atc_relationships.append({
            "pbs_code": rel.get("pbs_code", ""),
            "atc_code": rel.get("atc_code", ""),
            "atc_priority_pct": parse_decimal(rel.get("atc_priority_pct")),
        })

    # ── Copayments (schedule-level thresholds) ────────────────────────────────
    normalised_copayments = None
    for cp in (raw_copayments or []):
        normalised_copayments = {
            "general": parse_decimal(cp.get("general")),
            "concessional": parse_decimal(cp.get("concessional")),
            "safety_net_general": parse_decimal(cp.get("safety_net_general")),
            "safety_net_concessional": parse_decimal(cp.get("safety_net_concessional")),
            "safety_net_card_issue": parse_decimal(cp.get("safety_net_card_issue")),
            "increased_discount_limit": parse_decimal(cp.get("increased_discount_limit")),
            "safety_net_ctg_contribution": parse_decimal(cp.get("safety_net_ctg_contribution")),
        }
        break  # one row per schedule

    # ── Item pricing ──────────────────────────────────────────────────────────
    normalised_item_pricing = []
    for p in (raw_item_pricing or []):
        pbs_code = p.get("pbs_code") or (p.get("li_item_id", "").split("_")[0] if p.get("li_item_id") else "")
        normalised_item_pricing.append({
            "li_item_id": p.get("li_item_id", ""),
            "pbs_code": pbs_code,
            "dispensing_rule_mnem": p.get("dispensing_rule_mnem"),
            "brand_premium": parse_decimal(p.get("brand_premium")) or Decimal("0.00"),
            "commonwealth_price": parse_decimal(p.get("cmnwlth_price_to_pharmacist")),
            "max_general_patient_charge": parse_decimal(p.get("max_general_patient_charge")),
            "special_patient_contribution": parse_decimal(p.get("special_patient_contribution")),
            "fee_dispensing": parse_decimal(p.get("fee_dispensing")),
            "fee_dispensing_dangerous_drug": parse_decimal(p.get("fee_dispensing_dd")),
            "fee_container_other": parse_decimal(p.get("fee_container_other")),
            "fee_container_injectable": parse_decimal(p.get("fee_container_injectable")),
        })

    # ── Fees (program-level fee schedule) ────────────────────────────────────
    normalised_fees = []
    for f in (raw_fees or []):
        normalised_fees.append({
            # Real PBS API uses program_code as the identifier; fee_code is legacy alias
            "fee_code": f.get("fee_code") or f.get("program_code", ""),
            "program_code": f.get("program_code"),
            "fee_type": f.get("fee_type"),
            "description": f.get("description"),
            "amount": parse_decimal(f.get("amount")),
            "patient_contribution": parse_decimal(f.get("patient_contribution")),
            # Real PBS /fees fields
            "dispensing_fee_ready_prepared": parse_decimal(f.get("dispensing_fee_ready_prepared")),
            "dispensing_fee_dangerous_drug": parse_decimal(f.get("dispensing_fee_dangerous_drug")),
            "dispensing_fee_extra": parse_decimal(f.get("dispensing_fee_extra")),
            "dispensing_fee_extemporaneous": parse_decimal(f.get("dispensing_fee_extemporaneous")),
            "safety_net_recording_fee_ep": parse_decimal(f.get("safety_net_recording_fee_ep")),
            "safety_net_recording_fee_rp": parse_decimal(f.get("safety_net_recording_fee_rp")),
            "dispensing_fee_water_added": parse_decimal(f.get("dispensing_fee_water_added")),
            "container_fee_injectable": parse_decimal(f.get("container_fee_injectable")),
            "container_fee_other": parse_decimal(f.get("container_fee_other")),
            "gnrl_copay_discount_general": parse_decimal(f.get("gnrl_copay_discount_general")),
            "gnrl_copay_discount_hospital": parse_decimal(f.get("gnrl_copay_discount_hospital")),
            "con_copay_discount_general": parse_decimal(f.get("con_copay_discount_general")),
            "con_copay_discount_hospital": parse_decimal(f.get("con_copay_discount_hospital")),
            "efc_diluent_fee": parse_decimal(f.get("efc_diluent_fee")),
            "efc_preparation_fee": parse_decimal(f.get("efc_preparation_fee")),
            "efc_distribution_fee": parse_decimal(f.get("efc_distribution_fee")),
            "acss_imdq60_payment": parse_decimal(f.get("acss_imdq60_payment")),
            "acss_payment": parse_decimal(f.get("acss_payment")),
        })

    # ── Prescribing texts ─────────────────────────────────────────────────────
    normalised_prescribing_texts = []
    for pt in (raw_prescribing_texts or []):
        normalised_prescribing_texts.append({
            "prescribing_text_id": str(pt.get("prescribing_txt_id") or pt.get("prescribing_text_id") or ""),
            "text_type": pt.get("prescribing_type") or pt.get("text_type"),
            "complex_authority_required": yn(pt.get("complex_authority_rqrd_ind")) or bool(pt.get("complex_authority_required", False)),
            "prescribing_txt": pt.get("prescribing_txt"),
            "prescribing_txt_html": pt.get("prscrbg_txt_html"),
        })

    # ── Indications ───────────────────────────────────────────────────────────
    normalised_indications = []
    for ind in (raw_indications or []):
        normalised_indications.append({
            "indication_id": str(ind.get("indication_prescribing_txt_id") or ind.get("indication_id") or ""),
            "pbs_code": ind.get("pbs_code"),
            "condition": ind.get("condition"),
            "episodicity": ind.get("episodicity"),
            "severity": ind.get("severity"),
            "indication_text": ind.get("indication_text") or ind.get("condition"),
            "condition_description": ind.get("condition_description"),
        })

    # ── Item <-> dispensing rule relationships ────────────────────────────────
    normalised_item_dispensing_rules = []
    for rel in (raw_item_dispensing_rule_relationships or raw_item_dispensing_rules or []):
        normalised_item_dispensing_rules.append({
            "pbs_code": rel.get("pbs_code", ""),
            "rule_code": rel.get("dispensing_rule_mnem") or rel.get("rule_code", ""),
        })

    # ── Program dispensing rules ──────────────────────────────────────────────
    # Real PBS API /dispensing-rules has no program_code, dispensing_quantity,
    # dispensing_unit, or repeats_allowed. Those fields only appear in test fixtures.
    normalised_program_dispensing_rules = []
    for rule in (raw_dispensing_rules or raw_program_dispensing_rules or []):
        normalised_program_dispensing_rules.append({
            "program_code": rule.get("program_code") or None,  # NULL from real API
            "rule_code": rule.get("dispensing_rule_mnem") or rule.get("rule_code", ""),
            "dispensing_rule_reference": rule.get("dispensing_rule_reference"),
            "community_pharmacy_indicator": yn(rule.get("community_pharmacy_indicator")) if rule.get("community_pharmacy_indicator") is not None else None,
            "dispensing_quantity": rule.get("dispensing_quantity"),
            "dispensing_unit": rule.get("dispensing_unit"),
            "repeats_allowed": rule.get("repeats_allowed"),
            "description": rule.get("dispensing_rule_title") or rule.get("description"),
        })

    # ── Restriction <-> prescribing text relationships ────────────────────────
    normalised_restriction_pt_rels = []
    for rel in (raw_restriction_prescribing_text_relationships or []):
        normalised_restriction_pt_rels.append({
            "restriction_code": rel.get("res_code") or rel.get("restriction_code", ""),
            "prescribing_text_id": str(rel.get("prescribing_text_id") or rel.get("prescribing_txt_id") or ""),
        })

    # ── Item <-> prescribing text relationships ───────────────────────────────
    normalised_item_pt_rels = []
    for rel in (raw_item_prescribing_text_relationships or raw_item_prescribing_texts or []):
        normalised_item_pt_rels.append({
            "pbs_code": rel.get("pbs_code", ""),
            "prescribing_text_id": str(rel.get("prescribing_txt_id") or rel.get("prescribing_text_id") or ""),
        })

    # ── Item <-> organisation relationships ───────────────────────────────────
    normalised_item_org_rels = []
    for rel in (raw_item_organisation_relationships or []):
        normalised_item_org_rels.append({
            "pbs_code": rel.get("pbs_code", ""),
            "organisation_id": rel.get("organisation_id"),
        })

    # ── AMT items ─────────────────────────────────────────────────────────────
    # Real PBS API fields: pbs_concept_id, concept_type_code, amt_code, li_item_id,
    # preferred_term. Test fixtures use amt_id/concept_type (legacy names).
    normalised_amt_items = []
    for amt in (raw_amt_items or []):
        normalised_amt_items.append({
            "amt_id": str(amt.get("amt_code") or amt.get("amt_id") or ""),
            "pbs_concept_id": amt.get("pbs_concept_id"),
            "concept_type": amt.get("concept_type_code") or amt.get("concept_type"),
            "preferred_term": amt.get("preferred_term"),
            "atc_code": amt.get("atc_code"),
            "parent_amt_id": amt.get("parent_amt_id"),
            "exempt_ind": yn(amt.get("exempt_ind")) if amt.get("exempt_ind") is not None else None,
            # li_item_id is used to derive item relationships below
            "_li_item_id": amt.get("li_item_id", ""),
        })

    # ── Item <-> AMT relationships ────────────────────────────────────────────
    # Real PBS API: no /item-amt-relationships endpoint. Relationships are derived
    # from li_item_id in /amt-items (pbs_code = first segment of li_item_id).
    # Fallback: use raw_item_amt if explicitly provided (test fixtures / legacy).
    normalised_item_amt_relationships = []
    if raw_item_amt:
        for rel in raw_item_amt:
            normalised_item_amt_relationships.append({
                "pbs_code": rel.get("pbs_code", ""),
                "amt_id": rel.get("amt_id", ""),
                "relationship_type": rel.get("relationship_type"),
            })
    else:
        seen = set()
        for amt in normalised_amt_items:
            li = amt.get("_li_item_id", "")
            pbs_code = li.split("_")[0] if li else ""
            amt_id = amt["amt_id"]
            key = (pbs_code, amt_id)
            if pbs_code and amt_id and key not in seen:
                seen.add(key)
                normalised_item_amt_relationships.append({
                    "pbs_code": pbs_code,
                    "amt_id": amt_id,
                    "relationship_type": amt.get("concept_type"),
                })

    # Strip internal key before returning
    for amt in normalised_amt_items:
        amt.pop("_li_item_id", None)

    # ── Summary of changes ────────────────────────────────────────────────────
    import json as _json

    def _scalar(v):
        """Ensure v is a str, int, float, bool, or None — never a dict/list."""
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return _json.dumps(v)  # serialize dicts/lists so they land as TEXT

    normalised_summary_of_changes = []
    for chg in (raw_summary_of_changes or []):
        # table_keys is a dict in the real PBS API (e.g. {'li_item_id': '10000AC_...'})
        _table_keys = chg.get("table_keys")
        if isinstance(_table_keys, dict):
            _raw_pbs = _table_keys.get("pbs_code") or _table_keys.get("li_item_id", "").split("_")[0] or None
        elif _table_keys:
            _raw_pbs = str(_table_keys)
        else:
            _raw_pbs = None
        normalised_summary_of_changes.append({
            "pbs_code": _scalar(chg.get("pbs_code") or _raw_pbs),
            "change_type": _scalar(chg.get("change_type")),
            "effective_date": parse_date(chg.get("target_effective_date") or chg.get("effective_date")),
            "description": _scalar(chg.get("change_detail") or chg.get("description")),
            "section": _scalar(chg.get("changed_endpoint") or chg.get("section")),
        })

    # ── Containers ────────────────────────────────────────────────────────────
    normalised_containers = [
        {
            "container_code": str(c.get("container_code") or ""),
            "container_name": c.get("container_name"),
            "mark_up": parse_decimal(c.get("mark_up")),
            "agreed_purchasing_unit": parse_decimal(c.get("agreed_purchasing_unit")),
            "average_exact_unit_price": parse_decimal(c.get("average_exact_unit_price")),
            "average_rounded_unit_price": parse_decimal(c.get("average_rounded_unit_price")),
            "container_type": c.get("container_type"),
            "container_quantity": c.get("container_quantity"),
            "container_unit_of_measure": c.get("container_unit_of_measure"),
        }
        for c in (raw_containers or [])
    ]

    normalised_container_org_rels = [
        {
            "container_code": str(rel.get("container_code") or ""),
            "organisation_id": str(rel.get("organisation_id") or ""),
        }
        for rel in (raw_container_organisation_relationships or [])
    ]

    # ── Criteria ──────────────────────────────────────────────────────────────
    normalised_criteria = [
        {
            "criteria_id": str(c.get("criteria_prescribing_txt_id") or c.get("criteria_id") or ""),
            "criteria_type": c.get("criteria_type"),
            "parameter_relationship": c.get("parameter_relationship"),
        }
        for c in (raw_criteria or [])
    ]

    normalised_criteria_parameter_rels = [
        {
            "criteria_id": str(rel.get("criteria_prescribing_txt_id") or rel.get("criteria_id") or ""),
            "parameter_id": str(rel.get("parameter_prescribing_txt_id") or rel.get("parameter_id") or ""),
            "pt_position": rel.get("pt_position"),
        }
        for rel in (raw_criteria_parameter_relationships or [])
    ]

    # ── Parameters ────────────────────────────────────────────────────────────
    normalised_parameters = [
        {
            "parameter_id": str(p.get("parameter_prescribing_txt_id") or p.get("parameter_id") or ""),
            "assessment_type": p.get("assessment_type"),
            "parameter_type": p.get("parameter_type"),
        }
        for p in (raw_parameters or [])
    ]

    # ── Prescribers (item <-> prescriber-type relationship) ───────────────────
    normalised_item_prescribers = [
        {
            "pbs_code": p.get("pbs_code", ""),
            "prescriber_code": p.get("prescriber_code", ""),
            "prescriber_type": p.get("prescriber_type"),
        }
        for p in (raw_prescribers or [])
    ]

    # ── Markup bands ──────────────────────────────────────────────────────────
    normalised_markup_bands = [
        {
            "markup_band_code": mb.get("markup_band_code", ""),
            "program_code": mb.get("program_code"),
            "dispensing_rule_mnem": mb.get("dispensing_rule_mnem"),
            "limit_amount": parse_decimal(mb.get("limit")),
            "variable_rate": parse_decimal(mb.get("variable")),
            "offset_amount": parse_decimal(mb.get("offset")),
            "fixed_amount": parse_decimal(mb.get("fixed")),
        }
        for mb in (raw_markup_bands or [])
    ]

    # ── Item pricing events ───────────────────────────────────────────────────
    # Returns 204 for most schedules; fields are provisional.
    normalised_item_pricing_events = [
        {
            "pbs_code": e.get("pbs_code"),
            "event_type": e.get("event_type"),
            "effective_date": parse_date(e.get("effective_date")),
            "previous_price": parse_decimal(e.get("previous_price")),
            "new_price": parse_decimal(e.get("new_price")),
        }
        for e in (raw_item_pricing_events or [])
    ]

    # ── Extemporaneous ingredients ────────────────────────────────────────────
    normalised_extemporaneous_ingredients = [
        {
            "pbs_code": i.get("pbs_code", ""),
            "agreed_purchasing_unit": parse_decimal(i.get("agreed_purchasing_unit")),
            "exact_tenth_gram_per_ml_price": parse_decimal(i.get("exact_tenth_gram_per_ml_price")),
            "exact_one_gram_per_ml_price": parse_decimal(i.get("exact_one_gram_per_ml_price")),
            "exact_ten_gram_per_ml_price": parse_decimal(i.get("exact_ten_gram_per_ml_price")),
            "exact_hundred_gram_per_ml_price": parse_decimal(i.get("exact_hundred_gram_per_ml_price")),
            "rounded_tenth_gram_per_ml_price": parse_decimal(i.get("rounded_tenth_gram_per_ml_price")),
            "rounded_one_gram_per_ml_price": parse_decimal(i.get("rounded_one_gram_per_ml_price")),
            "rounded_ten_gram_per_ml_price": parse_decimal(i.get("rounded_ten_gram_per_ml_price")),
            "rounded_hundred_gram_per_ml_price": parse_decimal(i.get("rounded_hundred_gram_per_ml_price")),
        }
        for i in (raw_extemporaneous_ingredients or [])
    ]

    # ── Extemporaneous preparations ───────────────────────────────────────────
    normalised_extemporaneous_preparations = [
        {
            "pbs_code": p.get("pbs_code", ""),
            "preparation": p.get("preparation"),
            "maximum_quantity": p.get("maximum_quantity"),
            "maximum_quantity_unit": p.get("maximum_quantity_unit"),
        }
        for p in (raw_extemporaneous_preparations or [])
    ]

    normalised_extemporaneous_prep_sfp_rels = [
        {
            "sfp_pbs_code": rel.get("sfp_pbs_code", ""),
            "ex_prep_pbs_code": rel.get("ex_prep_pbs_code", ""),
        }
        for rel in (raw_extemporaneous_prep_sfp_relationships or [])
    ]

    # ── Extemporaneous tariffs ────────────────────────────────────────────────
    normalised_extemporaneous_tariffs = [
        {
            "pbs_code": t.get("pbs_code", ""),
            "drug_name": t.get("drug_name"),
            "agreed_purchasing_unit": parse_decimal(t.get("agreed_purchasing_unit")),
            "markup": parse_decimal(t.get("markup")),
            "rounded_rec_one_tenth_gram": parse_decimal(t.get("rounded_rec_one_tenth_gram")),
            "rounded_rec_one_gram": parse_decimal(t.get("rounded_rec_one_gram")),
            "rounded_rec_ten_gram": parse_decimal(t.get("rounded_rec_ten_gram")),
            "rounded_rec_hundred_gram": parse_decimal(t.get("rounded_rec_hundred_gram")),
            "exact_rec_one_tenth_gram": parse_decimal(t.get("exact_rec_one_tenth_gram")),
            "exact_rec_one_gram": parse_decimal(t.get("exact_rec_one_gram")),
            "exact_rec_ten_gram": parse_decimal(t.get("exact_rec_ten_gram")),
            "exact_rec_hundred_gram": parse_decimal(t.get("exact_rec_hundred_gram")),
        }
        for t in (raw_extemporaneous_tariffs or [])
    ]

    # ── Standard formula preparations ────────────────────────────────────────
    normalised_standard_formula_preparations = [
        {
            "pbs_code": s.get("pbs_code", ""),
            "sfp_drug_name": s.get("sfp_drug_name"),
            "sfp_reference": s.get("sfp_reference"),
            "container_fee": parse_decimal(s.get("container_fee")),
            "dispensing_fee_max_quantity": parse_decimal(s.get("dispensing_fee_max_quantity")),
            "safety_net_price": parse_decimal(s.get("safety_net_price")),
            "maximum_patient_charge": parse_decimal(s.get("maximum_patient_charge")),
            "maximum_quantity_unit": s.get("maximum_quantity_unit"),
            "maximum_quantity": s.get("maximum_quantity"),
        }
        for s in (raw_standard_formula_preparations or [])
    ]

    return {
        "month": month,
        "medicines": list(medicines_map.values()),
        "items": normalised_items,
        # Pricing
        "copayments": normalised_copayments,
        "item_pricing": normalised_item_pricing,
        "fees": normalised_fees,
        # Reference
        "organisations": normalised_organisations,
        "programs": normalised_programs,
        "atc_codes": normalised_atc_codes,
        "prescribing_texts": normalised_prescribing_texts,
        "indications": normalised_indications,
        "program_dispensing_rules": normalised_program_dispensing_rules,
        "summary_of_changes": normalised_summary_of_changes,
        # Relationships
        "item_atc_relationships": normalised_item_atc_relationships,
        "item_restriction_relationships": [
            {"pbs_code": rel.get("pbs_code", ""), "restriction_code": rel.get("res_code") or rel.get("restriction_code", "")}
            for rel in (raw_item_restriction_relationships or [])
        ],
        "item_dispensing_rules": normalised_item_dispensing_rules,
        "restriction_prescribing_text_relationships": normalised_restriction_pt_rels,
        "item_prescribing_text_relationships": normalised_item_pt_rels,
        "item_organisation_relationships": normalised_item_org_rels,
        "amt_items": normalised_amt_items,
        "item_amt_relationships": normalised_item_amt_relationships,
        # New endpoints (migration 007)
        "containers": normalised_containers,
        "container_organisation_relationships": normalised_container_org_rels,
        "criteria": normalised_criteria,
        "criteria_parameter_relationships": normalised_criteria_parameter_rels,
        "parameters": normalised_parameters,
        "item_prescribers": normalised_item_prescribers,
        "markup_bands": normalised_markup_bands,
        "item_pricing_events": normalised_item_pricing_events,
        "extemporaneous_ingredients": normalised_extemporaneous_ingredients,
        "extemporaneous_preparations": normalised_extemporaneous_preparations,
        "extemporaneous_prep_sfp_relationships": normalised_extemporaneous_prep_sfp_rels,
        "extemporaneous_tariffs": normalised_extemporaneous_tariffs,
        "standard_formula_preparations": normalised_standard_formula_preparations,
    }
