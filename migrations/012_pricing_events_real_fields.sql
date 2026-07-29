-- Migration 012: give item_pricing_events the fields PBS actually publishes.
--
-- ITEM_PRICING_EVENT is keyed on (li_item_id, schedule_code, event_type_code)
-- per the PBS API V3 Data Dictionary (docs/reference/). The live feed carries
-- exactly four columns:
--     li_item_id, event_type_code, percentage_applied, schedule_code
--
-- Our table instead had `pbs_code`, `event_type`, `effective_date`,
-- `previous_price` and `new_price`. The last three do not exist in the feed at
-- all, and the normaliser read `e.get("pbs_code")` — also absent — so every row
-- landed with a NULL key and every field null. The endpoint went unnoticed
-- because PBS returns no rows for most schedules (0 on 2026-05, 10 on 2026-06).
--
-- This adds the real fields. The three fabricated columns are retained for now
-- because api/routers/item_pricing_events.py, drugs.py and market.py still
-- select and filter on them; they remain permanently NULL and should be removed
-- once those callers are reworked onto percentage_applied.
--
-- migrate.py re-runs every migration on startup, so this must stay idempotent.

ALTER TABLE item_pricing_events
    ADD COLUMN IF NOT EXISTS li_item_id         TEXT,
    ADD COLUMN IF NOT EXISTS percentage_applied NUMERIC(6,2);

CREATE INDEX IF NOT EXISTS idx_item_pricing_events_li_item_id
    ON item_pricing_events (li_item_id, schedule_id);

CREATE INDEX IF NOT EXISTS idx_item_pricing_events_pbs_code
    ON item_pricing_events (pbs_code, schedule_id);

COMMENT ON COLUMN item_pricing_events.percentage_applied IS
    'PBS: brands of pharmaceutical items with approved ex-manufacturer price reduced '
    'by a lower percentage than would otherwise apply.';
COMMENT ON COLUMN item_pricing_events.event_type IS
    'PBS event_type_code: code describing the event that triggers a price change.';
COMMENT ON COLUMN item_pricing_events.effective_date IS
    'NOT PUBLISHED BY PBS — always NULL. Remove once callers stop selecting it.';
COMMENT ON COLUMN item_pricing_events.previous_price IS
    'NOT PUBLISHED BY PBS — always NULL. Remove once callers stop selecting it.';
COMMENT ON COLUMN item_pricing_events.new_price IS
    'NOT PUBLISHED BY PBS — always NULL. Remove once callers stop selecting it.';
