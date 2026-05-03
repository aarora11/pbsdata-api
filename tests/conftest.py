import pytest
import asyncio
import asyncpg
import pytest_asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from api.config import get_settings


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=2, max_size=5)
    migrations_dir = Path(__file__).parent.parent / "migrations"
    async with pool.acquire() as conn:
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            await conn.execute(sql_file.read_text())
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def db(db_pool):
    """Per-test DB connection with transaction rollback."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()


@pytest_asyncio.fixture
async def app_client(db_pool):
    """Async HTTP test client. Uses committed transactions so app can see test data."""
    from api.main import app
    import api.database as db_module
    db_module._pool = db_pool
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
