"""Drugs router — T2 Clinical + T3 Intelligence endpoints for GET /v1/drugs/..."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.tier import require_tier, tier_label
from api.routers.shared import _rl
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["drugs"])



async def _resolve_schedule(db, schedule: Optional[str]) -> tuple[str, str]:
    """Return (schedule_id, schedule_month) for the given schedule or latest."""
    if schedule:
        row = await db.fetchrow("SELECT id, month FROM schedules WHERE month = $1", schedule)
    else:
        row = await db.fetchrow(
            "SELECT id, month FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1"
        )
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Schedule not found."})
    return str(row["id"]), row["month"]


def _meta(key_data: dict, schedule_month: str, join_sources: list[str]) -> dict:
    return {
        "schedule_month": schedule_month,
        "tier": tier_label(key_data),
        "join_sources": join_sources,
    }


# ── 3.1  Drug search (MUST be first to avoid {pbs_code} capturing "search") ───

@router.get(
    "/drugs/search",
    summary="Search PBS Drugs",
    description=(
        "Full-text search across PBS items by ingredient name, brand name, or PBS code. "
        "Returns paginated results with benefit type, formulary status, and primary ATC code. "
        "Supports optional filters for program, benefit type, and ATC prefix.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def search_drugs(
    response: Response,
    q: str = Query(..., min_length=2, description="Search term — matches ingredient name, brand name, or PBS code (e.g. 'metformin', 'Glucophage', '2622M')"),
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format (e.g. '2026-05'); defaults to the latest complete schedule"),
    program_code: Optional[str] = Query(None, description="Filter by PBS program code (e.g. 'GE' for General Schedule)"),
    benefit_type: Optional[str] = Query(None, description="Filter by benefit type: U=Unrestricted, R=Restricted, A=Authority Required, S=Streamlined Authority"),
    atc_code: Optional[str] = Query(None, description="Filter by ATC code prefix (e.g. 'A10' returns all diabetes drugs)"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    conditions = ["i.schedule_id = $1"]
    params: list = [schedule_id]

    params.append(f"%{q}%")
    q_idx = len(params)
    conditions.append(
        f"(m.ingredient ILIKE ${q_idx} OR i.brand_name ILIKE ${q_idx} OR i.pbs_code ILIKE ${q_idx})"
    )

    if program_code:
        params.append(program_code.upper())
        conditions.append(f"i.program_code = ${len(params)}")

    if benefit_type:
        params.append(benefit_type.upper())
        conditions.append(f"i.benefit_type = ${len(params)}")

    if atc_code:
        params.append(f"{atc_code.upper()}%")
        conditions.append(
            f"EXISTS (SELECT 1 FROM item_atc_relationships iar WHERE iar.pbs_code = i.pbs_code "
            f"AND iar.schedule_id = i.schedule_id AND iar.atc_code LIKE ${len(params)})"
        )

    where = " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) FROM items i JOIN medicines m ON m.id = i.medicine_id WHERE {where}"
    data_sql = f"""
        SELECT i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.pack_unit,
               i.benefit_type, i.program_code, i.formulary,
               m.ingredient, m.atc_code AS medicine_atc_code
        FROM items i
        JOIN medicines m ON m.id = i.medicine_id
        WHERE {where}
        ORDER BY m.ingredient, i.pbs_code
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """
    offset = (page - 1) * limit
    total = await db.fetchval(count_sql, *params)
    rows = await db.fetch(data_sql, *params, limit, offset)

    benefit_labels = {
        "U": "Unrestricted", "R": "Restricted",
        "A": "Authority Required", "S": "Streamlined Authority",
    }
    data = []
    for r in rows:
        bc = r["benefit_type"] or "U"
        data.append({
            "pbs_code": r["pbs_code"],
            "ingredient": r["ingredient"],
            "brand_name": r["brand_name"],
            "form": r["form"],
            "strength": r["strength"],
            "pack_size": r["pack_size"],
            "pack_unit": r["pack_unit"],
            "benefit_type_code": bc,
            "benefit_type_label": benefit_labels.get(bc, bc),
            "program_code": r["program_code"],
            "formulary": r["formulary"],
            "atc_code": r["medicine_atc_code"],
        })

    return {
        "data": data,
        "meta": {
            "total": total or 0,
            "page": page,
            "limit": limit,
            "schedule_month": schedule_month,
            "tier": tier_label(api_key_data),
        },
    }


# ── 2.1  Drug identity ─────────────────────────────────────────────────────────

@router.get(
    "/drugs/{pbs_code}",
    summary="Get Drug Detail",
    description=(
        "Returns the full clinical profile for a single PBS item identified by its PBS code. "
        "Includes drug identity (ingredient, form, strength, pack), dispensing rules, restriction/benefit type, "
        "prescriber authorisations, primary ATC classification, manufacturer, and pricing summary.\n\n"
        "Set `include_brands=true` to embed the full brand and pricing list inline (equivalent to calling "
        "`/drugs/{pbs_code}/brands` separately).\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_drug(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    include_brands: bool = Query(False, description="Embed brand list inline (same data as /drugs/{pbs_code}/brands)"),
    api_key_data: dict = Depends(require_tier("growth")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.pack_unit,
               i.benefit_type, i.formulary, i.section, i.program_code,
               i.general_charge, i.concessional_charge, i.government_price,
               i.brand_premium, i.brand_premium_counts_to_safety_net,
               i.sixty_day_eligible, i.max_quantity, i.max_repeats,
               i.dangerous_drug, i.artg_id, i.sponsor, i.caution, i.biosimilar,
               m.ingredient, m.atc_code AS medicine_atc_code
        FROM items i
        JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    program = await db.fetchrow(
        "SELECT program_title FROM programs WHERE program_code = $1 AND schedule_id = $2",
        item["program_code"], schedule_id,
    )
    org = await db.fetchrow(
        """
        SELECT o.organisation_id, o.name, o.state, o.abn
        FROM item_organisation_relationships ior
        JOIN organisations o ON o.organisation_id = ior.organisation_id AND o.schedule_id = ior.schedule_id
        WHERE ior.pbs_code = $1 AND ior.schedule_id = $2
        LIMIT 1
        """,
        pbs_code.upper(), schedule_id,
    )
    atc = await db.fetchrow(
        """
        SELECT iar.atc_code, iar.atc_priority_pct, a.atc_description, a.atc_level
        FROM item_atc_relationships iar
        JOIN atc_codes a ON a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id
        WHERE iar.pbs_code = $1 AND iar.schedule_id = $2
        ORDER BY iar.atc_priority_pct DESC NULLS LAST
        LIMIT 1
        """,
        pbs_code.upper(), schedule_id,
    )
    prescribers = await db.fetch(
        "SELECT prescriber_code, prescriber_type FROM item_prescribers WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )
    brand_count = await db.fetchval(
        "SELECT COUNT(DISTINCT li_item_id) FROM item_pricing WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    ) or 1

    brands_data = None
    if include_brands:
        pricing_rows = await db.fetch(
            """
            SELECT li_item_id,
                   MAX(commonwealth_price)         AS commonwealth_price,
                   MAX(max_general_patient_charge)  AS max_general_patient_charge,
                   MAX(brand_premium)              AS brand_premium,
                   MAX(fee_dispensing)             AS fee_dispensing
            FROM item_pricing
            WHERE pbs_code = $1 AND schedule_id = $2
            GROUP BY li_item_id
            ORDER BY li_item_id
            """,
            pbs_code.upper(), schedule_id,
        )
        brands_data = [
            {
                "li_item_id": r["li_item_id"],
                "brand_name": item["brand_name"],
                "formulary": item["formulary"],
                "pricing": {
                    "commonwealth_price": float(r["commonwealth_price"]) if r["commonwealth_price"] else None,
                    "max_general_patient_charge": float(r["max_general_patient_charge"]) if r["max_general_patient_charge"] else None,
                    "brand_premium": float(r["brand_premium"]) if r["brand_premium"] else None,
                    "dispensing_fee": float(r["fee_dispensing"]) if r["fee_dispensing"] else None,
                },
            }
            for r in pricing_rows
        ] or [{"li_item_id": None, "brand_name": item["brand_name"], "formulary": item["formulary"], "pricing": None}]

    def _f(v):
        return float(v) if v is not None else None

    benefit_labels = {
        "U": "Unrestricted", "R": "Restricted",
        "A": "Authority Required", "S": "Streamlined Authority",
    }
    benefit_code = item["benefit_type"] or "U"

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "drug": {
                "drug_name": item["ingredient"],
                "form": item["form"],
                "strength": item["strength"],
                "pack_size": item["pack_size"],
                "pack_unit": item["pack_unit"],
            },
            "dispensing": {
                "maximum_quantity": item["max_quantity"],
                "number_of_repeats": item["max_repeats"],
            },
            "program": {
                "program_code": item["program_code"],
                "program_title": program["program_title"] if program else None,
            },
            "manufacturer": dict(org) if org else None,
            "classification": {
                "primary_atc_code": atc["atc_code"] if atc else item["medicine_atc_code"],
                "primary_atc_description": atc["atc_description"] if atc else None,
                "primary_atc_priority_pct": _f(atc["atc_priority_pct"]) if atc else None,
                "has_split_atc": False,
            },
            "restriction": {
                "benefit_type_code": benefit_code,
                "benefit_type_label": benefit_labels.get(benefit_code, benefit_code),
            },
            "prescribers": {
                "count": len(prescribers),
                "codes": [p["prescriber_code"] for p in prescribers],
                "types": [p["prescriber_type"] for p in prescribers],
            },
            "pricing_summary": {
                "formulary": item["formulary"],
                "brand_count": brand_count,
                "general_charge": _f(item["general_charge"]),
                "concessional_charge": _f(item["concessional_charge"]),
                "government_price": _f(item["government_price"]),
                "brand_premium": _f(item["brand_premium"]),
            },
            "status": {
                "is_active": True,
                "is_dangerous_drug": item["dangerous_drug"],
                "sixty_day_eligible": item["sixty_day_eligible"],
                "artg_id": item["artg_id"],
                "caution": item["caution"],
            },
            **({"brands": brands_data} if include_brands else {}),
        },
        "meta": _meta(api_key_data, schedule_month, [
            "/items", "/programs", "/organisations", "/atc-codes", "/prescribers",
        ]),
    }


# ── 2.2  Brands ────────────────────────────────────────────────────────────────

@router.get(
    "/drugs/{pbs_code}/brands",
    summary="List Drug Brands and Pricing",
    description=(
        "Returns all listed brands (li_item_id) for a PBS item along with per-brand pricing: "
        "commonwealth price (DPMQ), maximum general patient charge, brand premium, and dispensing fee. "
        "F1 items are brand-substitutable; F2 items are not.\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_drug_brands(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("growth")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        "SELECT pbs_code, brand_name, formulary FROM items WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    # item_pricing holds one row per (li_item_id, dispensing_rule_mnem) — aggregate to brand level
    pricing_rows = await db.fetch(
        """
        SELECT li_item_id,
               MAX(commonwealth_price)        AS commonwealth_price,
               MAX(max_general_patient_charge) AS max_general_patient_charge,
               MAX(brand_premium)             AS brand_premium,
               MAX(fee_dispensing)            AS fee_dispensing
        FROM item_pricing
        WHERE pbs_code = $1 AND schedule_id = $2
        GROUP BY li_item_id
        ORDER BY li_item_id
        """,
        pbs_code.upper(), schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    brands = []
    for r in pricing_rows:
        brands.append({
            "li_item_id": r["li_item_id"],
            "brand_name": item["brand_name"],  # schema stores one brand per pbs_code; see phase-2 roadmap
            "formulary": item["formulary"],
            "pricing": {
                "commonwealth_price": _f(r["commonwealth_price"]),
                "max_general_patient_charge": _f(r["max_general_patient_charge"]),
                "brand_premium": _f(r["brand_premium"]),
                "dispensing_fee": _f(r["fee_dispensing"]),
            },
        })

    # Fall back to single brand from items table if item_pricing is empty
    if not brands:
        brands.append({
            "li_item_id": None,
            "brand_name": item["brand_name"],
            "formulary": item["formulary"],
            "pricing": None,
        })

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "brand_count": len(brands),
            "brands": brands,
        },
        "meta": _meta(api_key_data, schedule_month, ["/items", "/item-dispensing-rule-relationships"]),
    }


# ── 2.3  Prescribers ───────────────────────────────────────────────────────────

@router.get(
    "/drugs/{pbs_code}/prescribers",
    summary="Get Drug Prescriber Rules",
    description=(
        "Returns the list of prescriber types authorised to prescribe this PBS item, together with "
        "the benefit type and authority requirements. "
        "Includes whether written or telephone authority is required, and the authority method code "
        "(S=Streamlined, T=Telephone, W=Written, O=Online).\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_drug_prescribers(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("growth")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        "SELECT pbs_code, benefit_type FROM items i JOIN medicines m ON m.id = i.medicine_id WHERE i.pbs_code = $1 AND i.schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    prescribers = await db.fetch(
        "SELECT prescriber_code, prescriber_type FROM item_prescribers WHERE pbs_code = $1 AND schedule_id = $2 ORDER BY prescriber_code",
        pbs_code.upper(), schedule_id,
    )
    restriction = await db.fetchrow(
        """
        SELECT r.authority_required, r.authority_method, r.written_authority_required
        FROM restrictions r
        JOIN items i ON i.id = r.item_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        LIMIT 1
        """,
        pbs_code.upper(), schedule_id,
    )

    benefit_labels = {
        "U": "Unrestricted", "R": "Restricted",
        "A": "Authority Required", "S": "Streamlined Authority",
    }
    benefit_code = item["benefit_type"] or "U"

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "benefit_type_code": benefit_code,
            "benefit_type_label": benefit_labels.get(benefit_code, benefit_code),
            "requires_authority": restriction["authority_required"] if restriction else False,
            "written_authority_required": restriction["written_authority_required"] if restriction else False,
            "authority_method": restriction["authority_method"] if restriction else None,
            "authorised_prescribers": [dict(p) for p in prescribers],
        },
        "meta": _meta(api_key_data, schedule_month, ["/prescribers", "/restrictions"]),
    }


# ── 2.4  ATC classifications ───────────────────────────────────────────────────

@router.get(
    "/drugs/{pbs_code}/atc",
    summary="Get Drug ATC Classifications",
    description=(
        "Returns all WHO ATC (Anatomical Therapeutic Chemical) classifications for a PBS item, "
        "including the full ancestor hierarchy from Level 1 (anatomical group) to Level 5 (chemical substance). "
        "Items with split ATC assignment (multiple classifications) will return all of them ranked by priority percentage.\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_drug_atc(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("growth")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    if not await db.fetchval("SELECT 1 FROM items WHERE pbs_code = $1 AND schedule_id = $2", pbs_code.upper(), schedule_id):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    atc_rels = await db.fetch(
        """
        SELECT iar.atc_code, iar.atc_priority_pct, a.atc_description, a.atc_level, a.atc_parent_code
        FROM item_atc_relationships iar
        JOIN atc_codes a ON a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id
        WHERE iar.pbs_code = $1 AND iar.schedule_id = $2
        ORDER BY iar.atc_priority_pct DESC NULLS LAST
        """,
        pbs_code.upper(), schedule_id,
    )

    classifications = []
    for rel in atc_rels:
        ancestors = await db.fetch(
            """
            WITH RECURSIVE chain AS (
                SELECT atc_code, atc_description, atc_level, atc_parent_code
                FROM atc_codes WHERE atc_code = $1 AND schedule_id = $2
                UNION ALL
                SELECT p.atc_code, p.atc_description, p.atc_level, p.atc_parent_code
                FROM atc_codes p JOIN chain c ON p.atc_code = c.atc_parent_code WHERE p.schedule_id = $2
            )
            SELECT * FROM chain ORDER BY atc_level
            """,
            rel["atc_code"], schedule_id,
        )
        classifications.append({
            "atc_code": rel["atc_code"],
            "atc_description": rel["atc_description"],
            "atc_level": rel["atc_level"],
            "priority_pct": float(rel["atc_priority_pct"]) if rel["atc_priority_pct"] else None,
            "is_primary": len(classifications) == 0,
            "hierarchy": [{"level": r["atc_level"], "atc_code": r["atc_code"], "description": r["atc_description"]} for r in ancestors],
            "breadcrumb": " → ".join(r["atc_code"] for r in ancestors),
        })

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "has_split_atc": len(classifications) > 1,
            "classifications": classifications,
        },
        "meta": _meta(api_key_data, schedule_month, ["/item-atc-relationships", "/atc-codes"]),
    }


# ── 2.5  AMT concepts ─────────────────────────────────────────────────────────

@router.get(
    "/drugs/{pbs_code}/amt",
    summary="Get Drug AMT Concepts",
    description=(
        "Returns Australian Medicines Terminology (AMT) concepts linked to this PBS item, "
        "grouped by concept type (e.g. CTPP, TPP, TP, MPP, MP). "
        "AMT provides a standardised clinical vocabulary for medicines used in Australian health systems.\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_drug_amt(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("growth")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    if not await db.fetchval("SELECT 1 FROM items WHERE pbs_code = $1 AND schedule_id = $2", pbs_code.upper(), schedule_id):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    amt_rows = await db.fetch(
        """
        SELECT a.amt_id, a.concept_type, a.preferred_term, a.atc_code, a.parent_amt_id,
               iar.relationship_type
        FROM item_amt_relationships iar
        JOIN amt_items a ON a.amt_id = iar.amt_id AND a.schedule_id = iar.schedule_id
        WHERE iar.pbs_code = $1 AND iar.schedule_id = $2
        ORDER BY a.concept_type, a.amt_id
        """,
        pbs_code.upper(), schedule_id,
    )

    concepts: dict[str, list] = {}
    for r in amt_rows:
        ct = r["concept_type"] or "UNKNOWN"
        concepts.setdefault(ct, []).append({
            "amt_id": r["amt_id"],
            "preferred_term": r["preferred_term"],
            "atc_code": r["atc_code"],
            "parent_amt_id": r["parent_amt_id"],
            "relationship_type": r["relationship_type"],
        })

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "concept_count": len(amt_rows),
            "concepts_by_type": concepts,
        },
        "meta": _meta(api_key_data, schedule_month, ["/amt-items", "/item-amt-relationships"]),
    }


# ── 2.9  Restrictions index ────────────────────────────────────────────────────

@router.get(
    "/drugs/{pbs_code}/restrictions",
    summary="List Drug Restrictions",
    description=(
        "Returns all PBS restrictions attached to an item, including restriction code, type, "
        "treatment phase, continuation rules, and authority method. "
        "Restricted (R) and Authority Required (A/S) items require the prescriber to satisfy "
        "specific clinical criteria before dispensing.\n\n"
        "Requires **Growth (T2)** tier."
    ),
)
async def get_drug_restrictions(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("growth")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.benefit_type, m.ingredient
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    restrictions = await db.fetch(
        """
        SELECT r.restriction_code, r.streamlined_code, r.restriction_type,
               r.authority_required, r.authority_method, r.treatment_of_code,
               r.written_authority_required, r.continuation_only, r.treatment_phase
        FROM restrictions r
        JOIN items i ON i.id = r.item_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        ORDER BY r.restriction_code
        """,
        pbs_code.upper(), schedule_id,
    )

    benefit_labels = {
        "U": "Unrestricted", "R": "Restricted",
        "A": "Authority Required", "S": "Streamlined Authority",
    }
    benefit_code = item["benefit_type"] or "U"

    restriction_list = []
    for r in restrictions:
        restriction_list.append({
            "restriction_code": r["restriction_code"],
            "streamlined_code": r["streamlined_code"],
            "restriction_type": r["restriction_type"],
            "authority_required": r["authority_required"],
            "authority_method": r["authority_method"],
            "treatment_of_code": r["treatment_of_code"],
            "written_authority_required": r["written_authority_required"],
            "continuation_only": r["continuation_only"],
            "treatment_phase": r["treatment_phase"],
        })

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "drug_name": item["ingredient"],
            "benefit_type_code": benefit_code,
            "benefit_type_label": benefit_labels.get(benefit_code, benefit_code),
            "restriction_count": len(restriction_list),
            "restrictions": restriction_list,
        },
        "meta": _meta(api_key_data, schedule_month, ["/item-restriction-relationships", "/restrictions"]),
    }


# ── T3  Intelligence endpoints (scale tier) ────────────────────────────────────

@router.get(
    "/drugs/{pbs_code}/full-profile",
    summary="Get Complete Drug Profile",
    description=(
        "Returns a single comprehensive response combining all drug sub-resources: identity, dispensing rules, "
        "program, manufacturer, ATC classifications, restrictions (with full text), prescribers, brands, "
        "pricing, and AMT concepts. Eliminates the need for multiple API calls when you need everything.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_full_profile(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.pack_unit,
               i.benefit_type, i.formulary, i.section, i.program_code,
               i.general_charge, i.concessional_charge, i.government_price,
               i.brand_premium, i.brand_premium_counts_to_safety_net,
               i.sixty_day_eligible, i.max_quantity, i.max_repeats,
               i.dangerous_drug, i.artg_id, i.sponsor, i.caution, i.biosimilar,
               m.ingredient, m.atc_code AS medicine_atc_code
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    program, org, prescribers, restrictions, atc_rels, pricing_rows, amt_rows = await _gather_drug_data(
        db, pbs_code.upper(), schedule_id
    )

    def _f(v):
        return float(v) if v is not None else None

    benefit_labels = {"U": "Unrestricted", "R": "Restricted", "A": "Authority Required", "S": "Streamlined Authority"}
    benefit_code = item["benefit_type"] or "U"

    brands = []
    for r in pricing_rows:
        brands.append({
            "li_item_id": r["li_item_id"],
            "brand_name": item["brand_name"],
            "pricing": {
                "commonwealth_price": _f(r["commonwealth_price"]),
                "max_general_patient_charge": _f(r["max_general_patient_charge"]),
                "brand_premium": _f(r["brand_premium"]),
                "dispensing_fee": _f(r["fee_dispensing"]),
            },
        })

    restriction_list = []
    for r in restrictions:
        restriction_list.append({
            "restriction_code": r["restriction_code"],
            "restriction_type": r["restriction_type"],
            "authority_required": r["authority_required"],
            "authority_method": r["authority_method"],
            "written_authority_required": r["written_authority_required"],
            "continuation_only": r["continuation_only"],
            "treatment_phase": r["treatment_phase"],
        })

    return {
        "data": {
            "pbs_code": item["pbs_code"],
            "identity": {
                "ingredient": item["ingredient"],
                "brand_name": item["brand_name"],
                "form": item["form"],
                "strength": item["strength"],
                "pack_size": item["pack_size"],
                "pack_unit": item["pack_unit"],
                "artg_id": item["artg_id"],
                "biosimilar": item["biosimilar"],
                "caution": item["caution"],
                "dangerous_drug": item["dangerous_drug"],
            },
            "dispensing": {
                "max_quantity": item["max_quantity"],
                "max_repeats": item["max_repeats"],
                "sixty_day_eligible": item["sixty_day_eligible"],
                "formulary": item["formulary"],
                "section": item["section"],
            },
            "program": dict(program) if program else None,
            "manufacturer": dict(org) if org else None,
            "classification": {
                "primary_atc_code": atc_rels[0]["atc_code"] if atc_rels else item["medicine_atc_code"],
                "primary_atc_description": atc_rels[0]["atc_description"] if atc_rels else None,
                "has_split_atc": len(atc_rels) > 1,
            },
            "restriction": {
                "benefit_type_code": benefit_code,
                "benefit_type_label": benefit_labels.get(benefit_code, benefit_code),
                "restriction_count": len(restriction_list),
                "restrictions": restriction_list,
            },
            "prescribers": [dict(p) for p in prescribers],
            "brands": {"brand_count": len(brands) or 1, "items": brands},
            "pricing": {
                "general_charge": _f(item["general_charge"]),
                "concessional_charge": _f(item["concessional_charge"]),
                "government_price": _f(item["government_price"]),
                "brand_premium": _f(item["brand_premium"]),
                "brand_premium_counts_to_safety_net": item["brand_premium_counts_to_safety_net"],
            },
            "amt": {
                "concept_count": len(amt_rows),
                "concepts": [dict(r) for r in amt_rows],
            },
        },
        "meta": _meta(api_key_data, schedule_month, [
            "/items", "/medicines", "/programs", "/organisations", "/restrictions",
            "/prescribers", "/item-pricing", "/amt-items", "/atc-codes",
        ]),
    }


async def _gather_drug_data(db, pbs_code: str, schedule_id: str):
    """Fetch all supporting data for a drug in parallel-friendly sequential calls."""
    program = await db.fetchrow(
        "SELECT program_code, program_title FROM programs WHERE program_code = ("
        "SELECT program_code FROM items WHERE pbs_code = $1 AND schedule_id = $2) AND schedule_id = $2",
        pbs_code, schedule_id,
    )
    org = await db.fetchrow(
        """
        SELECT o.organisation_id, o.name, o.state, o.abn
        FROM item_organisation_relationships ior
        JOIN organisations o ON o.organisation_id = ior.organisation_id AND o.schedule_id = ior.schedule_id
        WHERE ior.pbs_code = $1 AND ior.schedule_id = $2 LIMIT 1
        """,
        pbs_code, schedule_id,
    )
    prescribers = await db.fetch(
        "SELECT prescriber_code, prescriber_type FROM item_prescribers WHERE pbs_code = $1 AND schedule_id = $2 ORDER BY prescriber_code",
        pbs_code, schedule_id,
    )
    restrictions = await db.fetch(
        """
        SELECT r.restriction_code, r.restriction_type, r.authority_required, r.authority_method,
               r.written_authority_required, r.continuation_only, r.treatment_phase
        FROM restrictions r JOIN items i ON i.id = r.item_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2 ORDER BY r.restriction_code
        """,
        pbs_code, schedule_id,
    )
    atc_rels = await db.fetch(
        """
        SELECT iar.atc_code, iar.atc_priority_pct, a.atc_description, a.atc_level
        FROM item_atc_relationships iar
        JOIN atc_codes a ON a.atc_code = iar.atc_code AND a.schedule_id = iar.schedule_id
        WHERE iar.pbs_code = $1 AND iar.schedule_id = $2
        ORDER BY iar.atc_priority_pct DESC NULLS LAST
        """,
        pbs_code, schedule_id,
    )
    pricing_rows = await db.fetch(
        """
        SELECT li_item_id,
               MAX(commonwealth_price) AS commonwealth_price,
               MAX(max_general_patient_charge) AS max_general_patient_charge,
               MAX(brand_premium) AS brand_premium,
               MAX(fee_dispensing) AS fee_dispensing
        FROM item_pricing WHERE pbs_code = $1 AND schedule_id = $2
        GROUP BY li_item_id ORDER BY li_item_id
        """,
        pbs_code, schedule_id,
    )
    amt_rows = await db.fetch(
        """
        SELECT a.amt_id, a.concept_type, a.preferred_term, a.atc_code, iar.relationship_type
        FROM item_amt_relationships iar
        JOIN amt_items a ON a.amt_id = iar.amt_id AND a.schedule_id = iar.schedule_id
        WHERE iar.pbs_code = $1 AND iar.schedule_id = $2
        ORDER BY a.concept_type, a.amt_id
        """,
        pbs_code, schedule_id,
    )
    return program, org, prescribers, restrictions, atc_rels, pricing_rows, amt_rows


@router.get(
    "/drugs/{pbs_code}/restriction-full",
    summary="Get Full Restriction Texts",
    description=(
        "Returns complete restriction records for a PBS item including full clinical criteria text, "
        "indication, restriction HTML (li_html_text), and all linked prescribing text components. "
        "Use `restriction_code` to retrieve a single restriction record in detail.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_restriction_full(
    pbs_code: str,
    response: Response,
    restriction_code: Optional[str] = Query(None, description="Filter to a single restriction code (e.g. 'ASTHM01')"),
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    if not await db.fetchval("SELECT 1 FROM items WHERE pbs_code = $1 AND schedule_id = $2", pbs_code.upper(), schedule_id):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    restriction_filter = ""
    params: list = [pbs_code.upper(), schedule_id]
    if restriction_code:
        params.append(restriction_code.upper())
        restriction_filter = f"AND r.restriction_code = ${len(params)}"

    restrictions = await db.fetch(
        f"""
        SELECT r.restriction_code, r.streamlined_code, r.restriction_type, r.indication,
               r.restriction_text, r.authority_required, r.authority_method,
               r.written_authority_required, r.complex_authority_required,
               r.continuation_only, r.treatment_phase, r.treatment_of_code,
               r.prescriber_type, r.li_html_text, r.clinical_criteria
        FROM restrictions r JOIN items i ON i.id = r.item_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2 {restriction_filter}
        ORDER BY r.restriction_code
        """,
        *params,
    )

    result = []
    for r in restrictions:
        prescribing_texts = await db.fetch(
            """
            SELECT rel.prescribing_text_id, pt.text_type, pt.prescribing_txt, pt.complex_authority_required
            FROM restriction_prescribing_text_relationships rel
            JOIN prescribing_texts pt ON pt.prescribing_text_id = rel.prescribing_text_id AND pt.schedule_id = rel.schedule_id
            WHERE rel.restriction_code = $1 AND rel.schedule_id = $2
            ORDER BY rel.prescribing_text_id
            """,
            r["restriction_code"], schedule_id,
        )
        result.append({
            **dict(r),
            "prescribing_components": [
                {
                    "prescribing_text_id": pt["prescribing_text_id"],
                    "text_type": pt["text_type"],
                    "text": pt["prescribing_txt"],
                    "complex_authority_required": pt["complex_authority_required"],
                }
                for pt in prescribing_texts
            ],
        })

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "restriction_count": len(result),
            "restrictions": result,
        },
        "meta": _meta(api_key_data, schedule_month, [
            "/restrictions", "/restriction-prescribing-text-relationships", "/prescribing-texts",
        ]),
    }


@router.get(
    "/drugs/{pbs_code}/authority-workflow",
    summary="Get Authority Prescribing Workflow",
    description=(
        "Returns a structured prescribing workflow for authority items, including step-by-step checklists "
        "for each restriction. Shows authority method (streamlined self-assess, telephone, written, online), "
        "continuation requirements, and clinical criteria. "
        "Useful for building prescribing decision-support tools.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_authority_workflow(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        "SELECT pbs_code, benefit_type FROM items WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    restrictions = await db.fetch(
        """
        SELECT r.restriction_code, r.restriction_type, r.authority_required,
               r.authority_method, r.written_authority_required, r.complex_authority_required,
               r.streamlined_code, r.treatment_phase, r.continuation_only, r.prescriber_type,
               r.indication, r.clinical_criteria, r.li_html_text, r.restriction_text,
               r.treatment_of_code
        FROM restrictions r JOIN items i ON i.id = r.item_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        ORDER BY r.restriction_code
        """,
        pbs_code.upper(), schedule_id,
    )

    prescribers = await db.fetch(
        "SELECT prescriber_code, prescriber_type FROM item_prescribers WHERE pbs_code = $1 AND schedule_id = $2",
        pbs_code.upper(), schedule_id,
    )

    benefit_code = item["benefit_type"] or "U"
    requires_any_authority = any(r["authority_required"] for r in restrictions)
    written_required = any(r["written_authority_required"] for r in restrictions)
    complex_required = any(r["complex_authority_required"] for r in restrictions)

    method_labels = {
        "S": "Streamlined (self-assess)", "T": "Telephone approval",
        "W": "Written application", "O": "Online application",
    }

    workflows = []
    for r in restrictions:
        checklist = []
        if r["authority_required"]:
            if r["authority_method"] == "S":
                checklist.append("Confirm patient meets streamlined authority criteria")
                checklist.append("Record streamlined authority code on prescription")
            else:
                checklist.append("Obtain authority approval before prescribing")
                if r["written_authority_required"]:
                    checklist.append("Submit written authority application")
                if r["complex_authority_required"]:
                    checklist.append("Submit complex authority documentation")
            if r["continuation_only"]:
                checklist.append("Confirm this is a continuation of existing therapy")
        else:
            checklist.append("No authority required — prescribe as restricted benefit")

        workflows.append({
            "restriction_code": r["restriction_code"],
            "restriction_type": r["restriction_type"],
            "treatment_phase": r["treatment_phase"],
            "treatment_of_code": r["treatment_of_code"],
            "authority_required": r["authority_required"],
            "authority_method": r["authority_method"],
            "authority_method_label": method_labels.get(r["authority_method"], r["authority_method"]),
            "streamlined_code": r["streamlined_code"],
            "written_authority_required": r["written_authority_required"],
            "complex_authority_required": r["complex_authority_required"],
            "continuation_only": r["continuation_only"],
            "prescriber_types_allowed": r["prescriber_type"],
            "indication": r["indication"],
            "clinical_criteria": r["clinical_criteria"],
            "restriction_text": r["restriction_text"],
            "full_text_html": r["li_html_text"],
            "checklist": checklist,
        })

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "benefit_type_code": benefit_code,
            "requires_any_authority": requires_any_authority,
            "written_required_for_any": written_required,
            "complex_required_for_any": complex_required,
            "authorised_prescribers": [dict(p) for p in prescribers],
            "workflows": workflows,
        },
        "meta": _meta(api_key_data, schedule_month, ["/restrictions", "/item-prescribers"]),
    }


@router.get(
    "/drugs/{pbs_code}/substitution",
    summary="Get Drug Substitution Options",
    description=(
        "Returns PBS items that share the same active ingredient, which can be used as potential "
        "substitution candidates. Results include form, strength, pack size, formulary status, and patient charge. "
        "Note: formal brand-substitution groups (F1 substitution) are not yet available in this schema version; "
        "results are same-ingredient matches only.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_substitution(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.brand_name, i.formulary, i.program_code,
               m.ingredient, m.atc_code
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    # Same ingredient + same schedule — potential therapeutic substitutes
    same_ingredient = await db.fetch(
        """
        SELECT i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.formulary,
               i.general_charge, i.government_price
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE m.ingredient = $1 AND i.schedule_id = $2 AND i.pbs_code != $3
        ORDER BY i.pbs_code
        """,
        item["ingredient"], schedule_id, pbs_code.upper(),
    )

    def _f(v):
        return float(v) if v is not None else None

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "ingredient": item["ingredient"],
            "brand_name": item["brand_name"],
            "note": "Brand substitution group IDs are not yet available in this schema version. Substitutes shown are same-ingredient matches only.",
            "same_ingredient_items": [
                {
                    "pbs_code": r["pbs_code"],
                    "brand_name": r["brand_name"],
                    "form": r["form"],
                    "strength": r["strength"],
                    "pack_size": r["pack_size"],
                    "formulary": r["formulary"],
                    "general_charge": _f(r["general_charge"]),
                    "government_price": _f(r["government_price"]),
                }
                for r in same_ingredient
            ],
        },
        "meta": _meta(api_key_data, schedule_month, ["/items", "/medicines"]),
    }


@router.get(
    "/drugs/{pbs_code}/price-history",
    summary="Get Drug Price History",
    description=(
        "Returns a time-series of government price (DPMQ), general and concessional patient charges, "
        "and brand premium across up to 36 monthly schedule snapshots. "
        "Includes a trend summary showing total price delta and direction (up/down/stable) over the window.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_price_history(
    pbs_code: str,
    response: Response,
    months: int = Query(12, ge=1, le=36, description="Number of schedule months to look back (1–36); default is 12"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)

    # Fetch price snapshots across all available schedules ordered by month
    rows = await db.fetch(
        """
        SELECT s.month, i.general_charge, i.concessional_charge, i.government_price,
               i.brand_premium, i.formulary
        FROM items i
        JOIN schedules s ON s.id = i.schedule_id
        WHERE i.pbs_code = $1 AND s.ingest_status = 'complete'
        ORDER BY s.month DESC
        LIMIT $2
        """,
        pbs_code.upper(), months,
    )
    if not rows:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found or no history available."})

    def _f(v):
        return float(v) if v is not None else None

    # Rows are DESC (newest first); reverse for oldest-to-newest trend calc
    snapshots = [
        {
            "schedule_month": r["month"],
            "general_charge": _f(r["general_charge"]),
            "concessional_charge": _f(r["concessional_charge"]),
            "government_price": _f(r["government_price"]),
            "brand_premium": _f(r["brand_premium"]),
            "formulary": r["formulary"],
        }
        for r in rows
    ]

    # Compute government_price trend across the window
    prices = [s["government_price"] for s in snapshots if s["government_price"] is not None]
    if len(prices) >= 2:
        newest, oldest = prices[0], prices[-1]
        delta = round(newest - oldest, 4)
        delta_pct = round((newest - oldest) / oldest * 100, 2) if oldest else None
        direction = "up" if delta > 0 else ("down" if delta < 0 else "stable")
        trend = {
            "oldest_month": snapshots[-1]["schedule_month"],
            "newest_month": snapshots[0]["schedule_month"],
            "oldest_price": oldest,
            "newest_price": newest,
            "delta": delta,
            "delta_pct": delta_pct,
            "direction": direction,
        }
    else:
        trend = None

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "snapshot_count": len(snapshots),
            "trend": trend,
            "history": snapshots,
        },
        "meta": {
            "pbs_code": pbs_code.upper(),
            "tier": tier_label(api_key_data),
            "join_sources": ["/items", "/schedules"],
        },
    }


@router.get(
    "/drugs/{pbs_code}/pricing-events",
    summary="Get Drug Pricing Events",
    description=(
        "Returns discrete pricing change events for a PBS item within a given schedule, "
        "including event type, effective date, previous price, new price, and calculated price change. "
        "Events represent price reductions, increases, and special pricing adjustments recorded by the PBS.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_pricing_events(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    if not await db.fetchval("SELECT 1 FROM items WHERE pbs_code = $1 AND schedule_id = $2", pbs_code.upper(), schedule_id):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    rows = await db.fetch(
        """
        SELECT event_type, effective_date, previous_price, new_price
        FROM item_pricing_events
        WHERE pbs_code = $1 AND schedule_id = $2
        ORDER BY effective_date DESC NULLS LAST
        """,
        pbs_code.upper(), schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    events = [
        {
            "event_type": r["event_type"],
            "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
            "previous_price": _f(r["previous_price"]),
            "new_price": _f(r["new_price"]),
            "price_change": _f(r["new_price"] - r["previous_price"]) if r["new_price"] is not None and r["previous_price"] is not None else None,
        }
        for r in rows
    ]

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "event_count": len(events),
            "events": events,
        },
        "meta": _meta(api_key_data, schedule_month, ["/item-pricing-events"]),
    }


@router.get(
    "/drugs/{pbs_code}/safety-net",
    summary="Get Drug Safety Net Calculation",
    description=(
        "Calculates the out-of-pocket costs and safety net contribution for a PBS item. "
        "Returns patient copayment amounts (general and concessional), brand premium costs, "
        "and an estimate of how many scripts are required to reach the PBS safety net threshold. "
        "The safety net threshold is the point at which further copayments are waived or reduced.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_safety_net(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.general_charge, i.concessional_charge, i.government_price,
               i.brand_premium, i.brand_premium_counts_to_safety_net
        FROM items i
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    copayments = await db.fetchrow(
        "SELECT general, concessional, safety_net_general, safety_net_concessional, safety_net_card_issue FROM copayments WHERE schedule_id = $1",
        schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    general_charge = _f(item["general_charge"])
    concessional_charge = _f(item["concessional_charge"])
    brand_premium = _f(item["brand_premium"])
    copay_general = _f(copayments["general"]) if copayments else None
    copay_conc = _f(copayments["concessional"]) if copayments else None
    sn_general = _f(copayments["safety_net_general"]) if copayments else None
    sn_conc = _f(copayments["safety_net_concessional"]) if copayments else None

    # Patient cost = standard copayment (or lower if government price is lower), plus any brand premium
    actual_general = min(general_charge, copay_general) if general_charge is not None and copay_general is not None else general_charge
    actual_conc = min(concessional_charge, copay_conc) if concessional_charge is not None and copay_conc is not None else concessional_charge

    # Prescriptions needed to reach safety net (approximate)
    scripts_general = round(sn_general / actual_general) if sn_general and actual_general else None
    scripts_conc = round(sn_conc / actual_conc) if sn_conc and actual_conc else None

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "patient_cost": {
                "general_copayment": actual_general,
                "concessional_copayment": actual_conc,
                "brand_premium": brand_premium,
                "brand_premium_counts_to_safety_net": item["brand_premium_counts_to_safety_net"],
                "general_total_with_premium": round(actual_general + brand_premium, 2) if actual_general is not None and brand_premium else actual_general,
            },
            "safety_net": {
                "general_threshold": sn_general,
                "concessional_threshold": sn_conc,
                "card_issue_fee": _f(copayments["safety_net_card_issue"]) if copayments else None,
            },
            "estimated_scripts_to_safety_net": {
                "general": scripts_general,
                "concessional": scripts_conc,
            },
        },
        "meta": _meta(api_key_data, schedule_month, ["/items", "/copayments"]),
    }


@router.get(
    "/drugs/{pbs_code}/60-day-pair",
    summary="Get 60-Day Dispensing Eligibility",
    description=(
        "Returns whether a PBS item is eligible for 60-day dispensing (double the standard quantity per script) "
        "and lists other PBS items with the same active ingredient that are also 60-day eligible. "
        "60-day dispensing was introduced to reduce dispensing frequency for stable, chronic conditions.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_60_day_pair(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.brand_name, i.sixty_day_eligible, i.max_quantity, i.pack_size,
               m.ingredient
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    # Find other 60-day eligible items with same ingredient
    paired_items = await db.fetch(
        """
        SELECT i.pbs_code, i.brand_name, i.form, i.strength, i.pack_size, i.max_quantity,
               i.general_charge
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE m.ingredient = $1 AND i.schedule_id = $2 AND i.sixty_day_eligible = TRUE AND i.pbs_code != $3
        ORDER BY i.pbs_code
        """,
        item["ingredient"], schedule_id, pbs_code.upper(),
    )

    def _f(v):
        return float(v) if v is not None else None

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "ingredient": item["ingredient"],
            "sixty_day_eligible": item["sixty_day_eligible"],
            "note": "60-day dispensing allows dispensing of 2 months supply in one prescription where eligible.",
            "same_ingredient_60_day_eligible": [
                {
                    "pbs_code": r["pbs_code"],
                    "brand_name": r["brand_name"],
                    "form": r["form"],
                    "strength": r["strength"],
                    "pack_size": r["pack_size"],
                    "max_quantity": r["max_quantity"],
                    "general_charge": _f(r["general_charge"]),
                }
                for r in paired_items
            ],
        },
        "meta": _meta(api_key_data, schedule_month, ["/items", "/medicines"]),
    }


@router.get(
    "/drugs/{pbs_code}/formulary-status",
    summary="Get Drug Formulary Status",
    description=(
        "Returns the formulary classification (F1, F2, or F3) for a PBS item with explanatory labels, "
        "along with biosimilar flag, benefit type, and full pricing details. "
        "Also includes up to 5 most recent pricing events for context on recent price changes. "
        "F1 = brand-substitutable; F2 = not substitutable; F3 = price-disclosure items.\n\n"
        "Requires **Scale (T3)** tier."
    ),
)
async def get_drug_formulary_status(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None, description="Schedule month in YYYY-MM format; defaults to the latest complete schedule"),
    api_key_data: dict = Depends(require_tier("scale")),
    db=Depends(get_db),
):
    _rl(response, api_key_data)
    schedule_id, schedule_month = await _resolve_schedule(db, schedule)

    item = await db.fetchrow(
        """
        SELECT i.pbs_code, i.brand_name, i.formulary, i.section, i.program_code,
               i.general_charge, i.concessional_charge, i.government_price,
               i.brand_premium, i.biosimilar, i.benefit_type,
               m.ingredient
        FROM items i JOIN medicines m ON m.id = i.medicine_id
        WHERE i.pbs_code = $1 AND i.schedule_id = $2
        """,
        pbs_code.upper(), schedule_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Drug not found."})

    pricing_events = await db.fetch(
        """
        SELECT event_type, effective_date, previous_price, new_price
        FROM item_pricing_events
        WHERE pbs_code = $1 AND schedule_id = $2
        ORDER BY effective_date DESC NULLS LAST
        LIMIT 5
        """,
        pbs_code.upper(), schedule_id,
    )

    def _f(v):
        return float(v) if v is not None else None

    formulary_labels = {
        "F1": "Formulary 1 — highest cost-effectiveness",
        "F2": "Formulary 2 — medium cost-effectiveness",
        "F3": "Formulary 3 — lowest cost-effectiveness / price disclosure items",
    }

    benefit_labels = {"U": "Unrestricted", "R": "Restricted", "A": "Authority Required", "S": "Streamlined Authority"}
    bc = item["benefit_type"] or "U"

    return {
        "data": {
            "pbs_code": pbs_code.upper(),
            "ingredient": item["ingredient"],
            "brand_name": item["brand_name"],
            "formulary": item["formulary"],
            "formulary_label": formulary_labels.get(item["formulary"] or "", "Unknown formulary"),
            "section": item["section"],
            "program_code": item["program_code"],
            "benefit_type_code": bc,
            "benefit_type_label": benefit_labels.get(bc, bc),
            "biosimilar": item["biosimilar"],
            "pricing": {
                "general_charge": _f(item["general_charge"]),
                "concessional_charge": _f(item["concessional_charge"]),
                "government_price": _f(item["government_price"]),
                "brand_premium": _f(item["brand_premium"]),
            },
            "recent_pricing_events": [
                {
                    "event_type": r["event_type"],
                    "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                    "previous_price": _f(r["previous_price"]),
                    "new_price": _f(r["new_price"]),
                }
                for r in pricing_events
            ],
        },
        "meta": _meta(api_key_data, schedule_month, ["/items", "/item-pricing-events"]),
    }
