-- Migration 009: remediation of defects found in the 2026-07 data & join audit.
-- See docs/AUDIT_FINDINGS.md for the finding IDs referenced below.

-- ── [B3] benefit_type: store the PBS letter code, not an expanded string ──────
-- The ingest wrote "authority_required" while every API query matched on 'A'.
-- Convert existing rows back to the PBS codes. Idempotent: rows already holding a
-- letter code are left alone because they do not match any legacy value.
UPDATE items SET benefit_type = CASE benefit_type
    WHEN 'unrestricted'           THEN 'U'
    WHEN 'restricted'             THEN 'R'
    WHEN 'authority_streamlined'  THEN 'S'
    WHEN 'authority_required'     THEN 'A'
    WHEN 'hospital'               THEN 'O'
    WHEN 'brand_equivalent'       THEN 'B'
    WHEN 'combination'            THEN 'C'
    WHEN 'doctors_bag'            THEN 'D'
    WHEN 'mnemonics'              THEN 'F'
    WHEN 'general_schedule'       THEN 'G'
    WHEN 'highly_specialised'     THEN 'H'
    WHEN 'nursing_home'           THEN 'N'
    WHEN 'private'                THEN 'P'
    WHEN 'in_hospital'            THEN 'T'
    WHEN 'in_hospital_only'       THEN 'W'
    ELSE benefit_type
END
WHERE benefit_type IN (
    'unrestricted','restricted','authority_streamlined','authority_required',
    'hospital','brand_equivalent','combination','doctors_bag','mnemonics',
    'general_schedule','highly_specialised','nursing_home','private',
    'in_hospital','in_hospital_only'
);

-- ── [B4] summary_of_changes.section: drop the leading slash ───────────────────
-- PBS supplies changed_endpoint as "/items"; code compares against "items".
UPDATE summary_of_changes
SET section = LTRIM(section, '/')
WHERE section LIKE '/%';

-- ── [B1] item_atc_relationships.is_primary ───────────────────────────────────
-- /market/atc-summary?primary_atc_only=true referenced this column, which had
-- never been created — the endpoint returned HTTP 500. Adding it here and
-- populating from atc_priority_pct gives one definition of "primary ATC" that the
-- market, items and schedule-changes endpoints can all share.
ALTER TABLE item_atc_relationships
    ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT FALSE;

-- Highest atc_priority_pct per (pbs_code, schedule_id) wins. Ties broken by
-- atc_code so the choice is deterministic and reproducible across re-runs.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY pbs_code, schedule_id
               ORDER BY atc_priority_pct DESC NULLS LAST, atc_code
           ) AS rn
    FROM item_atc_relationships
)
UPDATE item_atc_relationships iar
SET is_primary = (ranked.rn = 1)
FROM ranked
WHERE ranked.id = iar.id
  AND iar.is_primary IS DISTINCT FROM (ranked.rn = 1);

CREATE INDEX IF NOT EXISTS idx_item_atc_primary
    ON item_atc_relationships (pbs_code, schedule_id) WHERE is_primary;

-- ── [C4] restriction lookup determinism ──────────────────────────────────────
-- restriction_code is not unique: one code maps to as many as 2,124 items, and
-- GET /restrictions/{code} used fetchrow with no ORDER BY. The query now orders
-- explicitly; this index keeps that ordering cheap.
CREATE INDEX IF NOT EXISTS idx_restrictions_code_item
    ON restrictions (restriction_code, item_id);

-- ── [D4] remove the dead pre-computed market tables ──────────────────────────
-- Created by migration 008, never written by the ingest, never read by any
-- router. Dropping them so the schema stops implying a capability that does not
-- exist. Re-introduce alongside a populating job if T4 query load warrants it.
DROP TABLE IF EXISTS market_atc_summary;
DROP TABLE IF EXISTS market_manufacturer_landscape;
