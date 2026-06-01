ALTER TABLE watchlist_items
    ADD COLUMN IF NOT EXISTS id uuid DEFAULT gen_random_uuid();

UPDATE watchlist_items
SET id = gen_random_uuid()
WHERE id IS NULL;

ALTER TABLE watchlist_items
    ALTER COLUMN id SET DEFAULT gen_random_uuid(),
    ALTER COLUMN id SET NOT NULL;

ALTER TABLE watchlist_items
    ADD COLUMN IF NOT EXISTS snapshot jsonb NOT NULL DEFAULT '{}'::jsonb;

UPDATE watchlist_items w
SET snapshot = jsonb_strip_nulls(
    jsonb_build_object(
        'movie_id', m.id,
        'public_id', m.public_id,
        'barcode', m.barcode,
        'title', m.title,
        'movie_title', m.title,
        'year', m.year,
        'movie_year', m.year,
        'format', m.format,
        'movie_format', m.format,
        'edition', m.edition,
        'poster_url', COALESCE(m.metadata->>'poster_url', m.metadata->>'posterUrl', m.metadata->>'poster'),
        'backdrop_url', COALESCE(m.metadata->>'backdrop_url', m.metadata->>'backdropUrl', m.metadata->>'backdrop')
    )
)
FROM movies m
WHERE w.movie_id = m.id
  AND w.snapshot = '{}'::jsonb;

ALTER TABLE watchlist_items
    DROP CONSTRAINT IF EXISTS watchlist_items_pkey,
    DROP CONSTRAINT IF EXISTS watchlist_items_movie_id_fkey;

ALTER TABLE watchlist_items
    ALTER COLUMN movie_id DROP NOT NULL;

ALTER TABLE watchlist_items
    ADD CONSTRAINT watchlist_items_pkey PRIMARY KEY (id),
    ADD CONSTRAINT fk_watchlist_items_movie
        FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_items_user_movie_active
    ON watchlist_items(user_id, movie_id)
    WHERE movie_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_watchlist_items_user_added
    ON watchlist_items(user_id, added_at DESC);
