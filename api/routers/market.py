"""Market router — T4 enterprise-tier aggregate endpoints."""
from typing import Optional
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.tier import require_tier, tier_label
from api.middleware.analytics import analytics_concurrency_check
from api.database import get_db
from api.cache import cache_get, cache_set
from api.routers.shared import _rl
from ingest.reference import benefit_type_label

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

@router.get(
    "/market/atc-summary",
    summary="ATC Class Market Summary",
    description=(
        "Aggregates all PBS items beneath an ATC code (at any level) for a given schedule. "
        "Returns counts of items, brands, authority vs unrestricted, F1 vs F2 formulary split, "
        "biosimilar count, 60-day eligible items, and DPMQ price statistics (min/max/mean/median). "
        "Also returns a per-child-code breakdown for the immediate children of the target ATC node.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_atc_summary(
    response: Response,
    atc_code: str = Query(..., description="WHO ATC code at any level (e.g. 'A10' for diabetes drugs, 'A10BA02' for metformin)"),
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    include_inactive: bool = Query(False, description="Include items with no benefit type assigned (typically delisted items)"),
    primary_atc_only: bool = Query(False, description="Only count items where this is their primary ATC code (excludes split-ATC secondary assignments)"),
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

    primary_filter = " AND iar.is_primary = TRUE" if primary_atc_only else ""
    inactive_filter = "" if include_inactive else "AND i.benefit_type IS NOT NULL"

    # Each metric is computed over its own grain. The previous version joined
    # items -> item_pricing -> item_organisation_relationships in one pass and used
    # COUNT(*) FILTER, which counted the cartesian product rather than items: an
    # item with 3 sponsors and 4 pricing rows contributed 12. Measured inflation on
    # live data was 3.12x, and f1_count + f2_count could exceed the item total
    # printed beside it. See docs/AUDIT_FINDINGS.md [C1].
    aggregate_sql = f"""
        WITH RECURSIVE subtree AS (
            SELECT atc_code FROM atc_codes WHERE atc_code = $1 AND schedule_id = $2
            UNION ALL
            SELECT c.atc_code FROM atc_codes c
            JOIN subtree s ON c.atc_parent_code = s.atc_code
            WHERE c.schedule_id = $2
        ),
        scoped_items AS (
            SELECT DISTINCT i.pbs_code, i.benefit_type, i.formulary,
                   i.sixty_day_eligible, i.biosimilar
            FROM subtree st
            JOIN item_atc_relationships iar
              ON iar.atc_code = st.atc_code AND iar.schedule_id = $2{primary_filter}
            JOIN items i ON i.pbs_code = iar.pbs_code AND i.schedule_id = $2
            {inactive_filter}
        ),
        -- One row per (brand, dispensing rule) for the scoped items. Price
        -- statistics are the distribution over these rows and are unaffected by
        -- how many sponsors an item has.
        scoped_pricing AS (
            SELECT ip.li_item_id, ip.commonwealth_price
            FROM item_pricing ip
            WHERE ip.schedule_id = $2
              AND ip.pbs_code IN (SELECT pbs_code FROM scoped_items)
        ),
        scoped_orgs AS (
            SELECT DISTINCT ior.organisation_id
            FROM item_organisation_relationships ior
            WHERE ior.schedule_id = $2
              AND ior.pbs_code IN (SELECT pbs_code FROM scoped_items)
        )
        SELECT
            (SELECT COUNT(*) FROM scoped_items)                          AS unique_prescribing_rules,
            (SELECT COUNT(DISTINCT li_item_id) FROM scoped_pricing)      AS unique_brands,
            (SELECT COUNT(*) FROM scoped_items WHERE benefit_type = 'A') AS authority_required_count,
            (SELECT COUNT(*) FROM scoped_items WHERE benefit_type = 'S') AS streamlined_count,
            (SELECT COUNT(*) FROM scoped_items WHERE formulary = 'F1')   AS f1_count,
            (SELECT COUNT(*) FROM scoped_items WHERE formulary = 'F2')   AS f2_count,
            (SELECT COUNT(*) FROM scoped_items WHERE sixty_day_eligible) AS sixty_day_items,
            (SELECT COUNT(*) FROM scoped_items WHERE biosimilar)         AS biosimilar_items,
            (SELECT MIN(commonwealth_price) FROM scoped_pricing)         AS min_dpmq,
            (SELECT MAX(commonwealth_price) FROM scoped_pricing)         AS max_dpmq,
            (SELECT AVG(commonwealth_price) FROM scoped_pricing)         AS mean_dpmq,
            (SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY commonwealth_price)
             FROM scoped_pricing)                                        AS median_dpmq,
            (SELECT COUNT(*) FROM scoped_orgs)                           AS manufacturer_count
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
            WITH child_items AS (
                SELECT DISTINCT i.pbs_code
                FROM item_atc_relationships iar
                JOIN items i ON i.pbs_code = iar.pbs_code AND i.schedule_id = $2
                WHERE iar.atc_code = $1 AND iar.schedule_id = $2
            )
            SELECT (SELECT COUNT(*) FROM child_items) AS item_count,
                   (SELECT AVG(ip.commonwealth_price) FROM item_pricing ip
                    WHERE ip.schedule_id = $2
                      AND ip.pbs_code IN (SELECT pbs_code FROM child_items)) AS mean_dpmq
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

@router.get(
    "/market/price-reduction-events",
    summary="List Market Price Reduction Events",
    description=(
        "Returns all PBS price reduction events across a schedule date range, with calculated percentage changes. "
        "Useful for tracking mandatory price disclosure reductions, government-mandated cuts, and F2 brand premium changes. "
        "Filter by ATC prefix or manufacturer to focus on a therapeutic class or specific company's portfolio. "
        "Results are capped at 500 items.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_price_reduction_events(
    response: Response,
    from_schedule: str = Query(..., description="Start schedule month in YYYY-MM format (inclusive)"),
    to_schedule: Optional[str] = Query(None, description="End schedule month in YYYY-MM format (inclusive); defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix (e.g. 'C09' for ACE inhibitors/ARBs)"),
    organisation_id: Optional[str] = Query(None, description="Filter by manufacturer/sponsor organisation ID"),
    min_pct_change: Optional[float] = Query(None, description="Only include events with absolute percentage change at least this value (e.g. 5.0 for ≥5% reductions)"),
    sort: str = Query("pct_change_asc", description="Sort order: 'pct_change_asc' (largest reductions first) or 'pct_change_desc' (smallest reductions first)"),
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
        to_month = to_row["month"]
    else:
        to_row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
        to_month = to_row["month"]

    # Fetch price events across the schedule range
    conditions = [
        "pe.schedule_id IN (SELECT id FROM schedules WHERE month >= $1 AND month <= $2)",
        # PBS publishes the reduction as a percentage, not as a before/after pair.
        # This previously filtered on `pe.previous_price > 0` against a column PBS does
        # not send, so it was NULL on every row and the whole clause was never true —
        # the endpoint returned an empty list for every possible query. Event types are
        # codes like SPR_SINGLE_NB, so matching '%reduction%' never fired either.
        "pe.percentage_applied IS NOT NULL",
        "pe.percentage_applied > 0",
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
            pe.li_item_id,
            pe.event_type,
            pe.percentage_applied,
            -- A statutory price reduction lowers the price, so express it as a negative
            -- change to keep the sign consistent with what callers expect.
            (-pe.percentage_applied) AS pct_change,
            s.month AS schedule_month,
            i.brand_name,
            m.ingredient
        FROM item_pricing_events pe
        JOIN schedules s ON s.id = pe.schedule_id
        -- items is brand grain, so joining it on pbs_code multiplies each event by the
        -- item's brand count: 8 events for 10104T came back as 32 rows. items_listing
        -- keeps it 1:1.
        LEFT JOIN items_listing i ON i.pbs_code = pe.pbs_code AND i.schedule_id = pe.schedule_id
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
            "li_item_id": r["li_item_id"],
            "event_type": r["event_type"],
            "schedule_month": r["schedule_month"],
            "percentage_applied": float(r["percentage_applied"]) if r["percentage_applied"] is not None else None,
            "pct_change": round(pct, 4) if pct is not None else None,
        })

    return {
        "data": data,
        "meta": {
            "from_schedule": from_schedule,
            "to_schedule": to_month,
            "total": len(data),
            "tier": tier_label(api_key_data),
            "join_sources": ["/item-pricing-events", "/items"],
            "caveat": (
                "PBS publishes price events as a percentage applied, not a before/after "
                "price pair, so pct_change is the negated percentage rather than a "
                "computed difference. Absolute prices are not available on this feed."
            ),
        },
    }


# ── 4.3  Manufacturer Landscape ────────────────────────────────────────────────

@router.get(
    "/market/manufacturer-landscape",
    summary="Manufacturer PBS Portfolio Landscape",
    description=(
        "Returns an aggregated view of all PBS manufacturers/sponsors with their portfolio statistics: "
        "total PBS codes, unique brands (li_item_ids), F1/F2 formulary split, biosimilar count, "
        "authority item count, and DPMQ price range. Results are sorted by item count descending. "
        "Use `atc_prefix` or `program_code` to scope the analysis to a therapeutic area.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_manufacturer_landscape(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix to scope to a therapeutic class (e.g. 'L01' for antineoplastics)"),
    program_code: Optional[str] = Query(None, description="Filter by PBS program code (e.g. 'GE' for General Schedule)"),
    formulary: Optional[str] = Query(None, description="Filter by formulary code, e.g. F1 (single-brand) or F2 (multiple-brand, price-disclosure)"),
    min_item_count: int = Query(1, ge=1, description="Minimum number of distinct PBS codes a manufacturer must have to appear in results"),
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
            f"EXISTS (SELECT 1 FROM items_listing i2 WHERE i2.pbs_code = ior.pbs_code "
            f"AND i2.schedule_id = $1 AND i2.program_code = ${len(params)})"
        )

    if formulary:
        params.append(formulary.upper())
        conditions.append(
            f"EXISTS (SELECT 1 FROM items_listing i3 WHERE i3.pbs_code = ior.pbs_code "
            f"AND i3.schedule_id = $1 AND i3.formulary = ${len(params)})"
        )

    where = " AND ".join(conditions)

    sql = f"""
        SELECT
            ior.organisation_id,
            o.name,
            COUNT(DISTINCT ior.pbs_code)                                AS pbs_code_count,
            COUNT(DISTINCT ip.li_item_id)                               AS brand_count,
            -- COUNT(DISTINCT pbs_code) rather than COUNT(*): the LEFT JOIN to
            -- item_pricing repeats each item once per (brand, dispensing rule).
            -- See docs/AUDIT_FINDINGS.md [C1].
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE i.formulary = 'F1')   AS f1_count,
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE i.formulary = 'F2')   AS f2_count,
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE i.biosimilar = TRUE)  AS biosimilar_count,
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE i.benefit_type = 'A') AS authority_count,
            MIN(ip.commonwealth_price)                                  AS min_dpmq,
            MAX(ip.commonwealth_price)                                  AS max_dpmq,
            AVG(ip.commonwealth_price)                                  AS mean_dpmq
        FROM item_organisation_relationships ior
        JOIN items i ON i.pbs_code = ior.pbs_code AND i.schedule_id = $1
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $1
        LEFT JOIN organisations o ON o.organisation_id = ior.organisation_id AND o.schedule_id = $1
        WHERE {where}
        GROUP BY ior.organisation_id, o.name
        -- Threshold and ordering are on distinct PBS codes, not brands. Brand
        -- counts come from item_pricing; gating on them dropped every manufacturer
        -- whenever pricing was unavailable. See docs/AUDIT_FINDINGS.md [C1].
        HAVING COUNT(DISTINCT ior.pbs_code) >= ${len(params) + 1}
        ORDER BY COUNT(DISTINCT ior.pbs_code) DESC
        LIMIT 200
    """
    params.append(min_item_count)

    rows = await db.fetch(sql, *params)
    data = []
    for r in rows:
        data.append({
            "organisation_id": r["organisation_id"],
            "organisation_name": r["name"],
            "pbs_code_count": r["pbs_code_count"],
            # Named to match what min_item_count actually gates on.
            "item_count": r["pbs_code_count"],
            "brand_count": r["brand_count"],
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

@router.get(
    "/market/schedule-comparison",
    summary="Compare Two PBS Schedules",
    description=(
        "Compares two PBS schedule months and returns: new listings (items in target but not base), "
        "removed listings (items in base but not target), price changes (items with government price changes), "
        "and copayment threshold differences. "
        "Use `atc_prefix` to limit comparison to a specific therapeutic class. "
        "Results are capped at 500 items per category.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_schedule_comparison(
    response: Response,
    base_schedule: str = Query(..., description="Base (earlier) schedule month in YYYY-MM format"),
    target_schedule: Optional[str] = Query(None, description="Target (later) schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter comparison to a specific ATC prefix (e.g. 'J01' for antibacterials)"),
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

    # The ATC filter is injected into three queries that use different table
    # aliases. Previously one filter string hardcoded alias "i" and was injected
    # into the price-change query, which aliases items as b/t — PostgreSQL raised
    # "missing FROM-clause entry for table i" whenever atc_prefix was supplied.
    # Build the filter per alias instead. See docs/AUDIT_FINDINGS.md [B2].
    atc_params: list = []
    atc_filter_base = ""
    atc_filter_target = ""
    atc_filter_price = ""
    if atc_prefix:
        atc_params.append(f"{atc_prefix.upper()}%")
        param_idx = len(atc_params) + 2

        def _atc_filter(alias: str) -> str:
            return (
                f"AND EXISTS (SELECT 1 FROM item_atc_relationships iar "
                f"WHERE iar.pbs_code = {alias}.pbs_code "
                f"AND iar.schedule_id = {alias}.schedule_id "
                f"AND iar.atc_code LIKE ${param_idx})"
            )

        atc_filter_base = _atc_filter("i")
        atc_filter_target = atc_filter_base
        # Price-change query aliases the base schedule's items as "b".
        atc_filter_price = _atc_filter("b")

    # Items added (in target but not in base)
    added_rows = await db.fetch(
        f"""
        SELECT i.pbs_code, i.brand_name, i.benefit_type, i.formulary
        FROM items_listing i
        WHERE i.schedule_id = $1 {atc_filter_target}
          AND NOT EXISTS (
              SELECT 1 FROM items_listing i2
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
        FROM items_listing i
        WHERE i.schedule_id = $1 {atc_filter_base}
          AND NOT EXISTS (
              SELECT 1 FROM items_listing i2
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
        FROM items_listing b
        JOIN items t ON t.pbs_code = b.pbs_code
        WHERE b.schedule_id = $1 AND t.schedule_id = $2
          AND b.government_price IS NOT NULL AND t.government_price IS NOT NULL
          AND b.government_price != t.government_price
          {atc_filter_price}
        ORDER BY ABS((t.government_price - b.government_price) / NULLIF(b.government_price, 0)) DESC
        LIMIT 500
        """,
        base_id, target_id, *atc_params,
    )

    # Copayment diff
    base_cp = await db.fetchrow(
        "SELECT general, concessional, safety_net_general, safety_net_concessional "
        "FROM copayments WHERE schedule_id = $1 LIMIT 1",
        base_id,
    )
    target_cp = await db.fetchrow(
        "SELECT general, concessional, safety_net_general, safety_net_concessional "
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

@router.get(
    "/market/formulary-landscape",
    summary="PBS Formulary Distribution Landscape",
    description=(
        "Breaks down PBS items by formulary category (F1, F2, F3, None) with item counts, "
        "percentage shares, and DPMQ price statistics for each formulary tier. "
        "Also includes the current general and concessional copayment amounts for context. "
        "Filter by ATC prefix or program to scope the analysis.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_formulary_landscape(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix to scope to a therapeutic class"),
    program_code: Optional[str] = Query(None, description="Filter by PBS program code"),
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
        FROM items_listing i
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
            # Named to match what min_item_count actually gates on.
            "item_count": r["item_count"],
            "percentage": round(r["item_count"] / total_items * 100, 2) if total_items else 0,
            "mean_dpmq": round(float(r["mean_dpmq"]), 4) if r["mean_dpmq"] else None,
            "min_dpmq": round(float(r["min_dpmq"]), 4) if r["min_dpmq"] else None,
            "max_dpmq": round(float(r["max_dpmq"]), 4) if r["max_dpmq"] else None,
        })

    # Copayment context (general / concessional)
    copayment = await db.fetchrow(
        "SELECT general, concessional FROM copayments WHERE schedule_id = $1 LIMIT 1",
        schedule_id,
    )

    result = {
        "data": {
            "distribution": data,
            "total_items": total_items,
            "copayment_context": {
                "general": float(copayment["general"]) if copayment and copayment["general"] else None,
                "concessional": float(copayment["concessional"]) if copayment and copayment["concessional"] else None,
            } if copayment else None,
        },
        "meta": _meta(api_key_data, schedule_month),
    }
    cache_set(cache_key, result, schedule_id)
    return result


# ── 4.6  Biosimilar Landscape ──────────────────────────────────────────────────

@router.get(
    "/market/biosimilar-landscape",
    summary="Biosimilar vs Originator Landscape",
    description=(
        "For each ingredient with at least one biosimilar on the PBS, returns a side-by-side comparison "
        "of biosimilar vs originator pricing and item counts. "
        "Includes price delta percentage (biosimilar minimum vs originator minimum DPMQ). "
        "Biological medicines are grouped by ingredient; results are sorted by biosimilar count descending.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_biosimilar_landscape(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix (e.g. 'L04' for immunosuppressants where many biosimilars exist)"),
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
        FROM items_listing i
        JOIN medicines m ON m.id = i.medicine_id
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $1
        WHERE i.schedule_id = $1 {atc_cond}
          AND (i.biosimilar = TRUE OR EXISTS (
              SELECT 1 FROM items_listing i2
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

@router.get(
    "/market/authority-landscape",
    summary="PBS Authority Prescribing Landscape",
    description=(
        "Returns aggregate distributions for prescribing complexity across the PBS: "
        "benefit type breakdown (U/R/A/S), authority method breakdown (streamlined/telephone/written/online), "
        "and prescriber type distribution. "
        "Use to understand what proportion of PBS items require authority and how prescribers are authorised.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_authority_landscape(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix to scope to a therapeutic class"),
    program_code: Optional[str] = Query(None, description="Filter by PBS program code"),
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
        FROM items_listing i WHERE {where}
        GROUP BY i.benefit_type ORDER BY item_count DESC
    """
    benefit_rows = await db.fetch(benefit_sql, *params)

    # Authority method distribution via restrictions
    auth_method_sql = f"""
        SELECT r.authority_method, COUNT(DISTINCT r.restriction_code) AS restriction_count
        FROM restrictions r
        JOIN items i ON i.id = r.item_id
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


    result = {
        "data": {
            "benefit_type_distribution": [
                {
                    "benefit_type": r["benefit_type"],
                    "label": benefit_type_label(r["benefit_type"]),
                    # Named to match what min_item_count actually gates on.
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

@router.get(
    "/market/safety-net-burden",
    summary="PBS Safety Net Burden Analysis",
    description=(
        "Calculates how many prescriptions each PBS item requires before a patient reaches the safety net threshold. "
        "Returns min/max/mean scripts-to-safety-net across all items, plus per-item data sorted by fewest scripts first. "
        "Higher-cost items reach the safety net threshold faster (fewer scripts needed). "
        "Results are capped at 500 items.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_safety_net_burden(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix to scope to a therapeutic class"),
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
        "SELECT general, concessional, "
        "       safety_net_general, safety_net_concessional "
        "FROM copayments WHERE schedule_id = $1 LIMIT 1",
        schedule_id,
    )
    if not copayment:
        return {"data": None, "meta": _meta(api_key_data, schedule_month), "message": "No copayment data for this schedule."}

    general_charge = float(copayment["general"]) if copayment["general"] else 0
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
        FROM items_listing i
        WHERE i.schedule_id = $1 {atc_cond}
          AND i.general_charge IS NOT NULL AND i.general_charge > 0
        ORDER BY scripts_to_safety_net ASC NULLS LAST
        LIMIT 500
    """
    params.append(safety_net_general)

    rows = await db.fetch(sql, *params)

    # Population statistics must come from a separate un-limited aggregate. The
    # item list above is truncated to the 500 lowest-scripts items, so computing
    # min/max/mean over it produced a downward-biased "mean across all items" and a
    # meaningless max. See docs/AUDIT_FINDINGS.md [C7].
    stats_sql = f"""
        SELECT
            MIN(CEIL(${len(params)}::numeric / i.general_charge)) AS min_scripts,
            MAX(CEIL(${len(params)}::numeric / i.general_charge)) AS max_scripts,
            AVG(CEIL(${len(params)}::numeric / i.general_charge)) AS mean_scripts,
            COUNT(*)                                              AS item_count
        FROM items_listing i
        WHERE i.schedule_id = $1 {atc_cond}
          AND i.general_charge IS NOT NULL AND i.general_charge > 0
    """
    stats = await db.fetchrow(stats_sql, *params)

    result = {
        "data": {
            "copayment_context": {
                "general": general_charge,
                "safety_net_threshold_general": safety_net_general,
                "concessional": float(copayment["concessional"]) if copayment["concessional"] else None,
                "safety_net_threshold_concessional": float(copayment["safety_net_concessional"]) if copayment["safety_net_concessional"] else None,
            },
            # Computed across every qualifying item, not just the returned page.
            "scripts_to_safety_net": {
                "min": float(stats["min_scripts"]) if stats and stats["min_scripts"] is not None else None,
                "max": float(stats["max_scripts"]) if stats and stats["max_scripts"] is not None else None,
                "mean": round(float(stats["mean_scripts"]), 2) if stats and stats["mean_scripts"] is not None else None,
                "population_item_count": stats["item_count"] if stats else 0,
                "basis": "All items with a positive general patient charge in scope; the "
                         "items array below is the 500 closest to threshold.",
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

@router.get(
    "/market/listings-pipeline",
    summary="PBS Listings Pipeline",
    description=(
        "Returns upcoming PBS delistings and new additions sourced from the summary_of_changes table "
        "for a given schedule. Useful for tracking market-entry signals and at-risk items. "
        "Note: data quality depends on PBS change descriptions; results may be sparse for schedules "
        "with limited change record population.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_listings_pipeline(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix to scope to a therapeutic class"),
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

@router.get(
    "/market/price-pressure-index",
    summary="PBS Price Pressure Index",
    description=(
        "Returns a composite price pressure index (0–100 scale) for the PBS or a therapeutic class. "
        "The index is derived from three signals: F2 brand premium prevalence (50% weight), "
        "recent price reductions in the last 3 schedules (30% weight), and DPMQ-to-government-price ratio (20% weight). "
        "Interpretation: ≥60 = High, 30–59 = Moderate, <30 = Low pressure.\n\n"
        "Requires **Enterprise (T4)** tier."
    ),
)
async def market_price_pressure_index(
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    atc_prefix: Optional[str] = Query(None, description="Filter by ATC code prefix to scope to a therapeutic class"),
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

    # F2 scope, counted per item rather than per pricing row. total_f2 was
    # previously COUNT(*) over items LEFT JOIN item_pricing, so it reported the
    # number of (item, brand, rule) combinations while being labelled an item
    # count — and was then used as the denominator for a DISTINCT-based numerator.
    # See docs/AUDIT_FINDINGS.md [C1], [C8].
    f2_sql = f"""
        SELECT
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE ip.brand_premium > 0) AS with_brand_premium,
            COUNT(DISTINCT i.pbs_code)                                     AS total_f2,
            AVG(ip.commonwealth_price / NULLIF(i.government_price, 0))     AS mean_dpmq_to_gov_ratio,
            COUNT(DISTINCT i.pbs_code) FILTER (WHERE ip.brand_premium > 0)::float
                / NULLIF(COUNT(DISTINCT i.pbs_code), 0) * 100              AS pct_with_premium
        FROM items_listing i
        LEFT JOIN item_pricing ip ON ip.pbs_code = i.pbs_code AND ip.schedule_id = $1
        WHERE i.schedule_id = $1 AND i.formulary = 'F2' {atc_cond}
    """
    f2_row = await db.fetchrow(f2_sql, *params)

    # Recent price reductions in the three schedules up to and including the target.
    # The window previously had no upper bound, so querying a historical schedule
    # counted reductions recorded in later months — the index for a past schedule
    # changed every time a new one was ingested. See docs/AUDIT_FINDINGS.md [C8].
    reductions_sql = f"""
        SELECT COUNT(DISTINCT pe.pbs_code) AS reduction_count
        FROM item_pricing_events pe
        JOIN schedules s ON s.id = pe.schedule_id
        WHERE s.month >= TO_CHAR(
            TO_DATE((SELECT month FROM schedules WHERE id = $1) || '-01', 'YYYY-MM-DD') - INTERVAL '3 months',
            'YYYY-MM'
        )
          AND s.month <= (SELECT month FROM schedules WHERE id = $1)
          -- Same defect as price-reduction-events: these columns are not in the PBS
          -- feed, so this signal counted zero reductions on every schedule and pulled
          -- the index down permanently.
          AND pe.percentage_applied IS NOT NULL
          AND pe.percentage_applied > 0
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
            # Raw signals and weights are published so callers can re-weight or
            # ignore the composite. The weighting is PBSdata.io's, not PBS policy.
            "signals": {
                "brand_premium_prevalence": round(premium_pressure, 2),
                "recent_reduction_signal": round(reduction_signal, 2),
                "dpmq_ratio_pressure": round(ratio_pressure, 2),
            },
            "weights": {
                "brand_premium_prevalence": 0.5,
                "recent_reduction_signal": 0.3,
                "dpmq_ratio_pressure": 0.2,
            },
        },
        "meta": {
            **_meta(api_key_data, schedule_month),
            "note": (
                "Composite index derived from F2 brand premium prevalence, recent price "
                "reduction events, and DPMQ/government price ratio."
            ),
            "derivation": "index=heuristic",
            "caveat": (
                "PBSdata.io metric, not a PBS-published measure. Weights are our own and "
                "are not derived from PBS policy. The dpmq_ratio_pressure signal compares "
                "item_pricing.commonwealth_price against items.government_price; where "
                "either is unavailable it contributes no pressure. Use the raw signals if "
                "you disagree with the weighting."
            ),
        },
    }
    cache_set(cache_key, result, schedule_id)
    return result
