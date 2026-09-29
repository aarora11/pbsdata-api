#!/usr/bin/env python3
"""Rebuild the on-disk fetch cache from the raw landing layer.

The raw layer (Postgres, plus compressed archives of older months) stores every row of every feed exactly as PBS served it, reconciled
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

from ingest.raw_archive import archived_months, load_raw_endpoint
from ingest.raw_store import FEED_ENDPOINTS

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")
CACHE = Path(os.environ.get("PBS_CACHE_DIR", "/tmp/pbs_raw_cache"))


async def rebuild(conn, month: str) -> int:
    out = CACHE / month
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for cache_name, endpoint in FEED_ENDPOINTS.items():
        # Postgres for recent months, the compressed archive for older ones.
        payloads = await load_raw_endpoint(conn, month, endpoint)
        if not payloads:
            print(f"  {cache_name:<36} MISSING ({endpoint})")
            continue
        (out / f"{cache_name}.json").write_text(json.dumps(payloads))
        print(f"  {cache_name:<36} {len(payloads):>7,} rows")
        written += 1
    return written


async def main(months: list[str]) -> int:
    conn = await asyncpg.connect(DSN)
    try:
        if not months:
            held = {r["month"] for r in await conn.fetch(
                "SELECT DISTINCT month FROM raw_feed_rows"
            )}
            months = sorted(held | set(archived_months()))
        if not months:
            print("No months in raw_feed_rows or the raw archive — nothing to rebuild.")
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
