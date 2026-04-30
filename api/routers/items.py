"""Items router — full implementation."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import is_tier_or_above, tier_label
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
