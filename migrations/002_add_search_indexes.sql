ALTER TABLE items ADD COLUMN IF NOT EXISTS search_vector TEXT
    GENERATED ALWAYS AS (brand_name_lower) STORED;

CREATE INDEX IF NOT EXISTS idx_items_search_vector_trgm
    ON items USING GIN (search_vector gin_trgm_ops);
