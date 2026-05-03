"""Criteria router — GET /v1/criteria"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
from api.routers.shared import _rl

router = APIRouter(tags=["criteria"])



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


@router.get("/criteria")
async def list_criteria(
    response: Response,
    schedule: Optional[str] = Query(None),
    criteria_type: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    query = "SELECT criteria_id, criteria_type, parameter_relationship FROM criteria WHERE schedule_id = $1"
    params = [schedule_id]
    if criteria_type:
        query += " AND criteria_type = $2"
        params.append(criteria_type)
    query += " ORDER BY criteria_id"

    rows = await db.fetch(query, *params)
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get("/criteria/{criteria_id}")
async def get_criterion(
    criteria_id: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        "SELECT criteria_id, criteria_type, parameter_relationship FROM criteria WHERE criteria_id = $1 AND schedule_id = $2",
        criteria_id, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Criterion not found."})

    result = dict(row)

    # Include related parameters
    param_rows = await db.fetch(
        "SELECT parameter_id, pt_position FROM criteria_parameter_relationships WHERE criteria_id = $1 AND schedule_id = $2 ORDER BY pt_position",
        criteria_id, schedule_id,
    )
    result["parameters"] = [dict(p) for p in param_rows]
    return result
