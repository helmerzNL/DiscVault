-- DiscVault Next: index the ordering every library page sorts by.
--
-- The Library is not one query. It is a first-paint page of 200 movies followed
-- by background pages of 500 (`library-paging.js`), all of them served by
-- `collection_movie_preview_entities()` in next_app.py, all of them ordered by:
--
--     ORDER BY lower(COALESCE(m.sort_title, m.title)), m.year NULLS LAST, m.id
--     LIMIT %s OFFSET %s
--
-- Two indexes on `movies` look like they cover that and do not:
-- `idx_movies_title ON movies(lower(title))` and
-- `idx_movies_sort_title ON movies(lower(sort_title))` (002_core_domain.sql).
-- An expression index serves only the exact expression it was built on, and
-- `lower(COALESCE(sort_title, title))` is neither of those. So the only plan
-- available is a full sort of every live movie per page, with `offset` rows
-- thrown away afterwards: O(total) per page rather than O(limit), paid once for
-- each of the six pages a 2,500-disc library needs.
--
-- What this index does and does not buy, measured on PostgreSQL 16 against this
-- schema (EXPLAIN ANALYZE of the page query at LIMIT 500 OFFSET 700):
--
--     rows        plan chosen
--     2,509       Seq Scan + Sort        <- unchanged; the index is not used
--     10,000      Index Scan
--     50,000      Index Scan
--     200,000     Index Scan
--
-- The crossover is the honest headline. Below roughly ten thousand movies the
-- planner keeps sorting and is right to -- sorting a few thousand rows costs
-- less than an ordered index scan with a random heap fetch per row, and no
-- index can change that. So this is **not** what fixes the stall reported in
-- #715 by a user with 2,509 movies; a stranded in-flight page was, and the fix
-- for it is in library-paging.js. This index is the scalability half: it stops
-- the per-page cost growing with the collection for the libraries large enough
-- that it would, and it costs nothing at the sizes where it stays unused.
--
-- Why the index matches the sort exactly:
--
--   * btree defaults to ASC NULLS LAST, so the explicit `NULLS LAST` on `year`
--     is a no-op and needs no counterpart in the index definition;
--   * `lower(text)` is IMMUTABLE and COALESCE is allowed in an index
--     expression, so `lower(COALESCE(sort_title, title))` is indexable as
--     written;
--   * `year` is `text` (002_core_domain.sql) and `id` is `uuid`; both have
--     default btree opclasses;
--   * the partial predicate is implied by the query's own
--     `AND m.deleted_at IS NULL`, so the planner may use a partial index and
--     tombstones stay out of it -- the same shape as
--     `idx_movies_titleyear_live` (045_sync_dedup.sql).
--
-- `m.id` is in the key because it is in the ORDER BY, and it is in the ORDER BY
-- because title and year do not identify a row: a 4K and a Blu-ray of one film
-- are two rows sharing both. Without it the sort is not a total order, and two
-- consecutive pages planned differently -- which is all it takes, and stats
-- changing under autovacuum is enough to cause it -- disagree about where a tie
-- group starts. Measured on 20,000 rows with a page boundary inside a tie
-- group, one page planned by index scan and the next by sort: 190 of 500 rows
-- were served on both pages, and 190 others on neither. With `m.id`: zero.
--
-- Not CONCURRENTLY, deliberately -- the same reasoning as 079. next_database.py
-- applies every migration inside `with conn.transaction():` and PostgreSQL
-- refuses CREATE INDEX CONCURRENTLY in a transaction block. What it buys is not
-- needed here: migrations run from the container's start command, before
-- gunicorn begins serving, so the brief SHARE lock blocks nobody.

DO $$
BEGIN
    IF to_regclass('public.movies') IS NULL THEN
        RETURN;
    END IF;

    CREATE INDEX IF NOT EXISTS idx_movies_library_sort_live
        ON movies (lower(COALESCE(sort_title, title)), year, id)
        WHERE deleted_at IS NULL;
END
$$;
