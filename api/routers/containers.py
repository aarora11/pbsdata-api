"""Containers router — GET /v1/containers"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
from api.routers.shared import _rl

router = APIRouter(tags=["containers"])



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


_NUMERIC_FIELDS = ["mark_up", "agreed_purchasing_unit", "average_exact_unit_price", "average_rounded_unit_price"]


@router.get("/containers")
async def list_containers(
    response: Response,
    schedule: Optional[str] = Query(None),
    container_type: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    query = """
        SELECT container_code, container_name, mark_up, agreed_purchasing_unit,
               average_exact_unit_price, average_rounded_unit_price,
               container_type, container_quantity, container_unit_of_measure
        FROM containers WHERE schedule_id = $1
    """
    params = [schedule_id]
    if container_type:
        query += " AND container_type = $2"
        params.append(container_type)
    query += " ORDER BY container_code"

    rows = await db.fetch(query, *params)
    data = []
    for row in rows:
        r = dict(row)
        for f in _NUMERIC_FIELDS:
            if r.get(f) is not None:
                r[f] = float(r[f])
        data.append(r)

    return {"data": data, "meta": {"total": len(data)}}


@router.get("/containers/{container_code}")
async def get_container(
    container_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT container_code, container_name, mark_up, agreed_purchasing_unit,
               average_exact_unit_price, average_rounded_unit_price,
               container_type, container_quantity, container_unit_of_measure
        FROM containers WHERE container_code = $1 AND schedule_id = $2
        """,
        container_code, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Container not found."})

    result = dict(row)
    for f in _NUMERIC_FIELDS:
        if result.get(f) is not None:
            result[f] = float(result[f])
    return result
