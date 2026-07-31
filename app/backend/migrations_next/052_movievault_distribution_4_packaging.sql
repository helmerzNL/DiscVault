-- DiscVault Next: MovieVault distribution-4 packaging consumption.
--
-- Additive within the existing distribution-4 contract, same as the
-- audioTracks/subtitleLanguages addition in 049. Unlike those, MovieVault's
-- packaging is a plain array column on the release itself
-- (content.release_technical_profiles.packaging), not a position-ordered
-- child table, so it lands directly on movievault_v2_releases - same
-- pattern as the existing `poster` column added in 039.

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS packaging jsonb;
