-- Migration 011: rekey `items` to the grain PBS actually publishes.
--
-- The PBS API V3 Data Dictionary (docs/reference/) defines ITEM_T as:
--   "Captures the PBS item (drug) and its basic elements. One row per unique
--    medicinal product, container and brand per schedule."
-- with primary key (li_item_id, schedule_code).
--
-- We keyed it on (pbs_code, schedule_id) instead, which forced 14,782 source
-- rows into 6,921 via ON CONFLICT DO UPDATE — last write wins, arbitrary.
-- Measured cost on 2026-06: brands disagree on brand_name for 35.8% of PBS
-- codes and on organisation_id for 34.0%, so roughly a third of brand names and
-- sponsors served were an arbitrary pick. It is also the source of the change
-- feed's spurious price movements (248 of 297 measured were collapse artifacts).
--
-- PBS uses two grains deliberately, and both are kept here:
--   brand   (li_item_id) — items, amt-items, item-dispensing-rule-relationships,
--                          item-pricing-events
--   listing (pbs_code)   — atc, restriction, prescribing-text, organisation and
--                          prescriber relationships
-- `items` becomes brand grain; `items_listing` below serves the listing grain
-- deterministically for callers that want one row per PBS code.
--
-- migrate.py re-runs every migration on startup, so this must stay idempotent.

-- ── items: brand grain ───────────────────────────────────────────────────────
ALTER TABLE items DROP CONSTRAINT IF EXISTS items_pbs_code_schedule_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS items_li_item_id_schedule_id_key
    ON items (li_item_id, schedule_id);

CREATE INDEX IF NOT EXISTS idx_items_pbs_code_schedule
    ON items (pbs_code, schedule_id);

-- ── items_listing: one deterministic row per PBS code ────────────────────────
-- DISTINCT ON with an explicit ORDER BY makes the representative brand stable:
-- the lowest li_item_id, always the same row for the same input. Replaces the
-- previous behaviour where whichever brand the loader happened to write last won.
--
-- Selecting i.* means existing queries can move from `items` to `items_listing`
-- unchanged and simply become deterministic.
DROP VIEW IF EXISTS items_listing;
CREATE VIEW items_listing AS
SELECT DISTINCT ON (i.pbs_code, i.schedule_id)
       i.*,
       agg.brand_count,
       agg.min_government_price,
       agg.max_government_price,
       (agg.brand_count > 1) AS has_multiple_brands
FROM items i
JOIN (
    SELECT pbs_code,
           schedule_id,
           count(*)              AS brand_count,
           min(government_price) AS min_government_price,
           max(government_price) AS max_government_price
    FROM items
    GROUP BY pbs_code, schedule_id
) agg ON agg.pbs_code = i.pbs_code AND agg.schedule_id = i.schedule_id
ORDER BY i.pbs_code, i.schedule_id, i.li_item_id;

COMMENT ON VIEW items_listing IS
    'One row per (pbs_code, schedule_id). The representative brand is the lowest '
    'li_item_id — deterministic, not arbitrary. brand_count/min_/max_government_price '
    'expose the spread that collapsing hides. Fields that never vary across brands '
    '(formulary, benefit_type, max_quantity) are safe to read here; brand_name and '
    'sponsor are one brand of brand_count.';
