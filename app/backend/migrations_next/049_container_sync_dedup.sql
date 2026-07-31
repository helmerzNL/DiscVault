-- Container sync idempotency & dedup (server-side).
--
-- Extends the movie identity ladder's schema (045_sync_dedup.sql) onto
-- containers (box_set / vault / collection), per sync-contract.md §2b/§4b.
-- All columns are nullable so existing rows and older clients keep working;
-- the server fills client_id as clients adopt the new payload field.

-- 1. Persistent per-record client identifier, mirroring movies.client_id.
--    A client mints this UUID once when a local container record is
--    created and reuses it on every retry, so trede 1 of the container
--    ladder (§2b) is a direct lookup.
ALTER TABLE containers
    ADD COLUMN IF NOT EXISTS client_id text;

-- Single shared catalog (containers.owner_id is used for shelf ownership,
-- not for sync scoping), so a partial unique index on client_id alone
-- mirrors uq_movies_client_id. Legacy NULLs are ignored and never collide.
CREATE UNIQUE INDEX IF NOT EXISTS uq_containers_client_id
    ON containers(client_id)
    WHERE client_id IS NOT NULL;

-- 2. Supporting match index for trede 4 (normalized title + container_type),
--    scoped to live rows so tombstones never win a title adoption match.
--    Containers have no year concept, so this indexes container_type where
--    idx_movies_titleyear_live indexes year.
CREATE INDEX IF NOT EXISTS idx_containers_title_type_live
    ON containers(lower(title), container_type)
    WHERE deleted_at IS NULL;
