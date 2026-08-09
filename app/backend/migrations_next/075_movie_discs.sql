-- DiscVault Next: the individual discs inside a release.
--
-- A release has always been one row. `movies.format` says "4K UHD + Blu-ray",
-- `movies.disc_count` (070) says there are three of them, and
-- `movie_technical_specs` describes the audio, video and subtitles of -- what,
-- exactly? Of the release as a whole, which for a multi-disc set is a union
-- that describes no disc in it. A three-disc set whose UHD carries Dolby Vision
-- and Atmos, whose Blu-ray carries neither, and whose third disc is bonus
-- material with no subtitles at all, currently records one flattened answer and
-- loses which disc each fact belongs to.
--
-- This adds the missing level. It does not replace one: the barcode, the
-- catalogue numbers (069), the packaging axes (067) and `disc_count` stay
-- exactly where they are, on the release, because that is what they describe --
-- a box carries one barcode however many discs are inside it. The release-level
-- `movie_technical_specs` row also stays, and stays authoritative for a
-- single-disc release and for everything that already reads it (MovieVault
-- contributions, import/export, the MCP server, the collection filters). A disc
-- row is a narrower statement layered on top, never a rewrite of the broader
-- one.
--
-- Three tables:
--
--   movie_discs         - one row per physical disc.
--   movie_disc_seasons  - which seasons sit on that disc.
--   movie_disc_episodes - which episodes sit on that disc.

CREATE TABLE IF NOT EXISTS movie_discs (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id  text UNIQUE NOT NULL,
    movie_id   uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,

    -- The curator's order, not a printed number. A set may lead with the UHD
    -- and file the bonus disc last regardless of what the spine says, and
    -- plenty of discs carry no number at all. `sort_order` matches
    -- movie_seasons and movie_credits; the UI counts "Disc 1, 2, 3" from it.
    sort_order integer NOT NULL DEFAULT 0,

    -- Two axes rather than one list, which is the lesson 067 already paid for.
    -- `disc_type` is the medium -- what the disc *is* (uhd_bluray, bluray, dvd)
    -- -- and `disc_role` is what is *on* it (the feature, bonus material, or
    -- both). One combined vocabulary would let a row claim to be both `dvd` and
    -- `special_features` with nothing to say which one it really is, and would
    -- leave the far more common question -- "is the bonus disc a BD or a DVD?"
    -- -- unanswerable. Both are nullable: "nobody said" is a real state.
    --
    -- No CHECK constraint on either, matching `movie_technical_specs
    -- .carrier_type`. The vocabulary lives in app/backend/next_discs.py, where
    -- adding a medium is a code change rather than a migration, and where an
    -- unrecognized value arriving from an import can be kept and logged instead
    -- of failing a whole record.
    disc_type       text,
    disc_role       text,

    -- The free-text escape hatch, and the reason `other` exists in the
    -- vocabulary at all. Constrained to `other` on purpose: a row naming both a
    -- known medium and a free-text one is two answers to one question, and the
    -- reader has no rule for choosing. Unrepresentable rather than merely
    -- discouraged.
    disc_type_other text
        CHECK (disc_type_other IS NULL OR disc_type = 'other'),

    -- What the disc is called, if anything -- "Bonus Disc", "The Appendices
    -- Part 3". Distinct from `disc_role`: the label is what is printed, the
    -- role is what it means.
    label      text,

    -- The per-disc technical facts. Column names and value shapes are copied
    -- from movie_technical_specs (002, 054, 055, 056) rather than reinvented,
    -- so one normalizer, one set of i18n labels and one renderer serve both
    -- levels. `audio_tracks` holds {languageCode, codec, channels,
    -- immersiveFormat} objects and `subtitles` holds {languageCode,
    -- subtitleType} objects, exactly as the release-level columns do.
    --
    -- `regions` earns its place here more than anywhere else: the UHD in a box
    -- is region-free while the DVD beside it is region 2, and a single
    -- release-level list has never been able to say so.
    video_resolution text,
    video_codecs     jsonb NOT NULL DEFAULT '[]'::jsonb,
    hdr              jsonb NOT NULL DEFAULT '[]'::jsonb,
    screen_ratios    jsonb NOT NULL DEFAULT '[]'::jsonb,
    audio_tracks     jsonb NOT NULL DEFAULT '[]'::jsonb,
    subtitles        jsonb NOT NULL DEFAULT '[]'::jsonb,
    regions          jsonb NOT NULL DEFAULT '[]'::jsonb,

    notes      text,
    metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    -- The composite the two link tables below reference, so a disc can only be
    -- named together with the release it belongs to. Same device 063 used for
    -- movie_seasons and 074 for movie_episodes.
    UNIQUE (movie_id, id)
);

