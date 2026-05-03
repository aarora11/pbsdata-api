"""Market router — T4 enterprise-tier aggregate endpoints."""
from typing import Optional
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.tier import require_tier, tier_label
from api.middleware.analytics import analytics_concurrency_check
from api.database import get_db
from api.cache import cache_get, cache_set
from api.routers.shared import _rl

router = APIRouter(tags=["market"])


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


def _meta(key_data: dict, schedule_month: str) -> dict:
    return {"schedule_code": schedule_month, "tier": tier_label(key_data)}


# ── 4.1  ATC Summary ───────────────────────────────────────────────────────────

@router.get("/market/atc-summary")
async def market_atc_summary(
    response: Response,
    atc_code: str = Query(..., description="ATC code to summarise (any level)"),
    schedule: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    primary_atc_only: bool = Query(False, description="Only count items where this is their primary ATC code"),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/atc-summary:{schedule_id}:{atc_code.upper()}:{primary_atc_only}"
    if cached := cache_get(cache_key):
        return cached

    atc_upper = atc_code.upper()

    subtree_join = "JOIN item_atc_relationships iar ON iar.atc_code = st.atc_code AND iar.schedule_id = $2"
    if primary_atc_only:
        subtree_join += " AND iar.is_primary = TRUE"

    inactive_filter = "" if include_inactive else "AND i.benefit_type IS NOT NULL"

    aggregate_sql = f"""
        WITH RECURSIVE subtree AS (
            SELECT atc_code FROM atc_codes WHERE atc_code = $1 AND schedule_id = $2
            UNION ALL
            SELECT c.atc_code FROM atc_codes c
            JOIN subtree s ON c.atc_parent_code = s.atc_code
            WHERE c.schedule_id = $2
        )
        SELECT
            COUNT(DISTINCT i.pbs_code)                                       AS unique_prescribing_rules,
            COUNT(DISTINCT ip.li_item_id)                                    AS unique_brands,
            COUNT(*) FILTER (WHERE i.benefit_type = 'A')                     AS authority_required_count,
            COUNT(*) FILTER (WHERE i.benefit_type = 'S')                     AS streamlined_count,
            COUNT(*) FILTER (WHERE i.formulary = 'F1')                       AS f1_count,
            COUNT(*) FILTER (WHERE i.formulary = 'F2')                       AS f2_count,
            COUNT(*) FILTER (WHERE i.sixty_day_eligible = TRUE)              AS sixty_day_items,
            COUNT(*) FILTER (WHERE i.biosimilar = TRUE)                      AS biosimilar_items,
            MIN(ip.commonwealth_price)                                       AS min_dpmq,
            MAX(ip.commonwealth_price)                                       AS max_dpmq,
            AVG(ip.commonwealth_price)                                       AS mean_dpmq,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ip.commonwealth_price) AS median_dpmq,
            COUNT(DISTINCT ior.organisation_id)                              AS manufacturer_count
        FROM subtree st
        {subtree_join}
        JOIN items i ON i.pbs_code = iar.pbs_code AND i.schedule_id = $2
        {inactive_filter}
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $2
        LEFT JOIN item_organisation_relationships ior
               ON ior.pbs_code = i.pbs_code AND ior.schedule_id = $2
    """

    row = await db.fetchrow(aggregate_sql, atc_upper, schedule_id)
    if not row or row["unique_prescribing_rules"] == 0:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"No items found for ATC code {atc_upper}."},
        )

    # Per-child breakdown (direct children of target ATC)
    children = await db.fetch(
        "SELECT atc_code, atc_description AS atc_name FROM atc_codes WHERE atc_parent_code = $1 AND schedule_id = $2",
        atc_upper, schedule_id,
    )
    breakdown = []
    for child in children:
        child_code = child["atc_code"]
        child_row = await db.fetchrow(
            """
            SELECT COUNT(DISTINCT i.pbs_code) AS item_count,
                   AVG(ip.commonwealth_price)  AS mean_dpmq
            FROM item_atc_relationships iar
            JOIN items i  ON i.pbs_code = iar.pbs_code AND i.schedule_id = $2
            LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $2
            WHERE iar.atc_code = $1 AND iar.schedule_id = $2
            """,
            child_code, schedule_id,
        )
        breakdown.append({
            "atc_code": child_code,
            "atc_name": child["atc_name"],
            "item_count": child_row["item_count"] or 0,
            "mean_dpmq": round(float(child_row["mean_dpmq"]), 4) if child_row["mean_dpmq"] else None,
        })

    result = {
        "data": {
            "atc_code": atc_upper,
            "unique_prescribing_rules": row["unique_prescribing_rules"],
            "unique_brands": row["unique_brands"],
            "authority_required_count": row["authority_required_count"],
            "streamlined_count": row["streamlined_count"],
            "f1_count": row["f1_count"],
            "f2_count": row["f2_count"],
            "sixty_day_items": row["sixty_day_items"],
            "biosimilar_items": row["biosimilar_items"],
            "min_dpmq": round(float(row["min_dpmq"]), 4) if row["min_dpmq"] else None,
            "max_dpmq": round(float(row["max_dpmq"]), 4) if row["max_dpmq"] else None,
            "mean_dpmq": round(float(row["mean_dpmq"]), 4) if row["mean_dpmq"] else None,
            "median_dpmq": round(float(row["median_dpmq"]), 4) if row["median_dpmq"] else None,
            "manufacturer_count": row["manufacturer_count"],
            "child_breakdown": breakdown,
        },
        "meta": _meta(api_key_data, schedule_month),
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.2  Price Reduction Events ────────────────────────────────────────────────

@router.get("/market/price-reduction-events")
async def market_price_reduction_events(
    response: Response,
    from_schedule: str = Query(..., description="Start schedule month YYYY-MM"),
    to_schedule: Optional[str] = Query(None, description="End schedule month YYYY-MM (inclusive, defaults to latest)"),
    atc_prefix: Optional[str] = Query(None),
    organisation_id: Optional[str] = Query(None),
    min_pct_change: Optional[float] = Query(None, description="Minimum absolute % price reduction to include"),
    sort: str = Query("pct_change_asc", description="Sort order: pct_change_asc or pct_change_desc"),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)

    from_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", from_schedule)
    if not from_row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"Schedule {from_schedule} not found."})

    if to_schedule:
        to_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", to_schedule)
        if not to_row:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"Schedule {to_schedule} not found."})
        to_id = str(to_row["id"])
        to_month = to_row["month"]
    else:
        to_row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
        to_id = str(to_row["id"])
        to_month = to_row["month"]

    # Fetch price events across the schedule range
    conditions = [
        "pe.schedule_id IN (SELECT id FROM schedules WHERE month >= $1 AND month <= $2)",
        "(pe.event_type ILIKE '%reduction%' OR pe.new_price < pe.previous_price)",
        "pe.previous_price > 0",
    ]
    params: list = [from_schedule, to_month]

    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        conditions.append(
            f"EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"JOIN schedules s ON s.id = iar.schedule_id "
            f"WHERE iar.pbs_code = pe.pbs_code AND s.month = (SELECT month FROM schedules WHERE id = pe.schedule_id) "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    if organisation_id:
        params.append(organisation_id)
        conditions.append(
            f"EXISTS (SELECT 1 FROM item_organisation_relationships ior "
            f"WHERE ior.pbs_code = pe.pbs_code AND ior.schedule_id = pe.schedule_id "
            f"AND ior.organisation_id = ${len(params)})"
        )

    where = " AND ".join(conditions)
    order = "pct_change ASC" if sort == "pct_change_asc" else "pct_change DESC"

    sql = f"""
        SELECT
            pe.pbs_code,
            pe.event_type,
            pe.effective_date,
            pe.previous_price,
            pe.new_price,
            (pe.new_price - pe.previous_price) / pe.previous_price * 100 AS pct_change,
            s.month AS schedule_month,
            i.brand_name,
            m.ingredient
        FROM item_pricing_events pe
        JOIN schedules s ON s.id = pe.schedule_id
        LEFT JOIN items i ON i.pbs_code = pe.pbs_code AND i.schedule_id = pe.schedule_id
        LEFT JOIN medicines m ON m.id = i.medicine_id
        WHERE {where}
        ORDER BY {order}
        LIMIT 500
    """

    rows = await db.fetch(sql, *params)

    data = []
    for r in rows:
        pct = float(r["pct_change"]) if r["pct_change"] is not None else None
        if min_pct_change is not None and pct is not None and abs(pct) < min_pct_change:
            continue
        data.append({
            "pbs_code": r["pbs_code"],
            "brand_name": r["brand_name"],
            "ingredient": r["ingredient"],
            "event_type": r["event_type"],
            "effective_date": str(r["effective_date"]) if r["effective_date"] else None,
            "schedule_month": r["schedule_month"],
            "previous_price": float(r["previous_price"]) if r["previous_price"] else None,
            "new_price": float(r["new_price"]) if r["new_price"] else None,
            "pct_change": round(pct, 4) if pct is not None else None,
        })

    return {
        "data": data,
        "meta": {
            "from_schedule": from_schedule,
            "to_schedule": to_month,
            "total": len(data),
            "tier": tier_label(api_key_data),
        },
    }


# ── 4.3  Manufacturer Landscape ────────────────────────────────────────────────

@router.get("/market/manufacturer-landscape")
async def market_manufacturer_landscape(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_prefix: Optional[str] = Query(None),
    program_code: Optional[str] = Query(None),
    formulary: Optional[str] = Query(None, description="F1, F2, or R (restricted)"),
    min_item_count: int = Query(1, ge=1),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/mfr-landscape:{schedule_id}:{atc_prefix}:{program_code}:{formulary}:{min_item_count}"
    if cached := cache_get(cache_key):
        return cached

    conditions = ["ior.schedule_id = $1"]
    params: list = [schedule_id]

    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        conditions.append(
            f"EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = ior.pbs_code AND iar.schedule_id = $1 "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    if program_code:
        params.append(program_code.upper())
        conditions.append(
            f"EXISTS (SELECT 1 FROM items i2 WHERE i2.pbs_code = ior.pbs_code "
            f"AND i2.schedule_id = $1 AND i2.program_code = ${len(params)})"
        )

    if formulary:
        params.append(formulary.upper())
        conditions.append(
            f"EXISTS (SELECT 1 FROM items i3 WHERE i3.pbs_code = ior.pbs_code "
            f"AND i3.schedule_id = $1 AND i3.formulary = ${len(params)})"
        )

    where = " AND ".join(conditions)

    sql = f"""
        SELECT
            ior.organisation_id,
            o.organisation_name,
            COUNT(DISTINCT ior.pbs_code)                                AS pbs_code_count,
            COUNT(DISTINCT ip.li_item_id)                               AS item_count,
            COUNT(*) FILTER (WHERE i.formulary = 'F1')                 AS f1_count,
            COUNT(*) FILTER (WHERE i.formulary = 'F2')                 AS f2_count,
            COUNT(*) FILTER (WHERE i.biosimilar = TRUE)                AS biosimilar_count,
            COUNT(*) FILTER (WHERE i.benefit_type = 'A')               AS authority_count,
            MIN(ip.commonwealth_price)                                  AS min_dpmq,
            MAX(ip.commonwealth_price)                                  AS max_dpmq,
            AVG(ip.commonwealth_price)                                  AS mean_dpmq
        FROM item_organisation_relationships ior
        JOIN items i ON i.pbs_code = ior.pbs_code AND i.schedule_id = $1
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $1
        LEFT JOIN organisations o ON o.organisation_id = ior.organisation_id AND o.schedule_id = $1
        WHERE {where}
        GROUP BY ior.organisation_id, o.organisation_name
        HAVING COUNT(DISTINCT ip.li_item_id) >= ${len(params) + 1}
        ORDER BY COUNT(DISTINCT ip.li_item_id) DESC
        LIMIT 200
    """
    params.append(min_item_count)

    rows = await db.fetch(sql, *params)
    data = []
    for r in rows:
        data.append({
            "organisation_id": r["organisation_id"],
            "organisation_name": r["organisation_name"],
            "pbs_code_count": r["pbs_code_count"],
            "item_count": r["item_count"],
            "f1_count": r["f1_count"],
            "f2_count": r["f2_count"],
            "biosimilar_count": r["biosimilar_count"],
            "authority_count": r["authority_count"],
            "min_dpmq": round(float(r["min_dpmq"]), 4) if r["min_dpmq"] else None,
            "max_dpmq": round(float(r["max_dpmq"]), 4) if r["max_dpmq"] else None,
            "mean_dpmq": round(float(r["mean_dpmq"]), 4) if r["mean_dpmq"] else None,
        })

    result = {
        "data": data,
        "meta": {**_meta(api_key_data, schedule_month), "total": len(data)},
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.4  Schedule Comparison ───────────────────────────────────────────────────

@router.get("/market/schedule-comparison")
async def market_schedule_comparison(
    response: Response,
    base_schedule: str = Query(..., description="Base schedule month YYYY-MM"),
    target_schedule: Optional[str] = Query(None, description="Target schedule month (defaults to latest)"),
    atc_prefix: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)

    base_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", base_schedule)
    if not base_row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"Schedule {base_schedule} not found."})
    base_id = str(base_row["id"])

    if target_schedule:
        target_row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", target_schedule)
        if not target_row:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"Schedule {target_schedule} not found."})
    else:
        target_row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    target_id = str(target_row["id"])
    target_month = target_row["month"]

    cache_key = f"/market/schedule-comparison:{base_id}:{target_id}:{atc_prefix}"
    if cached := cache_get(cache_key):
        return cached

    atc_filter_base = ""
    atc_filter_target = ""
    atc_params: list = []
    if atc_prefix:
        atc_params.append(f"{atc_prefix.upper()}%")
        atc_filter_base = (
            f"AND EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = i.schedule_id "
            f"AND iar.atc_code LIKE ${len(atc_params) + 2})"
        )
        atc_filter_target = atc_filter_base

    # Items added (in target but not in base)
    added_rows = await db.fetch(
        f"""
        SELECT i.pbs_code, i.brand_name, i.benefit_type, i.formulary
        FROM items i
        WHERE i.schedule_id = $1 {atc_filter_target}
          AND NOT EXISTS (
              SELECT 1 FROM items i2
              WHERE i2.pbs_code = i.pbs_code AND i2.schedule_id = $2
          )
        ORDER BY i.pbs_code
        LIMIT 500
        """,
        target_id, base_id, *atc_params,
    )

    # Items removed (in base but not in target)
    removed_rows = await db.fetch(
        f"""
        SELECT i.pbs_code, i.brand_name, i.benefit_type, i.formulary
        FROM items i
        WHERE i.schedule_id = $1 {atc_filter_base}
          AND NOT EXISTS (
              SELECT 1 FROM items i2
              WHERE i2.pbs_code = i.pbs_code AND i2.schedule_id = $2
          )
        ORDER BY i.pbs_code
        LIMIT 500
        """,
        base_id, target_id, *atc_params,
    )

    # Price changes
    price_change_rows = await db.fetch(
        f"""
        SELECT b.pbs_code, b.brand_name,
               b.government_price AS base_price,
               t.government_price AS target_price,
               (t.government_price - b.government_price) / NULLIF(b.government_price, 0) * 100 AS pct_change
        FROM items b
        JOIN items t ON t.pbs_code = b.pbs_code
        WHERE b.schedule_id = $1 AND t.schedule_id = $2
          AND b.government_price IS NOT NULL AND t.government_price IS NOT NULL
          AND b.government_price != t.government_price
          {atc_filter_base}
        ORDER BY ABS((t.government_price - b.government_price) / NULLIF(b.government_price, 0)) DESC
        LIMIT 500
        """,
        base_id, target_id, *atc_params,
    )

    # Copayment diff
    base_cp = await db.fetchrow(
        "SELECT general_patient_charge, concessional_patient_charge, safety_net_general, safety_net_concessional "
        "FROM copayments WHERE schedule_id = $1 LIMIT 1",
        base_id,
    )
    target_cp = await db.fetchrow(
        "SELECT general_patient_charge, concessional_patient_charge, safety_net_general, safety_net_concessional "
        "FROM copayments WHERE schedule_id = $1 LIMIT 1",
        target_id,
    )

    result = {
        "data": {
            "listings_added": [
                {"pbs_code": r["pbs_code"], "brand_name": r["brand_name"],
                 "benefit_type": r["benefit_type"], "formulary": r["formulary"]}
                for r in added_rows
            ],
            "listings_removed": [
                {"pbs_code": r["pbs_code"], "brand_name": r["brand_name"],
                 "benefit_type": r["benefit_type"], "formulary": r["formulary"]}
                for r in removed_rows
            ],
            "price_changes": [
                {
                    "pbs_code": r["pbs_code"],
                    "brand_name": r["brand_name"],
                    "base_price": float(r["base_price"]) if r["base_price"] else None,
                    "target_price": float(r["target_price"]) if r["target_price"] else None,
                    "pct_change": round(float(r["pct_change"]), 4) if r["pct_change"] else None,
                }
                for r in price_change_rows
            ],
            "copayment_diff": {
                "base": {k: float(v) for k, v in dict(base_cp).items() if v is not None} if base_cp else None,
                "target": {k: float(v) for k, v in dict(target_cp).items() if v is not None} if target_cp else None,
            },
            "summary": {
                "added_count": len(added_rows),
                "removed_count": len(removed_rows),
                "price_change_count": len(price_change_rows),
            },
        },
        "meta": {
            "base_schedule": base_schedule,
            "target_schedule": target_month,
            "atc_prefix": atc_prefix,
            "tier": tier_label(api_key_data),
        },
    }
    cache_set(cache_key, result, target_id)
    return result


