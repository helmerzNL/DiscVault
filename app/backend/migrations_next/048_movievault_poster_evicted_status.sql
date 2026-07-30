-- DiscVault Next: Add 'evicted' status to movievault_v2_poster_cache.
--
-- A poster whose remote URL returns HTTP 404 is not an error but a tombstone:
-- the asset has been permanently removed from MovieVault.  We track it as
-- 'evicted' so the worker does not retry it and downstream code can clean up
-- linked entity_media rows.

ALTER TABLE movievault_v2_poster_cache
    DROP CONSTRAINT IF EXISTS movievault_v2_poster_cache_status_check;

ALTER TABLE movievault_v2_poster_cache
    ADD CONSTRAINT movievault_v2_poster_cache_status_check
    CHECK (status IN ('pending', 'ready', 'degraded', 'error', 'evicted'));
