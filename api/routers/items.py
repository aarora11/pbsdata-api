from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
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
async def get_item(pbs_code: str, response: Response, schedule: Optional[str] = Query(None), api_key_data: dict = Depends(check_rate_limit)):
    _rl(response, api_key_data)
    check_history_limit(api_key_data, schedule)
    return {"status": "not_implemented"}
