#!/usr/bin/env python3
"""Tier A join validation CLI: check our materialised joins against PBS's own answer.

The comparison itself lives in `ingest/join_validation.py`, which the post-ingest
quality gate also runs — so join drift fails an ingest rather than waiting for someone
to run this. This script is for inspecting the detail across arbitrary months.

Usage:
    python scripts/validate_tier_a_joins.py [month ...]
With no months, validates every month held in the raw landing layer.
Exits non-zero if any join mismatches, or if nothing could be validated.
"""
import asyncio
import os
import sys

import asyncpg

from ingest.join_validation import load_overview, validate_month

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")


async def main(months):
    conn = await asyncpg.connect(DSN)
    try:
        if not months:
            months = [r["month"] for r in await conn.fetch(
                "SELECT DISTINCT month FROM raw_feed_rows WHERE endpoint = '/item-overview' "
                "ORDER BY month"
            )]
        failed = False
        no_data = []
        validated = 0

        for month in months:
            overview = await load_overview(conn, month)
            if not overview:
                # Not "skipping": a validator that silently validates nothing and then
                # reports success is worse than no validator at all.
                print(f"{month}: *** NO /item-overview DATA — CANNOT VALIDATE ***")
                no_data.append(month)
                continue
            validated += 1
            print(f"\n=== {month} " + "=" * 62)
            print(f"{'join':<38}{'PBS':>9}{'ours':>9}{'missing':>9}{'extra':>8}   verdict")
            print("-" * 82)
            for res in await validate_month(conn, month, overview=overview):
                failed |= not res.ok
                print(f"{res.name:<38}{res.expected:>9,}{res.actual:>9,}"
                      f"{res.missing:>9,}{res.extra:>8,}   "
                      f"{'MATCH' if res.ok else '*** MISMATCH ***'}")
                if res.sample_missing:
                    print(f"    missing e.g. {res.sample_missing}")
                if res.sample_extra:
                    print(f"    extra   e.g. {res.sample_extra}")
    finally:
        await conn.close()

    if failed:
        print("\nSOME JOINS DISAGREE WITH PBS")
    elif validated:
        print(f"\nALL TIER A JOINS MATCH PBS'S OWN PRE-JOINED VIEW "
              f"({validated} month{'s' if validated != 1 else ''} validated)")
    else:
        print("\nNOTHING WAS VALIDATED — no month had /item-overview data")
    if no_data:
        print(f"NOT VALIDATED: {', '.join(no_data)}")
    return 1 if (failed or no_data or not validated) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
