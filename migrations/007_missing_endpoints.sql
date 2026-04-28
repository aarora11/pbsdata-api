-- Migration 007: add tables for the 13 previously missing PBS API endpoints

CREATE TABLE IF NOT EXISTS containers (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    container_code TEXT NOT NULL,
    container_name TEXT,
    mark_up NUMERIC(10,4),
    agreed_purchasing_unit NUMERIC(10,4),
    average_exact_unit_price NUMERIC(14,8),
    average_rounded_unit_price NUMERIC(10,4),
    container_type TEXT,
    container_quantity INTEGER,
    container_unit_of_measure TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (container_code, schedule_id)
);

CREATE TABLE IF NOT EXISTS container_organisation_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    container_code TEXT NOT NULL,
    organisation_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (container_code, organisation_id, schedule_id)
);

CREATE TABLE IF NOT EXISTS criteria (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    criteria_id TEXT NOT NULL,
    criteria_type TEXT,
    parameter_relationship TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (criteria_id, schedule_id)
);

CREATE TABLE IF NOT EXISTS criteria_parameter_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    criteria_id TEXT NOT NULL,
    parameter_id TEXT NOT NULL,
    pt_position INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (criteria_id, parameter_id, schedule_id)
);

CREATE TABLE IF NOT EXISTS parameters (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    parameter_id TEXT NOT NULL,
    assessment_type TEXT,
    parameter_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parameter_id, schedule_id)
);

-- prescribers is an item <-> prescriber-type relationship
CREATE TABLE IF NOT EXISTS item_prescribers (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code TEXT NOT NULL,
    prescriber_code TEXT NOT NULL,
    prescriber_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pbs_code, prescriber_code, schedule_id)
);

CREATE TABLE IF NOT EXISTS markup_bands (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    markup_band_code TEXT NOT NULL,
    program_code TEXT,
    dispensing_rule_mnem TEXT,
    limit_amount NUMERIC(10,4),
    variable_rate NUMERIC(10,4),
    offset_amount NUMERIC(10,4),
    fixed_amount NUMERIC(10,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- item_pricing_events returned 204 for the March 2026 schedule;
-- fields are provisional and can be refined once we see live data.
CREATE TABLE IF NOT EXISTS item_pricing_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code TEXT,
    event_type TEXT,
    effective_date DATE,
    previous_price NUMERIC(10,4),
    new_price NUMERIC(10,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS extemporaneous_ingredients (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code TEXT NOT NULL,
    agreed_purchasing_unit NUMERIC(10,4),
    exact_tenth_gram_per_ml_price NUMERIC(14,8),
    exact_one_gram_per_ml_price NUMERIC(14,8),
    exact_ten_gram_per_ml_price NUMERIC(14,8),
    exact_hundred_gram_per_ml_price NUMERIC(14,8),
    rounded_tenth_gram_per_ml_price NUMERIC(10,4),
    rounded_one_gram_per_ml_price NUMERIC(10,4),
    rounded_ten_gram_per_ml_price NUMERIC(10,4),
    rounded_hundred_gram_per_ml_price NUMERIC(10,4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pbs_code, schedule_id)
);

CREATE TABLE IF NOT EXISTS extemporaneous_preparations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code TEXT NOT NULL,
    preparation TEXT,
    maximum_quantity INTEGER,
    maximum_quantity_unit TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pbs_code, schedule_id)
);

CREATE TABLE IF NOT EXISTS extemporaneous_prep_sfp_relationships (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    sfp_pbs_code TEXT NOT NULL,
    ex_prep_pbs_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (sfp_pbs_code, ex_prep_pbs_code, schedule_id)
);

CREATE TABLE IF NOT EXISTS extemporaneous_tariffs (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code TEXT NOT NULL,
    drug_name TEXT,
    agreed_purchasing_unit NUMERIC(10,4),
    markup NUMERIC(10,4),
    rounded_rec_one_tenth_gram NUMERIC(10,4),
    rounded_rec_one_gram NUMERIC(10,4),
    rounded_rec_ten_gram NUMERIC(10,4),
    rounded_rec_hundred_gram NUMERIC(10,4),
    exact_rec_one_tenth_gram NUMERIC(14,8),
    exact_rec_one_gram NUMERIC(14,8),
    exact_rec_ten_gram NUMERIC(14,8),
    exact_rec_hundred_gram NUMERIC(14,8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pbs_code, schedule_id)
);

CREATE TABLE IF NOT EXISTS standard_formula_preparations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code TEXT NOT NULL,
    sfp_drug_name TEXT,
    sfp_reference TEXT,
    container_fee NUMERIC(10,4),
    dispensing_fee_max_quantity NUMERIC(10,4),
    safety_net_price NUMERIC(10,4),
    maximum_patient_charge NUMERIC(10,4),
    maximum_quantity_unit TEXT,
    maximum_quantity INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pbs_code, schedule_id)
);

-- Indexes for common lookup patterns
CREATE INDEX IF NOT EXISTS idx_containers_schedule_id ON containers(schedule_id);
CREATE INDEX IF NOT EXISTS idx_criteria_schedule_id ON criteria(schedule_id);
CREATE INDEX IF NOT EXISTS idx_parameters_schedule_id ON parameters(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_prescribers_schedule_id ON item_prescribers(schedule_id);
CREATE INDEX IF NOT EXISTS idx_item_prescribers_pbs_code ON item_prescribers(pbs_code);
CREATE INDEX IF NOT EXISTS idx_markup_bands_schedule_id ON markup_bands(schedule_id);
CREATE INDEX IF NOT EXISTS idx_extemporaneous_ingredients_schedule_id ON extemporaneous_ingredients(schedule_id);
CREATE INDEX IF NOT EXISTS idx_extemporaneous_preparations_schedule_id ON extemporaneous_preparations(schedule_id);
CREATE INDEX IF NOT EXISTS idx_extemporaneous_tariffs_schedule_id ON extemporaneous_tariffs(schedule_id);
CREATE INDEX IF NOT EXISTS idx_standard_formula_preparations_schedule_id ON standard_formula_preparations(schedule_id);
