-- item_pricing was missing the dispensed price entirely.
--
-- `commonwealth_price` maps to cmnwlth_price_to_pharmacist — "the price that the
-- pharmacist pays for the pack" (data dictionary). The API called it the DPMQ and
-- built the patient cost breakdown on it, so every derived figure was computed from a
-- number materially below the real dispensed price. On 10005N that is 5.99 against a
-- true DPMQ of 19.78.
--
-- The dispensed price is cmnwlth_dsp_price_max_qty, confirmed against pbs.gov.au on
-- the current schedule: 13842M shows 22.84 and 10005N shows 20.17, both to the cent.
-- (The v3.6.5 dictionary describes this field as "Manufacturers Price to Pharmacist",
-- which contradicts both its own name and the published figures — the name and the
-- evidence agree, so they win.)
--
-- max_record_val_for_safety_net is a separate published field and is NOT what the
-- patient pays: brand premiums do not accrue to safety net, so Lamictal charges the
-- patient 33.16 while recording only 24.38. Estimating scripts-to-threshold from the
-- amount paid overstates progress toward the safety net.
--
-- Idempotent: migrate.py re-runs every file on startup.

ALTER TABLE item_pricing
    ADD COLUMN IF NOT EXISTS dispensed_price_max_qty       NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS max_record_val_for_safety_net NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS fee_safety_net_recording      NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS therapeutic_group_premium     NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS dispensing_rule_reference     VARCHAR(40);

COMMENT ON COLUMN item_pricing.dispensed_price_max_qty IS
    'PBS cmnwlth_dsp_price_max_qty — the dispensed price for maximum quantity (DPMQ), '
    'the figure pbs.gov.au publishes. Add brand_premium for the brand-specific price: '
    '13842M is 22.84 base and 31.00 for Lamictal, matching the site exactly.';

COMMENT ON COLUMN item_pricing.commonwealth_price IS
    'PBS cmnwlth_price_to_pharmacist — what the pharmacist pays for the pack. NOT the '
    'dispensed price; use dispensed_price_max_qty for anything patient-facing.';

COMMENT ON COLUMN item_pricing.max_general_patient_charge IS
    'PBS max_general_patient_charge — "Shown as General Patient Charge on PBS website" '
    '(data dictionary). Published, not derived: it already accounts for the copayment '
    'cap, brand premium, allowable additional fees on under-copayment items, and '
    'program rules such as RPBS patients paying the concessional rate. Never recompute it.';

COMMENT ON COLUMN item_pricing.max_record_val_for_safety_net IS
    'PBS max_record_val_for_safety_net — the amount recorded against a Safety Net card, '
    'capped at the general copayment and excluding brand premiums. Differs from what the '
    'patient pays; this is the correct basis for scripts-to-threshold estimates.';

COMMENT ON COLUMN item_pricing.dispensing_rule_reference IS
    'PBS dispensing_rule_reference — the unique rule identifier. dispensing_rule_mnem '
    'names a group of rules and is not a key.';

-- Re-key on the reference, matching migration 014. No rows are lost: on 2026-06 no
-- brand carries two rules sharing a mnemonic, so the old key happened not to collapse —
-- but it would the moment one did.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conname = 'item_pricing_li_item_id_dispensing_rule_mnem_schedule_id_key') THEN
        ALTER TABLE item_pricing
            DROP CONSTRAINT item_pricing_li_item_id_dispensing_rule_mnem_schedule_id_key;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'item_pricing_li_item_rule_schedule_key') THEN
        ALTER TABLE item_pricing
            ADD CONSTRAINT item_pricing_li_item_rule_schedule_key
            UNIQUE (li_item_id, dispensing_rule_reference, schedule_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_item_pricing_rule_reference
    ON item_pricing (dispensing_rule_reference);
