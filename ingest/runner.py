"""Orchestrate the full ingest pipeline."""
import structlog
from datetime import datetime
from ingest.pbs_client import PBSClient
from ingest.normaliser import normalise_schedule
from ingest.differ import compute_changes
from ingest.loader import load_to_database

logger = structlog.get_logger()


async def run_ingest(pool, month: str, schedule_date: str, is_embargo: bool = False):
    """
    Run the full ingest pipeline for a given PBS schedule month.

    Args:
        pool: asyncpg connection pool
        month: Schedule month in YYYY-MM format (e.g., "2026-04")
        schedule_date: Date string for PBS API (e.g., "2026-04-01")
        is_embargo: Whether this is an embargo (pre-release) schedule
    """
    log = logger.bind(month=month)

    # Mark schedule as running
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO schedules (month, released_at, is_embargo, ingest_status, ingest_started_at)
            VALUES ($1, NOW(), $2, 'running', NOW())
            ON CONFLICT (month) DO UPDATE SET
                ingest_status = 'running',
                ingest_started_at = NOW()
            """,
            month, is_embargo,
        )

    try:
        log.info("ingest.start")

        # Fetch from PBS API
        client = PBSClient()
        try:
            raw_items = await client.get_all_items(schedule_date)
            raw_restrictions = await client.get_all_restrictions(schedule_date)
        finally:
            await client.close()

        log.info("ingest.fetched", items=len(raw_items), restrictions=len(raw_restrictions))

        # Normalise
        normalised = normalise_schedule(month, raw_items, raw_restrictions)
        log.info("ingest.normalised", medicines=len(normalised["medicines"]), items=len(normalised["items"]))

        # Compute changes
        changes = await compute_changes(pool, normalised, month)
        log.info("ingest.diffed", changes=len(changes))

        # Load to database
        await load_to_database(pool, month, normalised, changes)
        log.info("ingest.loaded")

        # Mark complete
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE schedules
                SET ingest_status = 'complete', ingest_completed_at = NOW()
                WHERE month = $1
                """,
                month,
            )

        log.info("ingest.complete")
        log.info("webhooks.stub", note="Webhook delivery would be enqueued here")

    except Exception as exc:
        log.error("ingest.failed", error=str(exc))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET ingest_status = 'failed' WHERE month = $1",
                month,
            )
        raise
