-- DiscVault Next: MovieVault distribution-4 poster consumption.
--
-- Widens the movievault_v2 contract-version allow-list to include
-- distribution-4, adds a nullable poster descriptor column to releases and
-- box sets (unused/NULL for generations sourced from v2/v3), and creates a
-- bounded cache of locally-stored poster bytes keyed by MovieVault asset id,
-- variant, and checksum -- independent of collection membership and never
-- populated by an item view.

ALTER TABLE movievault_v2_sync_state
    DROP CONSTRAINT IF EXISTS movievault_v2_sync_state_contract_version_check;

ALTER TABLE movievault_v2_sync_state
    ADD CONSTRAINT movievault_v2_sync_state_contract_version_check
    CHECK (
        contract_version IS NULL
        OR contract_version IN ('distribution-2', 'distribution-3', 'distribution-4')
    );

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS poster jsonb;

ALTER TABLE movievault_v2_box_sets
    ADD COLUMN IF NOT EXISTS poster jsonb;

CREATE TABLE IF NOT EXISTS movievault_v2_poster_cache (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id        text NOT NULL,
    variant         text NOT NULL CHECK (variant IN ('thumbnail', 'display')),
    checksum        text NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'ready', 'degraded', 'error')),
    media_asset_id  uuid REFERENCES media_assets(id) ON DELETE SET NULL,
    attempts        integer NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    checked_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE(asset_id, variant, checksum)
);

CREATE INDEX IF NOT EXISTS idx_movievault_v2_poster_cache_asset
    ON movievault_v2_poster_cache(asset_id, variant);
CREATE INDEX IF NOT EXISTS idx_movievault_v2_poster_cache_status
    ON movievault_v2_poster_cache(status, checked_at);
CREATE INDEX IF NOT EXISTS idx_movievault_v2_poster_cache_media_asset
    ON movievault_v2_poster_cache(media_asset_id);
