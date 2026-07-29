-- Migration 010: raw landing layer.
--
-- Every defect found in the 2026-07 re-ingest was a lossy hand-written mapping
-- between fetching a PBS feed and storing it — not a failure to fetch:
--   * items.artg_id / sponsor / caution read field names PBS does not publish
--   * item_amt_relationships collapsed 59,495 rows to 1 by keying on a field
--     the /amt-items feed does not carry
--   * item_dispensing_rules collapsed 38,253 rows to 10 for the same reason
--   * /item-overview returns 86 fields per row; exactly one was retained
--
-- This table stores every row of every PBS endpoint verbatim, before any
-- interpretation. Extraction correctness becomes independent of mapping
-- correctness: a mapping bug is then a re-derivation, not data loss.
--
-- Deliberately generic rather than one typed table per endpoint. PBS added two
-- endpoints in API v3.7.8 (May 2026); a typed schema needs a migration for each,
-- and typed columns reintroduce exactly the hand-mapping step being removed.
--
-- migrate.py re-runs every migration on every startup, so this must stay
-- idempotent.

CREATE TABLE IF NOT EXISTS raw_feed_rows (
    id             BIGSERIAL PRIMARY KEY,
    month          VARCHAR(7)  NOT NULL,   -- our schedule month, e.g. '2026-05'
    schedule_code  TEXT,                   -- PBS numeric schedule code, e.g. '3966'
    endpoint       TEXT        NOT NULL,   -- PBS path, e.g. '/items'
    row_index      INTEGER     NOT NULL,   -- ordinal within the feed as returned
    payload        JSONB       NOT NULL,   -- the source record, unmodified
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per (month, endpoint, ordinal). Makes the loader idempotent via
-- ON CONFLICT and lets a re-ingest replace a feed in place.
CREATE UNIQUE INDEX IF NOT EXISTS raw_feed_rows_unique
    ON raw_feed_rows (month, endpoint, row_index);

CREATE INDEX IF NOT EXISTS raw_feed_rows_endpoint_month
    ON raw_feed_rows (endpoint, month);

-- Containment queries over the payload, e.g. finding every raw row for a
-- pbs_code without knowing which endpoint carries it.
CREATE INDEX IF NOT EXISTS raw_feed_rows_payload_gin
    ON raw_feed_rows USING GIN (payload jsonb_path_ops);

-- Per-feed row counts, for reconciling what was fetched against what landed.
CREATE OR REPLACE VIEW raw_feed_summary AS
SELECT month,
       schedule_code,
       endpoint,
       count(*)                        AS rows,
       count(DISTINCT payload)         AS distinct_payloads,
       min(fetched_at)                 AS first_fetched_at,
       max(fetched_at)                 AS last_fetched_at
FROM raw_feed_rows
GROUP BY month, schedule_code, endpoint;
