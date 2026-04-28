"""ATC codes router — GET /v1/atc-codes"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["atc-codes"])


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


@router.get("/atc-codes")
async def list_atc_codes(
    response: Response,
    schedule: Optional[str] = Query(None),
    level: Optional[int] = Query(None),
    parent_code: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    conditions = ["schedule_id = $1"]
    params = [schedule_id]

    if level is not None:
        params.append(level)
        conditions.append(f"atc_level = ${len(params)}")
    if parent_code is not None:
        params.append(parent_code.upper())
        conditions.append(f"atc_parent_code = ${len(params)}")

    where = " AND ".join(conditions)
    rows = await db.fetch(
        f"""
        SELECT atc_code, atc_description, atc_level, atc_parent_code
        FROM atc_codes WHERE {where}
        ORDER BY atc_code
        """,
        *params,
    )
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get("/atc-codes/{atc_code}")
async def get_atc_code(
    atc_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT atc_code, atc_description, atc_level, atc_parent_code
        FROM atc_codes WHERE atc_code = $1 AND schedule_id = $2
        """,
        atc_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "ATC code not found."})

    result = dict(row)
    # Items linked to this ATC code via item_atc_relationships
    linked_items = await db.fetch(
        """
        SELECT pbs_code, atc_priority_pct
        FROM item_atc_relationships WHERE atc_code = $1 AND schedule_id = $2
        ORDER BY pbs_code
        """,
        atc_code.upper(), schedule_id,
    )
    result["linked_items"] = [dict(r) for r in linked_items]
    # Children in the ATC tree
    children = await db.fetch(
        """
        SELECT atc_code, atc_description, atc_level
        FROM atc_codes WHERE atc_parent_code = $1 AND schedule_id = $2
        ORDER BY atc_code
        """,
        atc_code.upper(), schedule_id,
    )
    result["children"] = [dict(r) for r in children]
    return result
