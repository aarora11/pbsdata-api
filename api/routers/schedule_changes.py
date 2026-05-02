"""Schedule changes router — T3 Intelligence endpoints for /v1/schedule-changes/..."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.tier import require_tier, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["schedule-changes"])



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


def _classify_change(change_type: str | None, section: str | None) -> tuple[str, str]:
    """Return (change_type_code, severity) from PBS raw change_type (INSERT/UPDATE/DELETE) + section."""
    ct = (change_type or "").upper()
    sec = (section or "").lower()

    # Pricing events are the most common DELETE — classify first
    if "pricing-event" in sec or "dispensing-rule" in sec:
        if ct == "DELETE":
            return "PRICE_CHANGE", "MEDIUM"
        if ct in ("INSERT", "UPDATE"):
            return "PRICE_CHANGE", "MEDIUM"

    # Item listings
    if sec == "items":
        if ct == "INSERT":
            return "NEW_LISTING", "MEDIUM"
        if ct == "DELETE":
            return "DELISTING", "HIGH"
        if ct == "UPDATE":
            return "FORMULARY_CHANGE", "MEDIUM"

    # Restriction changes
    if any(x in sec for x in ("restriction", "prescribing-text", "criteria", "indication")):
        return "RESTRICTION_CHANGE", "MEDIUM"

    # Fee / copayment
    if "fee" in sec:
        return "FEE_CHANGE", "LOW"
    if "copayment" in sec:
        return "COPAYMENT_CHANGE", "HIGH"

    # Prescriber / ATC / AMT / other classification changes
    if any(x in sec for x in ("prescriber", "atc", "amt")):
        return "OTHER_MODIFICATION", "LOW"

    return "OTHER_MODIFICATION", "LOW"


async def _fetch_drug_names(db, pbs_codes: list[str], schedule_id: str) -> dict[str, str]:
    """Batch-fetch ingredient names for a list of PBS codes."""
    if not pbs_codes:
        return {}
    rows = await db.fetch(
        """
        SELECT i.pbs_code, m.ingredient
        FROM items i
        JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = ANY($1) AND i.schedule_id = $2
        """,
        pbs_codes, schedule_id,
    )
    return {r["pbs_code"]: r["ingredient"] for r in rows}


def _enrich_row(r, drug_names: dict, extra: dict | None = None) -> dict:
    ct_code, severity = _classify_change(r["change_type"], r["section"])
    out = {
        "pbs_code": r["pbs_code"],
        "drug_name": drug_names.get(r["pbs_code"]) if r["pbs_code"] else None,
        "change_type": r["change_type"],
        "change_type_code": ct_code,
        "severity": severity,
        "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
        "description": r["description"],
        "section": r["section"],
    }
    if extra:
        out.update(extra)
    return out


# ── 3.15  Full schedule change list (schedule as query param, defaults to latest) ─

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

    conditions = ["sc.schedule_id = $1"]
    params: list = [schedule_id]

    if change_type:
        params.append(change_type)
        conditions.append(f"sc.change_type = ${len(params)}")

    if pbs_code:
        params.append(pbs_code.upper())
        conditions.append(f"sc.pbs_code = ${len(params)}")

    if section:
        params.append(f"%{section}%")
        conditions.append(f"sc.section ILIKE ${len(params)}")

    where = " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) FROM summary_of_changes sc WHERE {where}"
    data_sql = f"""
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description, sc.section
        FROM summary_of_changes sc
        WHERE {where}
        ORDER BY sc.effective_date DESC NULLS LAST, sc.pbs_code
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """
    offset = (page - 1) * limit
    total = await db.fetchval(count_sql, *params)
    rows = await db.fetch(data_sql, *params, limit, offset)

    pbs_codes = [r["pbs_code"] for r in rows if r["pbs_code"]]
    drug_names = await _fetch_drug_names(db, pbs_codes, schedule_id)

    return {
        "data": [_enrich_row(r, drug_names) for r in rows],
        "meta": {
            "total": total or 0,
            "page": page,
            "limit": limit,
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
        },
    }


# ── 3.15  Full summary for a specific schedule (schedule in path) ───────────────

@router.get("/schedule-changes/{schedule_code}")
async def get_schedule_change_summary(
    schedule_code: str,
    response: Response,
    change_type: Optional[str] = Query(None),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    """Full enriched change summary for a specific schedule month."""
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule_code)

    conditions = ["sc.schedule_id = $1"]
    params: list = [schedule_id]

    if change_type:
        params.append(change_type)
        conditions.append(f"sc.change_type = ${len(params)}")

    where = " AND ".join(conditions)
    rows = await db.fetch(
        f"""
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description, sc.section
        FROM summary_of_changes sc
        WHERE {where}
        ORDER BY sc.change_type, sc.pbs_code
        """,
        *params,
    )

    pbs_codes = [r["pbs_code"] for r in rows if r["pbs_code"]]
    drug_names = await _fetch_drug_names(db, pbs_codes, schedule_id)

    enriched = [_enrich_row(r, drug_names) for r in rows]

    # Summary counts by structured change_type_code
    by_code: dict[str, int] = {}
    by_severity: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for e in enriched:
        by_code[e["change_type_code"]] = by_code.get(e["change_type_code"], 0) + 1
        by_severity[e["severity"]] = by_severity.get(e["severity"], 0) + 1

    return {
        "data": {
            "schedule_code": schedule_month,
            "total_changes": len(enriched),
            "summary_by_type": by_code,
            "summary_by_severity": by_severity,
            "changes": enriched,
        },
        "meta": {
            "schedule_code": schedule_month,
            "tier": tier_label(api_key_data),
            "join_sources": ["/summary-of-changes", "/items", "/medicines"],
        },
    }


# ── 3.16  New listings for a specific schedule ──────────────────────────────────

@router.get("/schedule-changes/{schedule_code}/new-listings")
async def get_schedule_new_listings(
    schedule_code: str,
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule_code)

    rows = await db.fetch(
        """
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description, sc.section
        FROM summary_of_changes sc
        WHERE sc.schedule_id = $1
          AND sc.change_type = 'INSERT' AND sc.section = 'items'
        ORDER BY sc.pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    total = await db.fetchval(
        "SELECT COUNT(*) FROM summary_of_changes WHERE schedule_id = $1 AND change_type = 'INSERT' AND section = 'items'",
        schedule_id,
    )

    pbs_codes = [r["pbs_code"] for r in rows if r["pbs_code"]]
    drug_names = await _fetch_drug_names(db, pbs_codes, schedule_id)

    # is_first_in_atc_class: true if no other item shares the same ATC Level-5 code in the current schedule
    atc_rows = await db.fetch(
        """
        SELECT iar.pbs_code, iar.atc_code
        FROM item_atc_relationships iar
        WHERE iar.pbs_code = ANY($1) AND iar.schedule_id = $2
        ORDER BY iar.atc_priority_pct DESC NULLS LAST
        """,
        pbs_codes or [""], schedule_id,
    ) if pbs_codes else []

    primary_atc = {r["pbs_code"]: r["atc_code"] for r in atc_rows}

    first_in_class: dict[str, bool] = {}
    for pc, atc_code in primary_atc.items():
        sibling_count = await db.fetchval(
            """
            SELECT COUNT(DISTINCT iar.pbs_code)
            FROM item_atc_relationships iar
            WHERE iar.atc_code = $1 AND iar.schedule_id = $2 AND iar.pbs_code != $3
            """,
            atc_code, schedule_id, pc,
        ) or 0
        first_in_class[pc] = sibling_count == 0

    data = []
    for r in rows:
        pc = r["pbs_code"]
        data.append(_enrich_row(r, drug_names, {
            "is_first_in_atc_class": first_in_class.get(pc),
            "primary_atc_code": primary_atc.get(pc),
        }))

    return {
        "data": data,
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }


