-- DiscVault Next: record how many discs are in the box.
--
-- `discCount` has been on MovieVault's correctable-field list since
-- contribution-2 shipped, and DiscVault has refused it with
-- `not_stored_by_discvault` -- an honest answer, and the only one of the
-- withheld fields that was a plain gap rather than a difference of meaning.
-- `movies` had no such column, `movie_technical_specs` had no such column, and
-- the edit form offered no such field.
--
-- It is also the first per-disc fact DiscVault can hold. The mirror already
-- carries MovieVault's count (`movievault_v2_releases.disc_count`), so the
-- comparison has always been possible from one side only: DiscVault could see
-- that a release has three discs and had nowhere to write it down, let alone
-- disagree.
--
-- Nullable rather than defaulted to 1. A default would claim every existing
-- record is a single-disc edition, which is a guess about tens of thousands of
-- rows, and it would make every one of them a contribution proposing "1" to a
-- catalogue that may know better. NULL means nobody said, and nothing is
-- proposed for a field nobody has answered.
--
-- Bounded to match `ReleaseSummary.discCount` (`ge=1, le=999`) so a value
-- DiscVault accepts is never one MovieVault refuses.

ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS disc_count smallint
        CHECK (disc_count IS NULL OR disc_count BETWEEN 1 AND 999);
