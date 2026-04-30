"""Extemporaneous router — T3 Intelligence endpoints for /v1/extemporaneous/..."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.tier import require_tier, tier_label
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["extemporaneous"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


async def _resolve_schedule(db, schedule: Optional[str]) -> tuple[str, str]:
    if schedule:
        row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", schedule)
    else:
        row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
    return str(row["id"]), row["month"]


@router.get("/extemporaneous/ingredients")
async def list_extemporaneous_ingredients(
    response: Response,
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    total = await db.fetchval("SELECT COUNT(*) FROM extemporaneous_ingredients WHERE schedule_id = $1", schedule_id)
    rows = await db.fetch(
        """
        SELECT pbs_code, agreed_purchasing_unit,
               rounded_tenth_gram_per_ml_price, rounded_one_gram_per_ml_price,
               rounded_ten_gram_per_ml_price, rounded_hundred_gram_per_ml_price,
               exact_tenth_gram_per_ml_price, exact_one_gram_per_ml_price,
               exact_ten_gram_per_ml_price, exact_hundred_gram_per_ml_price
        FROM extemporaneous_ingredients
        WHERE schedule_id = $1
        ORDER BY pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )

    def _f(v):
        return float(v) if v is not None else None

    data = [
        {
            "pbs_code": r["pbs_code"],
            "agreed_purchasing_unit": _f(r["agreed_purchasing_unit"]),
            "prices": {
                "rounded": {
                    "per_0_1g_ml": _f(r["rounded_tenth_gram_per_ml_price"]),
                    "per_1g_ml": _f(r["rounded_one_gram_per_ml_price"]),
                    "per_10g_ml": _f(r["rounded_ten_gram_per_ml_price"]),
                    "per_100g_ml": _f(r["rounded_hundred_gram_per_ml_price"]),
                },
                "exact": {
                    "per_0_1g_ml": _f(r["exact_tenth_gram_per_ml_price"]),
                    "per_1g_ml": _f(r["exact_one_gram_per_ml_price"]),
                    "per_10g_ml": _f(r["exact_ten_gram_per_ml_price"]),
                    "per_100g_ml": _f(r["exact_hundred_gram_per_ml_price"]),
                },
            },
        }
        for r in rows
    ]

    return {
        "data": data,
        "meta": {
            "total": total or 0,
            "page": page,
            "limit": limit,
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
        },
    }


