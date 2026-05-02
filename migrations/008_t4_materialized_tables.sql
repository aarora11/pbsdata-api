-- T4 Market: pre-computed aggregate tables populated at ingest time.
-- Querying these is a simple SELECT instead of a heavy cross-join aggregation.

CREATE TABLE IF NOT EXISTS market_atc_summary (
    id                       UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id              UUID        NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    atc_code                 TEXT        NOT NULL,
    unique_prescribing_rules INTEGER     NOT NULL DEFAULT 0,
    unique_brands            INTEGER     NOT NULL DEFAULT 0,
    authority_required_count INTEGER     NOT NULL DEFAULT 0,
    streamlined_count        INTEGER     NOT NULL DEFAULT 0,
    f1_count                 INTEGER     NOT NULL DEFAULT 0,
    f2_count                 INTEGER     NOT NULL DEFAULT 0,
    sixty_day_items          INTEGER     NOT NULL DEFAULT 0,
    biosimilar_items         INTEGER     NOT NULL DEFAULT 0,
    min_dpmq                 NUMERIC(12, 4),
    max_dpmq                 NUMERIC(12, 4),
    mean_dpmq                NUMERIC(12, 4),
    median_dpmq              NUMERIC(12, 4),
    manufacturer_count       INTEGER     NOT NULL DEFAULT 0,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (atc_code, schedule_id)
);

CREATE INDEX IF NOT EXISTS idx_market_atc_summary_schedule
    ON market_atc_summary (schedule_id);

CREATE TABLE IF NOT EXISTS market_manufacturer_landscape (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    schedule_id     UUID        NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    organisation_id TEXT        NOT NULL,
    organisation_name TEXT,
    atc_prefix      TEXT,
    item_count      INTEGER     NOT NULL DEFAULT 0,
    pbs_code_count  INTEGER     NOT NULL DEFAULT 0,
    f1_count        INTEGER     NOT NULL DEFAULT 0,
    f2_count        INTEGER     NOT NULL DEFAULT 0,
    biosimilar_count INTEGER    NOT NULL DEFAULT 0,
    authority_count INTEGER     NOT NULL DEFAULT 0,
    min_dpmq        NUMERIC(12, 4),
    max_dpmq        NUMERIC(12, 4),
    mean_dpmq       NUMERIC(12, 4),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organisation_id, atc_prefix, schedule_id)
);

CREATE INDEX IF NOT EXISTS idx_market_mfr_landscape_schedule
    ON market_manufacturer_landscape (schedule_id);

CREATE INDEX IF NOT EXISTS idx_market_mfr_landscape_org
    ON market_manufacturer_landscape (organisation_id, schedule_id);
