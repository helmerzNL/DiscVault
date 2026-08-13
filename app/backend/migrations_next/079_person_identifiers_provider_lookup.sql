-- DiscVault Next: index the lookup a metadata refresh makes once per credit.
--
-- `person_identifiers` answers two different questions, and it only had an
-- index for one of them. Its primary key is
-- (person_id, provider_id, identifier_type, identifier), which serves "what
-- identifiers does this person have" -- the direction next_people.py reads.
--
-- The refresh path asks the opposite question: "who is the person behind this
-- TMDB id" (next_metadata.py, resolve by provider identifier):
--
--     SELECT person_id
--     FROM person_identifiers
--     WHERE provider_id='tmdb'
--       AND identifier_type='person_id'
--       AND identifier=$1
--     LIMIT 1
--
-- `person_id` is not in that predicate, so the primary key cannot serve it and
-- PostgreSQL scans the table. Once per credit -- a film with forty credits
-- scans the whole table forty times.
--
-- Measured with EXPLAIN (ANALYZE, BUFFERS) on a scratch copy at three sizes:
--
--     rows       without          with              blocks touched
--     2,000      0.172 ms         0.012 ms          10 -> 3
--     50,000     2.179 ms         0.015 ms          234 -> 4
--     400,000   22.630 ms         0.016 ms          3,739 -> 4
--
-- The point is not the millisecond at today's size -- at the ~2,000 rows this
-- instance holds it is a fraction of a refresh. It is that the cost currently
-- grows with the table and is multiplied by the credit count, and with the
-- index it stops depending on either.
--
-- Not CONCURRENTLY, deliberately. next_database.py applies every migration
-- inside `with conn.transaction():`, and PostgreSQL refuses CREATE INDEX
-- CONCURRENTLY in a transaction block. What CONCURRENTLY buys -- not blocking
-- writers during the build -- is not needed here anyway: migrations run from
-- the container's start command, before gunicorn begins serving, so there is
-- no traffic to block. Building it plainly takes a SHARE lock on a table this
-- size for a few milliseconds against nobody.

DO $$
BEGIN
    IF to_regclass('public.person_identifiers') IS NULL THEN
        RETURN;
    END IF;

    CREATE INDEX IF NOT EXISTS idx_person_identifiers_provider_lookup
        ON person_identifiers (provider_id, identifier_type, identifier);
END
$$;
