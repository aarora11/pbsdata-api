"""Summary of changes router — GET /v1/summary-of-changes (official PBS changelog)"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from typing import Optional
import datetime
from api.routers.shared import _rl

router = APIRouter(tags=["summary-of-changes"])



@router.get("/summary-of-changes")
async def list_summary_of_changes(
    response: Response,
    schedule: Optional[str] = Query(None, description="Filter to a specific schedule month (YYYY-MM)"),
    since: Optional[str] = Query(None, description="Return changes from schedules on or after this month (YYYY-MM)"),
    pbs_code: Optional[str] = Query(None),
    change_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    offset = (page - 1) * limit

    conditions = []
    params: list = []

    if schedule:
        row = await db.fetchrow("SELECT id FROM schedules WHERE month = $1", schedule)
        if not row:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
        params.append(str(row["id"]))
        conditions.append(f"sc.id = ${len(params)}")
    elif since:
        params.append(since)
        conditions.append(f"s.month >= ${len(params)}")

    if pbs_code:
        params.append(pbs_code.upper())
        conditions.append(f"s.pbs_code = ${len(params)}")

    if change_type:
        params.append(change_type)
        conditions.append(f"s.change_type = ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = await db.fetch(
        f"""
        SELECT s.pbs_code, s.change_type, s.effective_date, s.description, s.section,
               sc.month AS schedule_month
        FROM summary_of_changes s
        JOIN schedules sc ON sc.id = s.schedule_id
        {where}
        ORDER BY sc.month DESC, s.pbs_code
        LIMIT ${len(params)+1} OFFSET ${len(params)+2}
        """,
        *params, limit, offset,
    )
    total = await db.fetchval(
        f"""
        SELECT COUNT(*) FROM summary_of_changes s
        JOIN schedules sc ON sc.id = s.schedule_id
        {where}
        """,
        *params,
    )

    data = []
    for row in rows:
        r = dict(row)
        if r.get("effective_date"):
            r["effective_date"] = r["effective_date"].isoformat()
        data.append(r)

    return {"data": data, "meta": {"total": total, "page": page, "limit": limit}}
