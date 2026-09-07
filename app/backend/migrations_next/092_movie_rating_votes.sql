-- DiscVault Next: how many votes stand behind movies.rating.
--
-- WHY A SCORE ON ITS OWN IS NOT ENOUGH
--
-- movies.rating (002_core_domain.sql:18) holds the external aggregate score --
-- TMDB's vote_average, or OMDb's imdbRating. The score filter added in 26.9.107
-- compares it numerically, and that comparison is only as meaningful as the
-- sample behind it. A title with 10.0 from three votes and a title with 8.4
-- from twelve thousand are indistinguishable today, and "score at least 8"
-- ranks the first above the second.
--
-- That is not a rare edge in a physical collection. Obscure pressings, regional
-- releases, documentaries and boutique restorations are exactly the titles with
-- few votes, and exactly the ones a high-score filter surfaces first while a
-- well-reviewed mainstream film sits below the threshold. The number on screen
-- is real, so the filter looks like it is working.
--
-- The data exists upstream and was being discarded at the plugin boundary, the
-- same way origin was before 087: TMDB returns vote_count on every
-- /movie/{id} response and OMDb returns imdbVotes.
--
-- WHY A COLUMN AND NOT A metadata KEY
--
-- movies.metadata is jsonb holding whatever a provider returned, and everything
-- read out of it is free text. A vote floor has to compare numerically -- "at
-- least 500" -- and comparing "1,204" as text puts it below "9". The score
-- itself is `text` for historical reasons and the filter pays for that in
-- parsing on every row; there is no reason to repeat the mistake for a value
-- that is an integer at every source that supplies it.
--
-- WHY NULLABLE, AND WHY NOT DEFAULT 0
--
-- This is the load-bearing decision of this migration.
--
-- NULL means "we have never asked". 0 means "we asked, and nobody has voted".
-- They must stay apart, because they are the two answers a vote floor has to
-- treat differently and because every film that exists today is the first case:
-- the column arrives empty on an established library, and it is filled by a
-- metadata refresh or by the backfill job.
--
-- DEFAULT 0 would collapse them permanently and silently. Every unfetched film
-- would claim, with a straight face, that TMDB has zero votes for it -- and a
-- filter reading that would exclude the entire library while reporting a
-- perfectly ordinary reason for doing so. There would be no way back: once
-- written, "0 because nobody voted" and "0 because nobody asked" are the same
-- byte.
--
-- The score column already suffers the mirror image of this and cannot be
-- repaired. TMDB returns vote_average = 0.0 for an unvoted title, DiscVault
-- stores it as the string "0", and nothing can now distinguish that from a film
-- whose score was never fetched. The score filter has to treat every stored
-- zero as "not scored" for that reason -- correct in practice, but it is a
-- workaround for data that was flattened at write time. This column does not
-- repeat it.
--
-- There is deliberately NO backfill statement here. Nothing already stored can
-- imply a vote count: a film having a score says nothing about how many people
-- gave it, and deriving a number would fabricate the precise fact this column
-- exists to record.

ALTER TABLE movies ADD COLUMN IF NOT EXISTS rating_votes integer;

-- A count, so it cannot be negative. Deliberately no upper bound: TMDB's most
-- voted films are in the tens of thousands today and the ceiling is nobody's to
-- guess. A CHECK is the whole protection here -- there is no catalogue table
-- and no foreign key -- and its job is to refuse a value that is not a count at
-- all, not to have an opinion about how popular a film may become.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'movies_rating_votes_non_negative'
    ) THEN
        ALTER TABLE movies
            ADD CONSTRAINT movies_rating_votes_non_negative
            CHECK (rating_votes IS NULL OR rating_votes >= 0);
    END IF;
END
$$;

-- No index. The vote floor is applied client-side over the hydrated library,
-- like every other Library filter, so no query ever selects on this column --
-- the backfill selector reads `rating_votes IS NULL`, which over a library that
-- is mostly null is a sequential scan whatever an index says.
--
-- No field_locks cleanup and no entry in MOVIE_LOCKABLE_FIELDS either, for the
-- same reason 087 gives: a lock only means something against a merge of a field
-- a human curates by hand. Nothing in the app offers an input for this, exactly
-- as nothing offers one for movies.rating. It is provider-owned or it is
-- absent.
