"""Post-ingest data quality gate.

The 2026-07 audit found a schedule serving 6,883 items with zero prices, zero
change records and an authority_method that was null on all 22,769 restrictions —
and nothing anywhere reported a problem. These checks turn that class of failure
into a loud signal instead of a silent one.

Checks are advisory by default: they annotate the ingest and raise alerts, but do
not roll back a load. `blocking=True` promotes failures to an exception so a
scheduled ingest can refuse to mark a bad schedule complete.
"""
import json
from dataclasses import dataclass, field
from typing import Callable, Optional

import structlog

from ingest.join_validation import load_overview, validate_month
from ingest.raw_store import endpoint_sources

log = structlog.get_logger()

# Payloads deep-compared per endpoint when reconciling the raw layer. Row counts
# catch loss and truncation; comparing actual payloads catches corruption in
# encoding or column ordering. A full compare is ~10M fields per month, far too
# slow for every ingest, so a deterministic sample is taken from each feed.
RAW_SAMPLE_PER_ENDPOINT = 3


# Tables that must contain rows for a schedule to be considered usable. A table
# listed here that comes back empty means either the PBS feed was empty, the
# subscription lacks access, or a fetch is unwired — all of which need a human.
REQUIRED_NON_EMPTY = [
    "items",
    "medicines",
    "restrictions",
    "atc_codes",
    "item_atc_relationships",
    "programs",
    "copayments",
]

# Tables that should normally be populated. Emptiness is reported but does not fail
# the ingest, because PBS legitimately returns 204 for some of these on some months.
EXPECTED_NON_EMPTY = [
    "item_pricing",
    "item_organisation_relationships",
    "item_prescribers",
    "summary_of_changes",
    "prescribing_texts",
    "amt_items",
    "item_amt_relationships",
    "fees",
    "markup_bands",
]

# Columns whose null rate is a proxy for a broken field mapping. Each entry is
# (table, column, max_null_fraction, blocking). A rate of 1.0 — every row null — is
# the signature the audit kept finding.
#
# `blocking` distinguishes "this is definitely broken" from "this is a known open
# question".
#
# li_item_id and authority_method were both non-blocking while it was unclear whether
# PBS supplied them. Both are now confirmed and populated, so both block:
#   - li_item_id measures 0.00% null (it is the PBS key for /items).
#   - authority_method measures 55.98% null, but only because notes and cautions carry
#     no authority — every restriction-kind row has one. The threshold sits above that
#     legitimate share and well below the 100% that the dropped-column bug produced.
NULL_RATE_LIMITS = [
    ("items", "benefit_type", 0.01, True),
    ("items", "program_code", 0.20, True),
    ("items", "formulary", 0.50, False),
    ("items", "government_price", 0.99, False),
    ("items", "li_item_id", 0.01, True),
    ("restrictions", "restriction_code", 0.05, True),
    ("restrictions", "restriction_kind", 0.01, True),
    ("restrictions", "authority_method", 0.70, True),
    ("item_atc_relationships", "atc_priority_pct", 0.50, False),
]

# Row count must stay within this factor of the previous schedule. Catches a feed
# that silently returns a fraction of its usual volume.
COUNT_DRIFT_TOLERANCE = 0.5


@dataclass
class QualityReport:
    month: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    table_counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return f"Schedule {self.month}: all data quality checks passed."
        parts = [f"Schedule {self.month} data quality:"]
        for e in self.errors:
            parts.append(f"  ERROR   {e}")
        for w in self.warnings:
            parts.append(f"  WARNING {w}")
        return "\n".join(parts)


async def _count(conn, table: str, schedule_id: str) -> int:
    """Row count for a schedule. Tables without schedule_id are counted whole."""
    if table == "medicines":
        # medicines is not schedule-scoped — see docs/AUDIT_FINDINGS.md [C3].
        return await conn.fetchval("SELECT COUNT(*) FROM medicines") or 0
    if table == "restrictions":
        # restrictions is scoped transitively through items.
        return await conn.fetchval(
            "SELECT COUNT(*) FROM restrictions r "
            "JOIN items i ON i.id = r.item_id WHERE i.schedule_id = $1",
            schedule_id,
        ) or 0
    return await conn.fetchval(
        f"SELECT COUNT(*) FROM {table} WHERE schedule_id = $1", schedule_id
    ) or 0


