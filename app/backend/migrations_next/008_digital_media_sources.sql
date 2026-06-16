CREATE TABLE IF NOT EXISTS digital_media_sources (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plugin_id       text NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
    name            text NOT NULL,
    source_type     text NOT NULL,
    base_url        text,
    machine_id      text,
    enabled         boolean NOT NULL DEFAULT true,
    last_synced_at  timestamptz,
    item_count      integer NOT NULL DEFAULT 0,
    last_status     text,
    last_error      text,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE(plugin_id)
);

CREATE INDEX IF NOT EXISTS idx_digital_media_sources_plugin
    ON digital_media_sources(plugin_id);

CREATE TABLE IF NOT EXISTS digital_media_items (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id        uuid NOT NULL REFERENCES digital_media_sources(id) ON DELETE CASCADE,
    external_id      text NOT NULL,
    media_type       text NOT NULL DEFAULT 'movie',
    title            text NOT NULL,
    year             text,
    tmdb_id          text,
    imdb_id          text,
    playback_url     text,
    matched_movie_id uuid REFERENCES movies(id) ON DELETE SET NULL,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    synced_at        timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_digital_media_items_source
    ON digital_media_items(source_id, media_type);
CREATE INDEX IF NOT EXISTS idx_digital_media_items_match
    ON digital_media_items(matched_movie_id);
CREATE INDEX IF NOT EXISTS idx_digital_media_items_tmdb
    ON digital_media_items(tmdb_id)
    WHERE tmdb_id IS NOT NULL AND tmdb_id <> '';
CREATE INDEX IF NOT EXISTS idx_digital_media_items_imdb
    ON digital_media_items(imdb_id)
    WHERE imdb_id IS NOT NULL AND imdb_id <> '';
