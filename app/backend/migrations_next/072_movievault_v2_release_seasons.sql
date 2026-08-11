-- DiscVault Next: store the seasons a release covers, as the feed publishes them.
--
-- 563 made the sync tolerate the `seasons` key so an updated MovieVault could
-- not take the whole catalog down. It deliberately stopped there: the value was
-- parsed away and dropped. This is the column that lets it land, so a television
-- box set can say which seasons are in it.
--
-- jsonb rather than a normalized child table, matching `packaging` (052) and
-- `finishes` (071) on the same table. movievault_v2_releases is a verbatim
-- mirror of what MovieVault published, not a modelled entity: the modelled
-- shape lives in `series_seasons` and `movie_seasons` (063), and enrichment
-- reads from this mirror to build it. Normalizing here would mean maintaining
-- the same hierarchy twice and keeping the two in step across a resync.
--
-- No NOT NULL and no default. A row written by an older build simply has NULL,
-- which reads as "this generation never carried seasons" -- distinct from `[]`,
-- which is MovieVault stating that the release covers no particular season (a
-- film, or a complete-series set). The consuming side must not collapse those
-- two: NULL means unknown, `[]` is an answer. Existing rows are backfilled by
-- the next sync rather than by this migration, for the same reason 071 left
-- `finishes` alone -- a generation's contents are whatever the feed said at the
-- time, and inventing values here would forge that record.

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS seasons jsonb;
