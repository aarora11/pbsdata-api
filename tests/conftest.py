import pytest
import asyncio
import asyncpg
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from api.config import get_settings


@pytest.fixture(scope="session")
async def db_pool():
    """Session-scoped database pool. Runs migrations once."""
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=2, max_size=5)

    # Run all migrations
    migrations_dir = Path(__file__).parent.parent / "migrations"
    async with pool.acquire() as conn:
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            await conn.execute(sql_file.read_text())

    yield pool
    await pool.close()


@pytest.fixture
async def db(db_pool):
    """Per-test database connection wrapped in a transaction that rolls back."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()


@pytest.fixture
async def app_client(db_pool):
    """Async HTTP test client for the FastAPI app."""
    from api.main import app
    import api.database as db_module

    # Point the app pool at our test pool
    db_module._pool = db_pool

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
