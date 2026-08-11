-- DiscVault Next: episodes, and which of them sit on a disc.
--
-- 063 modelled series and seasons and stopped there, deliberately: a season is
-- what a box set is sold by, so it was enough to answer "which seasons are in
-- this set". Episodes answer the question a season cannot -- a set that carries
-- 1-12 of a 13-episode season, a pilot on a sampler disc, a special that shipped
-- with the wrong season -- and they are what makes per-episode watched history
-- meaningful.
--
-- Shaped as a sibling of series_seasons rather than as jsonb on it. The two are
-- not alike: a season's contents are a fixed small list that the season row
-- describes, while an episode is a thing a *user* acts on -- marks watched,
-- counts as owned -- and a row you reference from another table cannot be a
-- jsonb element.

CREATE TABLE IF NOT EXISTS series_episodes (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id      text UNIQUE NOT NULL,
    client_id      text,
    series_id      uuid NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    season_id      uuid NOT NULL REFERENCES series_seasons(id) ON DELETE CASCADE,
    episode_number integer NOT NULL CHECK (episode_number >= 0),
    title          text,
    overview       text,
    air_date       text,
    runtime_minutes integer CHECK (runtime_minutes IS NULL OR runtime_minutes >= 0),
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
    deleted_at     timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    -- The pair, not the id alone. It is what lets movie_episodes below reference
    -- (season_id, id) and so refuse an episode filed under a season it does not
    -- belong to -- the same trick 063 used to keep a season inside its series.
    UNIQUE (season_id, id),
    UNIQUE (series_id, id)
);

-- Episode 0 is allowed: a recap, a prologue, a "chapter 0" that some shows
-- really ship. Season 0 was allowed for the same reason in 063.
CREATE UNIQUE INDEX IF NOT EXISTS uq_series_episodes_number_live
    ON series_episodes(season_id, episode_number) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_series_episodes_client_id
    ON series_episodes(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_series_episodes_season
    ON series_episodes(season_id, episode_number);
CREATE INDEX IF NOT EXISTS idx_series_episodes_series
    ON series_episodes(series_id);

-- Which episodes are physically on a disc.
--
-- Deliberately *not* implied by movie_seasons. "This box covers season 2" and
-- "this box carries episodes 1-12 of season 2" are different statements, and a
-- set that stops one episode short is exactly the case worth recording. A disc
-- may therefore carry episodes without every episode of the season, and a
-- season link without any episode rows still means what it always meant.
CREATE TABLE IF NOT EXISTS movie_episodes (
    movie_id   uuid NOT NULL,
    episode_id uuid NOT NULL,
    season_id  uuid NOT NULL,
    series_id  uuid NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (movie_id, episode_id),

    -- Plain references carry the cascade; the composite ones carry the meaning.
    -- Split exactly as 063 split them for movie_seasons: a composite key with
    -- ON DELETE would make the cascade depend on which pair matched.
    FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
    FOREIGN KEY (episode_id) REFERENCES series_episodes(id) ON DELETE CASCADE,

    -- The composites are the point: an episode can only be attached together
    -- with the season and series it actually belongs to, so a disc cannot claim
    -- episode 3 of season 1 "under" season 4. Unrepresentable in the schema
    -- rather than merely forbidden in code.
    FOREIGN KEY (season_id, episode_id) REFERENCES series_episodes(season_id, id),
    FOREIGN KEY (series_id, episode_id) REFERENCES series_episodes(series_id, id),
    FOREIGN KEY (movie_id, series_id) REFERENCES movies(id, series_id)
);

CREATE INDEX IF NOT EXISTS idx_movie_episodes_episode ON movie_episodes(episode_id);
CREATE INDEX IF NOT EXISTS idx_movie_episodes_series ON movie_episodes(series_id);

-- Per-episode watched history.
--
-- A column on watch_history rather than a parallel table: watching an episode is
-- still watching, and it belongs on the same timeline as everything else. A
-- separate table would mean every reader of "what did I watch" has to remember
-- to union two of them, and the one that forgets is the bug.
--
-- Nullable, because the overwhelming majority of rows are a film or a whole
-- disc. NULL means "not about a particular episode", which is different from a
-- row that names one.
ALTER TABLE watch_history
    ADD COLUMN IF NOT EXISTS episode_id uuid REFERENCES series_episodes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_watch_history_episode
    ON watch_history(user_id, episode_id) WHERE episode_id IS NOT NULL;
