"""Dispensing rules router — GET /v1/dispensing-rules"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import require_tier, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["dispensing-rules"])



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
    "/dispensing-rules",
    summary="List Dispensing Rules",
    description=(
        "Returns all PBS dispensing rules (program_dispensing_rules) specifying dispensing quantity, "
        "unit of measure, and number of repeats allowed for each program/rule combination. "
        "Filter by `program_code` to retrieve rules for a specific PBS program.\n\n"
        "Available on all tiers."
    ),
)
async def list_dispensing_rules(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    program_code: Optional[str] = Query(None, description="Filter by PBS program code (e.g. 'GE' for General Schedule)"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    if program_code:
        rows = await db.fetch(
            """
            SELECT program_code, rule_code, dispensing_quantity, dispensing_unit, repeats_allowed, description
            FROM program_dispensing_rules WHERE schedule_id = $1 AND program_code = $2
            ORDER BY rule_code
            """,
            schedule_id, program_code.upper(),
        )
    else:
        rows = await db.fetch(
            """
            SELECT program_code, rule_code, dispensing_quantity, dispensing_unit, repeats_allowed, description
            FROM program_dispensing_rules WHERE schedule_id = $1
            ORDER BY program_code, rule_code
            """,
            schedule_id,
        )

    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get(
    "/dispensing-rules/by-program/{program_code}",
    summary="Get Dispensing Rules by Program",
    description=(
        "Returns all dispensing rules for a specific PBS program, including program title, "
        "rule codes, dispensing quantities, units, and repeats allowed. "
        "Useful for understanding supply quantities within a program context.\n\n"
        "Requires **Starter (T1)** tier."
    ),
)
async def get_dispensing_rules_by_program(
    program_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("starter")),
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

    rows = await db.fetch(
        """
        SELECT rule_code, dispensing_quantity, dispensing_unit, repeats_allowed, description
        FROM program_dispensing_rules
        WHERE program_code = $1 AND schedule_id = $2
        ORDER BY rule_code
        """,
        program_code.upper(), schedule_id,
    )
    return {
        "data": {
            "program_code": program["program_code"],
            "program_title": program["program_title"],
            "dispensing_rules": [dict(r) for r in rows],
        },
        "meta": {
            "total": len(rows),
            "tier": tier_label(api_key_data),
            "join_sources": ["/programs", "/dispensing-rules"],
        },
    }


@router.get(
    "/dispensing-rules/{rule_code}",
    summary="Get Dispensing Rule Detail",
    description=(
        "Returns a single dispensing rule by rule code, including quantity, unit, repeats, description, "
        "and a list of PBS item codes linked to this rule.\n\n"
        "Available on all tiers."
    ),
)
async def get_dispensing_rule(
    rule_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT program_code, rule_code, dispensing_quantity, dispensing_unit, repeats_allowed, description
        FROM program_dispensing_rules WHERE rule_code = $1 AND schedule_id = $2
        """,
        rule_code, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Dispensing rule not found."})

    result = dict(row)
    # Include items linked to this rule
    linked_items = await db.fetch(
        "SELECT pbs_code FROM item_dispensing_rules WHERE rule_code = $1 AND schedule_id = $2",
        rule_code, schedule_id,
    )
    result["linked_items"] = [r["pbs_code"] for r in linked_items]
    return result
