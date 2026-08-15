-- DiscVault Next: repair the devices the old delta cursor stranded, by itself.
--
-- Migration 083 and the delta handler stop a client's cursor from jumping over
-- changes it was never sent. Neither repairs a device that already jumped: such
-- a cursor sits neatly *within* range (`since == currentRevision`) and is, from
-- the server, indistinguishable from a device that is up to date. Nothing in
-- the protocol can tell those apart.
--
-- The repair is to write the catalog back into the delta stream above every
-- cursor, and it has to happen without anyone rolling anything out -- a person
-- whose films stopped syncing updates the app, and it works. So the work is
-- enqueued here rather than left in a runbook: migrations already run
-- unattended on every container start (`next_database.py migrate` in
-- supervisord), and they run **once per version**, which is exactly the
-- cadence this needs.
--
-- Why a job rather than SQL in this file. A delta payload is the whole entity,
-- built by `next_app`'s `movie_entity` and friends. Reproducing that shape in
-- SQL would be a second implementation of the wire format, drifting from the
-- first the moment a field is added -- the one mistake `backfill_disc_union.py`
-- exists to warn about. So this file schedules; `next_sync_republish.py`
-- performs, through the same builders the live endpoints use.
--
-- Why exactly once, and never on a timer. The republish is deliberately not
-- idempotent: a change already below a cursor can only be made visible by
-- writing a new one above it, so every run appends a generation and costs each
-- connected device a full catalog download. Once per upgrade is the repair;
-- once per boot would be a permanent tax.

-- Nothing to repair on an empty database, and nothing stranded either: there
-- are no clients holding a cursor into a history that never existed. The guard
-- also keeps a fresh install's job list clean.
INSERT INTO background_jobs (job_type, status, payload)
SELECT
    'sync.catalog_republish',
    'pending',
    jsonb_build_object(
        'reason', 'cursor_jump_repair',
        'migration', '084',
        'note', 'One-time: re-emit the catalog above every client cursor.'
    )
WHERE EXISTS (SELECT 1 FROM movies WHERE deleted_at IS NULL)
  -- Belt and braces against a re-applied migration on a hand-repaired
  -- database. The migration runner already guarantees once-per-version; this
  -- makes a second insert impossible rather than merely unlikely, because the
  -- cost of an accidental repeat is every device re-downloading the library.
  AND NOT EXISTS (
      SELECT 1 FROM background_jobs
      WHERE job_type = 'sync.catalog_republish'
        AND payload->>'migration' = '084'
  );
