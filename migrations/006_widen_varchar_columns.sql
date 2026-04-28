-- Migration 006: widen VARCHAR columns that are too narrow for real PBS API data

-- summary_of_changes: section holds endpoint names (e.g. "/item-restriction-relationships" = 31 chars)
ALTER TABLE summary_of_changes ALTER COLUMN section TYPE TEXT;
-- pbs_code in summary_of_changes: derived from li_item_id which can exceed 10 chars
ALTER TABLE summary_of_changes ALTER COLUMN pbs_code TYPE TEXT;
