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


@router.get(
    "/amt",
    summary="List AMT Concepts",
    description=(
        "Returns Australian Medicines Terminology (AMT) concept records. "
        "AMT provides standardised clinical vocabulary with concept types such as: "
        "CTPP (Containered Trade Product Pack), TPP (Trade Product Pack), TP (Trade Product), "
        "MPP (Medicinal Product Pack), MP (Medicinal Product). "
        "Filter by `atc_code` or `concept_type` to narrow results.\n\n"
        "Available on all tiers."
    ),
)
async def list_amt_items(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_code: Optional[str] = Query(None, description="Filter by ATC code (exact match)"),
    concept_type: Optional[str] = Query(None, description="Filter by AMT concept type (e.g. 'CTPP', 'TPP', 'MP')"),
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


@router.get(
    "/amt/{amt_id}",
    summary="Get AMT Concept Detail",
    description=(
        "Returns a single AMT concept by its AMT ID, including concept type, preferred term, "
        "ATC code, parent concept, and all PBS items linked to this concept.\n\n"
        "Available on all tiers."
    ),
)
async def get_amt_item(
    amt_id: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
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
