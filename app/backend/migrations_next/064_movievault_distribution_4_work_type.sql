-- DiscVault Next: MovieVault distribution-4 workType consumption.
--
-- Additive within the existing distribution-4 contract, same shape as the
-- packaging column added in 052 and the poster column in 039: a plain scalar
-- on the release itself rather than a child table.
--
-- Values are MovieVault's own `movie`/`tv`, stored verbatim rather than
-- translated to DiscVault's MOVIE/SHOW here. The local index is a cache of what
-- the feed said, so it holds the feed's vocabulary; the translation to
-- movies.media_type happens at the point the plugin proposes a value, which is
-- the one boundary where the two dialects meet.
--
-- Nullable with no default and no CHECK. A record projected before MovieVault
-- added the field carries no value at all, and "the feed has not said" has to
-- stay distinguishable from "the feed said movie" -- the first must not be able
-- to overwrite a type the user set, the second may.

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS work_type text;
