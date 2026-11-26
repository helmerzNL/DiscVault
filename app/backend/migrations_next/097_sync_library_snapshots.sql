-- Safety copies taken by the link wizard before it replaces the library
-- (sync-contract §5f), one row per completed snapshot.
--
-- A row exists only once the archive has been written, read back and
-- checksummed, so the existence of a row IS the proof that the copy exists.
-- `POST /sync/retain` and the `supersedeTombstone` flag both ask for one, which
-- makes "no snapshot, no wipe" something the server enforces instead of
-- something a client is trusted to have done.
--
-- `created_by` ties the id to the person who took it: an id is not a
-- capability another user can pick up. The archive itself is an ordinary
-- functional backup in the backup directory, so an administrator restores it
-- through the existing restore routes.

CREATE TABLE IF NOT EXISTS sync_library_snapshots (
    id          uuid PRIMARY KEY,
    created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    file_name   text NOT NULL,
    sha256      text NOT NULL,
    size_bytes  bigint NOT NULL CHECK (size_bytes >= 0),
    counts      jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sync_library_snapshots_created_at
    ON sync_library_snapshots (created_at DESC);
