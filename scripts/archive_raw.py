#!/usr/bin/env python3
"""Move older raw months out of Postgres into compressed archive files, or back.

The ingest does this automatically after each successful schedule; this is for doing it
by hand, checking archives, and restoring a month. See ingest/raw_archive.py.

Usage:
    python scripts/archive_raw.py                  # archive all but the newest PBS_RAW_KEEP_MONTHS
    python scripts/archive_raw.py --keep 1
    python scripts/archive_raw.py --vacuum         # also return the freed space to the OS
    python scripts/archive_raw.py --list
    python scripts/archive_raw.py --restore 2026-05
"""
import argparse
import asyncio
import os
import sys

import asyncpg

from ingest.raw_archive import (
    ARCHIVE_DIR, KEEP_MONTHS, archive_path, archived_months, prune_raw_layer, restore_month,
)

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")


async def main(args) -> int:
    conn = await asyncpg.connect(DSN)
    try:
        if args.list:
            held = await conn.fetch(
                "SELECT month, count(*) AS n FROM raw_feed_rows GROUP BY month ORDER BY month"
            )
            print("In Postgres:")
            for r in held:
                print(f"  {r['month']}  {r['n']:>9,} rows")
            print(f"Archived ({ARCHIVE_DIR}):")
            for m in archived_months():
                print(f"  {m}  {archive_path(m).stat().st_size / 1e6:>8.1f} MB")
            return 0

        if args.restore:
            n = await restore_month(conn, args.restore)
            print(f"Restored {n:,} rows for {args.restore} into raw_feed_rows")
            return 0

        moved = await prune_raw_layer(conn, keep=args.keep)
        print(f"Archived {', '.join(moved)}" if moved else "Nothing to archive")
        if args.vacuum:
            # A plain DELETE leaves the space for Postgres to reuse; VACUUM FULL rewrites
            # the table to hand it back to the OS. Locks the table while it runs.
            await conn.execute("VACUUM FULL raw_feed_rows")
            await conn.execute("CHECKPOINT")
            print("Vacuumed raw_feed_rows")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=KEEP_MONTHS)
    ap.add_argument("--vacuum", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", metavar="MONTH")
    sys.exit(asyncio.run(main(ap.parse_args())))
