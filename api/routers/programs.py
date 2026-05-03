"""Programs router — GET /v1/programs"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import is_tier_or_above, require_tier, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["programs"])



async def _resolve_schedule_id(db, schedule: Optional[str]) -> str:
    if schedule:
        row = await db.fetchrow("SELECT id FROM schedules WHERE month = $1", schedule)
    else:
        row = await db.fetchrow(
            "SELECT id FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
    return str(row["id"])


@router.get(
    "/programs",
    summary="List PBS Programs",
    description=(
        "Returns all PBS programs (e.g. General Schedule 'GE', Palliative Care 'PL') for a schedule. "
        "Starter (T1) subscribers additionally receive dispensing rules embedded per program. "
        "Programs determine which dispensing rules and fees apply to items.\n\n"
        "Available on all tiers (dispensing rules from Starter T1+)."
    ),
)
async def list_programs(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    rows = await db.fetch(
        "SELECT program_code, program_title FROM programs WHERE schedule_id = $1 ORDER BY program_code",
        schedule_id,
    )

    # T1+ subscribers receive dispensing rules embedded per program.
    # Base subscribers receive the raw program list only.
    if not is_tier_or_above(api_key_data, "starter"):
        return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}

    rule_rows = await db.fetch(
        """
        SELECT program_code, rule_code, dispensing_quantity, dispensing_unit, repeats_allowed, description
        FROM program_dispensing_rules WHERE schedule_id = $1
        ORDER BY program_code, rule_code
        """,
        schedule_id,
    )
    rules_by_program: dict[str, list] = {}
    for r in rule_rows:
        rules_by_program.setdefault(r["program_code"], []).append(dict(r))

    programs = []
    for r in rows:
        p = dict(r)
        p["dispensing_rules"] = rules_by_program.get(r["program_code"], [])
        programs.append(p)

    return {
        "data": programs,
        "meta": {
            "total": len(programs),
            "tier": tier_label(api_key_data),
            "join_sources": ["/programs", "/program-dispensing-rules"],
        },
    }


@router.get(
    "/programs/{program_code}/fee-structure",
    summary="Get Program Fee Structure",
    description=(
        "Returns the complete fee structure for a PBS program: all dispensing rules, "
        "markup band schedules (used to calculate pharmacy mark-up by price tier), "
        "and applicable fees. The markup bands define the variable rate and fixed amounts "
        "applied at different DPMQ price ranges.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_program_fee_structure(
    program_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    program = await db.fetchrow(
        "SELECT program_code, program_title FROM programs WHERE program_code = $1 AND schedule_id = $2",
        program_code.upper(), schedule_id,
    )
    if not program:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Program not found."})

    dispensing_rules = await db.fetch(
        """
        SELECT rule_code, dispensing_quantity, dispensing_unit, repeats_allowed, description
        FROM program_dispensing_rules WHERE program_code = $1 AND schedule_id = $2
        ORDER BY rule_code
        """,
        program_code.upper(), schedule_id,
    )

    markup_bands = await db.fetch(
        """
        SELECT markup_band_code, dispensing_rule_mnem, limit_amount, variable_rate, offset_amount, fixed_amount
        FROM markup_bands WHERE program_code = $1 AND schedule_id = $2
        ORDER BY dispensing_rule_mnem, limit_amount NULLS LAST
        """,
        program_code.upper(), schedule_id,
    )

    fees = await db.fetch(
        "SELECT fee_code, fee_type, description, amount FROM fees WHERE schedule_id = $1 ORDER BY fee_code",
        schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    # Group markup bands by dispensing rule
    bands_by_rule: dict[str, list] = {}
    for mb in markup_bands:
        rule = mb["dispensing_rule_mnem"] or "GENERAL"
        bands_by_rule.setdefault(rule, []).append({
            "band_code": mb["markup_band_code"],
            "limit_amount": _f(mb["limit_amount"]),
            "variable_rate": _f(mb["variable_rate"]),
            "offset_amount": _f(mb["offset_amount"]),
            "fixed_amount": _f(mb["fixed_amount"]),
        })

    return {
        "data": {
            "program_code": program["program_code"],
            "program_title": program["program_title"],
            "dispensing_rules": [dict(r) for r in dispensing_rules],
            "markup_structure": {
                rule: bands for rule, bands in bands_by_rule.items()
            },
            "fees": [
                {"fee_code": f["fee_code"], "fee_type": f["fee_type"],
                 "description": f["description"], "amount": _f(f["amount"])}
                for f in fees
            ],
            "note": "Fees shown are schedule-level; not all may apply to this program.",
        },
        "meta": {
            "tier": tier_label(api_key_data),
            "join_sources": ["/programs", "/program-dispensing-rules", "/markup-bands", "/fees"],
        },
    }


@router.get(
    "/programs/{program_code}",
    summary="Get Program Detail",
    description=(
        "Returns a single PBS program record including title and all associated dispensing rules "
        "(quantity, unit, repeats allowed). Use the program code from any item's `program_code` field.\n\n"
        "Available on all tiers."
    ),
)
async def get_program(
    program_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT program_code, program_title
        FROM programs WHERE program_code = $1 AND schedule_id = $2
        """,
        program_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Program not found."})

    result = dict(row)
    dispensing_rules = await db.fetch(
        """
        SELECT rule_code, dispensing_quantity, dispensing_unit, repeats_allowed, description
        FROM program_dispensing_rules WHERE program_code = $1 AND schedule_id = $2
        ORDER BY rule_code
        """,
        program_code.upper(), schedule_id,
    )
    result["dispensing_rules"] = [dict(r) for r in dispensing_rules]
    return result
