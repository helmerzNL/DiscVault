-- Per-user sync stream for the offline iOS companion app.
--
-- The global sync_state/sync_changes tables (migration 004) are user-less and
-- only carry the shared catalog (movies, containers, identifiers, membership).
-- Personal watch-state (watchlist + watch history) is privacy-scoped, so it
-- rides a separate per-user append-only stream with its own monotonic revision
-- counter keyed by user_id. Endpoints /api/next/sync/user/bootstrap and
-- /api/next/sync/user/delta serve this stream, scoped to the authenticated
-- caller.

CREATE TABLE IF NOT EXISTS user_sync_state (
    user_id    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    revision   bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_sync_changes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    revision    bigint NOT NULL,
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    operation   text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_sync_changes_user_revision
    ON user_sync_changes(user_id, revision);
