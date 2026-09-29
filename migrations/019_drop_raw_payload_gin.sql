-- Migration 019: drop the unused GIN index on raw_feed_rows.payload.
--
-- Created by 010 for containment queries that were never written: nothing in the
-- codebase queries payload with @>, and the one ->> lookup cannot use a
-- jsonb_path_ops index. It cost ~36 MB per schedule month, a third of the raw
-- layer's index size. 010 no longer creates it, so this stays dropped on re-run.

DROP INDEX IF EXISTS raw_feed_rows_payload_gin;
