"""ATC codes router — GET /v1/atc-codes"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import require_tier, tier_label
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["atc-codes"])


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


@router.get("/atc-codes")
async def list_atc_codes(
    response: Response,
    schedule: Optional[str] = Query(None),
    level: Optional[int] = Query(None),
    parent_code: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    conditions = ["schedule_id = $1"]
    params = [schedule_id]

    if level is not None:
        params.append(level)
        conditions.append(f"atc_level = ${len(params)}")
    if parent_code is not None:
        params.append(parent_code.upper())
        conditions.append(f"atc_parent_code = ${len(params)}")

    where = " AND ".join(conditions)
    rows = await db.fetch(
        f"""
        SELECT atc_code, atc_description, atc_level, atc_parent_code
        FROM atc_codes WHERE {where}
        ORDER BY atc_code
        """,
        *params,
    )
    return {"data": [dict(r) for r in rows], "meta": {"total": len(rows)}}


@router.get("/atc-codes/by-level/{level}")
async def get_atc_codes_by_level(
    level: int,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("starter")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    if level < 1 or level > 5:
        raise HTTPException(status_code=422, detail={"code": "INVALID_LEVEL", "message": "ATC level must be between 1 and 5."})
    schedule_id = await _resolve_schedule_id(db, schedule)
    rows = await db.fetch(
        "SELECT atc_code, atc_description, atc_level, atc_parent_code FROM atc_codes WHERE schedule_id = $1 AND atc_level = $2 ORDER BY atc_code",
        schedule_id, level,
    )
    return {
        "data": [dict(r) for r in rows],
        "meta": {"total": len(rows), "level": level, "tier": tier_label(api_key_data)},
    }


@router.get("/atc-codes/{atc_code}/hierarchy")
async def get_atc_hierarchy(
    atc_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("starter")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    # Recursive CTE walks up the tree from the target node to the root.
    ancestors = await db.fetch(
        """
        WITH RECURSIVE ancestor_chain AS (
            SELECT atc_code, atc_description, atc_level, atc_parent_code
            FROM atc_codes
            WHERE atc_code = $1 AND schedule_id = $2
            UNION ALL
            SELECT p.atc_code, p.atc_description, p.atc_level, p.atc_parent_code
            FROM atc_codes p
            INNER JOIN ancestor_chain c ON p.atc_code = c.atc_parent_code
            WHERE p.schedule_id = $2
        )
        SELECT * FROM ancestor_chain ORDER BY atc_level
        """,
        atc_code.upper(), schedule_id,
    )
    if not ancestors:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "ATC code not found."})

    target = next((dict(r) for r in ancestors if r["atc_code"] == atc_code.upper()), None)
    ancestor_list = [dict(r) for r in ancestors if r["atc_code"] != atc_code.upper()]
    breadcrumb = " → ".join(r["atc_code"] for r in ancestors)

    children = await db.fetch(
        "SELECT atc_code, atc_description, atc_level FROM atc_codes WHERE atc_parent_code = $1 AND schedule_id = $2 ORDER BY atc_code",
        atc_code.upper(), schedule_id,
    )
    children_list = [dict(r) for r in children]

    return {
        "data": {
            **target,
            "ancestors": ancestor_list,
            "breadcrumb": breadcrumb,
            "children": children_list,
            "has_children": len(children_list) > 0,
            "is_leaf": len(children_list) == 0,
        },
        "meta": {"tier": tier_label(api_key_data), "join_sources": ["/atc-codes"]},
    }


@router.get("/atc-codes/{atc_code}/children")
async def get_atc_children(
    atc_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("starter")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    parent = await db.fetchrow(
        "SELECT atc_code, atc_description, atc_level FROM atc_codes WHERE atc_code = $1 AND schedule_id = $2",
        atc_code.upper(), schedule_id,
    )
    if not parent:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "ATC code not found."})

    children = await db.fetch(
        "SELECT atc_code, atc_description, atc_level, atc_parent_code FROM atc_codes WHERE atc_parent_code = $1 AND schedule_id = $2 ORDER BY atc_code",
        atc_code.upper(), schedule_id,
    )
    return {
        "data": {
            "parent": dict(parent),
            "children": [dict(r) for r in children],
            "child_count": len(children),
        },
        "meta": {"tier": tier_label(api_key_data)},
    }


@router.get("/atc-codes/{atc_code}")
async def get_atc_code(
    atc_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    row = await db.fetchrow(
        """
        SELECT atc_code, atc_description, atc_level, atc_parent_code
        FROM atc_codes WHERE atc_code = $1 AND schedule_id = $2
        """,
        atc_code.upper(), schedule_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "ATC code not found."})

    result = dict(row)
    # Items linked to this ATC code via item_atc_relationships
    linked_items = await db.fetch(
        """
        SELECT pbs_code, atc_priority_pct
        FROM item_atc_relationships WHERE atc_code = $1 AND schedule_id = $2
        ORDER BY pbs_code
        """,
        atc_code.upper(), schedule_id,
    )
    result["linked_items"] = [dict(r) for r in linked_items]
    # Children in the ATC tree
    children = await db.fetch(
        """
        SELECT atc_code, atc_description, atc_level
        FROM atc_codes WHERE atc_parent_code = $1 AND schedule_id = $2
        ORDER BY atc_code
        """,
        atc_code.upper(), schedule_id,
    )
    result["children"] = [dict(r) for r in children]
    return result
