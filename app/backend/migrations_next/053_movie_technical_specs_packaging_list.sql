-- DiscVault Next: widen movie_technical_specs.packaging from scalar text to
-- a jsonb list, matching its siblings audio_tracks/subtitles/regions on the
-- same table (002_core_domain.sql). A physical release can legitimately be
-- both a steelbook AND carry a slipcover (MovieVault-v2's distribution-4
-- packaging enum, see movievault_v2_releases.packaging added in 051), so a
-- single scalar column can no longer represent it.
--
-- Existing scalar values are preserved as a one-element array rather than
-- dropped: a manually-entered "Steelbook" becomes ["Steelbook"]. NULL/empty
-- becomes '[]'::jsonb, matching the sibling columns' empty-list convention.

ALTER TABLE movie_technical_specs
    ALTER COLUMN packaging DROP DEFAULT,
    ALTER COLUMN packaging TYPE jsonb USING (
        CASE
            WHEN packaging IS NULL OR packaging = '' THEN '[]'::jsonb
            ELSE jsonb_build_array(packaging)
        END
    ),
    ALTER COLUMN packaging SET DEFAULT '[]'::jsonb,
    ALTER COLUMN packaging SET NOT NULL;
