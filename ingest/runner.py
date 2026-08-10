"""Orchestrate the full ingest pipeline."""
import asyncio
import json
import os
import structlog
from datetime import datetime, date
from pathlib import Path
from ingest.pbs_client import PBSClient
from ingest.normaliser import normalise_schedule
from ingest.differ import compute_changes
from ingest.loader import load_to_database
from ingest.quality import assert_schedule_usable
from ingest.raw_store import store_raw_feeds

logger = structlog.get_logger()

# Default location for raw API response cache
DEFAULT_CACHE_DIR = Path(os.environ.get("PBS_CACHE_DIR", "/tmp/pbs_raw_cache"))


def _cache_path(cache_dir: Path, month: str) -> Path:
    return cache_dir / month


def _save_raw(cache_dir: Path, month: str, name: str, data: list) -> None:
    path = _cache_path(cache_dir, month)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.json").write_text(json.dumps(data))


def _load_raw(cache_dir: Path, month: str, name: str) -> list | None:
    p = _cache_path(cache_dir, month) / f"{name}.json"
    if p.exists():
        return json.loads(p.read_text())
    return None


# Every raw feed the fetch phase writes to the cache. Used both to decide
# whether a cached month is complete and to skip already-fetched feeds when a
# run is resumed after a failure.
RAW_FEEDS = (
    "items", "restrictions", "fees", "prescribing_texts", "indications",
    "amt_items", "program_dispensing_rules", "organisations", "programs",
    "copayments", "atc_codes", "item_dispensing_rules", "item_restriction_rels",
    "restriction_prescribing_text_rels", "item_prescribing_texts",
    "item_atc_relationships", "item_organisation_relationships",
    "summary_of_changes", "containers", "container_org_rels", "criteria",
    "criteria_parameter_rels", "parameters", "prescribers", "markup_bands",
    "item_pricing_events", "extemporaneous_ingredients",
    "extemporaneous_preparations", "extemporaneous_prep_sfp_rels",
    "extemporaneous_tariffs", "standard_formula_preparations",
    "item_pricing", "item_overviews", "item_amt",
    "medication_charts", "item_chart_eligibilities", "schedules",
)


def _cache_exists(cache_dir: Path, month: str) -> bool:
    """
    Return True only if a *complete* cache exists for this month.

    Every feed must be present. A partial cache (one feed 400s partway through a
    run) must not be treated as a hit — that would silently ingest a schedule
    missing whichever feeds had not been fetched yet.
    """
    path = _cache_path(cache_dir, month)
    return all((path / f"{name}.json").exists() for name in RAW_FEEDS)


