-- DiscVault Next: store the finish axis the distribution-5 feed carries.
--
-- 067 gave movie_technical_specs its `finishes` column, but that is the
-- collection side. This is the feed mirror: movievault_v2_releases holds what
-- MovieVault published, verbatim, and enrichment reads from there. Without a
-- column here the value is parsed and then dropped on the floor, so a release
-- would arrive with spot UV recorded upstream and show nothing.
--
-- jsonb rather than a text[] with a CHECK, matching `packaging` on the same
-- table (052). The sync path is deliberately forward-compatible: an
-- unrecognized value is logged and stored rather than rejected, because
-- MovieVault may ship vocabulary before this repo catches up, and a database
-- constraint would turn that into a failed synchronization of the whole
-- catalog. The vocabulary is enforced where a *user* writes it, in the edit
-- API - see next_app._movie_edit_case_axes.

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS finishes jsonb;
