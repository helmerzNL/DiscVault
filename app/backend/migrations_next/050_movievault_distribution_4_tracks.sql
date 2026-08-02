-- DiscVault Next: MovieVault distribution-4 audio track / subtitle language
-- consumption (additive within the existing distribution-4 contract; no
-- contract-version allow-list change needed).
--
-- Mirrors MovieVault-v2's own content.release_audio_tracks /
-- content.release_subtitles shape (see MovieVault-v2's
-- backend/src/movievault/migrations/0022_release_technical_fallback.sql),
-- adapted to this repo's generation-scoped local mirror convention (see
-- movievault_v2_box_set_members for the precedent this follows).
--
-- codec/channels/immersive_format are intentionally NOT constrained to the
-- current enum values here (unlike MovieVault's own producer-side schema):
-- DiscVault is a consumer and must stay readable if MovieVault ships a new
-- enum member before this repo's allow-list is updated. Application code
-- validates against the known enum and falls back to storing the raw string
-- with a logged warning for anything unrecognized; the column width is just
-- a defensive bound, not a whitelist. language_code keeps a real CHECK
-- pattern (matching movievault_v2_lookup_hashes/box_set_members precedent
-- for hash-like fields) because a malformed language code is a structural
-- feed defect, not a forward-compatible enum growth.

CREATE TABLE IF NOT EXISTS movievault_v2_release_audio_tracks (
    generation          uuid NOT NULL,
    release_id          uuid NOT NULL,
    position            smallint NOT NULL CHECK (position BETWEEN 1 AND 50),
    language_code       varchar(35) NOT NULL
        CHECK (language_code ~ '^[a-z]{2,8}(-[a-z0-9]{1,8})*$'),
    codec               varchar(64) NOT NULL,
    channels            varchar(16),
    immersive_format    varchar(64),
    PRIMARY KEY (generation, release_id, position),
    FOREIGN KEY (generation, release_id)
        REFERENCES movievault_v2_releases (generation, release_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS movievault_v2_release_subtitle_languages (
    generation          uuid NOT NULL,
    release_id          uuid NOT NULL,
    position            smallint NOT NULL CHECK (position BETWEEN 1 AND 50),
    language_code       varchar(35) NOT NULL
        CHECK (language_code ~ '^[a-z]{2,8}(-[a-z0-9]{1,8})*$'),
    PRIMARY KEY (generation, release_id, position),
    FOREIGN KEY (generation, release_id)
        REFERENCES movievault_v2_releases (generation, release_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_movievault_v2_release_audio_tracks_release
    ON movievault_v2_release_audio_tracks (generation, release_id);
CREATE INDEX IF NOT EXISTS idx_movievault_v2_release_subtitle_languages_release
    ON movievault_v2_release_subtitle_languages (generation, release_id);
