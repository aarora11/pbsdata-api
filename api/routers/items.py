"""Items router — full implementation."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import is_tier_or_above, require_tier, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional
import datetime

router = APIRouter(tags=["items"])


# The patient cost model lives in api/services/pricing so every router shares one
# implementation — the same quantity was previously computed three different ways.
# Re-exported here because existing callers and tests import them from this module.
from api.services.pricing import (  # noqa: E402
    patient_charge,
    government_contribution,
    additional_patient_fees,
    brand_dispensed_price,
    concessional_charge,
    scripts_to_safety_net,
)



def check_history_limit(api_key_data: dict, schedule: Optional[str]):
    if schedule is None:
        return
    history_months = api_key_data.get("history_months_limit", 3)
    today = datetime.date.today()
    cutoff = today.replace(day=1)
    for _ in range(history_months):
        if cutoff.month == 1:
            cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
        else:
            cutoff = cutoff.replace(month=cutoff.month - 1)
    try:
        year, month_num = schedule.split("-")
        req_date = datetime.date(int(year), int(month_num), 1)
    except Exception:
        return
    if req_date < cutoff:
        raise HTTPException(
            status_code=403,
            detail={"code": "HISTORY_LIMIT_EXCEEDED", "message": f"Your plan only allows access to the last {history_months} months of data."},
        )


@router.get(
    "/items/{pbs_code}",
    summary="Get PBS Item",
    description=(
        "Returns the raw PBS item record for a given PBS code, including drug details, dispensing rules, "
        "benefit type, pricing fields, and restrictions. "
        "T2+ (Growth) subscribers additionally receive manufacturer, program title, primary ATC, and prescriber data joined in.\n\n"
        "Available on all tiers (raw) with enrichment from **Growth (T2)** tier."
    ),
)
async def get_item(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    check_history_limit(api_key_data, schedule)

    if schedule:
        sched_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", schedule)
    else:
        sched_row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )

    if not sched_row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    schedule_id = sched_row["id"]
    schedule_month = sched_row["month"]

    item = await db.fetchrow(
        """
        SELECT i.id, i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.pack_unit,
               i.benefit_type, i.general_charge, i.concessional_charge, i.government_price,
               i.brand_premium, i.brand_premium_counts_to_safety_net, i.sixty_day_eligible,
               i.max_quantity, i.max_repeats, i.dangerous_drug, i.formulary, i.section, i.program_code,
               i.artg_id, i.sponsor, i.caution, i.biosimilar,
               m.ingredient, m.ingredient_lower, m.atc_code
        FROM items_listing i
        JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    restrictions = await db.fetch(
        """
        SELECT streamlined_code, indication, restriction_text, prescriber_type,
               authority_required, continuation_only
        FROM restrictions WHERE item_id = $1
        """,
        item["id"],
    )

    result = dict(item)
    result["restrictions"] = [dict(r) for r in restrictions]
    result["id"] = str(result["id"])
    for field in ["general_charge", "concessional_charge", "government_price", "brand_premium"]:
        if result.get(field) is not None:
            result[field] = float(result[field])

    # T2+ subscribers receive an enriched response with manufacturer, ATC,
    # program and prescriber joins resolved. Base subscribers get the raw
    # passthrough above. See pbs_joined_api_spec.md §2.6 for the design decision.
    if not is_tier_or_above(api_key_data, "growth"):
        return {"data": result, "meta": {"schedule_code": schedule_month, "tier": tier_label(api_key_data)}}

    # ── T2+ enrichment ────────────────────────────────────────────────────────

    # Manufacturer (primary organisation for this pbs_code)
    org_row = await db.fetchrow(
        """
        SELECT o.organisation_id, o.name, o.state, o.abn
        FROM item_organisation_relationships ior
        JOIN organisations o
          ON o.organisation_id = ior.organisation_id AND o.schedule_id = ior.schedule_id
        WHERE ior.pbs_code = $1 AND ior.schedule_id = $2
        LIMIT 1
        """,
        pbs_code.upper(), schedule_id,
    )

    # Program title
    program_row = await db.fetchrow(
        "SELECT program_title FROM programs WHERE program_code = $1 AND schedule_id = $2",
        result.get("program_code"), schedule_id,
    )

    # Primary ATC classification (highest priority)
    atc_row = await db.fetchrow(
        """
        SELECT iar.atc_code, iar.atc_priority_pct, a.atc_description, a.atc_level, a.atc_parent_code
        FROM item_atc_relationships iar
        JOIN atc_codes a ON a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id
        WHERE iar.pbs_code = $1 AND iar.schedule_id = $2
        ORDER BY iar.atc_priority_pct DESC NULLS LAST
        LIMIT 1
        """,
        pbs_code.upper(), schedule_id,
    )

    # Authorised prescribers
    prescriber_rows = await db.fetch(
        "SELECT prescriber_code, prescriber_type FROM item_prescribers WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )

    return {
        "data": {
            **result,
            "primary_manufacturer": dict(org_row) if org_row else None,
            "primary_manufacturer_note": "Primary sponsor only. Multiple organisations may be listed; use /organisations for full coverage.",
            "program_title": program_row["program_title"] if program_row else None,
            "primary_atc": dict(atc_row) if atc_row else None,
            "prescribers": [dict(p) for p in prescriber_rows],
        },
        "meta": {
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
            "join_sources": [
                "/items", "/medicines", "/restrictions",
                "/organisations", "/programs", "/atc-codes", "/prescribers",
            ],
        },
    }


@router.get(
    "/items/{pbs_code}/price",
    summary="Get Item Pricing Detail",
    description=(
        "Returns full pricing breakdown for a PBS item including commonwealth price (DPMQ), "
        "dispensing fee, container fees, dangerous drug surcharge, brand premium, and calculated "
        "patient outcomes (general and concessional out-of-pocket cost, government subsidy). "
        "One context row is returned per dispensing rule (li_item_id + rule combination).\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_item_price(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    if not is_tier_or_above(api_key_data, "growth"):
        raise HTTPException(
            status_code=403,
            detail={"code": "TIER_INSUFFICIENT", "message": "This endpoint requires T2 (growth) tier or above."},
        )
    _rl(response, api_key_data)

    if schedule:
        sched_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", schedule)
    else:
        sched_row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    if not sched_row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
    schedule_id, schedule_month = sched_row["id"], sched_row["month"]

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.brand_name, i.government_price, i.general_charge,
               i.concessional_charge, i.brand_premium, i.formulary, i.program_code,
               m.ingredient
        FROM items_listing i JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    pricing_rows = await db.fetch(
        """
        SELECT li_item_id, dispensing_rule_mnem, dispensing_rule_reference,
               commonwealth_price, dispensed_price_max_qty,
               max_general_patient_charge, max_record_val_for_safety_net, brand_premium,
               fee_dispensing, fee_dispensing_dangerous_drug,
               fee_container_other, fee_container_injectable,
               special_patient_contribution
        FROM item_pricing
        WHERE pbs_code = $1 AND schedule_id = $2
        ORDER BY li_item_id, dispensing_rule_reference
        """,
        pbs_code.upper(), schedule_id,
    )
    copayment = await db.fetchrow(
        "SELECT general, concessional FROM copayments WHERE schedule_id = $1",
        schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    contexts = []
    for r in pricing_rows:
        base_dpmq = _f(r["dispensed_price_max_qty"])
        premium = _f(r["brand_premium"])
        spc = _f(r["special_patient_contribution"])
        con_cp = _f(copayment["concessional"]) if copayment else None
        max_charge = _f(r["max_general_patient_charge"])
        # The dispensed price is dispensed_price_max_qty plus the brand's premium.
        # commonwealth_price is what the pharmacist pays for the pack and understates
        # this materially — using it made every derived figure wrong.
        dispensed = brand_dispensed_price(base_dpmq, premium)
        gen_patient = max_charge          # published by PBS
        gov_pays = government_contribution(dispensed, gen_patient)
        con_patient = concessional_charge(base_dpmq, con_cp, premium, spc, max_charge)  # derived
        con_gov_pays = government_contribution(dispensed, con_patient)

        contexts.append({
            "li_item_id": r["li_item_id"],
            "dispensing_rule_reference": r["dispensing_rule_reference"],
            "dispensing_rule_mnem": r["dispensing_rule_mnem"],
            "dispensed_price": dispensed,
            "base_dispensed_price": base_dpmq,
            "price_to_pharmacist": _f(r["commonwealth_price"]),
            "max_general_patient_charge": max_charge,
            "max_record_val_for_safety_net": _f(r["max_record_val_for_safety_net"]),
            "brand_premium": _f(r["brand_premium"]),
            "fees": {
                "dispensing_fee": _f(r["fee_dispensing"]),
                "dangerous_drug_fee": _f(r["fee_dispensing_dangerous_drug"]),
                "container_fee_other": _f(r["fee_container_other"]),
                "container_fee_injectable": _f(r["fee_container_injectable"]),
            },
            "patient_outcome": {
                "general_patient_charge": gen_patient,
                "concessional_patient_charge": con_patient,
                "government_pays": gov_pays,
                "government_pays_concessional": con_gov_pays,
            },
        })

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "drug_name": item["ingredient"],
            "brand_name": item["brand_name"],
            "formulary": item["formulary"],
            "government_price": _f(item["government_price"]),
            "pricing_contexts": contexts,
            "co_payment_reference": {
                "general": _f(copayment["general"]) if copayment else None,
                "concessional": _f(copayment["concessional"]) if copayment else None,
            },
        },
        "meta": {
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
            "join_sources": ["/items", "/item-dispensing-rule-relationships", "/copayments"],
        },
    }


@router.get(
    "/items/{pbs_code}/patient-cost",
    summary="Get Item Patient Cost Breakdown",
    description=(
        "Returns a patient-facing cost breakdown for a PBS item: what a general patient pays, "
        "what a concessional patient pays, the government subsidy, brand premium if applicable, "
        "safety net thresholds, and estimated number of scripts to reach the safety net. "
        "Useful for patient-facing apps and cost transparency tools.\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_item_patient_cost(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    if not is_tier_or_above(api_key_data, "growth"):
        raise HTTPException(
            status_code=403,
            detail={"code": "TIER_INSUFFICIENT", "message": "This endpoint requires T2 (growth) tier or above."},
        )
    _rl(response, api_key_data)

    if schedule:
        sched_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", schedule)
    else:
        sched_row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    if not sched_row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
    schedule_id = sched_row["id"]

    item = await db.fetchrow(
        "SELECT i.pbs_code, i.brand_name, i.brand_premium, i.government_price, m.ingredient FROM items_listing i JOIN medicines m ON m.id = i.medicine_id WHERE i.pbs_code = $1 AND i.schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    # The community-pharmacy rule is the one whose dispensed price pbs.gov.au publishes;
    # the same item also carries private- and public-hospital rules at different prices.
    # This previously took the highest row by price-to-pharmacist, an arbitrary pick that
    # could land on a hospital rule. Among brands, the lowest premium is the cheapest
    # option a patient can actually choose, so it is the representative one.
    pricing = await db.fetchrow(
        """
        SELECT ip.dispensed_price_max_qty, ip.max_general_patient_charge, ip.brand_premium,
               ip.special_patient_contribution, ip.max_record_val_for_safety_net,
               ip.dispensing_rule_reference
        FROM item_pricing ip
        LEFT JOIN program_dispensing_rules pdr
               ON pdr.rule_code = ip.dispensing_rule_reference
              AND pdr.schedule_id = ip.schedule_id
        WHERE ip.pbs_code = $1 AND ip.schedule_id = $2
          AND ip.dispensed_price_max_qty IS NOT NULL
        ORDER BY COALESCE(pdr.community_pharmacy_indicator, FALSE) DESC,
                 ip.brand_premium ASC NULLS LAST,
                 ip.dispensed_price_max_qty ASC
        LIMIT 1
        """,
        pbs_code.upper(), schedule_id,
    )
    premium_spread = await db.fetchrow(
        """
        SELECT MIN(brand_premium) AS min_premium, MAX(brand_premium) AS max_premium,
               COUNT(DISTINCT li_item_id) AS brands
        FROM item_pricing WHERE pbs_code = $1 AND schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    copayment = await db.fetchrow(
        "SELECT general, concessional, safety_net_general, safety_net_concessional, increased_discount_limit FROM copayments WHERE schedule_id = $1",
        schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    base_dpmq = _f(pricing["dispensed_price_max_qty"]) if pricing else None
    gen_cp = _f(copayment["general"]) if copayment else None
    con_cp = _f(copayment["concessional"]) if copayment else None
    sn_gen = _f(copayment["safety_net_general"]) if copayment else None
    sn_con = _f(copayment["safety_net_concessional"]) if copayment else None
    idl = _f(copayment["increased_discount_limit"]) if copayment else None
    brand_premium = _f(pricing["brand_premium"]) if pricing else _f(item["brand_premium"])
    spc = _f(pricing["special_patient_contribution"]) if pricing else None
    max_charge = _f(pricing["max_general_patient_charge"]) if pricing else None
    sn_record = _f(pricing["max_record_val_for_safety_net"]) if pricing else None

    # What this brand is dispensed at — the item's DPMQ plus its premium.
    dispensed_price = brand_dispensed_price(base_dpmq, brand_premium)

    # General: published by PBS, passed through. Not min(price, copayment) — see
    # api/services/pricing for why that formula gives the wrong answer.
    you_pay_gen = max_charge
    # Concessional: PBS publishes no figure, so this is ours and is labelled as such.
    you_pay_con = concessional_charge(base_dpmq, con_cp, brand_premium, spc, max_charge)

    gov_pays_gen = government_contribution(dispensed_price, you_pay_gen)
    gov_pays_con = government_contribution(dispensed_price, you_pay_con)
    # Safety net accrues max_record_val_for_safety_net, which excludes brand premiums
    # and is not what the patient pays.
    scripts_gen = scripts_to_safety_net(sn_gen, sn_record)
    # A concessional patient records what they pay; sn_record is capped at the general
    # copayment and would understate the number of scripts by roughly threefold.
    scripts_con = scripts_to_safety_net(sn_con, you_pay_con)
    extra_gen = additional_patient_fees(dispensed_price, you_pay_gen)
    extra_con = additional_patient_fees(dispensed_price, you_pay_con)

    price_basis = (
        "item_pricing.dispensed_price_max_qty on the community-pharmacy dispensing rule, "
        "plus brand_premium — the figure pbs.gov.au publishes as DPMQ"
        if pricing else "unavailable — no dispensed price held for this item"
    )
    brands = premium_spread["brands"] if premium_spread else 0
    min_prem = _f(premium_spread["min_premium"]) if premium_spread else None
    max_prem = _f(premium_spread["max_premium"]) if premium_spread else None

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "drug_name": item["ingredient"],
            "brand_name": item["brand_name"],
            "dispensed_price": dispensed_price,
            "base_dispensed_price": base_dpmq,
            "general_patient": {
                "copayment": gen_cp,
                "you_pay": you_pay_gen,
                "brand_premium": brand_premium or 0.0,
                "total_out_of_pocket": you_pay_gen,
                "government_pays": gov_pays_gen,
                "safety_net_threshold": sn_gen,
                "records_to_safety_net_per_script": sn_record,
                "estimated_scripts_to_safety_net": scripts_gen,
                "additional_patient_fees": extra_gen,
                "source": "published",
            },
            "concessional_patient": {
                "copayment": con_cp,
                "you_pay": you_pay_con,
                "brand_premium": brand_premium or 0.0,
                "total_out_of_pocket": you_pay_con,
                "government_pays": gov_pays_con,
                "safety_net_threshold": sn_con,
                "records_to_safety_net_per_script": you_pay_con,
                "estimated_scripts_to_safety_net": scripts_con,
                "additional_patient_fees": extra_con,
                "source": "derived",
            },
            "brand_choice": {
                "brands_available": brands,
                "lowest_brand_premium": min_prem,
                "highest_brand_premium": max_prem,
                "note": (
                    "Figures are for the lowest-premium brand — the cheapest option a "
                    "patient can choose. A brand carrying a premium costs that much more."
                ) if (max_prem or 0) > (min_prem or 0) else None,
            },
            "discount_zone": {
                "increased_discount_limit": idl,
            },
        },
        "meta": {
            "schedule_code": sched_row["month"],
            "tier": tier_label(api_key_data),
            "join_sources": ["/items", "/item-dispensing-rule-relationships", "/copayments"],
            "price_basis": price_basis,
            "derivation": "concessional=derived; estimated_scripts_to_safety_net=estimate",
            "caveat": (
                "general_patient.you_pay is max_general_patient_charge exactly as PBS "
                "publishes it — the figure shown as General Patient Charge on pbs.gov.au. "
                "concessional_patient.you_pay is derived: PBS publishes no concessional "
                "figure, so it is the concessional copayment (or the dispensed price if "
                "lower) plus any brand premium and special patient contribution. For items "
                "priced below the copayment a pharmacist may add allowable additional fees, "
                "so treat the concessional figure as a floor. Scripts-to-safety-net counts "
                "this item alone and ignores family aggregation, other medicines and "
                "mid-year concession changes."
            ),
        },
    }


@router.get(
    "/items/{pbs_code}/prescribing-texts",
    summary="Get Item Prescribing Texts",
    description=(
        "Returns all prescribing text components linked to a PBS item via the "
        "item_prescribing_text_relationships table. Each text component has a type "
        "(e.g. indication, restriction, criteria) and indicates whether complex authority is required.\n\n"
        "Available on all tiers."
    ),
)
async def get_item_prescribing_texts(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)

    if schedule:
        sched_row = await db.fetchrow("SELECT id FROM schedules WHERE month = $1", schedule)
        schedule_id = sched_row["id"] if sched_row else None
    else:
        sched_row = await db.fetchrow(
            "SELECT id FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
        schedule_id = sched_row["id"] if sched_row else None

    if not schedule_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})

    rows = await db.fetch(
        """
        SELECT pt.prescribing_text_id, pt.text_type, pt.complex_authority_required, pt.prescribing_txt
        FROM prescribing_texts pt
        JOIN item_prescribing_text_relationships rel
          ON rel.prescribing_text_id = pt.prescribing_text_id AND rel.schedule_id = pt.schedule_id
        WHERE rel.pbs_code = $1 AND rel.schedule_id = $2
        ORDER BY pt.prescribing_text_id
        """,
        pbs_code.upper(), schedule_id,
    )
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get(
    "/items/{li_item_id}/dispensing-context",
    summary="Get Item Dispensing Context",
    description=(
        "Returns complete dispensing context for a brand-specific item (li_item_id), including "
        "pricing per dispensing rule, all applicable fees, and the markup band schedule used to "
        "calculate pharmacy mark-up. One context per dispensing rule mnemonic is returned.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_item_dispensing_context(
    li_item_id: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)

    if schedule:
        sched_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", schedule)
    else:
        sched_row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    if not sched_row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
    schedule_id, schedule_month = sched_row["id"], sched_row["month"]

    pricing_rows = await db.fetch(
        """
        SELECT ip.li_item_id, ip.pbs_code, ip.dispensing_rule_mnem,
               ip.commonwealth_price, ip.dispensed_price_max_qty,
               ip.max_general_patient_charge,
               ip.brand_premium, ip.special_patient_contribution,
               ip.fee_dispensing, ip.fee_dispensing_dangerous_drug,
               ip.fee_container_other, ip.fee_container_injectable,
               i.program_code, i.dangerous_drug
        FROM item_pricing ip
        -- Join on li_item_id, the brand key both tables share. Joining on pbs_code
        -- pulled in every brand of the code, multiplying this item's dispensing-rule
        -- rows by the brand count.
        JOIN items i ON i.li_item_id = ip.li_item_id AND i.schedule_id = ip.schedule_id
        WHERE ip.li_item_id = $1 AND ip.schedule_id = $2
        ORDER BY ip.dispensing_rule_mnem
        """,
        li_item_id, schedule_id,
    )
    if not pricing_rows:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found for this li_item_id."})

    def _f(v):
        return float(v) if v is not None else None

    program_code = pricing_rows[0]["program_code"]
    contexts = []
    for pr in pricing_rows:
        markup_bands = await db.fetch(
            """
            SELECT markup_band_code, limit_amount, variable_rate, offset_amount, fixed_amount
            FROM markup_bands
            WHERE schedule_id = $1 AND program_code = $2 AND dispensing_rule_mnem = $3
            ORDER BY limit_amount NULLS LAST
            """,
            schedule_id, program_code, pr["dispensing_rule_mnem"],
        )
        dispensed = brand_dispensed_price(_f(pr["dispensed_price_max_qty"]), _f(pr["brand_premium"]))
        fee_disp = _f(pr["fee_dispensing"])
        fee_dd = _f(pr["fee_dispensing_dangerous_drug"])
        fee_container = _f(pr["fee_container_other"])
        total_fees = sum(x for x in [fee_disp, fee_dd if pr["dangerous_drug"] else None, fee_container] if x is not None)

        contexts.append({
            "dispensing_rule_mnem": pr["dispensing_rule_mnem"],
            "dispensed_price": dispensed,
            "price_to_pharmacist": _f(pr["commonwealth_price"]),
            "max_general_patient_charge": _f(pr["max_general_patient_charge"]),
            "brand_premium": _f(pr["brand_premium"]),
            "special_patient_contribution": _f(pr["special_patient_contribution"]),
            "fees": {
                "dispensing_fee": fee_disp,
                "dangerous_drug_fee": fee_dd,
                "container_fee_other": fee_container,
                "container_fee_injectable": _f(pr["fee_container_injectable"]),
                "total_fees": round(total_fees, 2),
            },
            "markup_bands": [
                {
                    "band_code": mb["markup_band_code"],
                    "limit_amount": _f(mb["limit_amount"]),
                    "variable_rate": _f(mb["variable_rate"]),
                    "offset_amount": _f(mb["offset_amount"]),
                    "fixed_amount": _f(mb["fixed_amount"]),
                }
                for mb in markup_bands
            ],
        })

    return {
        "data": {
            "li_item_id": li_item_id,
            "pbs_code": pricing_rows[0]["pbs_code"],
            "program_code": program_code,
            "dispensing_contexts": contexts,
        },
        "meta": {
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
            "join_sources": ["/item-pricing", "/markup-bands", "/items"],
        },
    }


@router.get(
    "/items/{pbs_code}/dispensing-rules",
    summary="Get Item Dispensing Rules",
    description=(
        "Returns all dispensing rules applicable to a PBS item (linked via item_dispensing_rules). "
        "Each rule specifies dispensing quantity, unit, and repeats allowed. "
        "This is the programmatic equivalent of the PBS Schedule dispensing rules section.\n\n"
        "Available on all tiers."
    ),
)
async def get_item_dispensing_rules(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)

    if schedule:
        sched_row = await db.fetchrow("SELECT id FROM schedules WHERE month = $1", schedule)
        schedule_id = sched_row["id"] if sched_row else None
    else:
        sched_row = await db.fetchrow(
            "SELECT id FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
        schedule_id = sched_row["id"] if sched_row else None

    if not schedule_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})

    rows = await db.fetch(
        """
        SELECT pdr.program_code, pdr.rule_code, pdr.dispensing_quantity,
               pdr.dispensing_unit, pdr.repeats_allowed, pdr.description
        FROM program_dispensing_rules pdr
        JOIN item_dispensing_rules idr
          ON idr.rule_code = pdr.rule_code AND idr.schedule_id = pdr.schedule_id
        WHERE idr.pbs_code = $1 AND idr.schedule_id = $2
        ORDER BY pdr.rule_code
        """,
        pbs_code.upper(), schedule_id,
    )
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}
