-- Migration 003: expand schema to support all 13 PBS API endpoints

-- 1. New columns on items (from /item-overview)
ALTER TABLE items ADD COLUMN IF NOT EXISTS artg_id VARCHAR(20);
ALTER TABLE items ADD COLUMN IF NOT EXISTS sponsor TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS caution TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS biosimilar BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. New columns on restrictions (richer data from /restrictions)
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS restriction_code VARCHAR(20);
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS restriction_type VARCHAR(30);
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS clinical_criteria TEXT;

-- 3. Fees (from /fees)
CREATE TABLE IF NOT EXISTS fees (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    fee_code VARCHAR(20) NOT NULL,
    fee_type VARCHAR(50),
    description TEXT,
    amount NUMERIC(10,2),
    patient_contribution NUMERIC(10,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(fee_code, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_fees_schedule_id ON fees(schedule_id);
CREATE INDEX IF NOT EXISTS idx_fees_fee_code ON fees(fee_code);

-- 4. Prescribing texts (from /prescribing-texts)
CREATE TABLE IF NOT EXISTS prescribing_texts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    prescribing_text_id VARCHAR(20) NOT NULL,
    text_type VARCHAR(30),
    complex_authority_required BOOLEAN NOT NULL DEFAULT FALSE,
    prescribing_txt TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(prescribing_text_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_prescribing_texts_schedule_id ON prescribing_texts(schedule_id);
CREATE INDEX IF NOT EXISTS idx_prescribing_texts_text_id ON prescribing_texts(prescribing_text_id);

-- 5. Indications (from /indications)
CREATE TABLE IF NOT EXISTS indications (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    indication_id VARCHAR(20) NOT NULL,
    pbs_code VARCHAR(10),
    indication_text TEXT,
    condition_description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(indication_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_indications_schedule_id ON indications(schedule_id);
CREATE INDEX IF NOT EXISTS idx_indications_pbs_code ON indications(pbs_code);

-- 6. AMT items / ATC classification tree (from /amt-items)
CREATE TABLE IF NOT EXISTS amt_items (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    amt_id VARCHAR(30) NOT NULL,
    concept_type VARCHAR(30),
    preferred_term TEXT,
    atc_code VARCHAR(20),
    parent_amt_id VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(amt_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_amt_items_schedule_id ON amt_items(schedule_id);
CREATE INDEX IF NOT EXISTS idx_amt_items_atc_code ON amt_items(atc_code);

-- 7. Program dispensing rules (from /program-dispensing-rules)
CREATE TABLE IF NOT EXISTS program_dispensing_rules (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    program_code VARCHAR(10) NOT NULL,
    rule_code VARCHAR(20) NOT NULL,
    dispensing_quantity INTEGER,
    dispensing_unit TEXT,
    repeats_allowed INTEGER,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(rule_code, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_program_dispensing_rules_schedule_id ON program_dispensing_rules(schedule_id);
CREATE INDEX IF NOT EXISTS idx_program_dispensing_rules_program_code ON program_dispensing_rules(program_code);

-- 8. Official PBS summary of changes (from /summary-of-changes)
CREATE TABLE IF NOT EXISTS summary_of_changes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10),
    change_type VARCHAR(50),
    effective_date DATE,
    description TEXT,
    section VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_summary_of_changes_schedule_id ON summary_of_changes(schedule_id);
CREATE INDEX IF NOT EXISTS idx_summary_of_changes_pbs_code ON summary_of_changes(pbs_code);

-- 9. Item <-> AMT relationships (from /item-amt)
CREATE TABLE IF NOT EXISTS item_amt_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10) NOT NULL,
    amt_id VARCHAR(30) NOT NULL,
    relationship_type VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pbs_code, amt_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_item_amt_schedule_id ON item_amt_relationships(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_amt_pbs_code ON item_amt_relationships(pbs_code);

-- 10. Item <-> dispensing rules (from /item-dispensing-rules-relationship)
CREATE TABLE IF NOT EXISTS item_dispensing_rules (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10) NOT NULL,
    rule_code VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pbs_code, rule_code, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_item_dispensing_rules_schedule_id ON item_dispensing_rules(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_dispensing_rules_pbs_code ON item_dispensing_rules(pbs_code);

-- 11. Item <-> restriction relationships (from /item-restriction-relationships)
CREATE TABLE IF NOT EXISTS item_restriction_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10) NOT NULL,
    restriction_code VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pbs_code, restriction_code, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_item_restriction_rel_schedule_id ON item_restriction_relationships(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_restriction_rel_pbs_code ON item_restriction_relationships(pbs_code);

-- 12. Restriction <-> prescribing text relationships (from /restriction-prescribing-text-relationships)
CREATE TABLE IF NOT EXISTS restriction_prescribing_text_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    restriction_code VARCHAR(20) NOT NULL,
    prescribing_text_id VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(restriction_code, prescribing_text_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_restr_prescrib_text_schedule_id ON restriction_prescribing_text_relationships(schedule_id);

-- 13. Item <-> prescribing text relationships (from /item-prescribing-texts)
CREATE TABLE IF NOT EXISTS item_prescribing_text_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10) NOT NULL,
    prescribing_text_id VARCHAR(20) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pbs_code, prescribing_text_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_item_prescrib_text_schedule_id ON item_prescribing_text_relationships(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_prescrib_text_pbs_code ON item_prescribing_text_relationships(pbs_code);
