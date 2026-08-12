-- DiscVault Next: an episode can sit on the watchlist, and carry a still.
--
-- 074 gave `watch_history` an `episode_id` and argued the case there: watching
-- an episode is still watching, so it belongs on the one timeline rather than
-- in a parallel table that every reader has to remember to union. The watchlist
-- had no such column, so "I still want to see S02E07" was unsayable while
-- "I saw it" was already recordable -- the two halves of the same act, split.
--
-- This closes that half the same way, and for the same reason.

-- Nullable, and SET NULL on delete, exactly like `movie_id` after 015 and like
-- `watch_history.episode_id` after 074. The snapshot is what survives: 015 made
-- a watchlist row outlive the film it names so a deleted disc does not silently
-- shorten somebody's list, and an episode deserves the same treatment.
ALTER TABLE watchlist_items
    ADD COLUMN IF NOT EXISTS episode_id uuid;

DO $$
BEGIN
    IF to_regclass('public.series_episodes') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint WHERE conname = 'fk_watchlist_items_episode'
       )
    THEN
        ALTER TABLE watchlist_items
            ADD CONSTRAINT fk_watchlist_items_episode
                FOREIGN KEY (episode_id) REFERENCES series_episodes(id) ON DELETE SET NULL;
    END IF;
END
$$;

-- The mirror of `idx_watchlist_items_user_movie_active`: one live entry per
-- user per episode, and rows whose episode is gone are exempt rather than
-- colliding with each other on a shared NULL.
CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_items_user_episode_active
    ON watchlist_items(user_id, episode_id)
    WHERE episode_id IS NOT NULL;

-- A row names a film or an episode, never both.
--
-- Not "exactly one": 015 deliberately allows *neither*, because a row whose
-- film was deleted keeps only its snapshot, and an XOR would have made that
-- surviving row unrepresentable -- deleting a film would fail instead of
-- orphaning the entry it was supposed to preserve. So the constraint forbids
-- the one combination that has no meaning, and stays silent about the one 015
-- relies on.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'watchlist_items_single_subject'
    ) THEN
        ALTER TABLE watchlist_items
            ADD CONSTRAINT watchlist_items_single_subject
                CHECK (movie_id IS NULL OR episode_id IS NULL);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_watchlist_items_episode
    ON watchlist_items(episode_id) WHERE episode_id IS NOT NULL;

-- The episode's own image, when a source supplied one.
--
-- A column rather than an `entity_media` row, which is the opposite of the
-- choice 063 made for a season poster, so the difference is worth stating. An
-- `entity_media` asset is something a *user* curates: uploaded, re-ordered,
-- hidden, promoted to primary -- and the series and season artwork tabs exist
-- to do exactly that. Nobody curates 73 episode stills. They arrive with the
-- episode list, they are replaced wholesale by the next refresh, and giving
-- each one a media_assets row plus an entity_media row would add two rows per
-- episode to carry a string that has no lifecycle of its own.
--
-- The fallback chain (episode still -> season poster -> series poster) is
-- therefore computed at read time rather than materialised: a season that gains
-- a poster tomorrow should improve every one of its episodes without a backfill.
ALTER TABLE series_episodes
    ADD COLUMN IF NOT EXISTS still_url text;
