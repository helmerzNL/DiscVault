-- DiscVault Next: a disc of a series takes the poster of the season it carries.
--
-- A disc linked through a barcode or a TMDb/TVDb id normally arrives with a
-- poster from the source. When it does not, the season it is filed under
-- already has one -- a picture of what is actually in the box -- and the disc
-- was showing nothing instead.
--
-- The application applies this from now on, at the two moments the answer can
-- change: when a disc's seasons are assigned, and when a series refresh brings
-- season artwork in. This backfills the discs that already exist, which are the
-- ones a user is looking at today.
--
-- `poster_from_season` is the provenance flag, mirroring `poster_from_members`
-- on a container that took a member's cover. It is what makes storing safe
-- rather than presumptuous: an inherited poster stays replaceable when the
-- season's artwork changes, while a poster the disc owns is never touched
-- again. Without the flag the two become indistinguishable the moment this
-- runs, and the disc could never be given a better one.

DO $$
BEGIN
    IF to_regclass('public.movie_seasons') IS NULL
       OR to_regclass('public.series_seasons') IS NULL
       OR to_regclass('public.entity_media') IS NULL THEN
        RETURN;
    END IF;

    WITH candidate AS (
        SELECT DISTINCT ON (ms.movie_id)
            ms.movie_id,
            ma.source_url AS poster_url
        FROM movie_seasons ms
        JOIN series_seasons ss ON ss.id = ms.season_id AND ss.deleted_at IS NULL
        JOIN entity_media em
          ON em.entity_type='series_season'
         AND em.entity_id=ss.id
         AND em.deleted_at IS NULL
         AND em.hidden_at IS NULL
        JOIN media_assets ma ON ma.id = em.media_id AND ma.kind='poster'
        -- Only a remote URL. Season artwork is stored as a provider URL, and a
        -- locally stored asset could not be served under a movie anyway --
        -- `actor_can_view_media_asset` has no branch for `series_season`.
        -- Skipping those leaves the disc as it was rather than pointing it at a
        -- URL that would 403.
        WHERE NULLIF(BTRIM(COALESCE(ma.source_url, '')), '') IS NOT NULL
        -- The lowest season number a set carries. Season 0 is not excluded: a
        -- curator who filed a disc under the specials has said what is in it.
        ORDER BY ms.movie_id, ss.season_number, em.is_primary DESC, em.sort_order, ma.created_at
    )
    UPDATE movies m
    SET metadata = COALESCE(m.metadata, '{}'::jsonb)
            || jsonb_build_object('poster_url', c.poster_url, 'poster_from_season', true),
        updated_at = now()
    FROM candidate c
    WHERE m.id = c.movie_id
      AND m.deleted_at IS NULL
      -- Never over a lock, and never over a poster the disc already has. Those
      -- are the two conditions the feature was asked for with, and they are the
      -- reason this backfill cannot damage a curated collection.
      AND LOWER(COALESCE(m.metadata->>'poster_locked', 'false')) NOT IN ('true', '1', 'yes')
      AND NULLIF(BTRIM(COALESCE(m.metadata->>'poster_url', '')), '') IS NULL;
END
$$;
