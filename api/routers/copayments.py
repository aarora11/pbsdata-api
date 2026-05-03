"""Copayments router — GET /v1/copayments"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import require_tier, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["copayments"])



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
    "/copayments/current",
    summary="Get Current Copayment Thresholds",
    description=(
        "Returns the current PBS patient copayment amounts and safety net thresholds from the latest "
        "complete schedule. Includes general copayment, concessional copayment, safety net threshold "
        "for each category, safety net card issue fee, and CTG (Closing the Gap) contribution.\n\n"
        "Requires **Starter (T1)** tier."
    ),
)
async def get_current_copayments(
    response: Response,
    api_key_data: dict = Depends(require_tier("starter")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    row = await db.fetchrow(
        """
        SELECT
            s.month AS schedule_code,
            s.released_at AS effective_date,
            c.general, c.concessional,
            c.safety_net_general, c.safety_net_concessional,
            c.safety_net_card_issue, c.increased_discount_limit,
            c.safety_net_ctg_contribution
        FROM copayments c
        JOIN schedules s ON s.id = c.schedule_id
        WHERE s.ingest_status = 'complete'
        ORDER BY s.month DESC
        LIMIT 1
        """
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No published copayment data found."})

    def _f(v):
        return float(v) if v is not None else None

    return {
        "data": {
            "schedule_code": row["schedule_code"],
            "effective_date": row["effective_date"].date().isoformat() if row["effective_date"] else None,
            "patient_charges": {
                "general_copayment": _f(row["general"]),
                "concessional_copayment": _f(row["concessional"]),
            },
            "safety_net": {
                "general_threshold": _f(row["safety_net_general"]),
                "concessional_threshold": _f(row["safety_net_concessional"]),
                "card_issue_fee": _f(row["safety_net_card_issue"]),
                "ctg_contribution": _f(row["safety_net_ctg_contribution"]),
            },
            "discount_limits": {
                "increased_discount_limit": _f(row["increased_discount_limit"]),
            },
        },
        "meta": {
            "tier": tier_label(api_key_data),
            "join_sources": ["/copayments", "/schedules"],
        },
    }


@router.get(
    "/copayments",
    summary="Get Schedule Copayment Data",
    description=(
        "Returns PBS copayment thresholds for a specific schedule month, or the latest schedule by default. "
        "Includes general and concessional patient charges, safety net thresholds, "
        "and the increased discount limit.\n\n"
        "Available on all tiers."
    ),
)
async def get_copayments(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
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
