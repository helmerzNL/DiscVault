-- Sync idempotency & dedup (server-side).
--
-- Backward-compatible schema for the create-path identity ladder
-- (clientId -> barcode -> tmdb+format+edition -> title+year) and for
-- tombstone-based deletions. All columns are nullable so existing rows and
-- older clients keep working; the server fills client_id as clients adopt.

-- 1. Persistent per-record client identifier.
--    The iOS client mints this UUID once when a local movie record is created
--    and reuses it on every retry, so trede 1 of the ladder is a direct lookup.
ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS client_id text;

-- Single shared catalog (movies.owner_id is nullable and unused by sync), so the
-- handoff's (owner_id, client_id) unique index collapses to a partial unique on
-- client_id alone. Legacy NULLs are ignored and never collide.
CREATE UNIQUE INDEX IF NOT EXISTS uq_movies_client_id
    ON movies(client_id)
    WHERE client_id IS NOT NULL;

-- 2. Soft-delete tombstones on movies and box-set containers. Hard deletes go
--    away in the sync API so the delta feed can surface deletions and a client
--    that re-pushes a stale copy does not resurrect the record.
ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE containers
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_movies_deleted_at
    ON movies(deleted_at)
    WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_containers_deleted_at
    ON containers(deleted_at)
    WHERE deleted_at IS NOT NULL;

-- 3. Supporting match index for trede 4 (normalized title + year), scoped to
--    live rows so tombstones never win a title/year adoption match.
--    Trede 3 (tmdb + format + edition) reuses idx_movie_identifiers_lookup plus
--    the movies row, so it needs no new index here.
CREATE INDEX IF NOT EXISTS idx_movies_titleyear_live
    ON movies(lower(title), year)
    WHERE deleted_at IS NULL;
