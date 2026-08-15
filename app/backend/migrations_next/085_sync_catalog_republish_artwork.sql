-- DiscVault Next: re-deliver the catalog after the artwork resolution changed.
--
-- #690 changed the *shape* of what movies and containers carry on the wire: the
-- cover is now resolved from the primary `entity_media` row instead of passed
-- through from `metadata.poster_url`. No row changed, so nothing was marked
-- changed, so the delta -- which carries changes and not reshapes -- had nothing
-- to send. Every device kept the covers it already had.
--
-- That is the general shape, not a detail of this one fix: **a payload-shape
-- change never reaches a client that already synced.** It needs the catalog
-- re-sent, exactly like 084's cursor repair did, and for the same reason the
-- work is scheduled here rather than left in a runbook -- someone who updates
-- must not have to know a script exists.
--
-- Why this may enqueue nothing, and why that is correct
-- ----------------------------------------------------
--
-- A republish is not "apply fix X". It re-sends every live entity **as it is
-- now**, built by whatever code is running. So two pending republishes are not
-- two fixes waiting their turn -- they are the same job twice, and the second
-- costs every connected device a second full catalog download for no new
-- information.
--
-- This matters for the person who updates once a week and skips three releases.
-- Their container runs 084 and 085 back to back; without the guard below they
-- would pay for two identical sweeps. With it, the first pending job is left to
-- do the work and it publishes a catalog that already carries *both* fixes,
-- because it is built by the code that ships them.
--
-- The guard deliberately looks at `pending`/`running` only. A `completed` job
-- has already published the old shape and cannot carry this one; a `failed` one
-- published nothing. Both must be followed by a fresh sweep.

INSERT INTO background_jobs (job_type, status, payload)
SELECT
    'sync.catalog_republish',
    'pending',
    jsonb_build_object(
        'reason', 'artwork_resolution_changed',
        'migration', '085',
        'note', 'Payload-shape change (#690): re-emit so clients receive resolved covers.'
    )
WHERE EXISTS (SELECT 1 FROM movies WHERE deleted_at IS NULL)
  -- Coalesce with a sweep that has not run yet: it will publish this fix too.
  AND NOT EXISTS (
      SELECT 1 FROM background_jobs
      WHERE job_type = 'sync.catalog_republish'
        AND status IN ('pending', 'running')
  )
  -- This migration's own once-ever guard, matching 084's.
  AND NOT EXISTS (
      SELECT 1 FROM background_jobs
      WHERE job_type = 'sync.catalog_republish'
        AND payload->>'migration' = '085'
  );
