-- DiscVault Next: the film's own origin -- country of production and the
-- language it was made in -- as distinct from the disc's release country.
--
-- WHY THIS IS NEW DATA AND NOT A NEW READING OF OLD DATA
--
-- movies.country and movies.language (002_core_domain.sql:13-14) already exist
-- and look like the answer. They are not. They are RELEASE facts: which market
-- this physical pressing was made for, and which language its packaging speaks.
-- They are free text, user-editable, lockable, and fed from MovieVault release
-- data. A Dutch Blu-ray of a Japanese film correctly stores country='NL'.
--
-- Nothing here may be backfilled from them. Doing so would record the
-- Netherlands as the country of origin of Ran, permanently, for every user who
-- happens to own a European pressing -- and the value would look plausible on
-- every screen that displayed it. There is no backfill block in this migration
-- for that reason, and this paragraph is here so nobody adds one later.
--
-- The data exists upstream: TMDB returns origin_country, production_countries
-- and original_language on every /movie/{id} response, and DiscVault has been
-- discarding all three at the plugin boundary. It arrives by re-fetching, which
-- is what the movie_origin_backfill job does.
--
-- WHY THERE IS NO CATALOGUE TABLE
--
-- 037_movie_genres.sql, the model for this migration, seeds a `genres` table
-- and points movie_genres at it with a foreign key. That works because genres
-- are a closed set of 19 TMDB-owned ids whose labels DiscVault ships as
-- `genre.<key>` i18n keys in all 29 locales.
--
-- Countries and languages are open sets of several hundred codes, and their
-- display names come from Intl.DisplayNames at render time rather than from a
-- translation key -- the rule App-Guidance records in
-- movievault-audio-variants-and-language-display.md. A hand-maintained
-- catalogue with a foreign key could therefore never *add* anything: its only
-- possible effect is to reject a valid code TMDB knows and we have not heard of
-- yet, losing real data over a table nobody remembered to update. The shape is
-- constrained instead, which is the part that actually protects the column.

ALTER TABLE movies ADD COLUMN IF NOT EXISTS original_language text;

-- A BCP-47 primary subtag: 2-3 letters, optional script/region subtags. TMDB
-- returns ISO 639-1 ('ja', 'fr') and occasionally 639-2 or a subtagged form
-- ('cmn-Hans'), so the constraint accepts all three rather than pinning two
-- letters and rejecting a legitimate answer at write time.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'movies_original_language_shape'
    ) THEN
        ALTER TABLE movies
            ADD CONSTRAINT movies_original_language_shape
            CHECK (
                original_language IS NULL
                OR original_language ~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$'
            );
    END IF;
END
$$;

-- Multi-valued: a co-production has several origin countries, and TMDB orders
-- them with the lead producer first. sort_order preserves that order because
-- listing "IT, FR" where TMDB said "FR, IT" states something it was never told.
-- movie_genres needs no equivalent -- genres are re-sorted into catalogue order
-- at read time, and here there is no catalogue to sort by.
CREATE TABLE IF NOT EXISTS movie_origin_countries (
    movie_id     uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    country_code text NOT NULL,
    sort_order   integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (movie_id, country_code),
    CONSTRAINT movie_origin_countries_code_shape CHECK (country_code ~ '^[A-Z]{2}$')
);

-- The filter asks "which movies come from this country", so the country is the
-- leading column. Mirrors idx_movie_genres_genre for the same access pattern.
CREATE INDEX IF NOT EXISTS idx_movie_origin_countries_country
    ON movie_origin_countries(country_code);

-- No field_locks cleanup either. 037 needed two jsonb_agg UPDATEs because
-- 'genre' had been a lockable field before it became a relational association.
-- Neither origin field has ever existed under any name, so there is nothing
-- stale to strip -- and both stay out of MOVIE_LOCKABLE_FIELDS for the same
-- reason genres did: a lock only means something against a merge, and these are
-- always-replace-on-hit associations, not free text a user corrects by hand.
