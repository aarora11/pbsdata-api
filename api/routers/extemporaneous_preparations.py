"""Extemporaneous preparations router — GET /v1/extemporaneous-preparations"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
from api.routers.shared import _rl

router = APIRouter(tags=["extemporaneous"])



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


@router.get("/extemporaneous-preparations")
async def list_extemporaneous_preparations(
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    rows = await db.fetch(
        """
        SELECT pbs_code, preparation, maximum_quantity, maximum_quantity_unit
        FROM extemporaneous_preparations WHERE schedule_id = $1 ORDER BY pbs_code
        """,
        schedule_id,
    )
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get("/extemporaneous-preparations/{pbs_code}")
async def get_extemporaneous_preparation(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        "SELECT pbs_code, preparation, maximum_quantity, maximum_quantity_unit FROM extemporaneous_preparations WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Extemporaneous preparation not found."})
    return dict(row)
