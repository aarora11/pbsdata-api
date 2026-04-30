"""Items router — full implementation."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import is_tier_or_above, require_tier, tier_label
from api.database import get_db
from typing import Optional
import datetime

router = APIRouter(tags=["items"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


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


@router.get("/items/{pbs_code}")
async def get_item(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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
        FROM items i
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
        return result

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
            "manufacturer": dict(org_row) if org_row else None,
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


@router.get("/items/{pbs_code}/price")
async def get_item_price(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    pricing_rows = await db.fetch(
        """
        SELECT li_item_id, dispensing_rule_mnem, commonwealth_price,
               max_general_patient_charge, brand_premium,
               fee_dispensing, fee_dispensing_dangerous_drug,
               fee_container_other, fee_container_injectable,
               special_patient_contribution
        FROM item_pricing
        WHERE pbs_code = $1 AND schedule_id = $2
        ORDER BY li_item_id, dispensing_rule_mnem
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
        dpmq = _f(r["commonwealth_price"])
        gen_cp = _f(copayment["general"]) if copayment else None
        con_cp = _f(copayment["concessional"]) if copayment else None
        max_charge = _f(r["max_general_patient_charge"])
        gen_patient = min(dpmq, max_charge or dpmq) if dpmq is not None else None
        gov_pays = round(dpmq - gen_patient, 2) if (dpmq is not None and gen_patient is not None) else None

        contexts.append({
            "li_item_id": r["li_item_id"],
            "dispensing_rule_mnem": r["dispensing_rule_mnem"],
            "commonwealth_price": dpmq,
            "max_general_patient_charge": max_charge,
            "brand_premium": _f(r["brand_premium"]),
            "fees": {
                "dispensing_fee": _f(r["fee_dispensing"]),
                "dangerous_drug_fee": _f(r["fee_dispensing_dangerous_drug"]),
                "container_fee_other": _f(r["fee_container_other"]),
                "container_fee_injectable": _f(r["fee_container_injectable"]),
            },
            "patient_outcome": {
                "general_patient_charge": gen_patient,
                "concessional_patient_charge": con_cp,
                "government_pays": gov_pays,
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


@router.get("/items/{pbs_code}/patient-cost")
async def get_item_patient_cost(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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
        "SELECT i.pbs_code, i.brand_name, i.brand_premium, m.ingredient FROM items i JOIN medicines m ON m.id = i.medicine_id WHERE i.pbs_code = $1 AND i.schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    pricing = await db.fetchrow(
        "SELECT commonwealth_price, max_general_patient_charge, brand_premium FROM item_pricing WHERE pbs_code = $1 AND schedule_id = $2 ORDER BY commonwealth_price DESC NULLS LAST LIMIT 1",
        pbs_code.upper(), schedule_id,
    )
    copayment = await db.fetchrow(
        "SELECT general, concessional, safety_net_general, safety_net_concessional, increased_discount_limit FROM copayments WHERE schedule_id = $1",
        schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    dpmq = _f(pricing["commonwealth_price"]) if pricing else _f(item.get("government_price"))
    gen_cp = _f(copayment["general"]) if copayment else None
    con_cp = _f(copayment["concessional"]) if copayment else None
    sn_gen = _f(copayment["safety_net_general"]) if copayment else None
    sn_con = _f(copayment["safety_net_concessional"]) if copayment else None
    idl = _f(copayment["increased_discount_limit"]) if copayment else None
    brand_premium = _f(pricing["brand_premium"]) if pricing else _f(item["brand_premium"])
    max_charge = _f(pricing["max_general_patient_charge"]) if pricing else None

    you_pay_gen = min(dpmq, max_charge or dpmq) if dpmq is not None else None
    you_pay_con = min(dpmq, con_cp) if (dpmq is not None and con_cp is not None) else con_cp
    gov_pays_gen = round(dpmq - you_pay_gen, 2) if (dpmq and you_pay_gen is not None) else None
    gov_pays_con = round(dpmq - you_pay_con, 2) if (dpmq and you_pay_con is not None) else None
    scripts_gen = round(sn_gen / you_pay_gen) if (sn_gen and you_pay_gen) else None
    scripts_con = round(sn_con / you_pay_con) if (sn_con and you_pay_con) else None

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "drug_name": item["ingredient"],
            "brand_name": item["brand_name"],
            "dispensed_price": dpmq,
            "general_patient": {
                "copayment": gen_cp,
                "you_pay": you_pay_gen,
                "brand_premium": brand_premium or 0.0,
                "total_out_of_pocket": you_pay_gen,
                "government_pays": gov_pays_gen,
                "safety_net_threshold": sn_gen,
                "estimated_scripts_to_safety_net": scripts_gen,
            },
            "concessional_patient": {
                "copayment": con_cp,
                "you_pay": you_pay_con,
                "brand_premium": brand_premium or 0.0,
                "total_out_of_pocket": you_pay_con,
                "government_pays": gov_pays_con,
                "safety_net_threshold": sn_con,
                "estimated_scripts_to_safety_net": scripts_con,
            },
            "discount_zone": {
                "increased_discount_limit": idl,
            },
        },
    }


@router.get("/items/{pbs_code}/prescribing-texts")
async def get_item_prescribing_texts(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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


@router.get("/items/{pbs_code}/dispensing-rules")
async def get_item_dispensing_rules(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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