# ── 3.17  Delistings for a specific schedule ────────────────────────────────────

@router.get("/schedule-changes/{schedule_code}/delistings")
async def get_schedule_delistings(
    schedule_code: str,
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule_code)

    rows = await db.fetch(
        """
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description, sc.section
        FROM summary_of_changes sc
        WHERE sc.schedule_id = $1
          AND sc.change_type = 'DELETE' AND sc.section = 'items'
        ORDER BY sc.pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    total = await db.fetchval(
        "SELECT COUNT(*) FROM summary_of_changes WHERE schedule_id = $1 AND change_type = 'DELETE' AND section = 'items'",
        schedule_id,
    )

    pbs_codes = [r["pbs_code"] for r in rows if r["pbs_code"]]
    drug_names = await _fetch_drug_names(db, pbs_codes, schedule_id)

    # therapeutic_alternatives: other active items sharing ATC Level-4
    atc_rows = await db.fetch(
        """
        SELECT iar.pbs_code,
               LEFT(iar.atc_code, 5) AS atc_l4
        FROM item_atc_relationships iar
        WHERE iar.pbs_code = ANY($1) AND iar.schedule_id = $2
        """,
        pbs_codes or [""], schedule_id,
    ) if pbs_codes else []

    pbs_to_atc_l4 = {r["pbs_code"]: r["atc_l4"] for r in atc_rows}

    alternatives: dict[str, list] = {}
    for pc, atc_l4 in pbs_to_atc_l4.items():
        alt_rows = await db.fetch(
            """
            SELECT DISTINCT i.pbs_code, m.ingredient, i.brand_name
            FROM item_atc_relationships iar
            JOIN items i ON i.pbs_code = iar.pbs_code AND i.schedule_id = $2
            JOIN medicines m ON m.id = i.medicine_id
            WHERE LEFT(iar.atc_code, 5) = $1 AND iar.schedule_id = $2 AND iar.pbs_code != $3
            LIMIT 5
            """,
            atc_l4, schedule_id, pc,
        )
        alternatives[pc] = [{"pbs_code": a["pbs_code"], "ingredient": a["ingredient"], "brand_name": a["brand_name"]} for a in alt_rows]

    data = []
    for r in rows:
        pc = r["pbs_code"]
        data.append(_enrich_row(r, drug_names, {
            "therapeutic_alternatives": alternatives.get(pc, []),
        }))

    return {
        "data": data,
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }


# ── 3.18  Price changes for a specific schedule ─────────────────────────────────

@router.get("/schedule-changes/{schedule_code}/price-changes")
async def get_schedule_price_changes(
    schedule_code: str,
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule_code)

    rows = await db.fetch(
        """
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description, sc.section
        FROM summary_of_changes sc
        WHERE sc.schedule_id = $1
          AND (sc.section ILIKE '%pricing-event%' OR sc.section ILIKE '%dispensing-rule%')
        ORDER BY sc.effective_date DESC NULLS LAST, sc.pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    total = await db.fetchval(
        """
        SELECT COUNT(*) FROM summary_of_changes
        WHERE schedule_id = $1
          AND (section ILIKE '%pricing-event%' OR section ILIKE '%dispensing-rule%')
        """,
        schedule_id,
    )

    pbs_codes = [r["pbs_code"] for r in rows if r["pbs_code"]]
    drug_names = await _fetch_drug_names(db, pbs_codes, schedule_id)

    # Find previous schedule to compute price delta
    prev_row = await db.fetchrow(
        "SELECT id FROM schedules WHERE month < $1 AND ingest_status = 'complete' ORDER BY month DESC LIMIT 1",
        schedule_month,
    )
    prev_id = str(prev_row["id"]) if prev_row else None

    current_prices: dict[str, float | None] = {}
    prev_prices: dict[str, float | None] = {}

    if pbs_codes:
        curr_rows = await db.fetch(
            "SELECT pbs_code, government_price FROM items WHERE pbs_code = ANY($1) AND schedule_id = $2",
            pbs_codes, schedule_id,
        )
        current_prices = {r["pbs_code"]: float(r["government_price"]) if r["government_price"] else None for r in curr_rows}

        if prev_id:
            prev_rows = await db.fetch(
                "SELECT pbs_code, government_price FROM items WHERE pbs_code = ANY($1) AND schedule_id = $2",
                pbs_codes, prev_id,
            )
            prev_prices = {r["pbs_code"]: float(r["government_price"]) if r["government_price"] else None for r in prev_rows}

    data = []
    for r in rows:
        pc = r["pbs_code"]
        curr = current_prices.get(pc)
        prev = prev_prices.get(pc)
        delta = None
        delta_pct = None
        if curr is not None and prev is not None and prev > 0:
            delta = round(curr - prev, 4)
            delta_pct = round((curr - prev) / prev * 100, 4)
        data.append(_enrich_row(r, drug_names, {
            "previous_price": prev,
            "current_price": curr,
            "price_delta": delta,
            "price_delta_pct": delta_pct,
        }))

    return {
        "data": data,
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }


# ── 3.19  Restriction changes for a specific schedule ───────────────────────────

@router.get("/schedule-changes/{schedule_code}/restriction-changes")
async def get_schedule_restriction_changes(
    schedule_code: str,
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule_code)

    rows = await db.fetch(
        """
        SELECT sc.pbs_code, sc.change_type, sc.effective_date, sc.description, sc.section
        FROM summary_of_changes sc
        WHERE sc.schedule_id = $1
          AND (sc.section ILIKE '%restriction%' OR sc.section ILIKE '%prescribing-text%'
               OR sc.section ILIKE '%criteria%' OR sc.section ILIKE '%indication%')
        ORDER BY sc.pbs_code
        LIMIT $2 OFFSET $3
        """,
        schedule_id, limit, (page - 1) * limit,
    )
    total = await db.fetchval(
        """
        SELECT COUNT(*) FROM summary_of_changes
        WHERE schedule_id = $1
          AND (section ILIKE '%restriction%' OR section ILIKE '%prescribing-text%'
               OR section ILIKE '%criteria%' OR section ILIKE '%indication%')
        """,
        schedule_id,
    )

    pbs_codes = [r["pbs_code"] for r in rows if r["pbs_code"]]
    drug_names = await _fetch_drug_names(db, pbs_codes, schedule_id)

    # Fetch current restriction codes for affected items
    restriction_map: dict[str, list[str]] = {}
    if pbs_codes:
        res_rows = await db.fetch(
            """
            SELECT i.pbs_code, r.restriction_code, r.authority_method
            FROM restrictions r
            JOIN items i ON i.id = r.item_id AND i.schedule_id = $2
            WHERE i.pbs_code = ANY($1)
            """,
            pbs_codes, schedule_id,
        )
        for r in res_rows:
            restriction_map.setdefault(r["pbs_code"], []).append(r["restriction_code"])

    data = []
    for r in rows:
        pc = r["pbs_code"]
        data.append(_enrich_row(r, drug_names, {
            "current_restriction_codes": restriction_map.get(pc, []),
        }))

    return {
        "data": data,
        "meta": {"total": total or 0, "page": page, "limit": limit, "schedule_code": schedule_month, "tier": tier_label(api_key_data)},
    }
