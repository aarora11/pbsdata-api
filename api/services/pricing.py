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


def patient_charge_formula(
    base_dispensed_price: Optional[float],
    copayment: Optional[float],
    allowable_fees: Optional[float] = None,
    premiums: Optional[float] = None,
) -> Optional[float]:
    """PBS's own construction of a patient charge, with the copayment left open.

        min(dispensed price + allowable fees, copayment) + premiums

    Sourced rather than inferred: the data dictionary defines fee_extra as "Allowable
    Extra fee" and fee_safety_net_recording as the fee for recording against the Safety
    Net. Applying this with the *general* copayment reproduces PBS's published
    max_general_patient_charge exactly on 36,164 of 38,159 rows on 2026-06, and on
    ~99.5% of the mainstream community-pharmacy and hospital rules.

    That reproduction is what makes the concessional figure defensible. PBS publishes no
    concessional charge, so it cannot be checked directly; but a formula that
    reconstructs the published general charge, applied with the concessional copayment,
    is a sourced substitution rather than an invention.

    Residuals are the rp-np-* and rp-np-nc-* families — reduced- and no-charge settings
    such as the Prescriber Bag — where the real charge is always LOWER than this
    predicts. concessional_charge caps at the published general charge, which covers them.
    """
    if base_dispensed_price is None:
        return None
    subtotal = base_dispensed_price + (allowable_fees or 0.0)
    capped = subtotal if copayment is None else min(subtotal, copayment)
    return _r(capped + (premiums or 0.0))


def concessional_charge(
    base_dispensed_price: Optional[float],
    concessional_copayment: Optional[float],
    brand_premium: Optional[float] = None,
    special_patient_contribution: Optional[float] = None,
    general_patient_charge: Optional[float] = None,
    allowable_fees: Optional[float] = None,
) -> Optional[float]:
    """DERIVED — PBS publishes no concessional charge, so this is our calculation.

    It is PBS's own formula (see patient_charge_formula) with the concessional
    copayment substituted for the general one. Premiums sit on top of the copayment
    rather than being absorbed by it: the explanatory notes state that "any applicable
    special patient contributions, brand premiums or therapeutic group premiums must
    still be met by the patient".

    Capped at the published general charge, because a concession card can never leave a
    patient worse off. That also covers the rule families where the formula overshoots:
    a Prescriber Bag item is free to the patient, and an uncapped derivation billed a
    concessional patient the 7.70 copayment for one.
    """
    if base_dispensed_price is None:
        return None
    charge = patient_charge_formula(
        base_dispensed_price,
        concessional_copayment,
        allowable_fees=allowable_fees,
        premiums=(brand_premium or 0.0) + (special_patient_contribution or 0.0),
    )
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
