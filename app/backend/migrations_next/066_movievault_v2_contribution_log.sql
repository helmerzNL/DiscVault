-- DiscVault Next: remember what was contributed, and what became of it.
--
-- A contribution's outcome lived only in the background job that sent it. That
-- is enough to *deliver* one and useless for answering the question the person
-- who pressed the button actually has: what happened to my correction. Two
-- reasons the job row cannot answer it.
--
-- It cannot be found. The only thing tying a job to a film is a nested value
-- in its JSON payload, so "show the contributions for this record" is
-- `payload->'correction'->'target'->>'entityId' = ...` over every job the
-- instance has ever run — poster caching, metadata refreshes, imports and all.
-- On a detail screen that renders constantly, that is a sequential scan per
-- render. A queue is a work list, not a record store, and reading it as one is
-- what makes it slow.
--
-- And it does not survive. Jobs are operational rows that get pruned; a
-- moderation verdict can arrive weeks later and is worth keeping after the job
-- that fetched it is gone.
--
-- So the log is its own table, written when a correction is queued and updated
-- twice after: once when MovieVault acknowledges it, once when a moderator
-- decides. `status` therefore carries DiscVault's own vocabulary before the
-- first of those and MovieVault's after — 'queued' and 'failed' are ours,
-- 'pending'/'quarantined'/'accepted'/'partially_accepted'/'rejected' are
-- theirs. Deliberately not constrained to a fixed set: a status this side does
-- not recognise must be storable and shown verbatim rather than rejected at
-- the write, or a new upstream state becomes a crash in a worker.
--
-- Both owning references are nullable and ON DELETE SET NULL. Deleting a film
-- locally does not retract a correction someone already sent, and losing the
-- record of it would be the wrong kind of tidy: the entity ids stay, so the
-- history remains true even when the local row is gone.

CREATE TABLE IF NOT EXISTS movievault_v2_contributions (
    id             uuid PRIMARY KEY,
    entity_type    varchar(16) NOT NULL CHECK (entity_type IN ('release', 'box_set')),
    entity_id      uuid NOT NULL,
    movie_id       uuid REFERENCES movies(id) ON DELETE SET NULL,
    container_id   uuid REFERENCES containers(id) ON DELETE SET NULL,
    job_id         uuid,
    contribution_id varchar(128),
    status         varchar(32) NOT NULL DEFAULT 'queued',
    fields         text[] NOT NULL DEFAULT '{}',
    base_revision  bigint,
    canonical_target_id varchar(128),
    duplicate_of   varchar(128),
    last_error     varchar(160),
    submitted_by   uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- The two reads the detail screens make: the latest contribution for this
-- film, and for this box set. Both are "newest first, limit one".
CREATE INDEX IF NOT EXISTS idx_movievault_v2_contributions_movie
    ON movievault_v2_contributions(movie_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_movievault_v2_contributions_container
    ON movievault_v2_contributions(container_id, created_at DESC);

-- The worker's read: find the row a finished job belongs to. Unique so a job
-- cannot end up updating two rows, which would silently halve the history.
CREATE UNIQUE INDEX IF NOT EXISTS idx_movievault_v2_contributions_job
    ON movievault_v2_contributions(job_id)
    WHERE job_id IS NOT NULL;

-- The status poll's read: it knows the MovieVault contribution id and nothing
-- else about the local record.
CREATE INDEX IF NOT EXISTS idx_movievault_v2_contributions_contribution
    ON movievault_v2_contributions(contribution_id)
    WHERE contribution_id IS NOT NULL;
