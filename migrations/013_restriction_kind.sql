-- Restrictions carry three different kinds of text, and we served all three as
-- restrictions.
--
-- /restrictions returns restrictions, notes and cautions in one feed. PBS marks the
-- difference three independent ways, all of which agree exactly (3,030 / 444 / 84 on
-- 2026-06): the res_code suffix (_R/_N/_C), the note_indicator and caution_indicator
-- flags, and whether li_html_text is populated. Confirmed against pbs.gov.au, which
-- shows one restriction for item 10001J where this API returned three.
--
-- Idempotent: every statement guards itself, because migrate.py re-runs the whole
-- directory on each startup.

ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS restriction_kind VARCHAR(20);

COMMENT ON COLUMN restrictions.restriction_kind IS
    'restriction | note | caution. From note_indicator/caution_indicator, falling back '
    'to the res_code suffix. Only "restriction" rows are true restrictions.';

COMMENT ON COLUMN restrictions.authority_method IS
    'PBS letter: A (Authority Required), S (Streamlined), R (Restricted). The data '
    'dictionary states this holds the same information as items.benefit_type, which '
    'measures as an exact 1:1 across all 5,726 items carrying both.';

COMMENT ON COLUMN restrictions.li_html_text IS
    'Legal restriction text. Null for notes and cautions by design — the data '
    'dictionary notes they "are not legal". restriction_text falls back to '
    'schedule_html_text so notes and cautions still carry their content.';

COMMENT ON COLUMN restrictions.streamlined_code IS
    'PBS treatment_of_code, which the data dictionary states "also represents the '
    'Streamline Code" for Streamlined Authority listings. Null for other listings. '
    'Previously a copy of restriction_code, which was never the streamlined code.';

-- Backfill existing rows from the res_code suffix. The indicator flags are not stored
-- on this table, but the dictionary documents the suffix as carrying the same meaning
-- ("Relates to N suffix in res_code"), so it reconstructs the split without a re-ingest.
UPDATE restrictions
SET restriction_kind = CASE
        WHEN upper(restriction_code) LIKE '%\_N' THEN 'note'
        WHEN upper(restriction_code) LIKE '%\_C' THEN 'caution'
        ELSE 'restriction'
    END
WHERE restriction_kind IS NULL;

-- ADD CONSTRAINT has no IF NOT EXISTS, so guard it explicitly.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'restrictions_kind_check'
    ) THEN
        ALTER TABLE restrictions
            ADD CONSTRAINT restrictions_kind_check
            CHECK (restriction_kind IS NULL
                   OR restriction_kind IN ('restriction', 'note', 'caution'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_restrictions_kind ON restrictions (restriction_kind);
CREATE INDEX IF NOT EXISTS idx_restrictions_item_kind ON restrictions (item_id, restriction_kind);
