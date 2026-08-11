-- item_pricing_events carried three columns PBS does not publish.
--
-- Migration 012 established that the feed sends only li_item_id, event_type_code,
-- percentage_applied and schedule_code, and documented previous_price, new_price and
-- effective_date as permanently null pending removal once no router selected them.
--
-- Leaving them was not harmless. /market/price-reduction-events filtered on
-- `previous_price > 0`, which is NULL on every row, so the whole WHERE clause could
-- never be true and the endpoint returned an empty list for every possible query.
-- /market/price-pressure-index counted reductions the same way, so that signal was
-- permanently zero and dragged the published index down. Both now read
-- percentage_applied, which the feed populates on every row.
--
-- Idempotent: migrate.py re-runs every file on startup.

ALTER TABLE item_pricing_events
    DROP COLUMN IF EXISTS previous_price,
    DROP COLUMN IF EXISTS new_price,
    DROP COLUMN IF EXISTS effective_date;

COMMENT ON COLUMN item_pricing_events.percentage_applied IS
    'PBS percentage_applied — the magnitude of the price event, as a percentage. This '
    'is the only quantity PBS publishes for an event; there is no before/after price '
    'pair. Verified on 10104T, whose SPR_SINGLE_NB event of 25.00 matches its 54.63 to '
    '40.97 move exactly.';
