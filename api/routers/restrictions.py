"""Restrictions router — GET /v1/restrictions"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import is_tier_or_above, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["restrictions"])



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


@router.get("/restrictions")
async def list_restrictions(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month YYYY-MM, defaults to latest"),
    pbs_code: Optional[str] = Query(None, description="Filter by PBS item code"),
    restriction_type: Optional[str] = Query(None, description="Filter by restriction type"),
    authority_required: Optional[bool] = Query(None, description="Filter by authority required"),
    streamlined_code: Optional[str] = Query(None, description="Filter by streamlined authority code"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    conditions = ["i.schedule_id = $1"]
    params = [schedule_id]
    idx = 2

    if pbs_code:
        conditions.append(f"i.pbs_code = ${idx}")
        params.append(pbs_code.upper())
        idx += 1

    if restriction_type:
        conditions.append(f"r.restriction_type = ${idx}")
        params.append(restriction_type)
        idx += 1

    if authority_required is not None:
        conditions.append(f"r.authority_required = ${idx}")
        params.append(authority_required)
        idx += 1

    if streamlined_code:
        conditions.append(f"r.streamlined_code = ${idx}")
        params.append(streamlined_code)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)

    count_sql = f"""
        SELECT COUNT(*)
        FROM restrictions r
        JOIN items i ON i.id = r.item_id
        {where}
    """
    data_sql = f"""
        SELECT r.id, i.pbs_code, r.restriction_code, r.streamlined_code,
               r.restriction_type, r.indication, r.restriction_text,
               r.prescriber_type, r.authority_required, r.continuation_only,
               r.clinical_criteria, r.treatment_phase, r.authority_method,
               r.treatment_of_code, r.written_authority_required,
               r.complex_authority_required, r.li_html_text
        FROM restrictions r
        JOIN items i ON i.id = r.item_id
        {where}
        ORDER BY i.pbs_code, r.restriction_code
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    offset = (page - 1) * limit

    total = await db.fetchval(count_sql, *params)
    rows = await db.fetch(data_sql, *params, limit, offset)

    data = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        data.append(d)

    return {"data": data, "meta": {"total": total or 0, "page": page, "limit": limit}}


@router.get("/restrictions/{restriction_code}")
async def get_restriction(
    restriction_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT r.id, i.pbs_code, r.restriction_code, r.streamlined_code,
               r.restriction_type, r.indication, r.restriction_text,
               r.prescriber_type, r.authority_required, r.continuation_only,
               r.clinical_criteria, r.treatment_phase, r.authority_method,
               r.treatment_of_code, r.written_authority_required,
               r.complex_authority_required, r.li_html_text
        FROM restrictions r
        JOIN items i ON i.id = r.item_id
        WHERE r.restriction_code = $1 AND i.schedule_id = $2
        """,
        restriction_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Restriction not found."})

    result = dict(row)
    result["id"] = str(result["id"])

    # T2+ subscribers receive the prescribing text chain joined in.
    # Base subscribers receive the raw restriction record only.
    if not is_tier_or_above(api_key_data, "growth"):
        return result

    prescribing_text_rels = await db.fetch(
        """
        SELECT rel.prescribing_text_id, pt.text_type, pt.prescribing_txt, pt.complex_authority_required
        FROM restriction_prescribing_text_relationships rel
        JOIN prescribing_texts pt ON pt.prescribing_text_id = rel.prescribing_text_id AND pt.schedule_id = rel.schedule_id
        WHERE rel.restriction_code = $1 AND rel.schedule_id = $2
        ORDER BY rel.prescribing_text_id
        """,
        restriction_code.upper(), schedule_id,
    )

    return {
        "data": {
            **result,
            "prescribing_components": [
                {
                    "prescribing_text_id": r["prescribing_text_id"],
                    "type": r["text_type"],
                    "text": r["prescribing_txt"],
                    "complex_authority_required": r["complex_authority_required"],
                }
                for r in prescribing_text_rels
            ],
        },
        "meta": {
            "tier": tier_label(api_key_data),
            "join_sources": ["/restrictions", "/restriction-prescribing-text-relationships", "/prescribing-texts"],
        },
    }
