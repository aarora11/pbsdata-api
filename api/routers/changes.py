"""Changes router — full implementation."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from api.routers.shared import _rl

router = APIRouter(tags=["changes"])



@router.get(
    "/changes",
    summary="List Field-Level Changes",
    description=(
        "Returns field-level change records across PBS schedules — showing old and new values "
        "for individual fields that changed between schedule ingests. "
        "Use `since` to start from a schedule month and optionally `until` to bound the range. "
        "Filter by `change_type` (INSERT/UPDATE/DELETE) to narrow results.\n\n"
        "Available on all tiers."
    ),
)
async def list_changes(
    response: Response,
    since: str = Query(..., description="Start schedule month in YYYY-MM format (required); returns changes from this month onwards"),
    until: str = Query(None, description="End schedule month in YYYY-MM format (inclusive); defaults to all available months"),
    change_type: str = Query(None, description="Filter by change type: INSERT, UPDATE, or DELETE"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)

    conditions = ["s.month >= $1"]
    params = [since]
    param_idx = 2

    if until:
        conditions.append(f"s.month <= ${param_idx}")
        params.append(until)
        param_idx += 1

    if change_type:
        conditions.append(f"c.change_type = ${param_idx}")
        params.append(change_type)
        param_idx += 1

    where = "WHERE " + " AND ".join(conditions)

    count_sql = f"""
        SELECT COUNT(*) FROM changes c
        JOIN schedules s ON s.id = c.schedule_id
        {where}
    """
    data_sql = f"""
        SELECT c.id, c.pbs_code, c.change_type, c.field_name, c.old_value, c.new_value,
               c.created_at, s.month
        FROM changes c
        JOIN schedules s ON s.id = c.schedule_id
        {where}
        ORDER BY s.month DESC, c.pbs_code
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    offset = (page - 1) * limit
    all_params = params + [limit, offset]

    total = await db.fetchval(count_sql, *params)
    rows = await db.fetch(data_sql, *all_params)

    data = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        data.append(d)

    return {"data": data, "meta": {"total": total or 0, "page": page, "limit": limit}}
