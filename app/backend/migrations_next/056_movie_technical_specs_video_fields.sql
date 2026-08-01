-- DiscVault Next: the two video facts the local movie had no column for at all.
--
-- Deliberately separate from 055: that migration rewrites existing data, this
-- one is purely additive, and a failure in one should not have to be diagnosed
-- through the other.
--
-- video_resolution is a scalar because a disc has exactly one; video_codecs is
-- a list because a dual-layer disc can carry both an AVC and an HEVC stream.
-- That mirrors the shape MovieVault publishes.
--
-- Unlike the mirror table (054), the list column here is NOT NULL DEFAULT '[]'
-- to match every sibling on movie_technical_specs, and because
-- upsert_movie_technical_edits performs partial updates that never touch
-- columns the caller did not mention.

ALTER TABLE movie_technical_specs
    ADD COLUMN IF NOT EXISTS video_resolution text,
    ADD COLUMN IF NOT EXISTS video_codecs jsonb NOT NULL DEFAULT '[]'::jsonb;
