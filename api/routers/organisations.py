"""Organisations router — GET /v1/organisations"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import require_tier, tier_label
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["organisations"])


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


@router.get("/organisations")
async def list_organisations(
    response: Response,
    schedule: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    if state:
        rows = await db.fetch(
            """
            SELECT organisation_id, name, abn, street_address, city, state, postcode
            FROM organisations WHERE schedule_id = $1 AND state = $2
            ORDER BY name
            """,
            schedule_id, state.upper(),
        )
    else:
        rows = await db.fetch(
            """
            SELECT organisation_id, name, abn, street_address, city, state, postcode
            FROM organisations WHERE schedule_id = $1
            ORDER BY name
            """,
            schedule_id,
        )

    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get("/organisations/search")
async def search_organisations(
    response: Response,
    q: Optional[str] = Query(None, description="Name search (case-insensitive, partial match)"),
    state: Optional[str] = Query(None),
    schedule: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    api_key_data: dict = Depends(require_tier("starter")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    conditions = ["o.schedule_id = $1"]
    params: list = [schedule_id]

    if q:
        params.append(f"%{q}%")
        conditions.append(f"o.name ILIKE ${len(params)}")
    if state:
        params.append(state.upper())
        conditions.append(f"o.state = ${len(params)}")

    where = " AND ".join(conditions)
    rows = await db.fetch(
        f"""
        SELECT o.organisation_id, o.name, o.abn, o.state,
               COUNT(ior.pbs_code) AS item_count
        FROM organisations o
        LEFT JOIN item_organisation_relationships ior
            ON ior.organisation_id = o.organisation_id AND ior.schedule_id = o.schedule_id
        WHERE {where}
        GROUP BY o.organisation_id, o.name, o.abn, o.state
        ORDER BY o.name
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
        """,
        *params, limit, offset,
    )
    total = await db.fetchval(
        f"SELECT COUNT(*) FROM organisations o WHERE {where}",
        *params,
    )
    data = [dict(r) for r in rows]
    return {
        "data": data,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(data) < total,
            "tier": tier_label(api_key_data),
            "join_sources": ["/organisations", "/item-organisation-relationships"],
        },
    }


@router.get("/organisations/{organisation_id}")
async def get_organisation(
    organisation_id: int,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT organisation_id, name, abn, street_address, city, state, postcode
        FROM organisations WHERE organisation_id = $1 AND schedule_id = $2
        """,
        organisation_id, schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Organisation not found."})

    result = dict(row)
    linked_items = await db.fetch(
        "SELECT pbs_code FROM item_organisation_relationships WHERE organisation_id = $1 AND schedule_id = $2",
        organisation_id, schedule_id,
    )
    result["linked_items"] = [r["pbs_code"] for r in linked_items]
    return result
