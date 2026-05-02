"""Prescribing texts router — GET /v1/prescribing-texts"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["prescribing-texts"])



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


@router.get("/prescribing-texts")
async def list_prescribing_texts(
    response: Response,
    schedule: Optional[str] = Query(None),
    pbs_code: Optional[str] = Query(None),
    restriction_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)
    offset = (page - 1) * limit

    if pbs_code:
        # Filter by item via item_prescribing_text_relationships
        rows = await db.fetch(
            """
            SELECT pt.prescribing_text_id, pt.text_type, pt.complex_authority_required, pt.prescribing_txt
            FROM prescribing_texts pt
            JOIN item_prescribing_text_relationships rel
              ON rel.prescribing_text_id = pt.prescribing_text_id AND rel.schedule_id = pt.schedule_id
            WHERE pt.schedule_id = $1 AND rel.pbs_code = $2
            ORDER BY pt.prescribing_text_id
            LIMIT $3 OFFSET $4
            """,
            schedule_id, pbs_code.upper(), limit, offset,
        )
        total = await db.fetchval(
            """
            SELECT COUNT(*) FROM prescribing_texts pt
            JOIN item_prescribing_text_relationships rel
              ON rel.prescribing_text_id = pt.prescribing_text_id AND rel.schedule_id = pt.schedule_id
            WHERE pt.schedule_id = $1 AND rel.pbs_code = $2
            """,
            schedule_id, pbs_code.upper(),
        )
    elif restriction_code:
        rows = await db.fetch(
            """
            SELECT pt.prescribing_text_id, pt.text_type, pt.complex_authority_required, pt.prescribing_txt
            FROM prescribing_texts pt
            JOIN restriction_prescribing_text_relationships rel
              ON rel.prescribing_text_id = pt.prescribing_text_id AND rel.schedule_id = pt.schedule_id
            WHERE pt.schedule_id = $1 AND rel.restriction_code = $2
            ORDER BY pt.prescribing_text_id
            LIMIT $3 OFFSET $4
            """,
            schedule_id, restriction_code, limit, offset,
        )
        total = await db.fetchval(
            """
            SELECT COUNT(*) FROM prescribing_texts pt
            JOIN restriction_prescribing_text_relationships rel
              ON rel.prescribing_text_id = pt.prescribing_text_id AND rel.schedule_id = pt.schedule_id
            WHERE pt.schedule_id = $1 AND rel.restriction_code = $2
            """,
            schedule_id, restriction_code,
        )
    else:
        rows = await db.fetch(
            """
            SELECT prescribing_text_id, text_type, complex_authority_required, prescribing_txt
            FROM prescribing_texts WHERE schedule_id = $1
            ORDER BY prescribing_text_id
            LIMIT $2 OFFSET $3
            """,
            schedule_id, limit, offset,
        )
        total = await db.fetchval(
            "SELECT COUNT(*) FROM prescribing_texts WHERE schedule_id = $1", schedule_id
        )

    return {
        "data": [dict(r) for r in rows],
        "meta": {"total": total, "page": page, "limit": limit},
    }


@router.get("/prescribing-texts/{prescribing_text_id}")
async def get_prescribing_text(
    prescribing_text_id: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT prescribing_text_id, text_type, complex_authority_required, prescribing_txt
        FROM prescribing_texts WHERE prescribing_text_id = $1 AND schedule_id = $2
        """,
        prescribing_text_id, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Prescribing text not found."})
    return dict(row)