@router.get("/extemporaneous/tariffs")
async def list_extemporaneous_tariffs(
    response: Response,
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    total = await db.fetchval("SELECT COUNT(*) FROM extemporaneous_tariffs WHERE schedule_id = $1", schedule_id)
    rows = await db.fetch(
        """
        SELECT pbs_code, drug_name, agreed_purchasing_unit, markup,
               rounded_rec_one_tenth_gram, rounded_rec_one_gram,
               rounded_rec_ten_gram, rounded_rec_hundred_gram,
               exact_rec_one_tenth_gram, exact_rec_one_gram,
               exact_rec_ten_gram, exact_rec_hundred_gram
        FROM extemporaneous_tariffs
        WHERE schedule_id = $1
        ORDER BY pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )

    def _f(v):
        return float(v) if v is not None else None

    data = [
        {
            "pbs_code": r["pbs_code"],
            "drug_name": r["drug_name"],
            "agreed_purchasing_unit": _f(r["agreed_purchasing_unit"]),
            "markup": _f(r["markup"]),
            "recommended_prices": {
                "rounded": {
                    "per_0_1g": _f(r["rounded_rec_one_tenth_gram"]),
                    "per_1g": _f(r["rounded_rec_one_gram"]),
                    "per_10g": _f(r["rounded_rec_ten_gram"]),
                    "per_100g": _f(r["rounded_rec_hundred_gram"]),
                },
                "exact": {
                    "per_0_1g": _f(r["exact_rec_one_tenth_gram"]),
                    "per_1g": _f(r["exact_rec_one_gram"]),
                    "per_10g": _f(r["exact_rec_ten_gram"]),
                    "per_100g": _f(r["exact_rec_hundred_gram"]),
                },
            },
        }
        for r in rows
    ]

    return {
        "data": data,
        "meta": {
            "total": total or 0,
            "page": page,
            "limit": limit,
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
        },
    }


@router.get("/extemporaneous/preparations")
async def list_extemporaneous_preparations(
    response: Response,
    schedule: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    total = await db.fetchval("SELECT COUNT(*) FROM extemporaneous_preparations WHERE schedule_id = $1", schedule_id)
    rows = await db.fetch(
        """
        SELECT ep.pbs_code, ep.preparation, ep.maximum_quantity, ep.maximum_quantity_unit,
               ARRAY_AGG(epr.sfp_pbs_code) FILTER (WHERE epr.sfp_pbs_code IS NOT NULL) AS linked_sfp_codes
        FROM extemporaneous_preparations ep
        LEFT JOIN extemporaneous_prep_sfp_relationships epr
            ON epr.ex_prep_pbs_code = ep.pbs_code AND epr.schedule_id = ep.schedule_id
        WHERE ep.schedule_id = $1
        GROUP BY ep.pbs_code, ep.preparation, ep.maximum_quantity, ep.maximum_quantity_unit
        ORDER BY ep.pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )

    return {
        "data": [
            {
                "pbs_code": r["pbs_code"],
                "preparation": r["preparation"],
                "maximum_quantity": r["maximum_quantity"],
                "maximum_quantity_unit": r["maximum_quantity_unit"],
                "linked_sfp_codes": r["linked_sfp_codes"] or [],
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


@router.get("/extemporaneous/{pbs_code}")
async def get_extemporaneous(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    ingredient = await db.fetchrow(
        """
        SELECT pbs_code, agreed_purchasing_unit,
               rounded_tenth_gram_per_ml_price, rounded_one_gram_per_ml_price,
               rounded_ten_gram_per_ml_price, rounded_hundred_gram_per_ml_price,
               exact_tenth_gram_per_ml_price, exact_one_gram_per_ml_price,
               exact_ten_gram_per_ml_price, exact_hundred_gram_per_ml_price
        FROM extemporaneous_ingredients WHERE pbs_code = $1 AND schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    tariff = await db.fetchrow(
        """
        SELECT drug_name, agreed_purchasing_unit, markup,
               rounded_rec_one_tenth_gram, rounded_rec_one_gram,
               rounded_rec_ten_gram, rounded_rec_hundred_gram
        FROM extemporaneous_tariffs WHERE pbs_code = $1 AND schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    preparation = await db.fetchrow(
        "SELECT preparation, maximum_quantity, maximum_quantity_unit FROM extemporaneous_preparations WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )

    if not ingredient and not tariff and not preparation:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No extemporaneous data for this PBS code."})

    def _f(v):
        return float(v) if v is not None else None

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "ingredient": {
                "agreed_purchasing_unit": _f(ingredient["agreed_purchasing_unit"]) if ingredient else None,
                "prices": {
                    "rounded": {
                        "per_0_1g_ml": _f(ingredient["rounded_tenth_gram_per_ml_price"]) if ingredient else None,
                        "per_1g_ml": _f(ingredient["rounded_one_gram_per_ml_price"]) if ingredient else None,
                        "per_10g_ml": _f(ingredient["rounded_ten_gram_per_ml_price"]) if ingredient else None,
                        "per_100g_ml": _f(ingredient["rounded_hundred_gram_per_ml_price"]) if ingredient else None,
                    },
                },
            } if ingredient else None,
            "tariff": {
                "drug_name": tariff["drug_name"] if tariff else None,
                "agreed_purchasing_unit": _f(tariff["agreed_purchasing_unit"]) if tariff else None,
                "markup": _f(tariff["markup"]) if tariff else None,
                "recommended_prices": {
                    "per_0_1g": _f(tariff["rounded_rec_one_tenth_gram"]) if tariff else None,
                    "per_1g": _f(tariff["rounded_rec_one_gram"]) if tariff else None,
                    "per_10g": _f(tariff["rounded_rec_ten_gram"]) if tariff else None,
                    "per_100g": _f(tariff["rounded_rec_hundred_gram"]) if tariff else None,
                },
            } if tariff else None,
            "preparation": dict(preparation) if preparation else None,
        },
        "meta": {
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
            "join_sources": ["/extemporaneous-ingredients", "/extemporaneous-tariffs", "/extemporaneous-preparations"],
        },
    }