# ── 4.5  Formulary Landscape ───────────────────────────────────────────────────

@router.get("/market/formulary-landscape")
async def market_formulary_landscape(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_prefix: Optional[str] = Query(None),
    program_code: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/formulary-landscape:{schedule_id}:{atc_prefix}:{program_code}"
    if cached := cache_get(cache_key):
        return cached

    conditions = ["i.schedule_id = $1"]
    params: list = [schedule_id]

    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        conditions.append(
            f"EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = $1 "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    if program_code:
        params.append(program_code.upper())
        conditions.append(f"i.program_code = ${len(params)}")

    where = " AND ".join(conditions)

    sql = f"""
        SELECT
            COALESCE(i.formulary, 'None') AS formulary,
            COUNT(DISTINCT i.pbs_code)     AS item_count,
            AVG(ip.commonwealth_price)     AS mean_dpmq,
            MIN(ip.commonwealth_price)     AS min_dpmq,
            MAX(ip.commonwealth_price)     AS max_dpmq
        FROM items i
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $1
        WHERE {where}
        GROUP BY COALESCE(i.formulary, 'None')
        ORDER BY item_count DESC
    """

    rows = await db.fetch(sql, *params)
    total_items = sum(r["item_count"] for r in rows)

    data = []
    for r in rows:
        data.append({
            "formulary": r["formulary"],
            "item_count": r["item_count"],
            "percentage": round(r["item_count"] / total_items * 100, 2) if total_items else 0,
            "mean_dpmq": round(float(r["mean_dpmq"]), 4) if r["mean_dpmq"] else None,
            "min_dpmq": round(float(r["min_dpmq"]), 4) if r["min_dpmq"] else None,
            "max_dpmq": round(float(r["max_dpmq"]), 4) if r["max_dpmq"] else None,
        })

    # Copayment context (general / concessional)
    copayment = await db.fetchrow(
        "SELECT general_patient_charge, concessional_patient_charge FROM copayments WHERE schedule_id = $1 LIMIT 1",
        schedule_id,
    )

    result = {
        "data": {
            "distribution": data,
            "total_items": total_items,
            "copayment_context": {
                "general_patient_charge": float(copayment["general_patient_charge"]) if copayment and copayment["general_patient_charge"] else None,
                "concessional_patient_charge": float(copayment["concessional_patient_charge"]) if copayment and copayment["concessional_patient_charge"] else None,
            } if copayment else None,
        },
        "meta": _meta(api_key_data, schedule_month),
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.6  Biosimilar Landscape ──────────────────────────────────────────────────

@router.get("/market/biosimilar-landscape")
async def market_biosimilar_landscape(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_prefix: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/biosimilar-landscape:{schedule_id}:{atc_prefix}"
    if cached := cache_get(cache_key):
        return cached

    atc_cond = ""
    params: list = [schedule_id]
    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        atc_cond = (
            f"AND EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = $1 "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    sql = f"""
        SELECT
            m.ingredient,
            m.atc_code       AS medicine_atc_code,
            -- Biosimilar
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE i.biosimilar = TRUE)           AS biosimilar_count,
            AVG(ip.commonwealth_price) FILTER (WHERE i.biosimilar = TRUE)            AS biosimilar_mean_dpmq,
            MIN(ip.commonwealth_price) FILTER (WHERE i.biosimilar = TRUE)            AS biosimilar_min_dpmq,
            -- Originator
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE i.biosimilar = FALSE)          AS originator_count,
            AVG(ip.commonwealth_price) FILTER (WHERE i.biosimilar = FALSE)           AS originator_mean_dpmq,
            MIN(ip.commonwealth_price) FILTER (WHERE i.biosimilar = FALSE)           AS originator_min_dpmq
        FROM items i
        JOIN medicines m ON m.id = i.medicine_id
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $1
        WHERE i.schedule_id = $1 {atc_cond}
          AND (i.biosimilar = TRUE OR EXISTS (
              SELECT 1 FROM items i2
              JOIN medicines m2 ON m2.id = i2.medicine_id
              WHERE m2.ingredient = m.ingredient AND i2.schedule_id = $1 AND i2.biosimilar = TRUE
          ))
        GROUP BY m.ingredient, m.atc_code
        HAVING COUNT(DISTINCT i.pbs_code) FILTER (WHERE i.biosimilar = TRUE) > 0
        ORDER BY biosimilar_count DESC
    """

    rows = await db.fetch(sql, *params)
    data = []
    for r in rows:
        biosimilar_min = float(r["biosimilar_min_dpmq"]) if r["biosimilar_min_dpmq"] else None
        originator_min = float(r["originator_min_dpmq"]) if r["originator_min_dpmq"] else None
        price_delta_pct = None
        if biosimilar_min and originator_min and originator_min > 0:
            price_delta_pct = round((biosimilar_min - originator_min) / originator_min * 100, 2)
        data.append({
            "ingredient": r["ingredient"],
            "medicine_atc_code": r["medicine_atc_code"],
            "biosimilar_count": r["biosimilar_count"],
            "biosimilar_mean_dpmq": round(float(r["biosimilar_mean_dpmq"]), 4) if r["biosimilar_mean_dpmq"] else None,
            "biosimilar_min_dpmq": round(biosimilar_min, 4) if biosimilar_min else None,
            "originator_count": r["originator_count"],
            "originator_mean_dpmq": round(float(r["originator_mean_dpmq"]), 4) if r["originator_mean_dpmq"] else None,
            "originator_min_dpmq": round(originator_min, 4) if originator_min else None,
            "price_delta_pct_vs_originator": price_delta_pct,
        })

    result = {
        "data": data,
        "meta": {**_meta(api_key_data, schedule_month), "total": len(data)},
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.7  Authority Landscape ───────────────────────────────────────────────────

@router.get("/market/authority-landscape")
async def market_authority_landscape(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_prefix: Optional[str] = Query(None),
    program_code: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/authority-landscape:{schedule_id}:{atc_prefix}:{program_code}"
    if cached := cache_get(cache_key):
        return cached

    conditions = ["i.schedule_id = $1"]
    params: list = [schedule_id]

    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        conditions.append(
            f"EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = $1 "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    if program_code:
        params.append(program_code.upper())
        conditions.append(f"i.program_code = ${len(params)}")

    where = " AND ".join(conditions)

    # Benefit type distribution
    benefit_sql = f"""
        SELECT i.benefit_type, COUNT(DISTINCT i.pbs_code) AS item_count
        FROM items i WHERE {where}
        GROUP BY i.benefit_type ORDER BY item_count DESC
    """
    benefit_rows = await db.fetch(benefit_sql, *params)

    # Authority method distribution via restrictions
    auth_method_sql = f"""
        SELECT r.authority_method, COUNT(DISTINCT r.restriction_code) AS restriction_count
        FROM restrictions r
        JOIN items i ON i.pbs_code = r.pbs_code AND i.schedule_id = r.schedule_id
        WHERE {where} AND r.authority_method IS NOT NULL
        GROUP BY r.authority_method ORDER BY restriction_count DESC
    """
    auth_rows = await db.fetch(auth_method_sql, *params)

    # Prescriber type distribution
    prescriber_sql = f"""
        SELECT ip.prescriber_type, COUNT(DISTINCT ip.pbs_code) AS item_count
        FROM item_prescribers ip
        JOIN items i ON i.pbs_code = ip.pbs_code AND i.schedule_id = ip.schedule_id
        WHERE {where}
        GROUP BY ip.prescriber_type ORDER BY item_count DESC
    """
    prescriber_rows = await db.fetch(prescriber_sql, *params)

    benefit_labels = {
        "U": "Unrestricted", "R": "Restricted",
        "A": "Authority Required", "S": "Streamlined Authority",
    }

    result = {
        "data": {
            "benefit_type_distribution": [
                {
                    "benefit_type": r["benefit_type"],
                    "label": benefit_labels.get(r["benefit_type"], r["benefit_type"]),
                    "item_count": r["item_count"],
                }
                for r in benefit_rows
            ],
            "authority_method_distribution": [
                {"authority_method": r["authority_method"], "restriction_count": r["restriction_count"]}
                for r in auth_rows
            ],
            "prescriber_distribution": [
                {"prescriber_type": r["prescriber_type"], "item_count": r["item_count"]}
                for r in prescriber_rows
            ],
        },
        "meta": _meta(api_key_data, schedule_month),
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.8  Safety Net Burden ─────────────────────────────────────────────────────

@router.get("/market/safety-net-burden")
async def market_safety_net_burden(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_prefix: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/safety-net-burden:{schedule_id}:{atc_prefix}"
    if cached := cache_get(cache_key):
        return cached

    copayment = await db.fetchrow(
        "SELECT general_patient_charge, concessional_patient_charge, "
        "       safety_net_general, safety_net_concessional "
        "FROM copayments WHERE schedule_id = $1 LIMIT 1",
        schedule_id,
    )
    if not copayment:
        return {"data": None, "meta": _meta(api_key_data, schedule_month), "message": "No copayment data for this schedule."}

    general_charge = float(copayment["general_patient_charge"]) if copayment["general_patient_charge"] else 0
    safety_net_general = float(copayment["safety_net_general"]) if copayment["safety_net_general"] else 0

    atc_cond = ""
    params: list = [schedule_id]
    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        atc_cond = (
            f"AND EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = $1 "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    sql = f"""
        SELECT
            i.pbs_code,
            i.brand_name,
            i.general_charge,
            CASE WHEN i.general_charge > 0
                 THEN CEIL(${ len(params) + 1 }::numeric / i.general_charge)
                 ELSE NULL
            END AS scripts_to_safety_net
        FROM items i
        WHERE i.schedule_id = $1 {atc_cond}
          AND i.general_charge IS NOT NULL AND i.general_charge > 0
        ORDER BY scripts_to_safety_net ASC NULLS LAST
        LIMIT 500
    """
    params.append(safety_net_general)

    rows = await db.fetch(sql, *params)

    scripts_list = [float(r["scripts_to_safety_net"]) for r in rows if r["scripts_to_safety_net"] is not None]

    result = {
        "data": {
            "copayment_context": {
                "general_patient_charge": general_charge,
                "safety_net_threshold_general": safety_net_general,
                "concessional_patient_charge": float(copayment["concessional_patient_charge"]) if copayment["concessional_patient_charge"] else None,
                "safety_net_threshold_concessional": float(copayment["safety_net_concessional"]) if copayment["safety_net_concessional"] else None,
            },
            "scripts_to_safety_net": {
                "min": min(scripts_list) if scripts_list else None,
                "max": max(scripts_list) if scripts_list else None,
                "mean": round(sum(scripts_list) / len(scripts_list), 2) if scripts_list else None,
            },
            "items": [
                {
                    "pbs_code": r["pbs_code"],
                    "brand_name": r["brand_name"],
                    "general_charge": float(r["general_charge"]) if r["general_charge"] else None,
                    "scripts_to_safety_net": float(r["scripts_to_safety_net"]) if r["scripts_to_safety_net"] else None,
                }
                for r in rows
            ],
        },
        "meta": _meta(api_key_data, schedule_month),
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.9  Listings Pipeline ─────────────────────────────────────────────────────

@router.get("/market/listings-pipeline")
async def market_listings_pipeline(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_prefix: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/listings-pipeline:{schedule_id}:{atc_prefix}"
    if cached := cache_get(cache_key):
        return cached

    # Items signalled for future status change via summary_of_changes
    atc_cond = ""
    params: list = [schedule_id]
    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        atc_cond = (
            f"AND EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = sc.pbs_code AND iar.schedule_id = $1 "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    upcoming_delistings = await db.fetch(
        f"""
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description,
               i.brand_name, m.ingredient
        FROM summary_of_changes sc
        LEFT JOIN items i ON i.pbs_code = sc.pbs_code AND i.schedule_id = $1
        LEFT JOIN medicines m ON m.id = i.medicine_id
        WHERE sc.schedule_id = $1
          AND (sc.change_type ILIKE '%delist%' OR sc.description ILIKE '%delist%'
               OR sc.change_type ILIKE '%removal%' OR sc.description ILIKE '%removal%')
          {atc_cond}
        ORDER BY sc.effective_date ASC NULLS LAST
        LIMIT 200
        """,
        *params,
    )

    upcoming_additions = await db.fetch(
        f"""
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description,
               i.brand_name, m.ingredient
        FROM summary_of_changes sc
        LEFT JOIN items i ON i.pbs_code = sc.pbs_code AND i.schedule_id = $1
        LEFT JOIN medicines m ON m.id = i.medicine_id
        WHERE sc.schedule_id = $1
          AND (sc.change_type ILIKE '%list%' OR sc.change_type ILIKE '%addition%'
               OR sc.description ILIKE '%new listing%')
          AND sc.change_type NOT ILIKE '%delist%'
          {atc_cond}
        ORDER BY sc.effective_date ASC NULLS LAST
        LIMIT 200
        """,
        *params,
    )

    def _row(r) -> dict:
        return {
            "pbs_code": r["pbs_code"],
            "brand_name": r["brand_name"],
            "ingredient": r["ingredient"],
            "change_type": r["change_type"],
            "effective_date": str(r["effective_date"]) if r["effective_date"] else None,
            "description": r["description"],
        }

    result = {
        "data": {
            "upcoming_delistings": [_row(r) for r in upcoming_delistings],
            "upcoming_additions": [_row(r) for r in upcoming_additions],
        },
        "meta": {
            **_meta(api_key_data, schedule_month),
            "note": "Based on summary_of_changes content. Results may be sparse until multiple schedules are available.",
        },
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.10  Price Pressure Index ─────────────────────────────────────────────────

@router.get("/market/price-pressure-index")
async def market_price_pressure_index(
    response: Response,
    schedule: Optional[str] = Query(None),
    atc_prefix: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("enterprise")),
    db=Depends(get_db),
    _concurrency=Depends(analytics_concurrency_check),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    cache_key = f"/market/price-pressure-index:{schedule_id}:{atc_prefix}"
    if cached := cache_get(cache_key):
        return cached

    atc_cond = ""
    params: list = [schedule_id]
    if atc_prefix:
        params.append(f"{atc_prefix.upper()}%")
        atc_cond = (
            f"AND EXISTS (SELECT 1 FROM item_atc_relationships iar "
            f"WHERE iar.pbs_code = i.pbs_code AND iar.schedule_id = $1 "
            f"AND iar.atc_code LIKE ${len(params)})"
        )

    # F2 items with brand premium
    f2_sql = f"""
        SELECT
            COUNT(*) FILTER (WHERE ip.brand_premium > 0)             AS with_brand_premium,
            COUNT(*)                                                  AS total_f2,
            AVG(ip.commonwealth_price / NULLIF(i.government_price, 0)) AS mean_dpmq_to_gov_ratio,
            SUM(CASE WHEN ip.brand_premium > 0 THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0) * 100                          AS pct_with_premium
        FROM items i
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $1
        WHERE i.schedule_id = $1 AND i.formulary = 'F2' {atc_cond}
    """
    f2_row = await db.fetchrow(f2_sql, *params)

    # Recent price reductions: items in current schedule that had reductions in pricing events
    reductions_sql = f"""
        SELECT COUNT(DISTINCT pe.pbs_code) AS reduction_count
        FROM item_pricing_events pe
        JOIN schedules s ON s.id = pe.schedule_id
        WHERE s.month >= (
            SELECT month FROM schedules WHERE id = $1
        )::date - INTERVAL '3 months'
          AND (pe.new_price < pe.previous_price OR pe.event_type ILIKE '%reduction%')
          AND pe.previous_price > 0
          {f"AND EXISTS (SELECT 1 FROM item_atc_relationships iar WHERE iar.pbs_code = pe.pbs_code AND iar.schedule_id = $1 AND iar.atc_code LIKE ${len(params)})" if atc_prefix else ""}
    """
    reduction_row = await db.fetchrow(reductions_sql, *params)

    # Compute composite pressure index (0–100 scale, higher = more price pressure)
    total_f2 = f2_row["total_f2"] or 0
    with_premium = f2_row["with_brand_premium"] or 0
    pct_with_premium = float(f2_row["pct_with_premium"] or 0)
    recent_reductions = reduction_row["reduction_count"] or 0
    dpmq_ratio = float(f2_row["mean_dpmq_to_gov_ratio"]) if f2_row["mean_dpmq_to_gov_ratio"] else 1.0

    # Higher DPMQ ratio means DPMQ > government price = less reduction pressure
    # Higher pct_with_premium = more price pressure
    # More recent reductions = higher pressure
    ratio_pressure = max(0.0, (1.0 - dpmq_ratio) * 100) if dpmq_ratio <= 1.0 else 0.0
    premium_pressure = pct_with_premium
    reduction_signal = min(recent_reductions / max(total_f2, 1) * 100, 100) if total_f2 else 0

    composite_index = round((premium_pressure * 0.5 + reduction_signal * 0.3 + ratio_pressure * 0.2), 2)

    result = {
        "data": {
            "f2_scope": {
                "total_f2_items": total_f2,
                "items_with_brand_premium": with_premium,
                "pct_with_brand_premium": round(pct_with_premium, 2),
                "mean_dpmq_to_government_price_ratio": round(dpmq_ratio, 4),
            },
            "recent_reductions": {
                "items_with_price_reductions_last_3_schedules": recent_reductions,
            },
            "price_pressure_index": composite_index,
            "interpretation": (
                "High" if composite_index >= 60
                else "Moderate" if composite_index >= 30
                else "Low"
            ),
        },
        "meta": {
            **_meta(api_key_data, schedule_month),
            "note": "Composite index derived from F2 brand premium prevalence, recent price reduction events, and DPMQ/government price ratio.",
        },
    }
    cache_set(cache_key, result, schedule_id)
    return result