CREATE INDEX IF NOT EXISTS idx_movie_discs_movie
    ON movie_discs(movie_id, sort_order);

-- No soft delete, unlike series/seasons/episodes (063, 074). Those are shared,
-- server-owned entities that other rows point at, so removing one has to leave
-- a trace. A disc is a part of exactly one release, edited as part of it, and
-- deleting it is the same act as deleting a line from its audio track list.

-- Which seasons sit on this disc.
--
-- A child of movie_seasons, not a parallel to it. The release-level row still
-- answers "which seasons are in this box"; this one answers "and where in the
-- box". Because a season routinely spans several discs *and* a disc routinely
-- carries several seasons, the key is the pair -- neither side is unique.
--
-- The foreign key onto movie_seasons(movie_id, season_id) is the point: a disc
-- cannot carry a season the release is not recorded as covering, and dropping
-- the season from the release drops it from every disc in the same statement.
-- The alternative -- pointing straight at series_seasons -- would let the two
-- levels contradict each other, and nothing would notice.
CREATE TABLE IF NOT EXISTS movie_disc_seasons (
    disc_id    uuid NOT NULL,
    movie_id   uuid NOT NULL,
    season_id  uuid NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (disc_id, season_id),

    -- Plain references carry the cascade; the composite ones carry the meaning
    -- (074's split, for the same reason: ON DELETE on a composite would make
    -- the cascade depend on which pair matched).
    FOREIGN KEY (disc_id) REFERENCES movie_discs(id) ON DELETE CASCADE,
    FOREIGN KEY (movie_id, disc_id) REFERENCES movie_discs(movie_id, id),
    FOREIGN KEY (movie_id, season_id)
        REFERENCES movie_seasons(movie_id, season_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_movie_disc_seasons_season
    ON movie_disc_seasons(season_id);
CREATE INDEX IF NOT EXISTS idx_movie_disc_seasons_movie
    ON movie_disc_seasons(movie_id);

-- Which episodes sit on this disc.
--
-- Same shape and same reasoning as the seasons table above, one level down.
-- 074 created movie_episodes and deliberately left it unwritten -- there was no
-- surface that could state which episodes a release carries, because the
-- release *is* the disc in that model and "episodes 5-8" is a fact about one
-- disc rather than about a box. This is the level at which the statement can be
-- made, so it is also what finally populates movie_episodes and makes the
-- `onDisc` flag in `season_episode_entities` mean something.
--
-- Season membership still does not imply episode membership (074's rule): a
-- disc may name a season and no episodes, which reads as "this disc holds that
-- season" without claiming to enumerate it.
CREATE TABLE IF NOT EXISTS movie_disc_episodes (
    disc_id    uuid NOT NULL,
    movie_id   uuid NOT NULL,
    episode_id uuid NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (disc_id, episode_id),

    FOREIGN KEY (disc_id) REFERENCES movie_discs(id) ON DELETE CASCADE,
    FOREIGN KEY (movie_id, disc_id) REFERENCES movie_discs(movie_id, id),
    FOREIGN KEY (movie_id, episode_id)
        REFERENCES movie_episodes(movie_id, episode_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_movie_disc_episodes_episode
    ON movie_disc_episodes(episode_id);
CREATE INDEX IF NOT EXISTS idx_movie_disc_episodes_movie
    ON movie_disc_episodes(movie_id);

-- No backfill. Every existing release could be given one disc row carrying a
-- copy of its technical specs, and it would be a guess dressed as a record: a
-- two-disc set would get one disc, and a single-disc release would get a row
-- nobody entered, indistinguishable from one somebody did. An empty list means
-- "nobody has broken this release down yet", which is the truth for all of
-- them, and the release-level specs keep answering until someone does.
