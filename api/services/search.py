"""Fuzzy search service using pg_trgm."""
from typing import Optional


async def search_medicines(
    db,
    q: Optional[str],
    sixty_day: Optional[bool],
    page: int,
    limit: int,
    schedule: Optional[str],
) -> tuple[list[dict], int]:
    """
    Search medicines with optional fuzzy query, filters, and pagination.
    Returns (items, total_count).
    """
    conditions = []
    params = []
    param_idx = 1

    if sixty_day is not None:
        conditions.append(f"i.sixty_day_eligible = ${param_idx}")
        params.append(sixty_day)
        param_idx += 1

    if q:
        conditions.append(
            f"(m.ingredient_lower ILIKE ${param_idx} "
            f"OR m.ingredient_lower % ${param_idx + 1} "
            f"OR EXISTS (SELECT 1 FROM items it2 WHERE it2.medicine_id = m.id AND it2.brand_name_lower ILIKE ${param_idx + 2} AND it2.is_active = TRUE))"
        )
        params.extend([f"%{q.lower()}%", q.lower(), f"%{q.lower()}%"])
        param_idx += 3

    where_clause = f"AND {' AND '.join(conditions)}" if conditions else ""

    count_sql = f"""
        WITH latest_schedule AS (
            SELECT id FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1
        )
        SELECT COUNT(DISTINCT m.id)
        FROM medicines m
        JOIN items i ON i.medicine_id = m.id
        JOIN latest_schedule ls ON i.schedule_id = ls.id
        WHERE m.is_active = TRUE AND i.is_active = TRUE
        {where_clause}
    """

    data_sql = f"""
        WITH latest_schedule AS (
            SELECT id FROM schedules WHERE ingest_status = 'complete' ORDER BY month DESC LIMIT 1
        )
        SELECT
            m.id,
            m.ingredient,
            m.ingredient_lower,
            m.atc_code,
            m.therapeutic_group,
            m.therapeutic_subgroup,
            COUNT(DISTINCT i.id) AS item_count,
            BOOL_OR(i.sixty_day_eligible) AS sixty_day_eligible,
            COUNT(DISTINCT CASE WHEN i.brand_name_lower != m.ingredient_lower THEN i.id END) > 0 AS has_generic
        FROM medicines m
        JOIN items i ON i.medicine_id = m.id
        JOIN latest_schedule ls ON i.schedule_id = ls.id
        WHERE m.is_active = TRUE AND i.is_active = TRUE
        {where_clause}
        GROUP BY m.id, m.ingredient, m.ingredient_lower, m.atc_code, m.therapeutic_group, m.therapeutic_subgroup
        ORDER BY m.ingredient_lower
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
    """
    offset = (page - 1) * limit
    data_params = params + [limit, offset]

    total = await db.fetchval(count_sql, *params)
    rows = await db.fetch(data_sql, *data_params)

    return [dict(r) for r in rows], total or 0
