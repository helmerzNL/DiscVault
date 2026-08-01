-- DiscVault Next: widen movie_technical_specs.hdr and .screen_ratios from a
-- scalar text column to a jsonb list.
--
-- Same widening 053 applied to `packaging`, and for the same reason: MovieVault
-- distribution-4 publishes `hdrFormats` and `aspectRatios` as arrays, because a
-- 4K disc is routinely HDR10 *and* Dolby Vision, and a disc can carry a 2.39:1
-- feature alongside a 1.85:1 sequence. A single scalar cannot represent either.
--
-- Existing scalar values are preserved as a one-element array rather than
-- dropped, so a hand-entered "HDR10" becomes ["HDR10"]. NULL and empty string
-- become '[]'::jsonb, matching the empty-list convention of every sibling
-- column on this table (002_core_domain.sql).
--
-- A legacy value that a user typed as "1.85:1, 2.39:1" becomes the single
-- element ["1.85:1, 2.39:1"], not two elements. Splitting on commas here would
-- be an irreversible data rewrite performed sight unseen; the split happens
-- instead on the next pass through normalize_list_field, where it is visible in
-- the edit form and the user can correct it.

ALTER TABLE movie_technical_specs
    ALTER COLUMN hdr DROP DEFAULT,
    ALTER COLUMN hdr TYPE jsonb USING (
        CASE
            WHEN hdr IS NULL OR hdr = '' THEN '[]'::jsonb
            ELSE jsonb_build_array(hdr)
        END
    ),
    ALTER COLUMN hdr SET DEFAULT '[]'::jsonb,
    ALTER COLUMN hdr SET NOT NULL;

ALTER TABLE movie_technical_specs
    ALTER COLUMN screen_ratios DROP DEFAULT,
    ALTER COLUMN screen_ratios TYPE jsonb USING (
        CASE
            WHEN screen_ratios IS NULL OR screen_ratios = '' THEN '[]'::jsonb
            ELSE jsonb_build_array(screen_ratios)
        END
    ),
    ALTER COLUMN screen_ratios SET DEFAULT '[]'::jsonb,
    ALTER COLUMN screen_ratios SET NOT NULL;
