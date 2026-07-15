-- DiscVault Next: privacy-preserving local MovieVault v2 distribution index.

CREATE TABLE IF NOT EXISTS movievault_v2_sync_state (
    plugin_id text PRIMARY KEY,
    origin text NOT NULL,
    active_generation uuid,
    contract_version text,
    cursor text,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    dataset_checksum char(64),
    status text NOT NULL DEFAULT 'unconfigured'
        CHECK (status IN ('unconfigured', 'syncing', 'current', 'stale', 'error')),
    stale_threshold_hours integer NOT NULL DEFAULT 48
        CHECK (stale_threshold_hours BETWEEN 1 AND 720),
    last_attempt_at timestamptz,
    last_success_at timestamptz,
    last_error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (plugin_id = 'movievault_v2'),
    CHECK (contract_version IS NULL OR contract_version = 'distribution-2'),
    CHECK (dataset_checksum IS NULL OR dataset_checksum ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS movievault_v2_releases (
    generation uuid NOT NULL,
    release_id uuid NOT NULL,
    film_id uuid NOT NULL,
    canonical_title text NOT NULL,
    release_year integer,
    provider_ids jsonb NOT NULL DEFAULT '{}'::jsonb,
    release_title text NOT NULL,
    edition text,
    format text,
    region text,
    country_code char(2),
    language_code text,
    release_date date,
    disc_count integer,
    assets jsonb NOT NULL DEFAULT '[]'::jsonb,
    revision bigint NOT NULL CHECK (revision > 0),
    PRIMARY KEY (generation, release_id),
    CHECK (release_year IS NULL OR release_year BETWEEN 1870 AND 2200),
    CHECK (disc_count IS NULL OR disc_count BETWEEN 1 AND 999)
);

CREATE TABLE IF NOT EXISTS movievault_v2_box_sets (
    generation uuid NOT NULL,
    box_set_id uuid NOT NULL,
    title text NOT NULL,
    edition text,
    year_range text,
    format text,
    country_code char(2),
    language_code text,
    revision bigint NOT NULL CHECK (revision > 0),
    PRIMARY KEY (generation, box_set_id)
);

CREATE TABLE IF NOT EXISTS movievault_v2_box_set_members (
    generation uuid NOT NULL,
    box_set_id uuid NOT NULL,
    position integer NOT NULL CHECK (position > 0),
    release_id uuid NOT NULL,
    film_id uuid NOT NULL,
    canonical_title text NOT NULL,
    release_title text NOT NULL,
    release_edition text,
    format text,
    region text,
    relationship text NOT NULL DEFAULT 'contains' CHECK (relationship = 'contains'),
    disc_number integer,
    disc_format text,
    disc_barcode_hash char(64),
    PRIMARY KEY (generation, box_set_id, position),
    FOREIGN KEY (generation, box_set_id)
        REFERENCES movievault_v2_box_sets (generation, box_set_id)
        ON DELETE CASCADE,
    CHECK (disc_number IS NULL OR disc_number BETWEEN 1 AND 999),
    CHECK (disc_barcode_hash IS NULL OR disc_barcode_hash ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS movievault_v2_lookup_hashes (
    generation uuid NOT NULL,
    lookup_hash char(64) NOT NULL CHECK (lookup_hash ~ '^[0-9a-f]{64}$'),
    entity_type text NOT NULL CHECK (entity_type IN ('release', 'box_set')),
    entity_id uuid NOT NULL,
    source_type text NOT NULL
        CHECK (source_type IN ('release_ean', 'box_set_ean', 'member_ean', 'member_barcode')),
    member_position integer NOT NULL DEFAULT 0 CHECK (member_position >= 0),
    PRIMARY KEY (
        generation,
        lookup_hash,
        entity_type,
        entity_id,
        source_type,
        member_position
    )
);

CREATE INDEX IF NOT EXISTS idx_movievault_v2_lookup_hash
    ON movievault_v2_lookup_hashes (generation, lookup_hash);

CREATE INDEX IF NOT EXISTS idx_movievault_v2_release_title
    ON movievault_v2_releases (
        generation,
        (lower(canonical_title)) text_pattern_ops,
        (lower(release_title)) text_pattern_ops
    );

CREATE INDEX IF NOT EXISTS idx_movievault_v2_box_set_title
    ON movievault_v2_box_sets (generation, (lower(title)) text_pattern_ops);

CREATE INDEX IF NOT EXISTS idx_movievault_v2_members_release
    ON movievault_v2_box_set_members (generation, release_id);
