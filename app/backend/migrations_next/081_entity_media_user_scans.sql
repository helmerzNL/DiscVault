-- DiscVault Next: the user's own photographs of a release, container or series.
--
-- Every other image in DiscVault comes from a source that can hand it over
-- again. A photograph of the spine of a rare edition, of the insert, of the
-- disc itself, exists only where the user put it. That difference is the whole
-- reason this row is worth carrying: losing it is not a refresh away from
-- repair.
--
-- Nothing here creates a table. `entity_media` already carries the four things
-- a curated image needs -- a role, an order, a primary flag, and a soft delete
-- with a trash retention (017) plus a hidden flag (043). A parallel table would
-- need the authorisation branch, the trash sweeper and the read helper all a
-- second time, and the second copy is where the two would come to disagree.
--
-- What it adds is what an image needs to survive *synchronisation*, which
-- `entity_media` was never asked to do before:
--
--   client_id   a per-record token minted by the client, set-once, exactly as
--               `movies.client_id` and `containers.client_id` already work
--               (sync-contract §4.5). It is what makes an upload retried after
--               a timeout resolve to the record it already created instead of
--               a second one.
--   label       which part of the packaging this is -- front, back, spine,
--               insert, disc. Free text rather than a CHECK: the vocabulary is
--               a display concern, and a client sending a word this server has
--               not heard of should get an image with an odd caption, not a
--               400 that loses the photograph.
--   updated_at  when this row last moved. The arbiter between two devices is
--               the sync revision, not this column, but a row that never
--               records being touched cannot be reasoned about at all.
--   created_by  who uploaded it. `deleted_by` has existed since 017 and its
--               counterpart did not, so the trash could say who removed an
--               image and nobody could say who added it.

DO $$
DECLARE
    constraint_name text;
BEGIN
    IF to_regclass('public.media_assets') IS NULL
       OR to_regclass('public.entity_media') IS NULL THEN
        RETURN;
    END IF;

    -- `kind` is constrained to a fixed vocabulary and 'scan' is not in it.
    -- The constraint is looked up rather than named literally: it was created
    -- inline in 003 and therefore carries whatever name PostgreSQL chose,
    -- which is stable in practice and not guaranteed by anything.
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE nsp.nspname = 'public'
      AND rel.relname = 'media_assets'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%kind%'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE media_assets DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE media_assets
        ADD CONSTRAINT media_assets_kind_check
        CHECK (kind IN ('poster', 'backdrop', 'profile', 'still', 'logo', 'scan'));

    ALTER TABLE entity_media
        ADD COLUMN IF NOT EXISTS client_id  text,
        ADD COLUMN IF NOT EXISTS label      text,
        ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now(),
        ADD COLUMN IF NOT EXISTS created_by uuid;

    -- Added separately from the column so a re-run finds the column present and
    -- still installs the reference; ADD COLUMN IF NOT EXISTS would skip both.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_entity_media_created_by'
    ) AND to_regclass('public.users') IS NOT NULL THEN
        ALTER TABLE entity_media
            ADD CONSTRAINT fk_entity_media_created_by
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
    END IF;

    -- Set-once token, unique across the instance. Partial, because every row
    -- that predates this migration has none and NULLs must not collide.
    CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_media_client_id
        ON entity_media (client_id)
        WHERE client_id IS NOT NULL;

    -- The order the images are drawn in, and the order the sync snapshot is
    -- built in. `created_at` and `media_id` are in the index because they are
    -- the tiebreak: two images uploaded in one batch share a sort_order, and
    -- without a total order two devices show the same set in a different
    -- sequence while nothing has actually changed between them.
    CREATE INDEX IF NOT EXISTS idx_entity_media_scans
        ON entity_media (entity_type, entity_id, sort_order, created_at, media_id)
        WHERE role = 'scan' AND deleted_at IS NULL;
END
$$;

-- How many own images one release, container or series may hold, beside its own
-- poster and backdrop -- those two are free and are not counted here.
--
-- Ten is the number the product spec fixes for the native clients
-- (App-Guidance, film-detail-media.md). It is a setting rather than a constant
-- because a self-hosted instance owns its own disk: the ceiling exists to keep
-- the free backup carryable, and the operator is the one who knows how much
-- that is worth on their hardware.
--
-- The limit blocks *adding* only. Lowering it never hides, deletes or stops
-- syncing an image that is already stored.
INSERT INTO app_settings (key, value, is_secret) VALUES
    ('artwork_scan_limit_per_entity', '10'::jsonb, false)
ON CONFLICT (key) DO NOTHING;
