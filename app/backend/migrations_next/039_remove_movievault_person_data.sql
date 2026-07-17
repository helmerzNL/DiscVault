CREATE TEMP TABLE movievault_person_cleanup ON COMMIT DROP AS
SELECT
    p.id AS person_id,
    EXISTS (
        SELECT 1
        FROM person_identifiers tmdb_identifier
        WHERE tmdb_identifier.person_id = p.id
          AND lower(tmdb_identifier.provider_id) = 'tmdb'
          AND tmdb_identifier.identifier <> ''
    ) AS has_tmdb_identifier
FROM people p
WHERE
    EXISTS (
        SELECT 1
        FROM person_identifiers movievault_identifier
        WHERE movievault_identifier.person_id = p.id
          AND lower(movievault_identifier.provider_id) IN ('movievault', 'movievault_26')
    )
    OR lower(COALESCE(p.metadata ->> 'person_metadata_source', '')) IN ('movievault', 'movievault_26')
    OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(
            CASE
                WHEN jsonb_typeof(p.metadata -> 'person_metadata_sources') = 'array'
                    THEN p.metadata -> 'person_metadata_sources'
                ELSE '[]'::jsonb
            END
        ) AS person_source(source_id)
        WHERE lower(source_id) IN ('movievault', 'movievault_26')
    )
    OR EXISTS (
        SELECT 1
        FROM jsonb_array_elements(
            CASE
                WHEN jsonb_typeof(p.metadata -> 'awards') = 'array'
                    THEN p.metadata -> 'awards'
                ELSE '[]'::jsonb
            END
        ) AS person_award(award)
        WHERE lower(COALESCE(award ->> 'source', '')) IN ('movievault', 'movievault_26')
    )
    OR EXISTS (
        SELECT 1
        FROM media_assets profile_asset
        WHERE profile_asset.id = p.profile_asset_id
          AND lower(COALESCE(profile_asset.provider_id, '')) IN ('movievault', 'movievault_26')
    )
    OR EXISTS (
        SELECT 1
        FROM entity_media em
        JOIN media_assets ma ON ma.id = em.media_id
        WHERE em.entity_type = 'person'
          AND em.entity_id = p.id
          AND lower(COALESCE(ma.provider_id, '')) IN ('movievault', 'movievault_26')
    );

CREATE TEMP TABLE movievault_only_person_movies ON COMMIT DROP AS
SELECT DISTINCT mc.movie_id
FROM movie_credits mc
JOIN movievault_person_cleanup cleanup ON cleanup.person_id = mc.person_id
WHERE cleanup.has_tmdb_identifier = false;

UPDATE people p
SET profile_asset_id = NULL,
    updated_at = now()
FROM movievault_person_cleanup cleanup, media_assets ma
WHERE cleanup.person_id = p.id
  AND ma.id = p.profile_asset_id
  AND lower(COALESCE(ma.provider_id, '')) IN ('movievault', 'movievault_26');

DELETE FROM entity_media em
USING movievault_person_cleanup cleanup, media_assets ma
WHERE cleanup.person_id = em.entity_id
  AND em.entity_type = 'person'
  AND ma.id = em.media_id
  AND lower(COALESCE(ma.provider_id, '')) IN ('movievault', 'movievault_26');

DELETE FROM person_identifiers identifier
USING movievault_person_cleanup cleanup
WHERE cleanup.person_id = identifier.person_id
  AND lower(identifier.provider_id) IN ('movievault', 'movievault_26');

DELETE FROM person_localizations localization
USING movievault_person_cleanup cleanup
WHERE cleanup.person_id = localization.person_id
  AND cleanup.has_tmdb_identifier = true;

UPDATE people p
SET birth_date = NULL,
    death_date = NULL,
    place_of_birth = NULL,
    known_for = NULL,
    metadata = p.metadata
        - 'alsoKnownAs'
        - 'also_known_as'
        - 'awardGroups'
        - 'awards'
        - 'awards_fetched_at'
        - 'awards_source'
        - 'awards_source_ref'
        - 'biography'
        - 'imdbId'
        - 'imdb_id'
        - 'movieVaultId'
        - 'movievault_id'
        - 'person_metadata_fetched_at'
        - 'person_metadata_source'
        - 'person_metadata_source_ref'
        - 'person_metadata_sources'
        - 'profile_url'
        - 'profiles',
    updated_at = now()
FROM movievault_person_cleanup cleanup
WHERE cleanup.person_id = p.id
  AND cleanup.has_tmdb_identifier = true;

INSERT INTO background_jobs (job_type, payload)
SELECT
    'metadata.refresh_person',
    jsonb_build_object(
        'personId', cleanup.person_id::text,
        'source', 'migration_039_remove_movievault_person_data'
    )
FROM movievault_person_cleanup cleanup
WHERE cleanup.has_tmdb_identifier = true
  AND NOT EXISTS (
      SELECT 1
      FROM background_jobs existing
      WHERE existing.job_type = 'metadata.refresh_person'
        AND existing.payload ->> 'personId' = cleanup.person_id::text
        AND (
            existing.status IN ('pending', 'running')
            OR existing.payload ->> 'source' = 'migration_039_remove_movievault_person_data'
        )
  );

DELETE FROM people p
USING movievault_person_cleanup cleanup
WHERE cleanup.person_id = p.id
  AND cleanup.has_tmdb_identifier = false;

INSERT INTO background_jobs (job_type, payload)
SELECT
    'metadata.refresh_movie',
    jsonb_build_object(
        'movieId', affected.movie_id::text,
        'refreshPeople', true,
        'personRefreshScope', 'all',
        'source', 'migration_039_remove_movievault_person_data'
    )
FROM movievault_only_person_movies affected
WHERE NOT EXISTS (
    SELECT 1
    FROM background_jobs existing
    WHERE existing.job_type = 'metadata.refresh_movie'
      AND existing.payload ->> 'movieId' = affected.movie_id::text
      AND (
          existing.status IN ('pending', 'running')
          OR existing.payload ->> 'source' = 'migration_039_remove_movievault_person_data'
      )
);
