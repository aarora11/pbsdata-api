-- Infusible items carry a maximum amount, not a maximum quantity in units.
--
-- Found by spot-checking against pbs.gov.au: the site shows "3000 mcg" as the maximum
-- for bortezomib (12219D) where this API returned null. The normaliser read only
-- maximum_quantity_units, and PBS never sends that for infusibles.
--
-- The two are different measures and must not be conflated. The data dictionary:
--   maximum_quantity_units — "Described in units of the drug that can be dispensed"
--   maximum_amount         — "based on the maximum amount in terms of the
--                             unit_of_measure. Defined only for infusible items,
--                             i.e. program_code = IN, IP, TY, TZ"
-- Measured on 2026-06 they partition exactly: maximum_amount is populated for all 730
-- IN/IP/TY/TZ rows and no others, and maximum_quantity_units for every other program.
-- So max_quantity stays units-only and is correctly null for infusibles.
--
-- unit_of_measure is stored alongside because an amount without its unit is unusable —
-- 3000 means nothing until you know it is mcg. It is also populated for the 104
-- extemporaneous (EP) rows, which carry neither quantity field.
--
-- Idempotent: migrate.py re-runs every file on startup.

ALTER TABLE items
    ADD COLUMN IF NOT EXISTS maximum_amount NUMERIC,
    ADD COLUMN IF NOT EXISTS unit_of_measure VARCHAR(20);

COMMENT ON COLUMN items.maximum_amount IS
    'PBS maximum_amount — the maximum prescribable amount for infusible items '
    '(program_code IN/IP/TY/TZ), expressed in unit_of_measure. Null for every other '
    'program, which uses max_quantity instead. The two never overlap.';

COMMENT ON COLUMN items.unit_of_measure IS
    'PBS unit_of_measure (mg, mcg, iu, g, ml, L). Qualifies maximum_amount; also '
    'populated for extemporaneous items, which carry neither quantity field.';

COMMENT ON COLUMN items.max_quantity IS
    'PBS maximum_quantity_units — the maximum in units of the drug dispensed. Null for '
    'infusible items, which use maximum_amount with unit_of_measure.';

COMMENT ON COLUMN items.max_repeats IS
    'PBS number_of_repeats. Zero is a real value meaning no repeats are allowed, and '
    'must be stored as 0 rather than null — it read as null on 2,608 of 14,782 items '
    'because a falsy-zero test fell through to an absent legacy field.';

-- items_listing selects i.*, so it must be rebuilt for the new columns to appear.
-- Definition is migration 011's, unchanged apart from being recreated; keep in step.
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