async def run_ingest(
    pool,
    month: str,
    schedule_date: str,
    is_embargo: bool = False,
    cache_dir: Path | None = None,
):
    """
    Run the full ingest pipeline for a given PBS schedule month.

    Args:
        pool: asyncpg connection pool
        month: Schedule month in YYYY-MM format (e.g., "2026-04")
        schedule_date: Date string for PBS API (e.g., "2026-04-01")
        is_embargo: Whether this is an embargo (pre-release) schedule
        cache_dir: Directory to cache raw API responses. Defaults to
                   $PBS_CACHE_DIR or /tmp/pbs_raw_cache. Pass None to disable.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR

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

        # ── Fetch phase (or load from cache) ──────────────────────────────────
        schedule_code = None
        # PBS's own effective_date for this schedule. schedules.released_at was set to
        # NOW() at insert, i.e. when we happened to ingest — so /v1/schedules reported
        # the May schedule as released in July. The catalogue carries the real date.
        schedule_effective_date = None
        if _cache_exists(cache_dir, month):
            log.info("ingest.cache_hit", cache_dir=str(cache_dir / month))
            raw_items                            = _load_raw(cache_dir, month, "items")
            raw_restrictions                     = _load_raw(cache_dir, month, "restrictions")
            raw_fees                             = _load_raw(cache_dir, month, "fees")
            raw_prescribing_texts                = _load_raw(cache_dir, month, "prescribing_texts")
            raw_indications                      = _load_raw(cache_dir, month, "indications")
            raw_amt_items                        = _load_raw(cache_dir, month, "amt_items")
            raw_program_dispensing_rules         = _load_raw(cache_dir, month, "program_dispensing_rules")
            raw_organisations                    = _load_raw(cache_dir, month, "organisations")
            raw_programs                         = _load_raw(cache_dir, month, "programs")
            raw_copayments                       = _load_raw(cache_dir, month, "copayments")
            raw_atc_codes                        = _load_raw(cache_dir, month, "atc_codes")
            raw_item_dispensing_rules            = _load_raw(cache_dir, month, "item_dispensing_rules")
            raw_item_restriction_rels            = _load_raw(cache_dir, month, "item_restriction_rels")
            raw_restriction_prescribing_text_rels = _load_raw(cache_dir, month, "restriction_prescribing_text_rels")
            raw_item_prescribing_texts           = _load_raw(cache_dir, month, "item_prescribing_texts")
            raw_item_atc_relationships           = _load_raw(cache_dir, month, "item_atc_relationships")
            raw_item_organisation_relationships  = _load_raw(cache_dir, month, "item_organisation_relationships")
            raw_summary_of_changes               = _load_raw(cache_dir, month, "summary_of_changes")
            raw_item_pricing                     = _load_raw(cache_dir, month, "item_pricing") or []
            raw_item_overviews                   = _load_raw(cache_dir, month, "item_overviews") or []
            raw_item_amt                         = _load_raw(cache_dir, month, "item_amt") or []
            raw_containers                           = _load_raw(cache_dir, month, "containers") or []
            raw_container_org_rels                   = _load_raw(cache_dir, month, "container_org_rels") or []
            raw_criteria                             = _load_raw(cache_dir, month, "criteria") or []
            raw_criteria_parameter_rels              = _load_raw(cache_dir, month, "criteria_parameter_rels") or []
            raw_parameters                           = _load_raw(cache_dir, month, "parameters") or []
            raw_prescribers                          = _load_raw(cache_dir, month, "prescribers") or []
            raw_markup_bands                         = _load_raw(cache_dir, month, "markup_bands") or []
            raw_item_pricing_events                  = _load_raw(cache_dir, month, "item_pricing_events") or []
            raw_extemporaneous_ingredients           = _load_raw(cache_dir, month, "extemporaneous_ingredients") or []
            raw_extemporaneous_preparations          = _load_raw(cache_dir, month, "extemporaneous_preparations") or []
            raw_extemporaneous_prep_sfp_rels         = _load_raw(cache_dir, month, "extemporaneous_prep_sfp_rels") or []
            raw_extemporaneous_tariffs               = _load_raw(cache_dir, month, "extemporaneous_tariffs") or []
            raw_standard_formula_preparations        = _load_raw(cache_dir, month, "standard_formula_preparations") or []
            raw_medication_charts                    = _load_raw(cache_dir, month, "medication_charts") or []
            raw_item_chart_eligibilities             = _load_raw(cache_dir, month, "item_chart_eligibilities") or []
            # The catalogue is cached alongside the feeds, so a cache run can resolve
            # the schedule the same way a live fetch does. It previously left both
            # schedule_code and the effective date unset.
            cached_schedules = _load_raw(cache_dir, month, "schedules") or []
            matched_cached = [s for s in cached_schedules
                              if str(s.get("effective_date", "")).startswith(schedule_date)]
            if matched_cached:
                schedule_code = str(matched_cached[-1]["schedule_code"])
                schedule_effective_date = (matched_cached[-1].get("start_tsp")
                                           or matched_cached[-1].get("effective_date"))
            log.info(
                "ingest.loaded_from_cache",
                items=len(raw_items or []),
                restrictions=len(raw_restrictions or []),
                amt_items=len(raw_amt_items or []),
            )
        else:
            log.info("ingest.fetching_from_api")
            client = PBSClient()
            try:
                # Resolve numeric schedule_code from the PBS API
                all_schedules = await client.get_available_schedules()
                # Land the schedule catalogue too, so the raw layer holds every
                # endpoint we call rather than every endpoint we normalise.
                _save_raw(cache_dir, month, "schedules", all_schedules)
                matched = [s for s in all_schedules if s.get("effective_date", "").startswith(schedule_date)]
                if not matched:
                    schedule_code = schedule_date
                else:
                    schedule_code = str(matched[-1]["schedule_code"])
                    schedule_effective_date = (matched[-1].get("start_tsp")
                                               or matched[-1].get("effective_date"))
                log.info("ingest.resolved_schedule_code", schedule_code=schedule_code)

                # Fetch all endpoints concurrently — max 2 in-flight at once to
                # stay well under the government API's rate limit.
                sem = asyncio.Semaphore(2)

                async def fetch(endpoint_name, cache_name, coro):
                    """
                    Fetch one feed, writing it to the cache as soon as it lands.

                    Saving per-feed rather than after the whole gather means a
                    failure part-way through does not discard the feeds that
                    already succeeded — at ~21s per request a full fetch is
                    ~25 minutes, so a resumed run reuses all of that.
                    """
                    cached = _load_raw(cache_dir, month, cache_name)
                    if cached is not None:
                        log.info("ingest.fetch_skipped_cached",
                                 endpoint=endpoint_name, count=len(cached))
                        coro.close()
                        return cached
                    async with sem:
                        result = await coro
                        _save_raw(cache_dir, month, cache_name, result)
                        log.info("ingest.fetched", endpoint=endpoint_name, count=len(result))
                        return result

                (
                    raw_items,
                    raw_restrictions,
                    raw_fees,
                    raw_prescribing_texts,
                    raw_indications,
                    raw_amt_items,
                    raw_program_dispensing_rules,
                    raw_organisations,
                    raw_programs,
                    raw_copayments,
                    raw_atc_codes,
                    raw_item_dispensing_rules,
                    raw_item_restriction_rels,
                    raw_restriction_prescribing_text_rels,
                    raw_item_prescribing_texts,
                    raw_item_atc_relationships,
                    raw_item_organisation_relationships,
                    raw_summary_of_changes,
                    raw_containers,
                    raw_container_org_rels,
                    raw_criteria,
                    raw_criteria_parameter_rels,
                    raw_parameters,
                    raw_prescribers,
                    raw_markup_bands,
                    raw_item_pricing_events,
                    raw_extemporaneous_ingredients,
                    raw_extemporaneous_preparations,
                    raw_extemporaneous_prep_sfp_rels,
                    raw_extemporaneous_tariffs,
                    raw_standard_formula_preparations,
                    raw_item_pricing,
                    raw_item_overviews,
                    raw_item_amt,
                    raw_medication_charts,
                    raw_item_chart_eligibilities,
                ) = await asyncio.gather(
                    fetch("/items", "items",                                    client.get_all_items(schedule_code)),
                    fetch("/restrictions", "restrictions",                             client.get_all_restrictions(schedule_code)),
                    fetch("/fees", "fees",                                     client.get_all_fees(schedule_code)),
                    fetch("/prescribing-texts", "prescribing_texts",                        client.get_all_prescribing_texts(schedule_code)),
                    fetch("/indications", "indications",                              client.get_all_indications(schedule_code)),
                    fetch("/amt-items", "amt_items",                                client.get_all_amt_items(schedule_code)),
                    fetch("/dispensing-rules", "program_dispensing_rules",                         client.get_all_program_dispensing_rules(schedule_code)),
                    fetch("/organisations", "organisations",                            client.get_all_organisations(schedule_code)),
                    fetch("/programs", "programs",                                 client.get_all_programs(schedule_code)),
                    fetch("/copayments", "copayments",                               client.get_all_copayments(schedule_code)),
                    fetch("/atc-codes", "atc_codes",                                client.get_all_atc_codes(schedule_code)),
                    fetch("/item-dispensing-rule-relationships", "item_dispensing_rules",       client.get_all_item_dispensing_rules(schedule_code)),
                    fetch("/item-restriction-relationships", "item_restriction_rels",           client.get_all_item_restriction_relationships(schedule_code)),
                    fetch("/restriction-prescribing-text-relationships", "restriction_prescribing_text_rels", client.get_all_restriction_prescribing_text_relationships(schedule_code)),
                    fetch("/item-prescribing-text-relationships", "item_prescribing_texts",      client.get_all_item_prescribing_texts(schedule_code)),
                    fetch("/item-atc-relationships", "item_atc_relationships",                   client.get_all_item_atc_relationships(schedule_code)),
                    fetch("/item-organisation-relationships", "item_organisation_relationships",          client.get_all_item_organisation_relationships(schedule_code)),
                    fetch("/summary-of-changes", "summary_of_changes",                       client.get_all_summary_of_changes(schedule_code)),
                    fetch("/containers", "containers",                               client.get_all_containers(schedule_code)),
                    fetch("/container-organisation-relationships", "container_org_rels",     client.get_all_container_organisation_relationships(schedule_code)),
                    fetch("/criteria", "criteria",                                 client.get_all_criteria(schedule_code)),
                    fetch("/criteria-parameter-relationships", "criteria_parameter_rels",         client.get_all_criteria_parameter_relationships(schedule_code)),
                    fetch("/parameters", "parameters",                               client.get_all_parameters(schedule_code)),
                    fetch("/prescribers", "prescribers",                              client.get_all_prescribers(schedule_code)),
                    fetch("/markup-bands", "markup_bands",                             client.get_all_markup_bands(schedule_code)),
                    fetch("/item-pricing-events", "item_pricing_events",                      client.get_all_item_pricing_events(schedule_code)),
                    fetch("/extemporaneous-ingredients", "extemporaneous_ingredients",               client.get_all_extemporaneous_ingredients(schedule_code)),
                    fetch("/extemporaneous-preparations", "extemporaneous_preparations",              client.get_all_extemporaneous_preparations(schedule_code)),
                    fetch("/extemporaneous-prep-sfp-relationships", "extemporaneous_prep_sfp_rels",    client.get_all_extemporaneous_prep_sfp_relationships(schedule_code)),
                    fetch("/extemporaneous-tariffs", "extemporaneous_tariffs",                   client.get_all_extemporaneous_tariffs(schedule_code)),
                    fetch("/standard-formula-preparations", "standard_formula_preparations",            client.get_all_standard_formula_preparations(schedule_code)),
                    # Pricing, overview and AMT feeds. These were previously never
                    # fetched, which left item_pricing, item_amt_relationships and the
                    # item overview fields (artg_id, sponsor, caution, biosimilar) empty.
                    # Note: get_all_item_dispensing_rule_relationships is the PRICING
                    # feed; get_all_item_dispensing_rules (above) is the rule-link feed.
                    fetch("/item-dispensing-rule-relationships/pricing", "item_pricing", client.get_all_item_dispensing_rule_relationships(schedule_code)),
                    fetch("/item-overviews", "item_overviews",                            client.get_all_item_overviews(schedule_code)),
                    fetch("/item-amt-relationships", "item_amt",                    client.get_all_item_amt(schedule_code)),
                    # Added in PBS API v3.7.8 (2026-05). Not consumed by the
                    # normaliser yet; fetched so the raw landing layer is complete.
                    fetch("/medication-charts", "medication_charts",              client.get_all_medication_charts(schedule_code)),
                    fetch("/item-chart-eligibilities", "item_chart_eligibilities", client.get_all_item_chart_eligibilities(schedule_code)),
                )

            finally:
                await client.close()

            # Raw feeds are cached by fetch() as each one lands, so a run that
            # fails part-way keeps everything already retrieved.
            log.info("ingest.cache_saved", cache_dir=str(cache_dir / month))

        # ── Raw landing layer ─────────────────────────────────────────────────
        # Store every source record verbatim before any interpretation, so a
        # mapping bug downstream is a re-derivation rather than data loss. Runs
        # before normalisation so the raw rows survive a normalise/load failure.
        raw_counts = await store_raw_feeds(
            pool, month, schedule_code,
            lambda name: _load_raw(cache_dir, month, name),
        )
        log.info("ingest.raw_stored", endpoints=len(raw_counts), rows=sum(raw_counts.values()))

        # released_at is customer-facing on /v1/schedules, so it must be the date PBS
        # made the schedule effective — not the unrelated date we happened to ingest it.
        if schedule_effective_date:
            # PBS sends start_tsp with an explicit offset (2026-05-01T00:00+10:00).
            # Keep the instant it names rather than a bare date, which Postgres would
            # localise into the previous day in UTC and read as the wrong month.
            raw_effective = str(schedule_effective_date)
            try:
                effective = datetime.fromisoformat(raw_effective)
            except ValueError:
                try:
                    effective = date.fromisoformat(raw_effective[:10])
                except ValueError:
                    log.warning("ingest.bad_effective_date", value=raw_effective)
                    effective = None
            if effective is not None:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE schedules SET released_at = $1 WHERE month = $2",
                        effective, month,
                    )
                log.info("ingest.released_at", month=month, effective_date=str(effective))

        # ── Normalise all data ─────────────────────────────────────────────────
        normalised = normalise_schedule(
            month,
            raw_items,
            raw_restrictions,
            raw_item_pricing=raw_item_pricing,
            raw_fees=raw_fees,
            raw_prescribing_texts=raw_prescribing_texts,
            raw_indications=raw_indications,
            raw_amt_items=raw_amt_items,
            raw_item_overviews=raw_item_overviews,
            raw_item_amt=raw_item_amt,
            raw_item_dispensing_rules=raw_item_dispensing_rules,
            raw_program_dispensing_rules=raw_program_dispensing_rules,
            raw_item_restriction_relationships=raw_item_restriction_rels,
            raw_restriction_prescribing_text_relationships=raw_restriction_prescribing_text_rels,
            raw_item_prescribing_texts=raw_item_prescribing_texts,
            raw_summary_of_changes=raw_summary_of_changes,
            raw_organisations=raw_organisations,
            raw_programs=raw_programs,
            raw_copayments=raw_copayments,
            raw_atc_codes=raw_atc_codes,
            raw_item_atc_relationships=raw_item_atc_relationships,
            raw_item_organisation_relationships=raw_item_organisation_relationships,
            raw_containers=raw_containers,
            raw_container_organisation_relationships=raw_container_org_rels,
            raw_criteria=raw_criteria,
            raw_criteria_parameter_relationships=raw_criteria_parameter_rels,
            raw_parameters=raw_parameters,
            raw_prescribers=raw_prescribers,
            raw_markup_bands=raw_markup_bands,
            raw_item_pricing_events=raw_item_pricing_events,
            raw_extemporaneous_ingredients=raw_extemporaneous_ingredients,
            raw_extemporaneous_preparations=raw_extemporaneous_preparations,
            raw_extemporaneous_prep_sfp_relationships=raw_extemporaneous_prep_sfp_rels,
            raw_extemporaneous_tariffs=raw_extemporaneous_tariffs,
            raw_standard_formula_preparations=raw_standard_formula_preparations,
        )
        # Per-feed fetch vs normalise counts. A feed that arrives non-empty but
        # normalises to zero is a mapping bug; one that arrives empty is a source
        # or subscription problem. Logging both makes the difference visible.
        _raw_counts = {
            "items": raw_items, "restrictions": raw_restrictions, "fees": raw_fees,
            "prescribing_texts": raw_prescribing_texts, "indications": raw_indications,
            "amt_items": raw_amt_items, "item_amt": raw_item_amt,
            "item_pricing": raw_item_pricing, "item_overviews": raw_item_overviews,
            "summary_of_changes": raw_summary_of_changes,
            "organisations": raw_organisations, "atc_codes": raw_atc_codes,
            "item_atc_relationships": raw_item_atc_relationships,
            "item_organisation_relationships": raw_item_organisation_relationships,
            "markup_bands": raw_markup_bands, "item_pricing_events": raw_item_pricing_events,
        }
        for _name, _raw in _raw_counts.items():
            _fetched = len(_raw or [])
            _normalised = len(normalised.get(_name) or [])
            if _fetched and not _normalised:
                log.warning("ingest.normalise_dropped_all", feed=_name, fetched=_fetched)
            elif not _fetched:
                log.warning("ingest.feed_empty", feed=_name)
            log.info("ingest.feed_counts", feed=_name, fetched=_fetched, normalised=_normalised)

        log.info(
            "ingest.normalised",
            medicines=len(normalised["medicines"]),
            items=len(normalised["items"]),
            item_pricing=len(normalised.get("item_pricing") or []),
            fees=len(normalised["fees"]),
            prescribing_texts=len(normalised["prescribing_texts"]),
            indications=len(normalised["indications"]),
            amt_items=len(normalised["amt_items"]),
        )

        # ── Compute changes ────────────────────────────────────────────────────
        changes = await compute_changes(pool, normalised, month)
        log.info("ingest.diffed", changes=len(changes))

        # ── Load to database ───────────────────────────────────────────────────
        await load_to_database(pool, month, normalised, changes)
        log.info("ingest.loaded")

        # ── Data quality gate ──────────────────────────────────────────────────
        # A schedule is only marked complete if it is actually usable. Previously a
        # schedule could be marked complete holding zero items, or holding items
        # with no prices at all, and every endpoint would serve it as truth.
        async with pool.acquire() as conn:
            schedule_id = await conn.fetchval(
                "SELECT id FROM schedules WHERE month = $1", month
            )
            report = await assert_schedule_usable(
                conn, month, str(schedule_id), blocking=False,
                # Reconcile raw_feed_rows against what was actually fetched, so
                # an ingest that silently dropped source rows cannot be marked
                # complete.
                raw_source=lambda name: _load_raw(cache_dir, month, name),
            )

        if not report.ok:
            log.error("ingest.quality_failed", month=month, errors=report.errors)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE schedules SET ingest_status = 'failed' WHERE month = $1", month
                )
            raise ValueError(report.summary())

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

        log.info("ingest.complete", warnings=report.warnings)
        if report.warnings:
            log.warning("ingest.completed_with_warnings", month=month, warnings=report.warnings)

    except Exception as exc:
        log.error("ingest.failed", error=str(exc))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET ingest_status = 'failed' WHERE month = $1",
                month,
            )
        raise
