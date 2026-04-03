"""Schedules router — full implementation."""
from fastapi import APIRouter, Depends, Response
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db

router = APIRouter(tags=["schedules"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


@router.get("/schedules")
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
