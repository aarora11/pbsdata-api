"""Schedule changes router — T3 Intelligence endpoints for /v1/schedule-changes/..."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.tier import require_tier, tier_label
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["schedule-changes"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


async def _resolve_schedule(db, schedule_code: Optional[str]) -> tuple[str, str]:
    if schedule_code:
        row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", schedule_code)
    else:
        row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
    return str(row["id"]), row["month"]


@router.get("/schedule-changes")
async def list_schedule_changes(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month YYYY-MM, defaults to latest"),
    change_type: Optional[str] = Query(None, description="Filter by change type"),
    pbs_code: Optional[str] = Query(None, description="Filter by PBS code"),
    section: Optional[str] = Query(None, description="Filter by section/endpoint source"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    conditions = ["schedule_id = $1"]
    params: list = [schedule_id]

    if change_type:
        params.append(change_type)
        conditions.append(f"change_type = ${len(params)}")

    if pbs_code:
        params.append(pbs_code.upper())
        conditions.append(f"pbs_code = ${len(params)}")

    if section:
        params.append(f"%{section}%")
        conditions.append(f"section ILIKE ${len(params)}")

    where = " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) FROM summary_of_changes WHERE {where}"
    data_sql = f"""
        SELECT pbs_code, change_type, effective_date, description, section
        FROM summary_of_changes
        WHERE {where}
        ORDER BY effective_date DESC NULLS LAST, pbs_code
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """
    offset = (page - 1) * limit
    total = await db.fetchval(count_sql, *params)
    rows = await db.fetch(data_sql, *params, limit, offset)

    return {
        "data": [
            {
                "pbs_code": r["pbs_code"],
                "change_type": r["change_type"],
                "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                "description": r["description"],
                "section": r["section"],
            }
            for r in rows
        ],
        "meta": {
            "total": total or 0,
            "page": page,
            "limit": limit,
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
        },
    }


@router.get("/schedule-changes/additions")
async def get_schedule_additions(
    response: Response,
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    total = await db.fetchval(
        "SELECT COUNT(*) FROM summary_of_changes WHERE schedule_id = $1 AND change_type ILIKE '%addition%'",
        schedule_id,
    )
    rows = await db.fetch(
        """
        SELECT pbs_code, change_type, effective_date, description, section
        FROM summary_of_changes
        WHERE schedule_id = $1 AND change_type ILIKE '%addition%'
        ORDER BY pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    return {
        "data": [
            {
                "pbs_code": r["pbs_code"],
                "change_type": r["change_type"],
                "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                "description": r["description"],
                "section": r["section"],
            }
            for r in rows
        ],
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }


@router.get("/schedule-changes/deletions")
async def get_schedule_deletions(
    response: Response,
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    total = await db.fetchval(
        "SELECT COUNT(*) FROM summary_of_changes WHERE schedule_id = $1 AND change_type ILIKE '%delet%'",
        schedule_id,
    )
    rows = await db.fetch(
        """
        SELECT pbs_code, change_type, effective_date, description, section
        FROM summary_of_changes
        WHERE schedule_id = $1 AND change_type ILIKE '%delet%'
        ORDER BY pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    return {
        "data": [
            {
                "pbs_code": r["pbs_code"],
                "change_type": r["change_type"],
                "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                "description": r["description"],
                "section": r["section"],
            }
            for r in rows
        ],
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }


@router.get("/schedule-changes/price-changes")
async def get_schedule_price_changes(
    response: Response,
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    total = await db.fetchval(
        "SELECT COUNT(*) FROM summary_of_changes WHERE schedule_id = $1 AND change_type ILIKE '%price%'",
        schedule_id,
    )
    rows = await db.fetch(
        """
        SELECT pbs_code, change_type, effective_date, description, section
        FROM summary_of_changes
        WHERE schedule_id = $1 AND change_type ILIKE '%price%'
        ORDER BY effective_date DESC NULLS LAST, pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    return {
        "data": [
            {
                "pbs_code": r["pbs_code"],
                "change_type": r["change_type"],
                "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                "description": r["description"],
                "section": r["section"],
            }
            for r in rows
        ],
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }


@router.get("/schedule-changes/restriction-changes")
async def get_schedule_restriction_changes(
    response: Response,
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    total = await db.fetchval(
        "SELECT COUNT(*) FROM summary_of_changes WHERE schedule_id = $1 AND change_type ILIKE '%restrict%'",
        schedule_id,
    )
    rows = await db.fetch(
        """
        SELECT pbs_code, change_type, effective_date, description, section
        FROM summary_of_changes
        WHERE schedule_id = $1 AND change_type ILIKE '%restrict%'
        ORDER BY pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    return {
        "data": [
            {
                "pbs_code": r["pbs_code"],
                "change_type": r["change_type"],
                "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                "description": r["description"],
                "section": r["section"],
            }
            for r in rows
        ],
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }


@router.get("/schedule-changes/{schedule_code}")
async def get_schedule_change_summary(
    schedule_code: str,
    response: Response,
    change_type: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    """Full summary of changes for a specific schedule month."""
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule_code)

    conditions = ["schedule_id = $1"]
    params: list = [schedule_id]

    if change_type:
        params.append(change_type)
        conditions.append(f"change_type = ${len(params)}")

    where = " AND ".join(conditions)
    rows = await db.fetch(
        f"""
        SELECT pbs_code, change_type, effective_date, description, section
        FROM summary_of_changes WHERE {where}
        ORDER BY change_type, pbs_code
        """,
        *params,
    )

    # Group by change_type for summary
    by_type: dict[str, int] = {}
    for r in rows:
        ct = r["change_type"] or "UNKNOWN"
        by_type[ct] = by_type.get(ct, 0) + 1

    return {
        "data": {
            "schedule_code": schedule_month,
            "total_changes": len(rows),
            "summary_by_type": by_type,
            "changes": [
                {
                    "pbs_code": r["pbs_code"],
                    "change_type": r["change_type"],
                    "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                    "description": r["description"],
                    "section": r["section"],
                }
                for r in rows
            ],
        },
        "meta": {
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
            "join_sources": ["/summary-of-changes"],
        },
    }
