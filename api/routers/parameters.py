"""Parameters router — GET /v1/parameters"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["parameters"])



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


@router.get("/parameters")
async def list_parameters(
    response: Response,
    schedule: Optional[str] = Query(None),
    parameter_type: Optional[str] = Query(None),
    assessment_type: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    query = "SELECT parameter_id, assessment_type, parameter_type FROM parameters WHERE schedule_id = $1"
    params = [schedule_id]
    if parameter_type:
        query += f" AND parameter_type = ${len(params) + 1}"
        params.append(parameter_type)
    if assessment_type:
        query += f" AND assessment_type = ${len(params) + 1}"
        params.append(assessment_type)
    query += " ORDER BY parameter_id"

    rows = await db.fetch(query, *params)
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get("/parameters/{parameter_id}")
async def get_parameter(
    parameter_id: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        "SELECT parameter_id, assessment_type, parameter_type FROM parameters WHERE parameter_id = $1 AND schedule_id = $2",
        parameter_id, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Parameter not found."})
    return dict(row)
