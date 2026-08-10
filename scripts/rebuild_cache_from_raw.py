#!/usr/bin/env python3
"""Rebuild the on-disk fetch cache from the raw landing layer.

The raw layer stores every row of every feed exactly as PBS served it, reconciled
against the fetch on each ingest. That makes it a complete substitute for the fetch
cache, which is the point of holding it: normalisation and join changes can be
replayed over real data without re-hitting the PBS API.

Useful because the fetch cache defaults to /tmp and does not survive a reboot, while
a cold re-ingest costs ~30 minutes per month at PBS_REQUEST_DELAY_SECONDS=21.

Usage:
    python scripts/rebuild_cache_from_raw.py [month ...]      # default: all months held
Then re-run the ingest against it:
    PBS_CACHE_DIR=/tmp/pbs_raw_cache python scripts/run_ingest.py 2026-06
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

from ingest.raw_store import FEED_ENDPOINTS

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")
CACHE = Path(os.environ.get("PBS_CACHE_DIR", "/tmp/pbs_raw_cache"))


async def rebuild(conn, month: str) -> int:
    out = CACHE / month
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for cache_name, endpoint in FEED_ENDPOINTS.items():
        rows = await conn.fetch(
            "SELECT payload FROM raw_feed_rows WHERE month = $1 AND endpoint = $2 "
            "ORDER BY row_index",
            month, endpoint,
        )
        if not rows:
            print(f"  {cache_name:<36} MISSING ({endpoint})")
            continue
        payloads = [
            json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
            for r in rows
        ]
        (out / f"{cache_name}.json").write_text(json.dumps(payloads))
        print(f"  {cache_name:<36} {len(payloads):>7,} rows")
        written += 1
    return written


async def main(months: list[str]) -> int:
    conn = await asyncpg.connect(DSN)
    try:
        if not months:
            months = [r["month"] for r in await conn.fetch(
                "SELECT DISTINCT month FROM raw_feed_rows ORDER BY month"
            )]
        if not months:
            print("No months in raw_feed_rows — nothing to rebuild.")
            return 1
        for month in months:
            print(f"\n=== {month} -> {CACHE / month}")
            if not await rebuild(conn, month):
                print(f"{month}: no feeds found")
                return 1
    finally:
        await conn.close()
    print(f"\nRebuilt {len(months)} month(s). Re-ingest with PBS_CACHE_DIR={CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
