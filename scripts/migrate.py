#!/usr/bin/env python3
"""Run all database migrations in order."""
import asyncio
import sys
from pathlib import Path
import asyncpg
from api.config import get_settings


async def run_migrations():
    settings = get_settings()
    migrations_dir = Path(__file__).parent.parent / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))

    if not sql_files:
        print("No migration files found.")
        return

    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        for sql_file in sql_files:
            print(f"Running migration: {sql_file.name}")
            sql = sql_file.read_text()
            await conn.execute(sql)
            print(f"  OK: {sql_file.name}")
    finally:
        await conn.close()

    print(f"\nAll {len(sql_files)} migrations completed.")


if __name__ == "__main__":
    asyncio.run(run_migrations())
