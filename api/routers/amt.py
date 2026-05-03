"""AMT/ATC classification router — GET /v1/amt"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
from api.routers.shared import _rl

router = APIRouter(tags=["amt"])



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


@router.get("/amt")
async def list_amt_items(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_code: Optional[str] = Query(None),
    concept_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)
    offset = (page - 1) * limit

    conditions = ["schedule_id = $1"]
    params: list = [schedule_id]

    if atc_code:
        conditions.append(f"atc_code = ${len(params) + 1}")
        params.append(atc_code.upper())
    if concept_type:
        conditions.append(f"concept_type = ${len(params) + 1}")
        params.append(concept_type)

    where = " AND ".join(conditions)
    rows = await db.fetch(
        f"SELECT amt_id, concept_type, preferred_term, atc_code, parent_amt_id FROM amt_items WHERE {where} ORDER BY amt_id LIMIT ${len(params)+1} OFFSET ${len(params)+2}",
        *params, limit, offset,
    )
    total = await db.fetchval(
        f"SELECT COUNT(*) FROM amt_items WHERE {where}", *params
    )

    return {
        "data": [dict(r) for r in rows],
        "meta": {"total": total, "page": page, "limit": limit},
    }


@router.get("/amt/{amt_id}")
async def get_amt_item(
    amt_id: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT amt_id, concept_type, preferred_term, atc_code, parent_amt_id
        FROM amt_items WHERE amt_id = $1 AND schedule_id = $2
        """,
        amt_id, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "AMT concept not found."})

    # Include PBS items linked to this AMT concept
    linked_items = await db.fetch(
        "SELECT pbs_code, relationship_type FROM item_amt_relationships WHERE amt_id = $1 AND schedule_id = $2",
        amt_id, schedule_id,
    )

    result = dict(row)
    result["linked_items"] = [dict(r) for r in linked_items]
    return result
