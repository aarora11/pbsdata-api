"""Schedules router — full implementation."""
from fastapi import APIRouter, Depends, Response, HTTPException
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import require_tier
from api.routers.shared import _rl
from api.database import get_db

router = APIRouter(tags=["schedules"])



@router.get(
    "/schedules",
    summary="List Available Schedules",
    description=(
        "Returns all completed PBS schedule months available in the system, ordered newest first. "
        "Each record includes the schedule month (YYYY-MM), release date, item count, change count, "
        "and ingest status. Use the `month` field as the `schedule` parameter in other endpoints.\n\n"
        "Available on all tiers."
    ),
)
async def list_schedules(
    response: Response,
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    rows = await db.fetch(
        """
        SELECT id, month, released_at, is_embargo, item_count, change_count, ingest_status
        FROM schedules
        WHERE ingest_status = 'complete'
        ORDER BY month DESC
        """
    )
    data = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        data.append(d)
    return {"data": data, "meta": {"total": len(data)}}


@router.get(
    "/schedules/latest",
    summary="Get Latest Schedule",
    description=(
        "Returns the most recently published and fully ingested PBS schedule. "
        "The `month` field (YYYY-MM format) can be used as the `schedule` parameter in all other endpoints. "
        "Includes item count and change count for the schedule.\n\n"
        "Requires **Starter (T1)** tier."
    ),
)
async def get_latest_schedule(
    response: Response,
    api_key_data: dict = Depends(require_tier("starter")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    row = await db.fetchrow(
        """
        SELECT id, month, released_at, is_embargo, item_count, change_count, ingest_status
        FROM schedules
        WHERE ingest_status = 'complete'
        ORDER BY month DESC
        LIMIT 1
        """
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No published schedule found."})
    d = dict(row)
    d["id"] = str(d["id"])
    d["is_latest"] = True
    return {"data": d}
