"""Copayments router — GET /v1/copayments"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["copayments"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


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


@router.get("/copayments")
async def get_copayments(
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    """Return the schedule-level copayment thresholds for a given schedule (latest by default)."""
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT
            s.month,
            c.general, c.concessional,
            c.safety_net_general, c.safety_net_concessional,
            c.safety_net_card_issue, c.increased_discount_limit,
            c.safety_net_ctg_contribution
        FROM copayments c
        JOIN schedules s ON s.id = c.schedule_id
        WHERE c.schedule_id = $1
        """,
        schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Copayment data not available for this schedule."})

    return dict(row)
