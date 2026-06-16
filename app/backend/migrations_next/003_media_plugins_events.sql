CREATE TABLE IF NOT EXISTS media_assets (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            text NOT NULL CHECK (kind IN ('poster', 'backdrop', 'profile', 'still', 'logo')),
    variant         text NOT NULL CHECK (variant IN ('original', 'display', 'thumb')),
    storage_backend text NOT NULL DEFAULT 'local',
    storage_key     text NOT NULL,
    source_url      text,
    provider_id     text,
    content_type    text,
    width           integer,
    height          integer,
    size_bytes      bigint,
    sha256          text NOT NULL,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE(storage_backend, storage_key),
    UNIQUE(kind, variant, sha256)
);

CREATE INDEX IF NOT EXISTS idx_media_assets_sha256 ON media_assets(sha256);
CREATE INDEX IF NOT EXISTS idx_media_assets_provider ON media_assets(provider_id);

CREATE TABLE IF NOT EXISTS entity_media (
    entity_type text NOT NULL,
    entity_id   uuid NOT NULL,
    media_id    uuid NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    role        text NOT NULL DEFAULT 'primary',
    is_primary  boolean NOT NULL DEFAULT false,
    sort_order  integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_id, media_id, role)
);

CREATE INDEX IF NOT EXISTS idx_entity_media_entity
    ON entity_media(entity_type, entity_id, role, is_primary);

ALTER TABLE users
    ADD CONSTRAINT fk_users_avatar_asset
    FOREIGN KEY (avatar_asset_id) REFERENCES media_assets(id) ON DELETE SET NULL;

ALTER TABLE people
    ADD CONSTRAINT fk_people_profile_asset
    FOREIGN KEY (profile_asset_id) REFERENCES media_assets(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS metadata_plugins (
    id                  text PRIMARY KEY,
    name                text NOT NULL,
    version             text NOT NULL,
    enabled             boolean NOT NULL DEFAULT false,
    installed           boolean NOT NULL DEFAULT true,
    order_index         integer NOT NULL DEFAULT 100,
    manifest            jsonb NOT NULL,
    settings_schema     jsonb NOT NULL DEFAULT '{}'::jsonb,
    premium_feature_key text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_metadata_plugins_order ON metadata_plugins(enabled, order_index);

CREATE TABLE IF NOT EXISTS metadata_plugin_settings (
    plugin_id   text PRIMARY KEY REFERENCES metadata_plugins(id) ON DELETE CASCADE,
    settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
    secrets_ref jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS metadata_lookup_cache (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plugin_id  text NOT NULL REFERENCES metadata_plugins(id) ON DELETE CASCADE,
    cache_key  text NOT NULL,
    payload    jsonb NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(plugin_id, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_metadata_cache_expiry ON metadata_lookup_cache(expires_at);

CREATE TABLE IF NOT EXISTS metadata_field_provenance (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type text NOT NULL,
    entity_id   uuid NOT NULL,
    field_name  text NOT NULL,
    plugin_id   text NOT NULL REFERENCES metadata_plugins(id) ON DELETE RESTRICT,
    source_ref  text,
    confidence  numeric,
    captured_at timestamptz NOT NULL DEFAULT now(),
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_metadata_provenance_entity
    ON metadata_field_provenance(entity_type, entity_id, field_name);

CREATE TABLE IF NOT EXISTS domain_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    event_type  text NOT NULL,
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_domain_events_entity
    ON domain_events(entity_type, entity_id, created_at);
CREATE INDEX IF NOT EXISTS idx_domain_events_created ON domain_events(created_at);

CREATE TABLE IF NOT EXISTS background_jobs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type     text NOT NULL,
    status       text NOT NULL DEFAULT 'pending',
    requested_by uuid REFERENCES users(id) ON DELETE SET NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    result       jsonb NOT NULL DEFAULT '{}'::jsonb,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_status ON background_jobs(status, created_at);

