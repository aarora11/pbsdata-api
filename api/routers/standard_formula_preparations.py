"""Standard formula preparations router — GET /v1/standard-formula-preparations"""
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


_NUMERIC_FIELDS = [
    "container_fee", "dispensing_fee_max_quantity",
    "safety_net_price", "maximum_patient_charge",
]


@router.get("/standard-formula-preparations")
async def list_standard_formula_preparations(
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    rows = await db.fetch(
        """
        SELECT pbs_code, sfp_drug_name, sfp_reference, container_fee,
               dispensing_fee_max_quantity, safety_net_price, maximum_patient_charge,
               maximum_quantity_unit, maximum_quantity
        FROM standard_formula_preparations WHERE schedule_id = $1 ORDER BY pbs_code
        """,
        schedule_id,
    )
    data = []
    for row in rows:
        r = dict(row)
        for f in _NUMERIC_FIELDS:
            if r.get(f) is not None:
                r[f] = float(r[f])
        data.append(r)

    return {"data": data, "meta": {"total": len(data)}}


@router.get("/standard-formula-preparations/{pbs_code}")
async def get_standard_formula_preparation(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT pbs_code, sfp_drug_name, sfp_reference, container_fee,
               dispensing_fee_max_quantity, safety_net_price, maximum_patient_charge,
               maximum_quantity_unit, maximum_quantity
        FROM standard_formula_preparations WHERE pbs_code = $1 AND schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Standard formula preparation not found."})

    result = dict(row)
    for f in _NUMERIC_FIELDS:
        if result.get(f) is not None:
            result[f] = float(result[f])
    return result
