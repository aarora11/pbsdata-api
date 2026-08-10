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
            SELECT program_code, rule_code, dispensing_rule_mnem, dispensing_rule_title,
                   community_pharmacy_indicator, dispensing_quantity, dispensing_unit,
                   repeats_allowed, description
            FROM program_dispensing_rules WHERE schedule_id = $1 AND program_code = $2
            ORDER BY rule_code
            """,
            schedule_id, program_code.upper(),
        )
    else:
        rows = await db.fetch(
            """
            SELECT program_code, rule_code, dispensing_rule_mnem, dispensing_rule_title,
                   community_pharmacy_indicator, dispensing_quantity, dispensing_unit,
                   repeats_allowed, description
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
        SELECT rule_code, dispensing_rule_mnem, dispensing_rule_title,
               community_pharmacy_indicator, dispensing_quantity, dispensing_unit,
               repeats_allowed, description
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
        "Returns a dispensing rule by `dispensing_rule_reference` (e.g. `rp-s90-cp`), including "
        "quantity, unit, repeats, title, and the PBS item codes linked to it.\n\n"
        "A `dispensing_rule_mnem` (e.g. `s90-cp`) is also accepted. PBS defines a mnemonic as "
        "naming a *group* of rules, so it resolves to every rule in that group — `s90-cp` covers "
        "`in-s90-cp`, `rp-s90-cp`, `rp-np-s90-cp` and `rp-np-nc-s90-cp`. The response reports "
        "which form matched via `matched_on`.\n\n"
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

    columns = (
        "program_code, rule_code, dispensing_rule_reference, dispensing_rule_mnem, "
        "dispensing_rule_title, community_pharmacy_indicator, dispensing_quantity, "
        "dispensing_unit, repeats_allowed, description"
    )

    # The reference is the unique key, so try it first and return exactly one rule.
    rows = await db.fetch(
        f"SELECT {columns} FROM program_dispensing_rules "
        f"WHERE rule_code = $1 AND schedule_id = $2",
        rule_code, schedule_id,
    )
    matched_on = "dispensing_rule_reference"

    if not rows:
        # Fall back to the mnemonic, which PBS defines as naming a group of rules.
        # Callers written against the pre-014 paths land here rather than 404ing.
        rows = await db.fetch(
            f"SELECT {columns} FROM program_dispensing_rules "
            f"WHERE dispensing_rule_mnem = $1 AND schedule_id = $2 ORDER BY rule_code",
            rule_code, schedule_id,
        )
        matched_on = "dispensing_rule_mnem"

    if not rows:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Dispensing rule not found."})

    references = [r["rule_code"] for r in rows]
    linked_items = await db.fetch(
        "SELECT DISTINCT pbs_code FROM item_dispensing_rules "
        "WHERE rule_code = ANY($1::text[]) AND schedule_id = $2 ORDER BY pbs_code",
        references, schedule_id,
    )

    result = dict(rows[0])
    result["matched_on"] = matched_on
    # A mnemonic can match several rules; the group is always reported so a caller
    # arriving on the old vocabulary can see what it actually resolved to.
    result["matched_rules"] = [dict(r) for r in rows]
    result["linked_items"] = [r["pbs_code"] for r in linked_items]
    return result
