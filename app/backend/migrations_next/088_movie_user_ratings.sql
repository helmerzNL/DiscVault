-- DiscVault Next: a personal score per user per movie.
--
-- Rides the per-user append-only sync stream (025_user_sync_stream.sql) like
-- every other personal entity -- watchlist, watch history, tags, wishlist,
-- loans. The catalogue is shared with a visibility scope; what a person thinks
-- of a film is not part of it.
--
-- NOT movies.rating. That column (002_core_domain.sql:18) is the external
-- aggregate score, written only by metadata plugins (TMDB's vote_average,
-- OMDb's imdbRating), has no input anywhere in the edit form, and is `text`
-- because it stores whatever string a provider returned. This one is numeric
-- because it is sorted, filtered and compared. Nor is it a content rating:
-- movie_technical_specs.content_ratings holds the age certificates, and the
-- list column already called "rating" is that one.
--
-- WHY 0.5 - 10.0 IN HALF STEPS
--
-- The same 0-10 axis movies.rating uses, so "my 8.5 against TMDB's 7.4" is a
-- comparison a reader can make without being told the conversion. The half-step
-- CHECK keeps the input set to twenty values, which is what lets the UI be a
-- fixed picker rather than a free number field -- and it means a mobile client's
-- slider cannot send 7.34 and be silently rounded to something the user never
-- chose.
--
-- WHY "NO RATING" IS THE ABSENCE OF A ROW
--
-- Not NULL, and emphatically not 0. Zero is a legitimate-looking score, and a
-- nullable column invites COALESCE(score, 0) somewhere downstream -- which would
-- rank every unrated film below every rated one while claiming it had been
-- rated zero. Clearing a rating deletes the row. That is the same
-- presence-is-the-fact model watchlist_items and movie_tags already use.

CREATE TABLE IF NOT EXISTS movie_user_ratings (
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_id   uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    score      numeric(3,1) NOT NULL,
    rated_at   timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, movie_id),
    CONSTRAINT movie_user_ratings_score_range CHECK (score >= 0.5 AND score <= 10.0),
    CONSTRAINT movie_user_ratings_score_step CHECK ((score * 2) = trunc(score * 2))
);

-- (user_id, movie_id) is the natural key and there is no surrogate id, unlike
-- movie_tags -- that one needs one because (user, tag, movie) is a triple. The
-- per-user sync stream is already scoped by user_id, so the movie id alone is a
-- unique entity_id within it.

-- The library reads "every rating on these movies belonging to either the
-- viewer or the movie's owner", so the movie is the leading column. The primary
-- key already covers the per-user lookup from the other direction.
CREATE INDEX IF NOT EXISTS idx_movie_user_ratings_movie ON movie_user_ratings(movie_id);
