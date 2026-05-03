"""Fees router — GET /v1/fees"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from api.routers.shared import _rl
from typing import Optional

router = APIRouter(tags=["fees"])



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
    "/fees",
    summary="List PBS Dispensing Fees",
    description=(
        "Returns all PBS dispensing fee records for a given schedule, including dispensing fees, "
        "dangerous drug fees, container fees, and patient contribution amounts. "
        "Filter by `fee_type` to retrieve a specific category of fees.\n\n"
        "Available on all tiers."
    ),
)
async def list_fees(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    fee_type: Optional[str] = Query(None, description="Filter by fee type code (e.g. dispensing, container, dangerous drug)"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    query = "SELECT fee_code, fee_type, description, amount, patient_contribution FROM fees WHERE schedule_id = $1"
    params = [schedule_id]
    if fee_type:
        query += " AND fee_type = $2"
        params.append(fee_type)
    query += " ORDER BY fee_code"

    rows = await db.fetch(query, *params)
    data = []
    for row in rows:
        r = dict(row)
        for field in ["amount", "patient_contribution"]:
            if r.get(field) is not None:
                r[field] = float(r[field])
        data.append(r)

    return {"data": data, "meta": {"total": len(data)}}


@router.get(
    "/fees/{fee_code}",
    summary="Get Fee by Code",
    description=(
        "Returns a single PBS fee record by its fee code, including the description, amount, "
        "and patient contribution component for the given schedule.\n\n"
        "Available on all tiers."
    ),
)
async def get_fee(
    fee_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        "SELECT fee_code, fee_type, description, amount, patient_contribution FROM fees WHERE fee_code = $1 AND schedule_id = $2",
        fee_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Fee not found."})

    result = dict(row)
    for field in ["amount", "patient_contribution"]:
        if result.get(field) is not None:
            result[field] = float(result[field])
    return result
