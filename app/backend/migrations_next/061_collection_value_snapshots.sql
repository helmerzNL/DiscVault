-- DiscVault Next: a daily record of what a user's collection is worth.
--
-- `movies.estimated_value` is a single current number per disc. Nothing has
-- ever recorded what it *was*, so "the value of my collection over time" is a
-- question the database could not answer at all — not slowly, not
-- approximately: the data did not exist. This table starts recording it. It is
-- necessarily forward-looking; the first point is the day it ships.
--
-- Why a stored snapshot instead of deriving it:
--   • There is nothing to derive from. Editing an estimated value overwrites
--     the old one and leaves no trace, so history cannot be reconstructed
--     after the fact — only captured as it happens.
--   • The value moves for reasons that are not edits: adding or removing a
--     disc changes the total too. A snapshot catches all of them at once.
--
-- Why one row per user rather than one global row: the statistics surface is
-- visibility-scoped (`visible_movie_where_sql`), so two users on the same
-- instance legitimately see different collections. A shared total would be
-- wrong for both.
--
-- Why the "could not count" columns are stored rather than computed later:
-- a total is a claim about completeness. `estimated_value_currency` is
-- nullable by deliberate design (an amount without a recorded currency is a
-- real state and must never be assumed to be EUR), and such an amount cannot
-- be converted into the total. Storing how many discs were skipped, and why,
-- keeps the chart honest about what it is a total *of* — and keeps it honest
-- retroactively, which recomputing from today's data could not.

CREATE TABLE IF NOT EXISTS collection_value_snapshots (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    captured_on         date NOT NULL,
    -- The currency the totals below are expressed in. Snapshots are comparable
    -- only within one currency, so this travels with every row instead of
    -- being assumed from the user's current display preference — that
    -- preference can change, and it must not retroactively reinterpret a
    -- number that was already recorded.
    currency            text NOT NULL,
    total_value         numeric(14, 2) NOT NULL DEFAULT 0,
    -- Discs that contributed to `total_value`.
    valued_count        integer NOT NULL DEFAULT 0,
    -- Every disc the user could see, whether or not it carried a value.
    movie_count         integer NOT NULL DEFAULT 0,
    -- Seen but not counted, split by reason so the UI can say which.
    unpriced_count      integer NOT NULL DEFAULT 0,
    unconvertible_count integer NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- One snapshot per user per day. Re-running the job is then a harmless
    -- overwrite rather than a second point on the same date.
    UNIQUE (user_id, captured_on)
);

CREATE INDEX IF NOT EXISTS idx_collection_value_snapshots_user_day
    ON collection_value_snapshots (user_id, captured_on DESC);
