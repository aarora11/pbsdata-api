-- Dispensing rules were keyed on the mnemonic, which is not a key.
--
-- The data dictionary is explicit: dispensing_rule_reference is the "Unique identifier
-- for a Dispensing Rule", and it is the primary key of both DISPENSING_RULE_T
-- (/dispensing-rules) and ITEM_DISPENSING_RULE_RLTD_T
-- (/item-dispensing-rule-relationships). dispensing_rule_mnem is only a "Short
-- descriptive phrase to enable the identification of a GROUP of Dispensing Rules".
--
-- 17 references collapse to 10 mnemonics — s90-cp alone covers in-s90-cp, rp-s90-cp,
-- rp-np-s90-cp and rp-np-nc-s90-cp. Keying on the mnemonic meant
-- program_dispensing_rules held 10 rows where PBS publishes 17, so /v1/dispensing-rules
-- was silently missing 7 rules, and item_dispensing_rules disagreed with PBS's own
-- item-overview on 100% of rows.
--
-- rule_code now carries the reference (the real key, so the existing unique constraints
-- become correct as they stand), and the mnemonic moves to its own column as the group
-- label it always was. dispensing_rule_title is the human-readable name PBS itself
-- displays — pbs.gov.au shows "S100 HSD Public" for an item whose rule is
-- rp-s94-public-s100, titled "Public Hospital S100".
--
-- No backfill: both loaders delete per schedule before inserting, so a re-ingest
-- repopulates these with references. Idempotent — migrate.py re-runs every file.

-- References run to 19 characters today (rp-s94-private-s100) with no headroom in
-- VARCHAR(20).
ALTER TABLE item_dispensing_rules    ALTER COLUMN rule_code TYPE VARCHAR(40);
ALTER TABLE program_dispensing_rules ALTER COLUMN rule_code TYPE VARCHAR(40);

ALTER TABLE item_dispensing_rules
    ADD COLUMN IF NOT EXISTS dispensing_rule_mnem VARCHAR(40);

ALTER TABLE program_dispensing_rules
    ADD COLUMN IF NOT EXISTS dispensing_rule_mnem VARCHAR(40),
    ADD COLUMN IF NOT EXISTS dispensing_rule_title TEXT;

COMMENT ON COLUMN item_dispensing_rules.rule_code IS
    'PBS dispensing_rule_reference — the unique identifier and the documented key of '
    '/item-dispensing-rule-relationships. Previously held dispensing_rule_mnem, which '
    'identifies a group of rules and is not unique.';

COMMENT ON COLUMN item_dispensing_rules.dispensing_rule_mnem IS
    'PBS dispensing_rule_mnem — names a GROUP of dispensing rules (10 mnemonics span '
    'the 17 references). Useful for grouping; never as a key.';

COMMENT ON COLUMN program_dispensing_rules.rule_code IS
    'PBS dispensing_rule_reference — the unique identifier and the documented key of '
    '/dispensing-rules.';

COMMENT ON COLUMN program_dispensing_rules.dispensing_rule_title IS
    'PBS dispensing_rule_title, e.g. "Community Pharmacy", "Public Hospital S100". This '
    'is the concept pbs.gov.au surfaces to the public; the codes are never displayed. '
    'Not unique — it collapses the same way the mnemonic does.';

COMMENT ON COLUMN program_dispensing_rules.community_pharmacy_indicator IS
    'True for the s90-cp family. Identifies the retail rule whose '
    'cmnwlth_dsp_price_max_qty is the DPMQ pbs.gov.au publishes.';

CREATE INDEX IF NOT EXISTS idx_item_dispensing_rules_mnem
    ON item_dispensing_rules (dispensing_rule_mnem);
CREATE INDEX IF NOT EXISTS idx_program_dispensing_rules_mnem
    ON program_dispensing_rules (dispensing_rule_mnem);
