"""Extemporaneous tariffs router — GET /v1/extemporaneous-tariffs"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional

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
    "agreed_purchasing_unit", "markup",
    "rounded_rec_one_tenth_gram", "rounded_rec_one_gram",
    "rounded_rec_ten_gram", "rounded_rec_hundred_gram",
    "exact_rec_one_tenth_gram", "exact_rec_one_gram",
    "exact_rec_ten_gram", "exact_rec_hundred_gram",
]


@router.get("/extemporaneous-tariffs")
async def list_extemporaneous_tariffs(
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    rows = await db.fetch(
        """
        SELECT pbs_code, drug_name, agreed_purchasing_unit, markup,
               rounded_rec_one_tenth_gram, rounded_rec_one_gram,
               rounded_rec_ten_gram, rounded_rec_hundred_gram,
               exact_rec_one_tenth_gram, exact_rec_one_gram,
               exact_rec_ten_gram, exact_rec_hundred_gram
        FROM extemporaneous_tariffs WHERE schedule_id = $1 ORDER BY pbs_code
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


@router.get("/extemporaneous-tariffs/{pbs_code}")
async def get_extemporaneous_tariff(
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
        SELECT pbs_code, drug_name, agreed_purchasing_unit, markup,
               rounded_rec_one_tenth_gram, rounded_rec_one_gram,
               rounded_rec_ten_gram, rounded_rec_hundred_gram,
               exact_rec_one_tenth_gram, exact_rec_one_gram,
               exact_rec_ten_gram, exact_rec_hundred_gram
        FROM extemporaneous_tariffs WHERE pbs_code = $1 AND schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Extemporaneous tariff not found."})

    result = dict(row)
    for f in _NUMERIC_FIELDS:
        if result.get(f) is not None:
            result[f] = float(result[f])
    return result
