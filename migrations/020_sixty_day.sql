-- Migration 020: 60-day prescriptions, read from the fields PBS actually uses for them.
--
-- items.sixty_day_eligible was populated from continued_dispensing_flag, which is the
-- Continued Dispensing emergency-supply measure and has nothing to do with 60-day
-- prescribing: it marked 726 items across 149 medicines, where PBS's 60-day policy
-- flags mark 806 increased-quantity items across 283 medicines (2026-08; Grattan
-- counts "about 300 drugs").
--
-- PBS implements a 60-day script as a second item code with double the maximum
-- quantity, flagging both sides:
--   imdq60        policy_applied_imdq60_flag: this item IS the 60-day listing
--   imdq60_base   policy_applied_imdq60_base_flag: this item has a 60-day listing
-- sixty_day_eligible keeps its API meaning ("part of a 60-day pair") as imdq60 OR base.
--
-- sixty_day_pair links the two codes, which PBS does not publish; ingest/sixty_day.py
-- pairs them and leaves ambiguous cases null rather than guessing.
--
-- Idempotent: migrate.py re-runs every file on startup.

ALTER TABLE items
    ADD COLUMN IF NOT EXISTS imdq60 BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS imdq60_base BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sixty_day_pair VARCHAR(10);

COMMENT ON COLUMN items.imdq60 IS
    'PBS policy_applied_imdq60_flag: the increased-maximum-quantity item that allows a '
    '60-day prescription.';
COMMENT ON COLUMN items.imdq60_base IS
    'PBS policy_applied_imdq60_base_flag: the single-quantity item a 60-day item was '
    'created from.';
COMMENT ON COLUMN items.sixty_day_pair IS
    'The other side of this item''s 30-day/60-day pair (pbs_code), or null if it has '
    'none or the match was ambiguous. Derived by ingest/sixty_day.py; PBS publishes no link.';
COMMENT ON COLUMN items.sixty_day_eligible IS
    'imdq60 OR imdq60_base: the item is part of a 60-day prescribing pair. Before '
    'migration 020 this was wrongly read from continued_dispensing_flag.';

-- items_listing selects i.*, so it must be rebuilt for the new columns to appear.
-- Definition is migration 011's (as recreated in 015), unchanged; keep in step.
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
