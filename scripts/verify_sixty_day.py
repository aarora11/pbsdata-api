#!/usr/bin/env python3
"""Check our 60-day item codes against PBS's published 60-day list.

pbs.gov.au/browse/60-day-dispensing lists every 60-day item code, one page per initial
letter. It always shows the current schedule, so compare it with the month PBS is on.
Exits non-zero if the two sets differ in either direction.

    python scripts/verify_sixty_day.py 2026-08
"""
import asyncio
import html
import os
import re
import sys
import time
import urllib.request

import asyncpg

DSN = os.environ.get("DATABASE_URL", "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi")
BASE = "https://www.pbs.gov.au/browse/60-day-dispensing"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PBS data verification)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "ignore")


def published_codes():
    first = fetch(BASE)
    letters = sorted(set(re.findall(r"initial=([a-z])", first)))
    codes = set()
    for letter in letters:
        page = fetch(f"{BASE}?initial={letter}")
        table = re.search(r"<table.*?</table>", page, re.S)
        if table:
            for row in re.findall(r"<tr.*?</tr>", table.group(0), re.S):
                cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
                         for c in re.findall(r"<t[hd].*?</t[hd]>", row, re.S)]
                if len(cells) > 1 and re.fullmatch(r"\d{1,5}[A-Z]", cells[1]):
                    codes.add(cells[1])
        time.sleep(2)  # a public website: be polite
    return codes


async def ours(month):
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT i.pbs_code FROM items i JOIN schedules s ON s.id = i.schedule_id "
            "WHERE s.month = $1 AND i.imdq60", month)
    finally:
        await conn.close()
    return {r["pbs_code"] for r in rows}


def main(month):
    held, pub = asyncio.run(ours(month)), published_codes()
    print(f"{month}: ours {len(held)}, PBS {len(pub)}, both {len(held & pub)}")
    only_ours, only_pbs = sorted(held - pub), sorted(pub - held)
    if only_ours:
        print("Only ours:", " ".join(only_ours[:40]))
    if only_pbs:
        print("Only on PBS:", " ".join(only_pbs[:40]))
    return 0 if not only_ours and not only_pbs and held else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
