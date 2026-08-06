-- Media type on a disc release, plus the series/season hierarchy behind a TV disc.
--
-- media_type is NOT NULL DEFAULT 'MOVIE' rather than nullable, because there has
-- never been a way to enter a TV show: the collection type filter has always been
-- disabled and no write path accepted a type. Every existing row therefore really
-- is a movie, so the backfill states a fact rather than guessing one. "Absent" is a
-- wire property (an old client omitting the key), never a storage property.

ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS media_type text NOT NULL DEFAULT 'MOVIE';

ALTER TABLE movies DROP CONSTRAINT IF EXISTS movies_media_type_check;
ALTER TABLE movies
    ADD CONSTRAINT movies_media_type_check
    CHECK (media_type IN ('MOVIE', 'SHOW'));

CREATE INDEX IF NOT EXISTS idx_movies_media_type_live
    ON movies(media_type)
    WHERE deleted_at IS NULL;

-- Series and seasons are server-owned and read-only on the wire in this version:
-- no client models them yet. They nevertheless get the full sync-ready shape
-- (public_id, client_id, deleted_at, owner_id), following 045_sync_dedup.sql and
-- 049_container_sync_dedup.sql, so enabling sync later needs code and not another
-- migration.

CREATE TABLE IF NOT EXISTS series (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id      text UNIQUE NOT NULL,
    client_id      text,
    title          text NOT NULL,
    sort_title     text,
    original_title text,
    start_year     text,
    end_year       text,
    overview       text,
    owner_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    deleted_at     timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_series_client_id
    ON series(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_series_title_live
    ON series(lower(title)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_series_deleted_at
    ON series(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_series_owner ON series(owner_id);

-- Mirrors movie_identifiers. Empty in this version; carries the TMDB tv id once
-- TV enrichment exists.
CREATE TABLE IF NOT EXISTS series_identifiers (
    series_id       uuid NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    provider_id     text NOT NULL,
    identifier_type text NOT NULL,
    identifier      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, provider_id, identifier_type, identifier)
);

CREATE INDEX IF NOT EXISTS idx_series_identifiers_lookup
    ON series_identifiers(provider_id, identifier_type, identifier);

CREATE TABLE IF NOT EXISTS series_seasons (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id     text UNIQUE NOT NULL,
    client_id     text,
    series_id     uuid NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    season_number integer NOT NULL CHECK (season_number >= 0),
    title         text,
    year          text,
    overview      text,
    episode_count integer CHECK (episode_count IS NULL OR episode_count >= 0),
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    deleted_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_series_seasons_number_live
    ON series_seasons(series_id, season_number) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_series_seasons_client_id
    ON series_seasons(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_series_seasons_series
    ON series_seasons(series_id, season_number);

ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS series_id uuid REFERENCES series(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_movies_series
    ON movies(series_id) WHERE series_id IS NOT NULL;

ALTER TABLE movies DROP CONSTRAINT IF EXISTS movies_series_requires_show;
ALTER TABLE movies
    ADD CONSTRAINT movies_series_requires_show
    CHECK (series_id IS NULL OR media_type = 'SHOW');

-- The composite keys below make "a release linked to a season of a different
-- series" unrepresentable in the schema rather than merely forbidden in code.
ALTER TABLE series_seasons DROP CONSTRAINT IF EXISTS uq_series_seasons_id_series;
ALTER TABLE series_seasons
    ADD CONSTRAINT uq_series_seasons_id_series UNIQUE (id, series_id);

ALTER TABLE movies DROP CONSTRAINT IF EXISTS uq_movies_id_series;
ALTER TABLE movies
    ADD CONSTRAINT uq_movies_id_series UNIQUE (id, series_id);

-- Zero rows here is the complete-series / unspecified case, not a missing value.
-- Because series_id is NOT NULL, a release whose movies.series_id is NULL can hold
-- no season rows at all.
CREATE TABLE IF NOT EXISTS movie_seasons (
    movie_id   uuid NOT NULL,
    season_id  uuid NOT NULL,
    series_id  uuid NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (movie_id, season_id),
    FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
    FOREIGN KEY (season_id) REFERENCES series_seasons(id) ON DELETE CASCADE,
    FOREIGN KEY (season_id, series_id) REFERENCES series_seasons(id, series_id),
    FOREIGN KEY (movie_id, series_id) REFERENCES movies(id, series_id)
);

CREATE INDEX IF NOT EXISTS idx_movie_seasons_season
    ON movie_seasons(season_id, sort_order);
