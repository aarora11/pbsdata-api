"""Organisations router — GET /v1/organisations"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
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
