-- Re-publish the catalog once, so the two custom-field entities reach devices
-- that have already synced.
--
-- This is sync-contract 5e applied to its own case: the delta carries what
-- changed after a client's cursor, and defining a field changes no existing
-- film. `customFields` appears only in the bootstrap, and a value set only
-- travels when the film itself does. A device that simply continues from its
-- cursor therefore sees neither -- and reports a successful sync while doing so.
-- The symptom of skipping this is that there is no symptom.
--
-- A job rather than SQL here, for the reason 084 gives: a delta payload is the
-- whole entity, built by next_app's emitters. Rebuilding that shape in SQL would
-- be a second implementation of the wire format, drifting from the first the
-- moment a field is added. This file schedules; next_sync_republish.py performs,
-- through the same builders the live endpoints use.
--
-- The pending/running guard is 085's and it is not an optimisation. A sweep
-- that has not run yet re-sends every live entity *as it is now*, built by the
-- code that is running -- so it already carries this change. Queueing a second
-- one would add nothing and cost every connected device a second full catalogue
-- download. A completed sweep published the old shape and cannot carry this one;
-- a failed sweep published nothing. Both must be followed by a fresh one, which
-- is why the guard looks at pending and running only.

INSERT INTO background_jobs (job_type, status, payload)
SELECT
    'sync.catalog_republish',
    'pending',
    jsonb_build_object(
        'reason', 'custom_fields_added',
        'migration', '090',
        'note', 'One-time: publish custom_field and movie_custom_values above every client cursor.'
    )
WHERE EXISTS (SELECT 1 FROM movies WHERE deleted_at IS NULL)
  AND NOT EXISTS (
      SELECT 1 FROM background_jobs
      WHERE job_type = 'sync.catalog_republish'
        AND status IN ('pending', 'running')
  )
  AND NOT EXISTS (
      SELECT 1 FROM background_jobs
      WHERE job_type = 'sync.catalog_republish'
        AND payload->>'migration' = '090'
  );
