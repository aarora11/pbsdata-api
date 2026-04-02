CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

CREATE TABLE IF NOT EXISTS schedules (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    month VARCHAR(7) UNIQUE NOT NULL,
    released_at TIMESTAMPTZ NOT NULL,
    is_embargo BOOLEAN NOT NULL DEFAULT FALSE,
    ingest_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    ingest_started_at TIMESTAMPTZ,
    ingest_completed_at TIMESTAMPTZ,
    item_count INTEGER DEFAULT 0,
    change_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS medicines (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    ingredient TEXT NOT NULL,
    ingredient_lower TEXT NOT NULL,
    atc_code VARCHAR(20),
    therapeutic_group TEXT,
    therapeutic_subgroup TEXT,
    has_generic BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ingredient_lower)
);

CREATE TABLE IF NOT EXISTS items (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    pbs_code VARCHAR(10) NOT NULL,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    medicine_id UUID NOT NULL REFERENCES medicines(id),
    brand_name TEXT NOT NULL,
    brand_name_lower TEXT NOT NULL,
    form TEXT,
    strength TEXT,
    pack_size INTEGER,
    pack_unit TEXT,
    benefit_type VARCHAR(30) NOT NULL,
    formulary VARCHAR(10),
    section VARCHAR(10),
    program_code VARCHAR(10),
    general_charge NUMERIC(10,2),
    concessional_charge NUMERIC(10,2),
    government_price NUMERIC(10,2),
    brand_premium NUMERIC(10,2) DEFAULT 0,
    brand_premium_counts_to_safety_net BOOLEAN NOT NULL DEFAULT FALSE,
    sixty_day_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    max_quantity INTEGER,
    max_repeats INTEGER,
    dangerous_drug BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(pbs_code, schedule_id)
);

CREATE TABLE IF NOT EXISTS restrictions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    item_id UUID NOT NULL REFERENCES items(id),
    streamlined_code VARCHAR(20),
    indication TEXT,
    restriction_text TEXT,
    prescriber_type VARCHAR(10),
    authority_required BOOLEAN NOT NULL DEFAULT FALSE,
    continuation_only BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS changes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id UUID NOT NULL REFERENCES schedules(id),
    pbs_code VARCHAR(10) NOT NULL,
    change_type VARCHAR(30) NOT NULL,
    field_name VARCHAR(50),
    old_value TEXT,
    new_value TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    key_prefix VARCHAR(20) NOT NULL,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    name TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    tier VARCHAR(20) NOT NULL DEFAULT 'sandbox',
    monthly_limit INTEGER NOT NULL DEFAULT 500,
    requests_this_month INTEGER NOT NULL DEFAULT 0,
    history_months_limit INTEGER NOT NULL DEFAULT 3,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ,
    usage_reset_at TIMESTAMPTZ NOT NULL DEFAULT (date_trunc('month', NOW()) + INTERVAL '1 month'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhooks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    api_key_id UUID NOT NULL REFERENCES api_keys(id),
    endpoint_url TEXT NOT NULL,
    event_types TEXT[] NOT NULL DEFAULT '{}',
    signing_secret TEXT,
    secret_hash TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_delivery_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    webhook_id UUID NOT NULL REFERENCES webhooks(id),
    event_type TEXT NOT NULL,
    payload JSONB,
    response_status INTEGER,
    response_body TEXT,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    success BOOLEAN NOT NULL DEFAULT FALSE,
    attempt_number INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_items_pbs_code ON items(pbs_code);
CREATE INDEX IF NOT EXISTS idx_items_schedule_id ON items(schedule_id);
CREATE INDEX IF NOT EXISTS idx_items_medicine_id ON items(medicine_id);
CREATE INDEX IF NOT EXISTS idx_items_brand_name_lower ON items(brand_name_lower);
CREATE INDEX IF NOT EXISTS idx_medicines_ingredient_lower ON medicines(ingredient_lower);
CREATE INDEX IF NOT EXISTS idx_medicines_ingredient_trgm ON medicines USING GIN (ingredient_lower gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_changes_schedule_id ON changes(schedule_id);
CREATE INDEX IF NOT EXISTS idx_changes_pbs_code ON changes(pbs_code);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_webhooks_api_key_id ON webhooks(api_key_id);
