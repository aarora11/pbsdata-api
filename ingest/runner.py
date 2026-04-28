"""Orchestrate the full ingest pipeline."""
import asyncio
import json
import os
import structlog
from datetime import datetime
from pathlib import Path
from ingest.pbs_client import PBSClient
from ingest.normaliser import normalise_schedule
from ingest.differ import compute_changes
from ingest.loader import load_to_database

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


def _cache_exists(cache_dir: Path, month: str) -> bool:
    """Return True if a complete cache exists for this month (items.json present)."""
    return (_cache_path(cache_dir, month) / "items.json").exists()


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
            raw_item_overviews = []
            raw_item_amt = []
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
                matched = [s for s in all_schedules if s.get("effective_date", "").startswith(schedule_date)]
                if not matched:
                    schedule_code = schedule_date
                else:
                    schedule_code = str(matched[-1]["schedule_code"])
                log.info("ingest.resolved_schedule_code", schedule_code=schedule_code)

                # Phase 1: Core item and restriction data
                raw_items = await client.get_all_items(schedule_code)
                raw_restrictions = await client.get_all_restrictions(schedule_code)
                log.info("ingest.fetched_core", items=len(raw_items), restrictions=len(raw_restrictions))

                # Phases 2–5: reference + relationship data fetched sequentially
                raw_fees = await client.get_all_fees(schedule_code)
                log.info("ingest.fetched", endpoint="/fees", count=len(raw_fees))
                raw_prescribing_texts = await client.get_all_prescribing_texts(schedule_code)
                log.info("ingest.fetched", endpoint="/prescribing-texts", count=len(raw_prescribing_texts))
                raw_indications = await client.get_all_indications(schedule_code)
                log.info("ingest.fetched", endpoint="/indications", count=len(raw_indications))
                raw_amt_items = await client.get_all_amt_items(schedule_code)
                log.info("ingest.fetched", endpoint="/amt-items", count=len(raw_amt_items))
                raw_program_dispensing_rules = await client.get_all_program_dispensing_rules(schedule_code)
                log.info("ingest.fetched", endpoint="/dispensing-rules", count=len(raw_program_dispensing_rules))
                raw_organisations = await client.get_all_organisations(schedule_code)
                log.info("ingest.fetched", endpoint="/organisations", count=len(raw_organisations))
                raw_programs = await client.get_all_programs(schedule_code)
                log.info("ingest.fetched", endpoint="/programs", count=len(raw_programs))
                raw_copayments = await client.get_all_copayments(schedule_code)
                log.info("ingest.fetched", endpoint="/copayments", count=len(raw_copayments))
                raw_atc_codes = await client.get_all_atc_codes(schedule_code)
                log.info("ingest.fetched", endpoint="/atc-codes", count=len(raw_atc_codes))

                raw_item_overviews = []
                raw_item_amt = []
                raw_item_dispensing_rules = await client.get_all_item_dispensing_rules(schedule_code)
                log.info("ingest.fetched", endpoint="/item-dispensing-rule-relationships", count=len(raw_item_dispensing_rules))
                raw_item_restriction_rels = await client.get_all_item_restriction_relationships(schedule_code)
                log.info("ingest.fetched", endpoint="/item-restriction-relationships", count=len(raw_item_restriction_rels))
                raw_restriction_prescribing_text_rels = await client.get_all_restriction_prescribing_text_relationships(schedule_code)
                log.info("ingest.fetched", endpoint="/restriction-prescribing-text-relationships", count=len(raw_restriction_prescribing_text_rels))
                raw_item_prescribing_texts = await client.get_all_item_prescribing_texts(schedule_code)
                log.info("ingest.fetched", endpoint="/item-prescribing-text-relationships", count=len(raw_item_prescribing_texts))
                raw_item_atc_relationships = await client.get_all_item_atc_relationships(schedule_code)
                log.info("ingest.fetched", endpoint="/item-atc-relationships", count=len(raw_item_atc_relationships))
                raw_item_organisation_relationships = await client.get_all_item_organisation_relationships(schedule_code)
                log.info("ingest.fetched", endpoint="/item-organisation-relationships", count=len(raw_item_organisation_relationships))
                raw_summary_of_changes = await client.get_all_summary_of_changes(schedule_code)
                log.info("ingest.fetched", endpoint="/summary-of-changes", count=len(raw_summary_of_changes))

                # New endpoints (migration 007)
                raw_containers = await client.get_all_containers(schedule_code)
                log.info("ingest.fetched", endpoint="/containers", count=len(raw_containers))
                raw_container_org_rels = await client.get_all_container_organisation_relationships(schedule_code)
                log.info("ingest.fetched", endpoint="/container-organisation-relationships", count=len(raw_container_org_rels))
                raw_criteria = await client.get_all_criteria(schedule_code)
                log.info("ingest.fetched", endpoint="/criteria", count=len(raw_criteria))
                raw_criteria_parameter_rels = await client.get_all_criteria_parameter_relationships(schedule_code)
                log.info("ingest.fetched", endpoint="/criteria-parameter-relationships", count=len(raw_criteria_parameter_rels))
                raw_parameters = await client.get_all_parameters(schedule_code)
                log.info("ingest.fetched", endpoint="/parameters", count=len(raw_parameters))
                raw_prescribers = await client.get_all_prescribers(schedule_code)
                log.info("ingest.fetched", endpoint="/prescribers", count=len(raw_prescribers))
                raw_markup_bands = await client.get_all_markup_bands(schedule_code)
                log.info("ingest.fetched", endpoint="/markup-bands", count=len(raw_markup_bands))
                raw_item_pricing_events = await client.get_all_item_pricing_events(schedule_code)
                log.info("ingest.fetched", endpoint="/item-pricing-events", count=len(raw_item_pricing_events))
                raw_extemporaneous_ingredients = await client.get_all_extemporaneous_ingredients(schedule_code)
                log.info("ingest.fetched", endpoint="/extemporaneous-ingredients", count=len(raw_extemporaneous_ingredients))
                raw_extemporaneous_preparations = await client.get_all_extemporaneous_preparations(schedule_code)
                log.info("ingest.fetched", endpoint="/extemporaneous-preparations", count=len(raw_extemporaneous_preparations))
                raw_extemporaneous_prep_sfp_rels = await client.get_all_extemporaneous_prep_sfp_relationships(schedule_code)
                log.info("ingest.fetched", endpoint="/extemporaneous-prep-sfp-relationships", count=len(raw_extemporaneous_prep_sfp_rels))
                raw_extemporaneous_tariffs = await client.get_all_extemporaneous_tariffs(schedule_code)
                log.info("ingest.fetched", endpoint="/extemporaneous-tariffs", count=len(raw_extemporaneous_tariffs))
                raw_standard_formula_preparations = await client.get_all_standard_formula_preparations(schedule_code)
                log.info("ingest.fetched", endpoint="/standard-formula-preparations", count=len(raw_standard_formula_preparations))

            finally:
                await client.close()

            # Save all raw data to cache so subsequent runs skip the API
            _save_raw(cache_dir, month, "items",                             raw_items)
            _save_raw(cache_dir, month, "restrictions",                      raw_restrictions)
            _save_raw(cache_dir, month, "fees",                              raw_fees)
            _save_raw(cache_dir, month, "prescribing_texts",                 raw_prescribing_texts)
            _save_raw(cache_dir, month, "indications",                       raw_indications)
            _save_raw(cache_dir, month, "amt_items",                         raw_amt_items)
            _save_raw(cache_dir, month, "program_dispensing_rules",          raw_program_dispensing_rules)
            _save_raw(cache_dir, month, "organisations",                     raw_organisations)
            _save_raw(cache_dir, month, "programs",                          raw_programs)
            _save_raw(cache_dir, month, "copayments",                        raw_copayments)
            _save_raw(cache_dir, month, "atc_codes",                         raw_atc_codes)
            _save_raw(cache_dir, month, "item_dispensing_rules",             raw_item_dispensing_rules)
            _save_raw(cache_dir, month, "item_restriction_rels",             raw_item_restriction_rels)
            _save_raw(cache_dir, month, "restriction_prescribing_text_rels", raw_restriction_prescribing_text_rels)
            _save_raw(cache_dir, month, "item_prescribing_texts",            raw_item_prescribing_texts)
            _save_raw(cache_dir, month, "item_atc_relationships",            raw_item_atc_relationships)
            _save_raw(cache_dir, month, "item_organisation_relationships",   raw_item_organisation_relationships)
            _save_raw(cache_dir, month, "summary_of_changes",               raw_summary_of_changes)
            _save_raw(cache_dir, month, "containers",                        raw_containers)
            _save_raw(cache_dir, month, "container_org_rels",               raw_container_org_rels)
            _save_raw(cache_dir, month, "criteria",                          raw_criteria)
            _save_raw(cache_dir, month, "criteria_parameter_rels",          raw_criteria_parameter_rels)
            _save_raw(cache_dir, month, "parameters",                        raw_parameters)
            _save_raw(cache_dir, month, "prescribers",                       raw_prescribers)
            _save_raw(cache_dir, month, "markup_bands",                      raw_markup_bands)
            _save_raw(cache_dir, month, "item_pricing_events",               raw_item_pricing_events)
            _save_raw(cache_dir, month, "extemporaneous_ingredients",        raw_extemporaneous_ingredients)
            _save_raw(cache_dir, month, "extemporaneous_preparations",       raw_extemporaneous_preparations)
            _save_raw(cache_dir, month, "extemporaneous_prep_sfp_rels",      raw_extemporaneous_prep_sfp_rels)
            _save_raw(cache_dir, month, "extemporaneous_tariffs",            raw_extemporaneous_tariffs)
            _save_raw(cache_dir, month, "standard_formula_preparations",     raw_standard_formula_preparations)
            log.info("ingest.cache_saved", cache_dir=str(cache_dir / month))

        # ── Normalise all data ─────────────────────────────────────────────────
        normalised = normalise_schedule(
            month,
            raw_items,
            raw_restrictions,
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
        log.info(
            "ingest.normalised",
            medicines=len(normalised["medicines"]),
            items=len(normalised["items"]),
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

    except Exception as exc:
        log.error("ingest.failed", error=str(exc))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE schedules SET ingest_status = 'failed' WHERE month = $1",
                month,
            )
        raise
