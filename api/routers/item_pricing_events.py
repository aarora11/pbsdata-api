"""Item pricing events router — GET /v1/item-pricing-events"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["item-pricing-events"])



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


_NUMERIC_FIELDS = ["previous_price", "new_price"]


@router.get("/item-pricing-events")
async def list_item_pricing_events(
    response: Response,
    schedule: Optional[str] = Query(None),
    pbs_code: Optional[str] = Query(None, description="Filter by PBS item code"),
    event_type: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    query = """
        SELECT pbs_code, event_type, effective_date, previous_price, new_price
        FROM item_pricing_events WHERE schedule_id = $1
    """
    params = [schedule_id]
    if pbs_code:
        query += f" AND pbs_code = ${len(params) + 1}"
        params.append(pbs_code.upper())
    if event_type:
        query += f" AND event_type = ${len(params) + 1}"
        params.append(event_type)
    query += " ORDER BY effective_date DESC, pbs_code"

    rows = await db.fetch(query, *params)
    data = []
    for row in rows:
        r = dict(row)
        for f in _NUMERIC_FIELDS:
            if r.get(f) is not None:
                r[f] = float(r[f])
        if r.get("effective_date"):
            r["effective_date"] = r["effective_date"].isoformat()
        data.append(r)

    return {"data": data, "meta": {"total": len(data)}}
