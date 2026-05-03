"""Prescribers router — GET /v1/prescribers"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
from api.routers.shared import _rl

router = APIRouter(tags=["prescribers"])



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


@router.get("/prescribers")
async def list_prescribers(
    response: Response,
    schedule: Optional[str] = Query(None),
    pbs_code: Optional[str] = Query(None, description="Filter by PBS item code"),
    prescriber_type: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    query = "SELECT pbs_code, prescriber_code, prescriber_type FROM item_prescribers WHERE schedule_id = $1"
    params = [schedule_id]
    if pbs_code:
        query += f" AND pbs_code = ${len(params) + 1}"
        params.append(pbs_code.upper())
    if prescriber_type:
        query += f" AND prescriber_type = ${len(params) + 1}"
        params.append(prescriber_type)
    query += " ORDER BY pbs_code, prescriber_code"

    rows = await db.fetch(query, *params)
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}
