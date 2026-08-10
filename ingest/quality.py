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
