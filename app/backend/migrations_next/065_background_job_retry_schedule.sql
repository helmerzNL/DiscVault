-- DiscVault Next: let a background job be retried later instead of dying.
--
-- `claim_job` picks the oldest row with status='pending' and nothing else, and
-- a handler that raises is marked 'failed' on the spot. That is fine for work
-- whose only failure mode is a bad payload — retrying a malformed import will
-- fail identically forever. It is wrong for work that talks to another system:
-- a MovieVault instance that is down for a minute is not a permanent failure,
-- but there is nowhere to record "try this again at 14:05" and no count to
-- decide when to stop.
--
-- Two columns, both nullable/defaulted so every existing job type keeps its
-- current behaviour exactly. `run_after` NULL means "eligible now", which is
-- what every row already written means, so no backfill is needed and the claim
-- predicate stays true for them. `attempts` starts at 0 and is only advanced by
-- handlers that opt into retrying.
--
-- The index carries the same shape as the claim query so the added predicate
-- does not turn the poll into a scan. NULLS FIRST keeps un-scheduled work — the
-- overwhelming majority — at the front, where the old index already put it.

ALTER TABLE background_jobs
    ADD COLUMN IF NOT EXISTS run_after timestamptz,
    ADD COLUMN IF NOT EXISTS attempts  integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_background_jobs_ready
    ON background_jobs(status, run_after NULLS FIRST, created_at);
