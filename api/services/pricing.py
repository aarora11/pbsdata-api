"""The PBS patient cost model — one implementation, shared by every router.

Validated field by field against pbs.gov.au on 2026-08-11. A medicine page publishes
exactly three monetary figures per brand, and each maps to a PBS field we hold:

    "DPMQ: $22.84"                 -> dispensed_price_max_qty
    "DPMQ: $31.00" (Lamictal)      -> dispensed_price_max_qty + brand_premium
    "General Patient Charge: $33.16" -> max_general_patient_charge
    "Max safety net: $24.38"       -> max_record_val_for_safety_net

Three rules follow from that, and they are why this module exists:

1. **The general patient charge is published, never computed.** It is not
   `min(price, copayment)`. Lamictal's base price of 22.40 sits *below* the 25.00
   copayment, yet the published charge is 33.56 — the copayment plus the brand premium,
   because allowable additional fees apply to items priced under the copayment. The
   field also already accounts for programs whose patients pay the concessional rate
   (RPBS), so any formula we invented would be wrong for a whole program.

2. **What a patient pays and what accrues to their safety net are different numbers.**
   Brand premiums do not accrue: Lamictal charges 33.16 while recording 24.38, and that
   24.38 is identical for every brand of the item. Estimating scripts-to-threshold from
   the amount paid overstates progress.

3. **The concessional charge is genuinely derived.** PBS publishes no concessional
   figure — the medicine page shows none — so it is computed here and labelled as a
   derivation. It is the one Tier B calculation in this module.

`commonwealth_price` is deliberately absent: it maps to cmnwlth_price_to_pharmacist,
what the pharmacist pays for the pack, and using it as the dispensed price understated
every downstream figure (on 10005N, 5.99 against a true 19.78).
"""
from typing import Optional


def _r(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def brand_dispensed_price(
    base_dispensed_price: Optional[float], brand_premium: Optional[float]
) -> Optional[float]:
    """Dispensed price for a specific brand: the item's DPMQ plus its premium.

    Confirmed on pbs.gov.au: 13842M lists 22.84 for the base brands and 31.00 for
    Lamictal, whose premium is 8.16.
    """
    if base_dispensed_price is None:
        return None
    return _r(base_dispensed_price + (brand_premium or 0.0))


def patient_charge(dpmq: Optional[float], cap: Optional[float]) -> Optional[float]:
    """The lower of dispensed price and an applicable cap.

    A cap of 0.00 is a real PBS value (fully subsidised and CTG items) and must be
    honoured — treating it as absent charged the patient the full dispensed price.
    See docs/AUDIT_FINDINGS.md [C5].
    """
    if dpmq is None:
        return None
    if cap is None:
        return _r(dpmq)
    return _r(min(dpmq, cap))


def concessional_charge(
    base_dispensed_price: Optional[float],
    concessional_copayment: Optional[float],
    brand_premium: Optional[float] = None,
    special_patient_contribution: Optional[float] = None,
    general_patient_charge: Optional[float] = None,
) -> Optional[float]:
    """DERIVED — PBS publishes no concessional charge, so this is our calculation.

    A concessional patient pays the concessional copayment, or the dispensed price if
    that is lower, plus any brand premium and special patient contribution. Both
    premiums are payable on top of the copayment rather than absorbed by it.

    It is additionally capped at the published general charge, since a concessional
    patient never pays more than a general one.

    Caveat carried into every response that uses this: for items priced below the
    copayment, pharmacists may add allowable additional fees, exactly as they do for
    general patients. PBS publishes that adjustment for general patients and not for
    concessional, so this figure is a floor rather than a guaranteed final price.
    """
    if base_dispensed_price is None:
        return None
    capped = patient_charge(base_dispensed_price, concessional_copayment)
    charge = capped + (brand_premium or 0.0) + (special_patient_contribution or 0.0)
    # A concession card can never leave a patient worse off than a general patient, so
    # the published general charge is an upper bound. This matters for whole programs:
    # a Prescriber Bag item is free to the patient (general charge 0.00) and the naive
    # derivation billed a concessional patient the 7.70 copayment for it.
    if general_patient_charge is not None:
        charge = min(charge, general_patient_charge)
    return _r(charge)


def government_contribution(
    dispensed_price: Optional[float], patient_pays: Optional[float]
) -> Optional[float]:
    """Subsidy for one script, never below zero.

    For items priced under the copayment the patient pays *more* than the dispensed
    price — 13842M is dispensed at 22.40 against a published charge of 25.00 — because
    allowable additional fees apply. The government subsidises nothing there; the excess
    is pharmacy fees, not a negative subsidy. Subtracting plainly reported −2.60.

    Recomposition therefore holds in one direction only:
        patient + government == dispensed price, whenever patient <= dispensed price.
    Above that, government is 0 and the excess is `additional_patient_fees`.

    The invariant is also only meaningful if `dispensed_price` is the real dispensed
    price. It held perfectly against cmnwlth_price_to_pharmacist while every figure was
    wrong — internal consistency against the wrong total.
    """
    if dispensed_price is None or patient_pays is None:
        return None
    return _r(max(0.0, dispensed_price - patient_pays))


def additional_patient_fees(
    dispensed_price: Optional[float], patient_pays: Optional[float]
) -> Optional[float]:
    """Amount the patient pays above the dispensed price — allowable pharmacy fees.

    Zero for anything priced at or above the copayment. Reported so the patient-facing
    total always reconciles against the dispensed price rather than appearing to exceed
    it for no stated reason.
    """
    if dispensed_price is None or patient_pays is None:
        return None
    return _r(max(0.0, patient_pays - dispensed_price))


def scripts_to_safety_net(
    threshold: Optional[float], recorded_per_script: Optional[float]
) -> Optional[int]:
    """ESTIMATE — scripts of this item alone to reach the safety net threshold.

    For a general patient, pass max_record_val_for_safety_net: the amount PBS records
    against the card, which excludes brand premiums and is not what the patient pays.

    For a concessional patient, pass the concessional charge instead. That published
    field is "capped at the General Co Payment amount", so it is the general figure —
    applying it to a concessional patient made the threshold look roughly three times
    nearer than it is.

    Ignores family aggregation, other medicines, and mid-year concession changes, so it
    is indicative only.
    """
    if not threshold or not recorded_per_script:
        return None
    from math import ceil
    return ceil(threshold / recorded_per_script)


# Rules whose cmnwlth_dsp_price_max_qty is the price pbs.gov.au publishes. PBS marks
# them with community_pharmacy_indicator; the family is the s90-cp variants.
COMMUNITY_PHARMACY_RULE_SQL = """
    COALESCE(
        (SELECT p.community_pharmacy_indicator
         FROM program_dispensing_rules p
         WHERE p.rule_code = ip.dispensing_rule_reference
           AND p.schedule_id = ip.schedule_id),
        FALSE)
"""
