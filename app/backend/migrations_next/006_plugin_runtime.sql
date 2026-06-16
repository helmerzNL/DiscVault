CREATE TABLE IF NOT EXISTS plugins (
    id                  text PRIMARY KEY,
    name                text NOT NULL,
    version             text NOT NULL,
    enabled             boolean NOT NULL DEFAULT false,
    installed           boolean NOT NULL DEFAULT true,
    categories          jsonb NOT NULL DEFAULT '[]'::jsonb,
    capabilities        jsonb NOT NULL DEFAULT '[]'::jsonb,
    order_index         integer NOT NULL DEFAULT 100,
    manifest            jsonb NOT NULL,
    settings_schema     jsonb NOT NULL DEFAULT '{}'::jsonb,
    premium_feature_key text,
    source_path         text,
    runtime_module      text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_plugins_categories ON plugins USING gin(categories);
CREATE INDEX IF NOT EXISTS idx_plugins_order ON plugins(enabled, order_index);

CREATE TABLE IF NOT EXISTS plugin_settings (
    plugin_id   text PRIMARY KEY REFERENCES plugins(id) ON DELETE CASCADE,
    settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
    secrets_ref jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid REFERENCES users(id) ON DELETE SET NULL
);

INSERT INTO feature_entitlements (feature_key, enabled, source, limits) VALUES
    ('metadata.source.tmdb.use', true, 'beta', '{}'::jsonb),
    ('metadata.source.tmdb.bulk', true, 'beta', '{}'::jsonb),
    ('metadata.source.omdb.use', true, 'beta', '{}'::jsonb),
    ('metadata.source.upcitemdb.use', true, 'beta', '{}'::jsonb),
    ('metadata.source.bluray_com.use', true, 'beta', '{}'::jsonb),
    ('metadata.source.movievault.use', true, 'beta', '{}'::jsonb),
    ('metadata.source.movievault.bulk', true, 'beta', '{}'::jsonb),
    ('metadata.receiver.movievault.contribute', true, 'beta', '{}'::jsonb),
    ('digital_source.plex.connect', true, 'beta', '{}'::jsonb),
    ('digital_source.plex.sync', true, 'beta', '{}'::jsonb),
    ('digital_source.jellyfin.connect', true, 'beta', '{}'::jsonb),
    ('digital_source.jellyfin.sync', true, 'beta', '{}'::jsonb)
ON CONFLICT (feature_key) DO NOTHING;
