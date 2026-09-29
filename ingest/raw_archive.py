"""Move older months of the raw landing layer out of Postgres into compressed files.

raw_feed_rows stores every row of every feed, every month: ~345 MB per schedule in
Postgres, three quarters of the database. Only the newest months are ever read hot —
the quality gate reconciles the month just ingested, and the join check reads that
month's /item-overview. Older months exist for replay, which reads them once.

So after a schedule passes its gate, every month beyond the newest RAW_KEEP_MONTHS is
written to `{month}.jsonl.gz` (~26 MB, about 18x smaller), read back and checked
row for row against the table, and only then deleted from it. A month that fails
verification stays in Postgres. Nothing is deleted on the strength of a write alone.

The raw layer's promise is that it equals what PBS served. The archive keeps that
promise only as long as the files survive: PBS_RAW_ARCHIVE_DIR must be durable storage,
not /tmp — the fetch cache there has been lost once already.
"""
import gzip
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import structlog

logger = structlog.get_logger()

ARCHIVE_DIR = Path(os.environ.get(
    "PBS_RAW_ARCHIVE_DIR",
    Path(__file__).resolve().parent.parent / "data" / "raw_archive",
))
KEEP_MONTHS = int(os.environ.get("PBS_RAW_KEEP_MONTHS", "2"))

_SELECT_MONTH = (
    "SELECT endpoint, row_index, schedule_code, fetched_at, payload::text AS payload "
    "FROM raw_feed_rows WHERE month = $1 ORDER BY endpoint, row_index"
)


def archive_path(month: str, archive_dir: Path | None = None) -> Path:
    return (archive_dir or ARCHIVE_DIR) / f"{month}.jsonl.gz"


def archived_months(archive_dir: Path | None = None) -> list[str]:
    d = archive_dir or ARCHIVE_DIR
    return sorted(p.name.removesuffix(".jsonl.gz") for p in d.glob("*.jsonl.gz"))


def _digest_update(h, endpoint: str, row_index: int, payload_text: str) -> None:
    h.update(f"{endpoint}\t{row_index}\t{payload_text}\n".encode())


def iter_archive(month: str, archive_dir: Path | None = None):
    """Yield the archived rows for a month, in (endpoint, row_index) order.

    `payload` is Postgres's jsonb text, kept as a string rather than nested JSON so it
    round-trips exactly: parsing would turn a PBS price of 25.00 into 25.0.
    """
    with gzip.open(archive_path(month, archive_dir), "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def load_endpoint(month: str, endpoint: str, archive_dir: Path | None = None) -> list | None:
    """Payloads for one endpoint from the archive, or None if the month isn't archived."""
    if not archive_path(month, archive_dir).exists():
        return None
    return [json.loads(r["payload"]) for r in iter_archive(month, archive_dir)
            if r["endpoint"] == endpoint]


async def load_raw_endpoint(conn, month: str, endpoint: str) -> list:
    """Payloads for one endpoint, from Postgres if held there, else from the archive."""
    rows = await conn.fetch(
        "SELECT payload FROM raw_feed_rows WHERE month = $1 AND endpoint = $2 "
        "ORDER BY row_index",
        month, endpoint,
    )
    if rows:
        return [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
                for r in rows]
    return load_endpoint(month, endpoint) or []


async def archive_month(conn, month: str, archive_dir: Path | None = None) -> int:
    """Write a month to its archive file and verify it. Does not delete anything.

    Returns the number of rows archived. Raises if the file does not read back
    identical to the table, leaving any previous archive for that month untouched.
    """
    path = archive_path(month, archive_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".gz.tmp")

    table_digest = hashlib.sha256()
    count = 0
    async with conn.transaction():
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            async for r in conn.cursor(_SELECT_MONTH, month, prefetch=5000):
                f.write(json.dumps({
                    "endpoint": r["endpoint"],
                    "row_index": r["row_index"],
                    "schedule_code": r["schedule_code"],
                    "fetched_at": r["fetched_at"].isoformat(),
                    "payload": r["payload"],
                }, ensure_ascii=False) + "\n")
                _digest_update(table_digest, r["endpoint"], r["row_index"], r["payload"])
                count += 1
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())

    if count == 0:
        tmp.unlink()
        raise ValueError(f"raw_feed_rows holds no rows for {month}; nothing to archive")

    file_digest = hashlib.sha256()
    file_count = 0
    with gzip.open(tmp, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            _digest_update(file_digest, r["endpoint"], r["row_index"], r["payload"])
            file_count += 1

    if file_count != count or file_digest.digest() != table_digest.digest():
        tmp.unlink()
        raise ValueError(
            f"archive of {month} did not read back identical to raw_feed_rows "
            f"({file_count} of {count} rows); nothing was deleted"
        )

    os.replace(tmp, path)
    return count


async def prune_raw_layer(conn, keep: int = KEEP_MONTHS,
                          archive_dir: Path | None = None) -> list[str]:
    """Archive and delete every month in raw_feed_rows beyond the newest `keep`.

    Returns the months moved. Each month is archived and verified before its rows
    are deleted; a failure leaves that month in Postgres and stops the run.
    """
    months = [r["month"] for r in await conn.fetch(
        "SELECT DISTINCT month FROM raw_feed_rows ORDER BY month DESC"
    )]
    moved = []
    for month in months[keep:]:
        rows = await archive_month(conn, month, archive_dir)
        async with conn.transaction():
            deleted = await conn.fetchval(
                "WITH d AS (DELETE FROM raw_feed_rows WHERE month = $1 RETURNING 1) "
                "SELECT count(*) FROM d",
                month,
            )
            if deleted != rows:
                # Rows landed between archive and delete. Roll back; the next run
                # re-archives the month with them included.
                raise ValueError(
                    f"{month}: archived {rows} rows but would delete {deleted}; rolled back"
                )
        logger.info("raw_archive.moved", month=month, rows=rows,
                    path=str(archive_path(month, archive_dir)))
        moved.append(month)
    return moved


async def restore_month(conn, month: str, archive_dir: Path | None = None) -> int:
    """Load an archived month back into raw_feed_rows, replacing any rows held for it."""
    records = [
        (month, r["schedule_code"], r["endpoint"], r["row_index"], r["payload"],
         datetime.fromisoformat(r["fetched_at"]))
        for r in iter_archive(month, archive_dir)
    ]
    async with conn.transaction():
        await conn.execute("DELETE FROM raw_feed_rows WHERE month = $1", month)
        await conn.copy_records_to_table(
            "raw_feed_rows", records=records,
            columns=["month", "schedule_code", "endpoint", "row_index", "payload",
                     "fetched_at"],
        )
    return len(records)
