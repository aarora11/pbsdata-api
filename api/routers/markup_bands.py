"""Markup bands router — GET /v1/markup-bands"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["markup-bands"])


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


_NUMERIC_FIELDS = ["limit_amount", "variable_rate", "offset_amount", "fixed_amount"]


@router.get("/markup-bands")
async def list_markup_bands(
    response: Response,
    schedule: Optional[str] = Query(None),
    program_code: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    query = """
        SELECT markup_band_code, program_code, dispensing_rule_mnem,
               limit_amount, variable_rate, offset_amount, fixed_amount
        FROM markup_bands WHERE schedule_id = $1
    """
    params = [schedule_id]
    if program_code:
        query += " AND program_code = $2"
        params.append(program_code.upper())
    query += " ORDER BY markup_band_code"

    rows = await db.fetch(query, *params)
    data = []
    for row in rows:
        r = dict(row)
        for f in _NUMERIC_FIELDS:
            if r.get(f) is not None:
                r[f] = float(r[f])
        data.append(r)

    return {"data": data, "meta": {"total": len(data)}}
