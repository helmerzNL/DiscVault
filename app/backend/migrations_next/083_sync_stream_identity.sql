-- DiscVault Next: give the sync revision counter an identity.
--
-- `sync_state.revision` is a bare monotonic integer and a client's cursor is a
-- bare integer compared against it (`WHERE revision > since` in
-- `GET /api/next/sync/delta`). Nothing in that pair says *which* counter the
-- number came from, so a rebuilt or reset database -- revision back to 0, an
-- empty `sync_changes` -- is indistinguishable from a quiet one.
--
-- The failure that follows is silent in both directions. A client holding a
-- cursor from the previous database asks for `revision > 4711` against a
-- counter that now stands at 14, gets an empty change list, and the old
-- response handed back `nextSince: currentRevision` -- snapping the cursor onto
-- the tip and stepping over every change the new database had accumulated.
-- Films added on one device were then unreachable to the other for good, while
-- both sides reported a healthy sync.
--
-- `stream_id` is that missing identity: minted once per database, carried in
-- `/api/next/sync/state`, `/api/next/sync/bootstrap` and the delta response,
-- and echoed back by a client as `?streamId=`. A mismatch is a definite answer
-- ("this cursor belongs to a stream that no longer exists") where a comparison
-- of two integers could only guess.
--
-- The column is additive and defaulted, so a client that never sends
-- `streamId` keeps working -- and still recovers, because an out-of-range
-- cursor is now replayed from the start instead of skipped past.

ALTER TABLE sync_state
    ADD COLUMN IF NOT EXISTS stream_id uuid NOT NULL DEFAULT gen_random_uuid();

-- The per-user stream has the same shape and the same hazard: `user_sync_state`
-- rows are recreated with a fresh revision when the database is rebuilt, while
-- a client keeps the cursor it had.
ALTER TABLE user_sync_state
    ADD COLUMN IF NOT EXISTS stream_id uuid NOT NULL DEFAULT gen_random_uuid();
