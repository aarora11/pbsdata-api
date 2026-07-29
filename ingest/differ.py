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
    """Previous schedule's items, keyed by li_item_id (brand grain).

    `items` is keyed on (li_item_id, schedule_id) to match PBS's ITEM_T, so a
    PBS code appears once per brand. Keying this map on pbs_code kept whichever
    brand the query happened to return last and compared every current brand
    against that one row — which reported a price change whenever the winning
    brand differed between months, even though nothing had moved. Measured on
    2026-06: 246 of 295 government_price changes were that artifact.
    """
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
            SELECT li_item_id, pbs_code, general_charge, concessional_charge,
                   government_price, brand_premium, sixty_day_eligible,
                   benefit_type, max_quantity, max_repeats
            FROM items
            WHERE schedule_id = $1 AND li_item_id IS NOT NULL
            """,
            prev_schedule["id"],
        )
        return {row["li_item_id"]: dict(row) for row in rows}


async def compute_changes(pool, normalised: dict, month: str) -> list[dict]:
    """
    Compare normalised items against the previous schedule.
    Returns a list of change records.
    """
    prev = await get_previous_items(pool, month)
    changes = []

    # Price and metadata are compared brand-to-brand on li_item_id, because that
    # is the grain PBS publishes them at. Listings entering and leaving the
    # schedule stay at pbs_code grain — a brand appearing under an existing PBS
    # code is not a new listing — and are emitted once per code, not once per
    # brand.
    prev_codes = {row["pbs_code"] for row in prev.values()}
    current_codes = set()
    reported_new: set[str] = set()

    for item in normalised["items"]:
        code = item["pbs_code"]
        li_item_id = item.get("li_item_id")
        current_codes.add(code)

        old = prev.get(li_item_id) if li_item_id else None

        if old is None:
            # This brand was not present last month. Only a genuinely new PBS
            # code counts as a new listing.
            if code not in prev_codes and code not in reported_new:
                reported_new.add(code)
                changes.append({"pbs_code": code, "change_type": "new", "field_name": None, "old_value": None, "new_value": None})
        else:
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

    # Detect delisted listings — once per PBS code that has left the schedule
    # entirely, not once per brand that disappeared from it.
    for code in sorted(prev_codes - current_codes):
        changes.append({"pbs_code": code, "change_type": "delisted", "field_name": None, "old_value": None, "new_value": None})

    return changes
