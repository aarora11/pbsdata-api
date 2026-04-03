"""Medicines router — full implementation."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from api.services.search import search_medicines
from typing import Optional
import uuid

router = APIRouter(tags=["medicines"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


@router.get("/medicines")
async def list_medicines(
    response: Response,
    q: Optional[str] = Query(None, description="Fuzzy search on ingredient or brand name"),
    sixty_day: Optional[bool] = Query(None),
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    items, total = await search_medicines(db, q, sixty_day, page, limit, schedule)
    # Serialize UUIDs
    for item in items:
        if "id" in item:
            item["id"] = str(item["id"])
    return {
        "data": items,
        "meta": {"total": total, "page": page, "limit": limit},
    }


@router.get("/medicines/{medicine_id}")
async def get_medicine(
    medicine_id: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    _rl(response, api_key_data)

    # Validate UUID format
    try:
        uuid.UUID(medicine_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Medicine not found."})

    med = await db.fetchrow(
        "SELECT id, ingredient, ingredient_lower, atc_code, therapeutic_group, therapeutic_subgroup, is_active FROM medicines WHERE id = $1",
        uuid.UUID(medicine_id),
    )
    if not med or not med["is_active"]:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Medicine not found."})

    # Get latest schedule or requested schedule
    if schedule:
        sched_row = await db.fetchrow("SELECT id FROM schedules WHERE month = $1", schedule)
        schedule_id = sched_row["id"] if sched_row else None
    else:
        sched_row = await db.fetchrow(
            "SELECT id FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
        schedule_id = sched_row["id"] if sched_row else None

    items = []
    if schedule_id:
        rows = await db.fetch(
            """
            SELECT i.id, i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.pack_unit,
                   i.benefit_type, i.general_charge, i.concessional_charge, i.government_price,
                   i.brand_premium, i.brand_premium_counts_to_safety_net, i.sixty_day_eligible,
                   i.max_quantity, i.max_repeats, i.dangerous_drug
            FROM items i
            WHERE i.medicine_id = $1 AND i.schedule_id = $2 AND i.is_active = TRUE
            ORDER BY i.brand_name
            """,
            med["id"], schedule_id,
        )
        for r in rows:
            d = dict(r)
            d["id"] = str(d["id"])
            for field in ["general_charge", "concessional_charge", "government_price", "brand_premium"]:
                if d.get(field) is not None:
                    d[field] = float(d[field])
            items.append(d)

    return {
        "id": str(med["id"]),
        "ingredient": med["ingredient"],
        "ingredient_lower": med["ingredient_lower"],
        "atc_code": med["atc_code"],
        "therapeutic_group": med["therapeutic_group"],
        "therapeutic_subgroup": med["therapeutic_subgroup"],
        "items": items,
    }
