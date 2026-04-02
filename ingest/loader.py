"""Load normalised PBS data into the database."""
from decimal import Decimal
from typing import Optional


async def upsert_medicines(conn, medicines: list[dict]) -> dict[str, str]:
    """Upsert medicines and return {ingredient_lower: id} mapping."""
    medicine_ids = {}
    for med in medicines:
        medicine_id = await conn.fetchval(
            """
            INSERT INTO medicines (ingredient, ingredient_lower, atc_code, therapeutic_group, therapeutic_subgroup)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ingredient_lower) DO UPDATE
              SET atc_code = EXCLUDED.atc_code,
                  therapeutic_group = EXCLUDED.therapeutic_group,
                  therapeutic_subgroup = EXCLUDED.therapeutic_subgroup,
                  updated_at = NOW()
            RETURNING id
            """,
            med["ingredient"],
            med["ingredient_lower"],
            med.get("atc_code"),
            med.get("therapeutic_group"),
            med.get("therapeutic_subgroup"),
        )
        medicine_ids[med["ingredient_lower"]] = str(medicine_id)
    return medicine_ids


async def insert_items(conn, items: list[dict], schedule_id: str, medicine_ids: dict[str, str]) -> dict[str, str]:
    """Insert items and return {pbs_code: item_id} mapping."""
    item_ids = {}
    for item in items:
        ingredient_lower = item.get("ingredient_lower", "")
        medicine_id = medicine_ids.get(ingredient_lower)
        if medicine_id is None:
            continue

        item_id = await conn.fetchval(
            """
            INSERT INTO items (
                pbs_code, schedule_id, medicine_id, brand_name, brand_name_lower,
                form, strength, pack_size, pack_unit, benefit_type, formulary,
                section, program_code, general_charge, concessional_charge,
                government_price, brand_premium, brand_premium_counts_to_safety_net,
                sixty_day_eligible, max_quantity, max_repeats, dangerous_drug
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
            )
            ON CONFLICT (pbs_code, schedule_id) DO UPDATE SET
                brand_name = EXCLUDED.brand_name,
                brand_name_lower = EXCLUDED.brand_name_lower,
                benefit_type = EXCLUDED.benefit_type,
                general_charge = EXCLUDED.general_charge,
                concessional_charge = EXCLUDED.concessional_charge,
                government_price = EXCLUDED.government_price,
                brand_premium = EXCLUDED.brand_premium,
                sixty_day_eligible = EXCLUDED.sixty_day_eligible
            RETURNING id
            """,
            item["pbs_code"],
            schedule_id,
            medicine_id,
            item["brand_name"],
            item["brand_name_lower"],
            item.get("form"),
            item.get("strength"),
            item.get("pack_size"),
            item.get("pack_unit"),
            item["benefit_type"],
            item.get("formulary"),
            item.get("section"),
            item.get("program_code"),
            item.get("general_charge"),
            item.get("concessional_charge"),
            item.get("government_price"),
            item.get("brand_premium", Decimal("0.00")),
            item.get("brand_premium_counts_to_safety_net", False),
            item.get("sixty_day_eligible", False),
            item.get("max_quantity"),
            item.get("max_repeats"),
            item.get("dangerous_drug", False),
        )
        item_ids[item["pbs_code"]] = str(item_id)
    return item_ids


async def insert_restrictions(conn, items: list[dict], item_ids: dict[str, str]):
    """Insert restrictions for each item."""
    for item in items:
        pbs_code = item["pbs_code"]
        item_id = item_ids.get(pbs_code)
        if item_id is None:
            continue
        # Delete existing restrictions for this item before inserting
        await conn.execute("DELETE FROM restrictions WHERE item_id = $1", item_id)
        for r in item.get("restrictions", []):
            await conn.execute(
                """
                INSERT INTO restrictions (item_id, streamlined_code, indication, restriction_text, prescriber_type, authority_required, continuation_only)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                item_id,
                r.get("streamlined_code"),
                r.get("indication"),
                r.get("restriction_text"),
                r.get("prescriber_type"),
                r.get("authority_required", False),
                r.get("continuation_only", False),
            )


async def insert_changes(conn, changes: list[dict], schedule_id: str):
    """Insert change records."""
    for change in changes:
        await conn.execute(
            """
            INSERT INTO changes (schedule_id, pbs_code, change_type, field_name, old_value, new_value)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            schedule_id,
            change["pbs_code"],
            change["change_type"],
            change.get("field_name"),
            change.get("old_value"),
            change.get("new_value"),
        )


async def deactivate_removed_medicines(conn, schedule_id: str):
    """Mark medicines as inactive if they have no items in the new schedule."""
    await conn.execute(
        """
        UPDATE medicines SET is_active = FALSE
        WHERE id NOT IN (
            SELECT DISTINCT medicine_id FROM items WHERE schedule_id = $1
        )
        """,
        schedule_id,
    )


async def update_schedule_counts(conn, schedule_id: str, item_count: int, change_count: int):
    """Update item and change counts on the schedule."""
    await conn.execute(
        "UPDATE schedules SET item_count = $1, change_count = $2 WHERE id = $3",
        item_count, change_count, schedule_id,
    )


async def load_to_database(pool, month: str, normalised: dict, changes: list[dict]):
    """Load normalised data to the database in a single transaction (reusing existing conn if passed)."""
    async with pool.acquire() as conn:
        schedule_id = await conn.fetchval(
            "SELECT id FROM schedules WHERE month = $1", month
        )
        if schedule_id is None:
            raise ValueError(f"Schedule for month {month} not found in database")

        schedule_id = str(schedule_id)

        medicine_ids = await upsert_medicines(conn, normalised["medicines"])
        item_ids = await insert_items(conn, normalised["items"], schedule_id, medicine_ids)
        await insert_restrictions(conn, normalised["items"], item_ids)
        await insert_changes(conn, changes, schedule_id)
        await update_schedule_counts(conn, schedule_id, len(item_ids), len(changes))
