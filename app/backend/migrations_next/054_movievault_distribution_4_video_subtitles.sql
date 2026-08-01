-- DiscVault Next: consume MovieVault distribution-4's video profile and its
-- structured subtitle tracks.
--
-- Two additions, one migration, because they arrive together on the same feed
-- records and a half-applied state would mean the mirror rejects every v4
-- release either way.
--
-- 1. Video facts on movievault_v2_releases.
--
-- All five are a scalar or a plain array of scalars on the release itself, not
-- position-ordered structured children, so they land directly on the release
-- row - the rule 052 applied to `packaging`. A child table (050) stays
-- reserved for children carrying several attributes per row, where position is
-- semantically meaningful.
--
-- `disc_regions`, not `regions`: movievault_v2_releases.region (035) already
-- holds the release's own free-text region label and means something else.
-- MovieVault names the feed key `discRegions` for exactly this reason.
--
-- No CHECK constraints on the enum-ish values, matching the reasoning in 050:
-- DiscVault is a consumer and must stay readable if MovieVault ships a new
-- resolution, codec or HDR member before this repo's allow-list is updated.
-- Application code logs the unknown value and stores it raw.

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS video_resolution text,
    ADD COLUMN IF NOT EXISTS video_codecs jsonb,
    ADD COLUMN IF NOT EXISTS hdr_formats jsonb,
    ADD COLUMN IF NOT EXISTS aspect_ratios jsonb,
    ADD COLUMN IF NOT EXISTS disc_regions jsonb;

-- 2. Subtitle variants.
--
-- MovieVault PR #162 replaced distribution-4's `subtitleLanguages` string array
-- with structured `subtitles` objects, because a disc routinely carries several
-- tracks in the same language: a full track, an SDH track that also transcribes
-- sound effects and speaker identification, a forced track for foreign dialogue
-- only, subtitles belonging to a commentary, and closed captions.
--
-- The mirror table's primary key is already (generation, release_id, position),
-- so repeating a language is representable without touching the key. Only the
-- variant column is missing. Existing rows become 'full', which is a lossless
-- read: the pre-variant feed could express nothing else.
--
-- No CHECK on the value, for the same consumer-side forward-compat reason as
-- above and as documented in 050.

ALTER TABLE movievault_v2_release_subtitle_languages
    ADD COLUMN IF NOT EXISTS subtitle_type varchar(24) NOT NULL DEFAULT 'full';

COMMENT ON COLUMN movievault_v2_release_subtitle_languages.subtitle_type IS
    'distribution-4 subtitle variant: full, sdh, forced, commentary or closed_caption. Stored raw when unrecognized.';
