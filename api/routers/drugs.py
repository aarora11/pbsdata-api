"""Drugs router — T2 Clinical endpoints for GET /v1/drugs/{pbs_code}/..."""
from fastapi import APIRouter, Depends, Response, HTTPException, Query
from api.middleware.tier import require_tier, tier_label
from api.database import get_db
from typing import Optional

router = APIRouter(tags=["drugs"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


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
        "schedule_code": schedule_month,
        "tier": tier_label(key_data),
        "join_sources": join_sources,
    }


# ── 2.1  Drug identity ─────────────────────────────────────────────────────────

@router.get("/drugs/{pbs_code}")
async def get_drug(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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
        },
        "meta": _meta(api_key_data, schedule_month, [
            "/items", "/programs", "/organisations", "/atc-codes", "/prescribers",
        ]),
    }


# ── 2.2  Brands ────────────────────────────────────────────────────────────────

@router.get("/drugs/{pbs_code}/brands")
async def get_drug_brands(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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

@router.get("/drugs/{pbs_code}/prescribers")
async def get_drug_prescribers(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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

@router.get("/drugs/{pbs_code}/atc")
async def get_drug_atc(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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

@router.get("/drugs/{pbs_code}/amt")
async def get_drug_amt(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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

@router.get("/drugs/{pbs_code}/restrictions")
async def get_drug_restrictions(
    pbs_code: str,
    response: Response,
    schedule: Optional[str] = Query(None),
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
