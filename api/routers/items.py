"""Items router — full implementation."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
import datetime

router = APIRouter(tags=["items"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


def check_history_limit(api_key_data: dict, schedule: Optional[str]):
    if schedule is None:
        return
    history_months = api_key_data.get("history_months_limit", 3)
    today = datetime.date.today()
    cutoff = today.replace(day=1)
    for _ in range(history_months):
        if cutoff.month == 1:
            cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
        else:
            cutoff = cutoff.replace(month=cutoff.month - 1)
    try:
        year, month_num = schedule.split("-")
        req_date = datetime.date(int(year), int(month_num), 1)
    except Exception:
        return
    if req_date < cutoff:
        raise HTTPException(
            status_code=403,
            detail={"code": "HISTORY_LIMIT_EXCEEDED", "message": f"Your plan only allows access to the last {history_months} months of data."},
        )


@router.get("/items/{pbs_code}")
async def get_item(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    check_history_limit(api_key_data, schedule)

    # Get schedule
    if schedule:
        sched_row = await db.fetchrow("SELECT id FROM schedules WHERE month = $1", schedule)
        schedule_id = sched_row["id"] if sched_row else None
    else:
        sched_row = await db.fetchrow(
            "SELECT id FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
        schedule_id = sched_row["id"] if sched_row else None

    if not schedule_id:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    item = await db.fetchrow(
        """
        SELECT i.id, i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.pack_unit,
               i.benefit_type, i.general_charge, i.concessional_charge, i.government_price,
               i.brand_premium, i.brand_premium_counts_to_safety_net, i.sixty_day_eligible,
               i.max_quantity, i.max_repeats, i.dangerous_drug, i.formulary, i.section, i.program_code,
               m.ingredient, m.ingredient_lower, m.atc_code
        FROM items i
        JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Item not found."})

    restrictions = await db.fetch(
        "SELECT streamlined_code, indication, restriction_text, prescriber_type, authority_required, continuation_only FROM restrictions WHERE item_id = $1",
        item["id"],
    )

    result = dict(item)
    result["restrictions"] = [dict(r) for r in restrictions]
    # Convert UUID and Decimal to serializable types
    result["id"] = str(result["id"])
    for field in ["general_charge", "concessional_charge", "government_price", "brand_premium"]:
        if result.get(field) is not None:
            result[field] = float(result[field])

    return result
