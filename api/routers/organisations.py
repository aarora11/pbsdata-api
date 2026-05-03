"""Organisations router — GET /v1/organisations"""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.middleware.tier import require_tier, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["organisations"])



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
    "/organisations",
    summary="List PBS Organisations",
    description=(
        "Returns all sponsor/manufacturer organisations registered on the PBS for a given schedule. "
        "Each organisation record includes name, ABN, address, and state. "
        "Filter by `state` to narrow to organisations in a specific Australian state or territory.\n\n"
        "Available on all tiers."
    ),
)
async def list_organisations(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    state: Optional[str] = Query(None, description="Filter by Australian state/territory code (e.g. 'VIC', 'NSW', 'QLD')"),
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


@router.get(
    "/organisations/search",
    summary="Search PBS Organisations",
    description=(
        "Searches PBS sponsor/manufacturer organisations by name. Results include a count of PBS items "
        "linked to each organisation. Use `state` to filter by Australian state.\n\n"
        "Requires **Starter (T1)** tier."
    ),
)
async def search_organisations(
    response: Response,
    q: Optional[str] = Query(None, description="Organisation name search (case-insensitive partial match)"),
    state: Optional[str] = Query(None, description="Filter by Australian state/territory code (e.g. 'VIC', 'NSW')"),
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
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


@router.get(
    "/organisations/{organisation_id}/portfolio",
    summary="Get Organisation PBS Portfolio",
    description=(
        "Returns all PBS items listed by a given manufacturer/sponsor organisation, with ingredient, "
        "brand name, form, benefit type, formulary, ATC code, and pricing. "
        "Filter by `benefit_type` to view only unrestricted, restricted, or authority items.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_organisation_portfolio(
    organisation_id: int,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    benefit_type: Optional[str] = Query(None, description="Filter by benefit type: U=Unrestricted, R=Restricted, A=Authority Required, S=Streamlined Authority"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id = await _resolve_schedule_id(db, schedule)

    org = await db.fetchrow(
        "SELECT organisation_id, name, state FROM organisations WHERE organisation_id = $1 AND schedule_id = $2",
        organisation_id, schedule_id,
    )
    if not org:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Organisation not found."})

    conditions = ["ior.organisation_id = $1", "ior.schedule_id = $2"]
    params: list = [organisation_id, schedule_id]

    if benefit_type:
        params.append(benefit_type.upper())
        conditions.append(f"i.benefit_type = ${len(params)}")

    where = " AND ".join(conditions)
    count_sql = f"""
        SELECT COUNT(*)
        FROM item_organisation_relationships ior
        JOIN items i ON i.pbs_code = ior.pbs_code AND i.schedule_id = ior.schedule_id
        WHERE {where}
    """
    data_sql = f"""
        SELECT i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size,
               i.benefit_type, i.formulary, i.program_code,
               i.general_charge, i.government_price,
               m.ingredient, m.atc_code
        FROM item_organisation_relationships ior
        JOIN items i ON i.pbs_code = ior.pbs_code AND i.schedule_id = ior.schedule_id
        JOIN medicines m ON m.id = i.medicine_id
        WHERE {where}
        ORDER BY m.ingredient, i.pbs_code
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """
    offset = (page - 1) * limit
    total = await db.fetchval(count_sql, *params)
    rows = await db.fetch(data_sql, *params, limit, offset)

    def _f(v):
        return float(v) if v is not None else None

    benefit_labels = {"U": "Unrestricted", "R": "Restricted", "A": "Authority Required", "S": "Streamlined Authority"}
    items = []
    for r in rows:
        bc = r["benefit_type"] or "U"
        items.append({
            "pbs_code": r["pbs_code"],
            "ingredient": r["ingredient"],
            "brand_name": r["brand_name"],
            "form": r["form"],
            "strength": r["strength"],
            "pack_size": r["pack_size"],
            "benefit_type_code": bc,
            "benefit_type_label": benefit_labels.get(bc, bc),
            "formulary": r["formulary"],
            "program_code": r["program_code"],
            "atc_code": r["atc_code"],
            "general_charge": _f(r["general_charge"]),
            "government_price": _f(r["government_price"]),
        })

    return {
        "data": {
            "organisation_id": org["organisation_id"],
            "organisation_name": org["name"],
            "state": org["state"],
            "item_count": total or 0,
            "items": items,
        },
        "meta": {
            "total": total or 0,
            "page": page,
            "limit": limit,
            "tier": tier_label(api_key_data),
            "join_sources": ["/organisations", "/item-organisation-relationships", "/items"],
        },
    }


@router.get(
    "/organisations/{organisation_id}",
    summary="Get Organisation Detail",
    description=(
        "Returns a single PBS organisation record by numeric organisation ID, including address, ABN, "
        "and a list of PBS codes for items linked to this organisation in the given schedule.\n\n"
        "Available on all tiers."
    ),
)
async def get_organisation(
    organisation_id: int,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
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
