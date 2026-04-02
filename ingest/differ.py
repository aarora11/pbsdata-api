"""Compute changes between the previous and new schedule."""
from decimal import Decimal
from typing import Optional


PRICE_THRESHOLD = Decimal("0.01")

PRICE_FIELDS = ["general_charge", "concessional_charge", "government_price", "brand_premium"]
META_FIELDS = ["sixty_day_eligible", "benefit_type", "max_quantity", "max_repeats"]


def price_changed(old: Optional[Decimal], new: Optional[Decimal]) -> bool:
    """Check if price changed, ignoring sub-cent noise."""
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(old - new) >= PRICE_THRESHOLD


async def get_previous_items(pool, month: str) -> dict[str, dict]:
    """Get items from the most recent schedule before the given month."""
    async with pool.acquire() as conn:
        # Find the most recent completed schedule before this month
        prev_schedule = await conn.fetchrow(
            """
            SELECT id FROM schedules
            WHERE month < $1 AND ingest_status = 'complete'
            ORDER BY month DESC LIMIT 1
            """,
            month,
        )
        if not prev_schedule:
            return {}

        rows = await conn.fetch(
            """
            SELECT pbs_code, general_charge, concessional_charge, government_price,
                   brand_premium, sixty_day_eligible, benefit_type, max_quantity, max_repeats
            FROM items
            WHERE schedule_id = $1
            """,
            prev_schedule["id"],
        )
        return {row["pbs_code"]: dict(row) for row in rows}


async def compute_changes(pool, normalised: dict, month: str) -> list[dict]:
    """
    Compare normalised items against the previous schedule.
    Returns a list of change records.
    """
    prev = await get_previous_items(pool, month)
    changes = []

    current_codes = set()
    for item in normalised["items"]:
        code = item["pbs_code"]
        current_codes.add(code)

        if code not in prev:
            changes.append({"pbs_code": code, "change_type": "new", "field_name": None, "old_value": None, "new_value": None})
        else:
            old = prev[code]
            for field in PRICE_FIELDS:
                old_val = old.get(field)
                new_val = item.get(field)
                if old_val is not None:
                    old_val = Decimal(str(old_val))
                if new_val is not None:
                    new_val = Decimal(str(new_val))
                if price_changed(old_val, new_val):
                    changes.append({
                        "pbs_code": code,
                        "change_type": "price_changed",
                        "field_name": field,
                        "old_value": str(old_val) if old_val is not None else None,
                        "new_value": str(new_val) if new_val is not None else None,
                    })
            for field in META_FIELDS:
                old_val = old.get(field)
                new_val = item.get(field)
                if str(old_val) != str(new_val):
                    changes.append({
                        "pbs_code": code,
                        "change_type": f"{field}_changed",
                        "field_name": field,
                        "old_value": str(old_val),
                        "new_value": str(new_val),
                    })

    # Detect delisted items
    for code in prev:
        if code not in current_codes:
            changes.append({"pbs_code": code, "change_type": "delisted", "field_name": None, "old_value": None, "new_value": None})

    return changes
