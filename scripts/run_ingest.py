#!/usr/bin/env python3
"""Run the ingest pipeline from the command line or a container cron job.

Usage:
    python scripts/run_ingest.py           # ingest current month
    python scripts/run_ingest.py 2026-04   # ingest a specific month
"""
import asyncio
import sys
from datetime import datetime

import asyncpg

from api.config import get_settings
from ingest.runner import run_ingest


async def main(month: str | None = None):
    if month is None:
        month = datetime.now().strftime("%Y-%m")

    settings = get_settings()
    pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=2,
        max_size=5,
    )
    try:
        await run_ingest(pool, month, schedule_date=month)
    finally:
        await pool.close()


if __name__ == "__main__":
    month_arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(month_arg))
