-- Migration 004: align schema with real PBS API field names and structure

-- ── Items: add real fields ────────────────────────────────────────────────────
ALTER TABLE items ADD COLUMN IF NOT EXISTS li_item_id TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS schedule_form TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS manner_of_administration TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS therapeutic_group_title TEXT;
ALTER TABLE items ADD COLUMN IF NOT EXISTS note_indicator BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE items ADD COLUMN IF NOT EXISTS first_listed_date DATE;
ALTER TABLE items ADD COLUMN IF NOT EXISTS organisation_id INTEGER;
ALTER TABLE items ADD COLUMN IF NOT EXISTS section100_only_indicator BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE items ADD COLUMN IF NOT EXISTS extemporaneous_indicator BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE items ADD COLUMN IF NOT EXISTS infusible_indicator BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE items ADD COLUMN IF NOT EXISTS originator_brand_indicator BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE items ADD COLUMN IF NOT EXISTS innovator_indicator BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_items_li_item_id ON items(li_item_id);
CREATE INDEX IF NOT EXISTS idx_items_organisation_id ON items(organisation_id);

-- ── Restrictions: add real fields ─────────────────────────────────────────────
-- res_code already added in 003; add remaining real fields
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS treatment_phase TEXT;
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS authority_method TEXT;
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS treatment_of_code VARCHAR(20);
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS li_html_text TEXT;
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS written_authority_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE restrictions ADD COLUMN IF NOT EXISTS complex_authority_required BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_restrictions_res_code ON restrictions(restriction_code);

-- ── Organisations (from /organisations) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS organisations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    organisation_id INTEGER NOT NULL,
    name TEXT,
    abn VARCHAR(20),
    street_address TEXT,
    city TEXT,
    state VARCHAR(10),
    postcode VARCHAR(10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(organisation_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_organisations_schedule_id ON organisations(schedule_id);
CREATE INDEX IF NOT EXISTS idx_organisations_organisation_id ON organisations(organisation_id);

-- ── Programs (from /programs) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS programs (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    program_code VARCHAR(10) NOT NULL,
    program_title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(program_code, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_programs_schedule_id ON programs(schedule_id);

-- ── ATC codes (from /atc-codes) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS atc_codes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    atc_code VARCHAR(20) NOT NULL,
    atc_description TEXT,
    atc_level INTEGER,
    atc_parent_code VARCHAR(20),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(atc_code, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_atc_codes_schedule_id ON atc_codes(schedule_id);
CREATE INDEX IF NOT EXISTS idx_atc_codes_atc_code ON atc_codes(atc_code);
CREATE INDEX IF NOT EXISTS idx_atc_codes_parent ON atc_codes(atc_parent_code);

-- ── Item <-> ATC relationships (from /item-atc-relationships) ─────────────────
CREATE TABLE IF NOT EXISTS item_atc_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10) NOT NULL,
    atc_code VARCHAR(20) NOT NULL,
    atc_priority_pct NUMERIC(5,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pbs_code, atc_code, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_item_atc_schedule_id ON item_atc_relationships(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_atc_pbs_code ON item_atc_relationships(pbs_code);

-- ── Copayments: schedule-level thresholds (from /copayments) ─────────────────
CREATE TABLE IF NOT EXISTS copayments (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id) UNIQUE,
    general NUMERIC(10,2),
    concessional NUMERIC(10,2),
    safety_net_general NUMERIC(10,2),
    safety_net_concessional NUMERIC(10,2),
    safety_net_card_issue NUMERIC(10,2),
    increased_discount_limit NUMERIC(10,2),
    safety_net_ctg_contribution NUMERIC(10,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_copayments_schedule_id ON copayments(schedule_id);

-- ── Item pricing (from /item-dispensing-rule-relationships) ───────────────────
CREATE TABLE IF NOT EXISTS item_pricing (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    li_item_id TEXT NOT NULL,
    pbs_code VARCHAR(10),
    dispensing_rule_mnem VARCHAR(20),
    brand_premium NUMERIC(10,2) DEFAULT 0,
    commonwealth_price NUMERIC(10,2),
    max_general_patient_charge NUMERIC(10,2),
    special_patient_contribution NUMERIC(10,2),
    fee_dispensing NUMERIC(10,2),
    fee_dispensing_dangerous_drug NUMERIC(10,2),
    fee_container_other NUMERIC(10,2),
    fee_container_injectable NUMERIC(10,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(li_item_id, dispensing_rule_mnem, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_item_pricing_schedule_id ON item_pricing(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_pricing_li_item_id ON item_pricing(li_item_id);
CREATE INDEX IF NOT EXISTS idx_item_pricing_pbs_code ON item_pricing(pbs_code);

-- ── Item <-> organisation relationships (from /item-organisation-relationships)
CREATE TABLE IF NOT EXISTS item_organisation_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10) NOT NULL,
    organisation_id INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pbs_code, organisation_id, schedule_id)
);
CREATE INDEX IF NOT EXISTS idx_item_org_rel_schedule_id ON item_organisation_relationships(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_org_rel_pbs_code ON item_organisation_relationships(pbs_code);