async def _null_fraction(conn, table: str, column: str, schedule_id: str) -> Optional[float]:
    if table == "restrictions":
        row = await conn.fetchrow(
            f"SELECT COUNT(*) AS total, COUNT(r.{column}) AS present "
            "FROM restrictions r JOIN items i ON i.id = r.item_id "
            "WHERE i.schedule_id = $1",
            schedule_id,
        )
    else:
        row = await conn.fetchrow(
            f"SELECT COUNT(*) AS total, COUNT({column}) AS present "
            f"FROM {table} WHERE schedule_id = $1",
            schedule_id,
        )
    if not row or not row["total"]:
        return None
    return 1.0 - (row["present"] / row["total"])


def _sample_indices(n: int, k: int = RAW_SAMPLE_PER_ENDPOINT) -> list[int]:
    """Deterministic first/middle/last-style sample of row indices."""
    if n <= 0:
        return []
    if n <= k:
        return list(range(n))
    return sorted({0, n // 2, n - 1} | {i * (n - 1) // max(k - 1, 1) for i in range(k)})


async def check_raw_layer(conn, month: str, raw_source: Callable) -> tuple[list[str], list[str]]:
    """Reconcile raw_feed_rows against the records actually fetched.

    The raw layer only earns its keep if it is lossless, so a mismatch is an
    error, not a warning: an ingest that dropped source rows must not be marked
    complete. `raw_source` takes a cache name and returns the fetched records,
    or None if that feed was not fetched.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    total_expected = 0
    for endpoint, cache_name in sorted(endpoint_sources().items()):
        source = raw_source(cache_name)
        if source is None:
            warnings.append(f"raw: {endpoint} was never fetched")
            continue
        total_expected += len(source)

        stored = await conn.fetchval(
            "SELECT count(*) FROM raw_feed_rows WHERE month = $1 AND endpoint = $2",
            month, endpoint,
        ) or 0
        if stored != len(source):
            errors.append(
                f"raw: {endpoint} fetched {len(source)} rows but stored {stored}"
            )
            continue

        # Payload integrity on a deterministic sample.
        for i in _sample_indices(len(source)):
            raw = await conn.fetchval(
                "SELECT payload FROM raw_feed_rows "
                "WHERE month = $1 AND endpoint = $2 AND row_index = $3",
                month, endpoint, i,
            )
            if raw is None:
                errors.append(f"raw: {endpoint} row_index {i} missing")
                break
            got = json.loads(raw) if isinstance(raw, str) else raw
            if got != source[i]:
                errors.append(f"raw: {endpoint} row_index {i} payload differs from source")
                break

    if total_expected and not errors:
        log.info("quality.raw_reconciled", month=month, rows=total_expected)
    return errors, warnings


async def check_tier_a_joins(conn, month: str) -> tuple[list[str], list[str]]:
    """Compare every Tier A join against PBS's own pre-joined /item-overview.

    Every other check here is internal consistency — orphan counts, null rates, row
    volumes — and none of them can catch a join that is coherently wrong. This one
    can, because /item-overview is PBS's own answer to the same question.

    Mismatches are errors: all 8 joins matched exactly on 2026-05 and 2026-06 when this
    was wired in, so any disagreement is a regression rather than a known gap. A month
    with no overview rows is a warning, not an error — check_raw_layer already owns
    feed completeness, and duplicating it here would report one missing feed twice.
    """
    errors: list[str] = []
    warnings: list[str] = []

    overview = await load_overview(conn, month)
    if not overview:
        warnings.append(
            "tier A joins not validated: no /item-overview rows in the raw layer"
        )
        return errors, warnings

    for res in await validate_month(conn, month, overview=overview):
        if res.ok:
            continue
        detail = f"{res.name} disagrees with PBS: {res.missing} missing, {res.extra} extra"
        if res.sample_missing:
            detail += f"; missing e.g. {res.sample_missing}"
        if res.sample_extra:
            detail += f"; extra e.g. {res.sample_extra}"
        errors.append(detail)

    if not errors:
        log.info("quality.tier_a_joins_match", month=month, items=len(overview))
    return errors, warnings


async def check_internal_consistency(conn, month: str) -> tuple[list[str], list[str]]:
    """Cross-field invariants that held with zero exceptions on every real schedule.

    Measured across 13,857 PBS codes on 2026-05 and 2026-06: every item's program_code
    exists in programs, every Authority/Streamlined/Restricted item carries a
    restriction, and no Unrestricted item does.

    These warn rather than block, unlike the Tier A join comparison. That one checks us
    against PBS's own published answer, so a disagreement is definitionally our bug.
    These are patterns observed in the data, not guarantees PBS documents — so a future
    schedule could legitimately break one, and refusing to ingest it would be worse than
    flagging it. They are loud enough to investigate, cheap enough to run every time.
    """
    errors: list[str] = []
    warnings: list[str] = []

    orphans = await conn.fetch(
        """
        SELECT DISTINCT i.program_code
        FROM items i JOIN schedules s ON s.id = i.schedule_id
        WHERE s.month = $1 AND i.program_code IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM programs p
                          WHERE p.program_code = i.program_code
                            AND p.schedule_id = i.schedule_id)
        """,
        month,
    )
    if orphans:
        codes = ", ".join(sorted(o["program_code"] for o in orphans))
        warnings.append(f"items reference program codes not in programs: {codes}")

    mismatch = await conn.fetchrow(
        r"""
        WITH b AS (
            SELECT i.benefit_type,
                   EXISTS (SELECT 1 FROM item_restriction_relationships r
                           WHERE r.pbs_code = i.pbs_code AND r.schedule_id = i.schedule_id
                             AND r.restriction_code LIKE '%\_R') AS has_restriction
            FROM items_listing i JOIN schedules s ON s.id = i.schedule_id
            WHERE s.month = $1
        )
        SELECT count(*) FILTER (WHERE benefit_type = 'U' AND has_restriction) AS u_with,
               count(*) FILTER (WHERE benefit_type IN ('A','S','R') AND NOT has_restriction) AS r_without
        FROM b
        """,
        month,
    )
    if mismatch and mismatch["u_with"]:
        warnings.append(
            f"{mismatch['u_with']} unrestricted items carry a restriction"
        )
    if mismatch and mismatch["r_without"]:
        warnings.append(
            f"{mismatch['r_without']} restricted/authority items carry no restriction"
        )

    return errors, warnings


async def check_tier_b_derivations(conn, month: str) -> tuple[list[str], list[str]]:
    """Check the pricing fields every patient-facing derivation is built on.

    Tier B is validated by reversibility rather than against an external answer, but
    reversibility is worthless if the inputs are wrong: the patient/government split
    recomposed perfectly for months while being computed from
    cmnwlth_price_to_pharmacist, which is not the dispensed price. These checks guard
    the inputs.

    Each condition is either universal on real data or documented by PBS, measured
    across all 38,318 pricing rows on 2026-06:
      - dispensed_price_max_qty and max_general_patient_charge are populated for every
        non-extemporaneous row (PBS documents both as null for extemporaneous items)
      - max_record_val_for_safety_net never exceeds the general copayment, which the
        data dictionary states as a cap
      - no monetary field is negative

    Deliberately not checked: mn_pharmacy_price + fee_dispensing == dispensed price.
    That recomposes on only 21,399 of 38,159 rows because markups and chemotherapy and
    container fees vary by rule, so enforcing it would raise 16,760 false alarms.
    """
    errors: list[str] = []
    warnings: list[str] = []

    row = await conn.fetchrow(
        """
        WITH copay AS (
            SELECT c.general FROM copayments c
            JOIN schedules s ON s.id = c.schedule_id WHERE s.month = $1
        ),
        p AS (
            SELECT ip.dispensed_price_max_qty, ip.max_general_patient_charge,
                   ip.max_record_val_for_safety_net, i.program_code
            FROM item_pricing ip
            JOIN schedules s ON s.id = ip.schedule_id AND s.month = $1
            JOIN items i ON i.li_item_id = ip.li_item_id AND i.schedule_id = ip.schedule_id
        )
        SELECT count(*) AS rows,
               count(*) FILTER (WHERE dispensed_price_max_qty IS NULL
                                  AND program_code <> 'EP') AS dpmq_missing,
               count(*) FILTER (WHERE max_general_patient_charge IS NULL
                                  AND program_code <> 'EP') AS charge_missing,
               count(*) FILTER (WHERE max_record_val_for_safety_net
                                      > (SELECT general FROM copay)) AS sn_above_copay,
               count(*) FILTER (WHERE dispensed_price_max_qty < 0
                                   OR max_general_patient_charge < 0) AS negative
        FROM p
        """,
        month,
    )

    if not row or not row["rows"]:
        warnings.append("tier B pricing not validated: no item_pricing rows for this schedule")
        return errors, warnings

    if row["dpmq_missing"]:
        errors.append(
            f"{row['dpmq_missing']} pricing rows have no dispensed_price_max_qty — "
            f"every patient-facing figure is derived from it"
        )
    if row["charge_missing"]:
        errors.append(
            f"{row['charge_missing']} pricing rows have no max_general_patient_charge — "
            f"the general patient charge is published, not computed, so it cannot be recovered"
        )
    if row["sn_above_copay"]:
        errors.append(
            f"{row['sn_above_copay']} rows record more to safety net than the general "
            f"copayment, which PBS documents as a cap"
        )
    if row["negative"]:
        errors.append(f"{row['negative']} pricing rows carry a negative amount")

    # Reversibility: our own patient-charge formula, applied with the GENERAL copayment,
    # must reproduce PBS's published max_general_patient_charge. That is the closest
    # thing Tier B has to the /item-overview comparison — PBS publishes no concessional
    # charge, so the concessional figure can only be trusted insofar as the same formula
    # reconstructs the one PBS does publish.
    #
    # Restricted to the mainstream rules. The rp-np-* and rp-np-nc- families are
    # reduced- and no-charge settings where the real charge is always lower than the
    # formula predicts, and the API caps the concessional figure at the published
    # general charge to cover them.
    recompose = await conn.fetchrow(
        """
        WITH copay AS (
            SELECT c.general FROM copayments c
            JOIN schedules s ON s.id = c.schedule_id WHERE s.month = $1
        ),
        p AS (
            SELECT ip.max_general_patient_charge AS actual,
                   ROUND(
                       LEAST(ip.dispensed_price_max_qty
                             + COALESCE(ip.fee_extra, 0)
                             + COALESCE(ip.fee_safety_net_recording, 0),
                             (SELECT general FROM copay))
                       + COALESCE(ip.brand_premium, 0)
                       + COALESCE(ip.special_patient_contribution, 0)
                       + COALESCE(ip.therapeutic_group_premium, 0), 2) AS predicted
            FROM item_pricing ip
            JOIN schedules s ON s.id = ip.schedule_id AND s.month = $1
            WHERE ip.dispensed_price_max_qty IS NOT NULL
              AND ip.max_general_patient_charge IS NOT NULL
              AND ip.dispensing_rule_reference NOT LIKE 'rp-np-%'
        )
        SELECT count(*) AS rows, count(*) FILTER (WHERE predicted = actual) AS matched
        FROM p
        """,
        month,
    )
    if recompose and recompose["rows"]:
        matched, total = recompose["matched"], recompose["rows"]
        pct = 100.0 * matched / total
        if pct < 98.0:
            errors.append(
                f"patient charge formula reproduces only {pct:.1f}% of the published "
                f"max_general_patient_charge ({matched}/{total}) — the concessional "
                f"figure is derived from this formula and can no longer be trusted"
            )
        else:
            log.info("quality.patient_charge_recomposes", month=month,
                     matched=matched, rows=total, pct=round(pct, 2))

    if not errors:
        log.info("quality.tier_b_pricing_ok", month=month, rows=row["rows"])
    return errors, warnings


async def check_schedule(
    conn, month: str, schedule_id: str, raw_source: Optional[Callable] = None
) -> QualityReport:
    """Run every quality check against a freshly loaded schedule.

    `raw_source` enables raw-layer reconciliation; when omitted the raw checks
    are skipped (callers that have no access to the fetched records).
    """
    report = QualityReport(month=month)

    if raw_source is not None:
        raw_errors, raw_warnings = await check_raw_layer(conn, month, raw_source)
        report.errors.extend(raw_errors)
        report.warnings.extend(raw_warnings)

    try:
        consistency_errors, consistency_warnings = await check_internal_consistency(conn, month)
    except Exception as exc:
        report.warnings.append(f"internal consistency checks failed to run ({exc})")
    else:
        report.errors.extend(consistency_errors)
        report.warnings.extend(consistency_warnings)

    try:
        tier_b_errors, tier_b_warnings = await check_tier_b_derivations(conn, month)
    except Exception as exc:
        report.warnings.append(f"tier B pricing checks failed to run ({exc})")
    else:
        report.errors.extend(tier_b_errors)
        report.warnings.extend(tier_b_warnings)

    try:
        join_errors, join_warnings = await check_tier_a_joins(conn, month)
    except Exception as exc:  # never let the gate itself be the reason an ingest dies
        report.warnings.append(f"tier A join validation failed to run ({exc})")
    else:
        report.errors.extend(join_errors)
        report.warnings.extend(join_warnings)

    for table in REQUIRED_NON_EMPTY + EXPECTED_NON_EMPTY:
        try:
            count = await _count(conn, table, schedule_id)
        except Exception as exc:
            report.warnings.append(f"{table}: count failed ({exc})")
            continue
        report.table_counts[table] = count
        if count == 0:
            msg = f"{table} is empty"
            if table in REQUIRED_NON_EMPTY:
                report.errors.append(msg)
            else:
                report.warnings.append(msg)

    for table, column, limit, blocking in NULL_RATE_LIMITS:
        try:
            frac = await _null_fraction(conn, table, column, schedule_id)
        except Exception as exc:
            report.warnings.append(f"{table}.{column}: null check failed ({exc})")
            continue
        if frac is None:
            continue
        if frac > limit:
            pct = round(frac * 100, 1)
            msg = f"{table}.{column} is {pct}% null (limit {round(limit * 100, 1)}%)"
            if frac >= 1.0:
                msg += " — field is never populated"
            (report.errors if blocking else report.warnings).append(msg)

    # Volume drift against the previous completed schedule.
    prev = await conn.fetchrow(
        "SELECT id, month FROM schedules WHERE month < $1 AND ingest_status = 'complete' "
        "ORDER BY month DESC LIMIT 1",
        month,
    )
    if prev:
        prev_items = await _count(conn, "items", str(prev["id"]))
        this_items = report.table_counts.get("items", 0)
        if prev_items and this_items:
            ratio = this_items / prev_items
            if ratio < COUNT_DRIFT_TOLERANCE or ratio > (1 / COUNT_DRIFT_TOLERANCE):
                report.warnings.append(
                    f"items count {this_items} differs sharply from {prev['month']} "
                    f"({prev_items}); ratio {round(ratio, 2)}"
                )

    return report


async def assert_schedule_usable(
    conn, month: str, schedule_id: str, blocking: bool = False,
    raw_source: Optional[Callable] = None,
):
    """Run the checks, log and return the report. Raises if blocking and failing."""
    report = await check_schedule(conn, month, schedule_id, raw_source=raw_source)

    log.info(
        "ingest.quality",
        month=month,
        ok=report.ok,
        errors=len(report.errors),
        warnings=len(report.warnings),
        table_counts=report.table_counts,
    )
    for err in report.errors:
        log.error("ingest.quality_error", month=month, detail=err)
    for warn in report.warnings:
        log.warning("ingest.quality_warning", month=month, detail=warn)

    if blocking and not report.ok:
        raise ValueError(report.summary())
    return report
