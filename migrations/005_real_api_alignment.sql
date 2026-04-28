-- Migration 005: align schema with real PBS API field shapes

-- ── Fees: replace invented columns with real PBS API fields ───────────────────
ALTER TABLE fees ADD COLUMN IF NOT EXISTS dispensing_fee_ready_prepared NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS dispensing_fee_dangerous_drug NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS dispensing_fee_extra NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS dispensing_fee_extemporaneous NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS safety_net_recording_fee_ep NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS safety_net_recording_fee_rp NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS dispensing_fee_water_added NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS container_fee_injectable NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS container_fee_other NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS gnrl_copay_discount_general NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS gnrl_copay_discount_hospital NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS con_copay_discount_general NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS con_copay_discount_hospital NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS efc_diluent_fee NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS efc_preparation_fee NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS efc_distribution_fee NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS acss_imdq60_payment NUMERIC(10,2);
ALTER TABLE fees ADD COLUMN IF NOT EXISTS acss_payment NUMERIC(10,2);

-- ── Dispensing rules: make program_code nullable (not present in real API) ────
ALTER TABLE program_dispensing_rules ALTER COLUMN program_code DROP NOT NULL;
ALTER TABLE program_dispensing_rules ADD COLUMN IF NOT EXISTS dispensing_rule_reference TEXT;
ALTER TABLE program_dispensing_rules ADD COLUMN IF NOT EXISTS community_pharmacy_indicator BOOLEAN;

-- ── Indications: make pbs_code nullable (not in real API — comes from item links) ──
-- pbs_code is already nullable (no NOT NULL in migration 003)

-- ── AMT items: add pbs_concept_id field from real API ─────────────────────────
ALTER TABLE amt_items ADD COLUMN IF NOT EXISTS pbs_concept_id INTEGER;
ALTER TABLE amt_items ADD COLUMN IF NOT EXISTS exempt_ind BOOLEAN;
