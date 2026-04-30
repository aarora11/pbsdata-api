"""Programs router — GET /v1/programs"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import is_tier_or_above, require_tier, tier_label
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["programs"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


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


@router.get("/programs")
async def list_programs(
    response: Response,
    schedule: Optional[str] = Query(None),
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


@router.get("/programs/{program_code}")
async def get_program(
    program_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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
