"""Raw landing layer: store every PBS feed row verbatim, before interpretation.

Every defect found in the 2026-07 re-ingest was a lossy mapping between fetching
a feed and storing it, not a failure to fetch. Landing the source records intact
makes extraction correctness independent of mapping correctness — a mapping bug
becomes a re-derivation against data already held, not data loss.

Rows are written from the on-disk fetch cache rather than re-requested, so this
costs no additional PBS API calls and is guaranteed to match what was fetched.
"""
import json

import structlog

logger = structlog.get_logger()


# Internal cache name -> the PBS endpoint path it was fetched from.
#
# Several cache entries share an endpoint: `item_pricing` and
# `item_dispensing_rules` are both /item-dispensing-rule-relationships, and
# `item_amt` is a second copy of /amt-items. The raw layer stores one copy per
# endpoint — duplicates here are aliases, not separate sources.
FEED_ENDPOINTS = {
    "items": "/items",
    "restrictions": "/restrictions",
    "fees": "/fees",
    "prescribing_texts": "/prescribing-texts",
    "indications": "/indications",
    "amt_items": "/amt-items",
    "program_dispensing_rules": "/dispensing-rules",
    "organisations": "/organisations",
    "programs": "/programs",
    "copayments": "/copayments",
    "atc_codes": "/atc-codes",
    "item_dispensing_rules": "/item-dispensing-rule-relationships",
    "item_restriction_rels": "/item-restriction-relationships",
    "restriction_prescribing_text_rels": "/restriction-prescribing-text-relationships",
    "item_prescribing_texts": "/item-prescribing-text-relationships",
    "item_atc_relationships": "/item-atc-relationships",
    "item_organisation_relationships": "/item-organisation-relationships",
    "summary_of_changes": "/summary-of-changes",
    "containers": "/containers",
    "container_org_rels": "/container-organisation-relationships",
    "criteria": "/criteria",
    "criteria_parameter_rels": "/criteria-parameter-relationships",
    "parameters": "/parameters",
    "prescribers": "/prescribers",
    "markup_bands": "/markup-bands",
    "item_pricing_events": "/item-pricing-events",
    "extemporaneous_ingredients": "/extemporaneous-ingredients",
    "extemporaneous_preparations": "/extemporaneous-preparations",
    "extemporaneous_prep_sfp_rels": "/extemporaneous-prep-sfp-relationships",
    "extemporaneous_tariffs": "/extemporaneous-tariffs",
    "standard_formula_preparations": "/standard-formula-preparations",
    "item_pricing": "/item-dispensing-rule-relationships",
    "item_overviews": "/item-overview",
    "item_amt": "/amt-items",
    "medication_charts": "/medication-charts",
    "item_chart_eligibilities": "/item-chart-eligibilities",
    # Not schedule-scoped: the catalogue of all PBS schedules, snapshotted at
    # ingest time so the raw layer covers every endpoint we call.
    "schedules": "/schedules",
}


def endpoint_sources() -> dict[str, str]:
    """Return {pbs_endpoint: cache_name}, one cache entry per distinct endpoint."""
    chosen: dict[str, str] = {}
    for cache_name, endpoint in FEED_ENDPOINTS.items():
        chosen.setdefault(endpoint, cache_name)
    return chosen


async def store_raw_feeds(pool, month: str, schedule_code, loader) -> dict[str, int]:
    """Write every fetched feed into raw_feed_rows, replacing any prior rows.

    `loader` is a callable taking a cache name and returning the list of source
    records, or None when that feed was not fetched.

    Returns {endpoint: rows_stored}.
    """
    log = logger.bind(month=month)
    stored: dict[str, int] = {}

    async with pool.acquire() as conn:
        # Payloads are passed as JSON text: copy_records_to_table encodes in
        # binary format, where a custom text codec for jsonb does not apply.
        # asyncpg encodes jsonb from str natively.
        for endpoint, cache_name in sorted(endpoint_sources().items()):
            rows = loader(cache_name)
            if rows is None:
                log.warning("raw_store.feed_missing", endpoint=endpoint, cache=cache_name)
                continue

            records = [
                (month, str(schedule_code) if schedule_code is not None else None,
                 endpoint, i, json.dumps(row))
                for i, row in enumerate(rows)
            ]

            # Replace-then-insert, matching the convention every other loader in
            # this codebase uses, so a re-ingest is idempotent.
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM raw_feed_rows WHERE month = $1 AND endpoint = $2",
                    month, endpoint,
                )
                if records:
                    await conn.copy_records_to_table(
                        "raw_feed_rows",
                        records=records,
                        columns=["month", "schedule_code", "endpoint", "row_index", "payload"],
                    )
            stored[endpoint] = len(records)

    total = sum(stored.values())
    log.info("raw_store.complete", endpoints=len(stored), rows=total)
    return stored
