-- The allowable extra fee, which is what makes the patient charge reconstructable.
--
-- PBS builds the general patient charge as
--     min(dispensed price + fee_extra + fee_safety_net_recording, general copayment)
--     + brand_premium + special_patient_contribution + therapeutic_group_premium
--
-- Sourced, not inferred: the data dictionary defines fee_extra as "Allowable Extra
-- fee" and fee_safety_net_recording as the "Fee applied for recording against the
-- Safety Net for Ready Prepared items". Measured on 2026-06 the formula reproduces
-- max_general_patient_charge exactly on 36,164 of 38,159 rows, and ~99.5% of the
-- mainstream community-pharmacy and hospital rules.
--
-- It matters because PBS publishes no concessional charge. Reproducing the published
-- general figure with this formula is what makes substituting the concessional
-- copayment a sourced derivation rather than a guess — the Tier B equivalent of
-- checking a join against /item-overview.
--
-- The residual rows are the rp-np-* and rp-np-nc-* families, where the real charge is
-- always LOWER than the formula predicts. Those are reduced- and no-charge settings
-- such as the Prescriber Bag, and capping the concessional figure at the published
-- general charge already covers them.

ALTER TABLE item_pricing ADD COLUMN IF NOT EXISTS fee_extra NUMERIC(10,2);

COMMENT ON COLUMN item_pricing.fee_extra IS
    'PBS fee_extra — "Allowable Extra fee" (data dictionary). Added to the dispensed '
    'price before the copayment cap when computing what a patient is charged.';
