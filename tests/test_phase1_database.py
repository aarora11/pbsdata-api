"""Phase 1 tests: database connection and schema verification."""
import pytest


REQUIRED_TABLES = [
    "schedules",
    "medicines",
    "items",
    "restrictions",
    "changes",
    "api_keys",
    "webhooks",
    "webhook_delivery_log",
]

REQUIRED_EXTENSIONS = ["pgcrypto", "pg_trgm"]


@pytest.mark.asyncio
async def test_database_connection(db):
    """Database connection is established and working."""
    result = await db.fetchval("SELECT 1")
    assert result == 1


@pytest.mark.asyncio
async def test_extensions_installed(db):
    """Required Postgres extensions are installed."""
    for ext in REQUIRED_EXTENSIONS:
        result = await db.fetchval(
            "SELECT COUNT(*) FROM pg_extension WHERE extname = $1", ext
        )
        assert result == 1, f"Extension '{ext}' is not installed"


@pytest.mark.asyncio
async def test_all_tables_exist(db):
    """All required tables exist in the database."""
    for table in REQUIRED_TABLES:
        result = await db.fetchval(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = $1
            """,
            table,
        )
        assert result == 1, f"Table '{table}' does not exist"


@pytest.mark.asyncio
async def test_schedules_columns(db):
    """Schedules table has all required columns."""
    cols = await db.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'schedules' AND table_schema = 'public'
        ORDER BY ordinal_position
        """
    )
    col_names = [c["column_name"] for c in cols]
    for required in ["id", "month", "released_at", "is_embargo", "ingest_status"]:
        assert required in col_names, f"Column '{required}' missing from schedules"


@pytest.mark.asyncio
async def test_items_columns(db):
    """Items table has all required columns including pricing fields."""
    cols = await db.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'items' AND table_schema = 'public'
        """
    )
    col_names = [c["column_name"] for c in cols]
    for required_col in [
        "pbs_code", "schedule_id", "medicine_id", "brand_name",
        "brand_name_lower", "benefit_type", "general_charge",
        "concessional_charge", "government_price", "brand_premium",
        "brand_premium_counts_to_safety_net", "sixty_day_eligible",
        "max_quantity", "max_repeats", "is_active",
    ]:
        assert required_col in col_names, f"Column '{required_col}' missing from items"


@pytest.mark.asyncio
async def test_api_keys_columns(db):
    """API keys table has all required columns."""
    cols = await db.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'api_keys' AND table_schema = 'public'
        """
    )
    col_names = [c["column_name"] for c in cols]
    for required_col in [
        "id", "key_prefix", "key_hash", "name", "customer_email",
        "tier", "monthly_limit", "requests_this_month", "is_active",
    ]:
        assert required_col in col_names, f"Column '{required_col}' missing from api_keys"


@pytest.mark.asyncio
async def test_unique_constraint_on_schedule_month(db):
    """Unique constraint prevents duplicate schedule months."""
    await db.execute(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2026-01', NOW(), 'complete')"
    )
    with pytest.raises(Exception) as exc_info:
        await db.execute(
            "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2026-01', NOW(), 'complete')"
        )
    assert "unique" in str(exc_info.value).lower() or "duplicate" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_brand_premium_defaults_to_false(db):
    """brand_premium_counts_to_safety_net defaults to FALSE — this is a PBS rule."""
    schedule_id = await db.fetchval(
        "INSERT INTO schedules (month, released_at, ingest_status) VALUES ('2026-02', NOW(), 'complete') RETURNING id"
    )
    medicine_id = await db.fetchval(
        "INSERT INTO medicines (ingredient, ingredient_lower) VALUES ('Test Drug', 'test drug') RETURNING id"
    )
    item_id = await db.fetchval(
        """
        INSERT INTO items (pbs_code, schedule_id, medicine_id, brand_name, brand_name_lower, benefit_type)
        VALUES ('0001A', $1, $2, 'TestBrand', 'testbrand', 'unrestricted')
        RETURNING id
        """,
        schedule_id, medicine_id,
    )
    row = await db.fetchrow(
        "SELECT brand_premium_counts_to_safety_net FROM items WHERE id = $1", item_id
    )
    assert row["brand_premium_counts_to_safety_net"] is False


@pytest.mark.asyncio
async def test_trigram_indexes_exist(db):
    """Trigram indexes exist on searchable columns."""
    indexes = await db.fetch(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename IN ('medicines', 'items')
        AND indexdef LIKE '%gin_trgm_ops%'
        """
    )
    index_names = [r["indexname"] for r in indexes]
    assert any("trgm" in name for name in index_names), \
        f"No trigram indexes found. Indexes: {index_names}"
