"""Indications router — GET /v1/indications"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
from api.routers.shared import _rl

router = APIRouter(tags=["indications"])



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


@router.get("/indications")
async def list_indications(
    response: Response,
    schedule: Optional[str] = Query(None),
    pbs_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)
    offset = (page - 1) * limit

    if pbs_code:
        rows = await db.fetch(
            """
            SELECT indication_id, pbs_code, indication_text, condition_description
            FROM indications WHERE schedule_id = $1 AND pbs_code = $2
            ORDER BY indication_id LIMIT $3 OFFSET $4
            """,
            schedule_id, pbs_code.upper(), limit, offset,
        )
        total = await db.fetchval(
            "SELECT COUNT(*) FROM indications WHERE schedule_id = $1 AND pbs_code = $2",
            schedule_id, pbs_code.upper(),
        )
    else:
        rows = await db.fetch(
            """
            SELECT indication_id, pbs_code, indication_text, condition_description
            FROM indications WHERE schedule_id = $1
            ORDER BY indication_id LIMIT $2 OFFSET $3
            """,
            schedule_id, limit, offset,
        )
        total = await db.fetchval(
            "SELECT COUNT(*) FROM indications WHERE schedule_id = $1", schedule_id
        )

    return {
        "data": [dict(r) for r in rows],
        "meta": {"total": total, "page": page, "limit": limit},
    }


@router.get("/indications/{indication_id}")
async def get_indication(
    indication_id: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT indication_id, pbs_code, indication_text, condition_description
        FROM indications WHERE indication_id = $1 AND schedule_id = $2
        """,
        indication_id, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Indication not found."})
    return dict(row)
