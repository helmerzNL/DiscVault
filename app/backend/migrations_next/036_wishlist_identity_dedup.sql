-- DiscVault Next: stable-identity deduplication for wishlist items.
--
-- iOS treats MovieVault ids and TMDB ids as source-specific identities. Keep the
-- oldest matching wishlist row, merge user data and dependent shop history into
-- it, then enforce those identities at the database boundary.

CREATE OR REPLACE FUNCTION pg_temp.merge_wishlist_duplicate(
    canonical_id uuid,
    duplicate_id uuid
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    duplicate_shop record;
    canonical_shop_id uuid;
BEGIN
    UPDATE wishlist_items AS canonical
    SET year = COALESCE(canonical.year, duplicate.year),
        barcode = COALESCE(NULLIF(canonical.barcode, ''), NULLIF(duplicate.barcode, '')),
        format = COALESCE(NULLIF(canonical.format, ''), NULLIF(duplicate.format, '')),
        movievault_id = COALESCE(NULLIF(canonical.movievault_id, ''), NULLIF(duplicate.movievault_id, '')),
        poster_url = COALESCE(NULLIF(canonical.poster_url, ''), NULLIF(duplicate.poster_url, '')),
        note = CASE
            WHEN NULLIF(btrim(canonical.note), '') IS NULL THEN duplicate.note
            WHEN NULLIF(btrim(duplicate.note), '') IS NULL OR canonical.note = duplicate.note THEN canonical.note
            ELSE canonical.note || E'\n\n' || duplicate.note
        END,
        snapshot = jsonb_strip_nulls(COALESCE(duplicate.snapshot, '{}'::jsonb))
            || jsonb_strip_nulls(COALESCE(canonical.snapshot, '{}'::jsonb)),
        acquired_at = CASE
            WHEN canonical.acquired_at IS NULL THEN duplicate.acquired_at
            WHEN duplicate.acquired_at IS NULL THEN canonical.acquired_at
            ELSE LEAST(canonical.acquired_at, duplicate.acquired_at)
        END,
        acquired_movie_id = COALESCE(canonical.acquired_movie_id, duplicate.acquired_movie_id),
        created_by_user_id = COALESCE(canonical.created_by_user_id, duplicate.created_by_user_id),
        alert_enabled = canonical.alert_enabled OR duplicate.alert_enabled,
        target_price = COALESCE(canonical.target_price, duplicate.target_price),
        price_url = COALESCE(NULLIF(canonical.price_url, ''), NULLIF(duplicate.price_url, '')),
        last_seen_price = CASE
            WHEN duplicate.last_price_checked_at IS NOT NULL
                 AND (
                     canonical.last_price_checked_at IS NULL
                     OR duplicate.last_price_checked_at > canonical.last_price_checked_at
                 )
                THEN duplicate.last_seen_price
            ELSE COALESCE(canonical.last_seen_price, duplicate.last_seen_price)
        END,
        price_currency = CASE
            WHEN duplicate.last_price_checked_at IS NOT NULL
                 AND (
                     canonical.last_price_checked_at IS NULL
                     OR duplicate.last_price_checked_at > canonical.last_price_checked_at
                 )
                THEN COALESCE(NULLIF(duplicate.price_currency, ''), canonical.price_currency)
            ELSE COALESCE(NULLIF(canonical.price_currency, ''), duplicate.price_currency, 'EUR')
        END,
        last_price_checked_at = GREATEST(
            canonical.last_price_checked_at,
            duplicate.last_price_checked_at
        ),
        last_alerted_at = GREATEST(canonical.last_alerted_at, duplicate.last_alerted_at)
    FROM wishlist_items AS duplicate
    WHERE canonical.id = canonical_id
      AND duplicate.id = duplicate_id
      AND canonical.user_id = duplicate.user_id;

    FOR duplicate_shop IN
        SELECT *
        FROM wishlist_item_shops
        WHERE wishlist_item_id = duplicate_id
        ORDER BY created_at, id
    LOOP
        canonical_shop_id := NULL;
        SELECT id
        INTO canonical_shop_id
        FROM wishlist_item_shops
        WHERE user_id = duplicate_shop.user_id
          AND wishlist_item_id = canonical_id
          AND price_url = duplicate_shop.price_url
        ORDER BY created_at, id
        LIMIT 1;

        IF canonical_shop_id IS NOT NULL THEN
            UPDATE wishlist_item_shop_prices
            SET wishlist_item_id = canonical_id,
                wishlist_item_shop_id = canonical_shop_id
            WHERE wishlist_item_shop_id = duplicate_shop.id;

            UPDATE wishlist_item_shops AS canonical_shop
            SET shop_name = COALESCE(NULLIF(canonical_shop.shop_name, ''), duplicate_shop.shop_name),
                price_currency = COALESCE(
                    NULLIF(canonical_shop.price_currency, ''),
                    duplicate_shop.price_currency,
                    'EUR'
                ),
                last_seen_price = CASE
                    WHEN duplicate_shop.last_price_checked_at IS NOT NULL
                         AND (
                             canonical_shop.last_price_checked_at IS NULL
                             OR duplicate_shop.last_price_checked_at > canonical_shop.last_price_checked_at
                         )
                        THEN duplicate_shop.last_seen_price
                    ELSE COALESCE(canonical_shop.last_seen_price, duplicate_shop.last_seen_price)
                END,
                last_price_checked_at = GREATEST(
                    canonical_shop.last_price_checked_at,
                    duplicate_shop.last_price_checked_at
                ),
                selector_type = COALESCE(canonical_shop.selector_type, duplicate_shop.selector_type),
                selector_value = COALESCE(canonical_shop.selector_value, duplicate_shop.selector_value),
                selector_options = CASE
                    WHEN canonical_shop.selector_options = '{}'::jsonb
                        THEN duplicate_shop.selector_options
                    ELSE canonical_shop.selector_options
                END,
                provider_plugin_id = COALESCE(
                    canonical_shop.provider_plugin_id,
                    duplicate_shop.provider_plugin_id
                ),
                provider_product_ref = COALESCE(
                    canonical_shop.provider_product_ref,
                    duplicate_shop.provider_product_ref
                ),
                provider_last_status = COALESCE(
                    canonical_shop.provider_last_status,
                    duplicate_shop.provider_last_status
                ),
                provider_last_error = COALESCE(
                    canonical_shop.provider_last_error,
                    duplicate_shop.provider_last_error
                ),
                updated_at = GREATEST(canonical_shop.updated_at, duplicate_shop.updated_at)
            WHERE canonical_shop.id = canonical_shop_id;

            DELETE FROM wishlist_item_shops WHERE id = duplicate_shop.id;
        ELSE
            UPDATE wishlist_item_shop_prices
            SET wishlist_item_id = canonical_id
            WHERE wishlist_item_shop_id = duplicate_shop.id;

            UPDATE wishlist_item_shops
            SET wishlist_item_id = canonical_id
            WHERE id = duplicate_shop.id;
        END IF;
    END LOOP;

    DELETE FROM wishlist_items WHERE id = duplicate_id;
END;
$$;

-- First consolidate MovieVault catalog identities.
DO $$
DECLARE
    duplicate record;
BEGIN
    FOR duplicate IN
        SELECT duplicate_id, canonical_id
        FROM (
            SELECT id AS duplicate_id,
                   first_value(id) OVER identity_rows AS canonical_id,
                   row_number() OVER identity_rows AS identity_rank
            FROM wishlist_items
            WHERE NULLIF(btrim(movievault_id), '') IS NOT NULL
            WINDOW identity_rows AS (
                PARTITION BY user_id, movievault_id
                ORDER BY added_at, id
            )
        ) AS ranked
        WHERE identity_rank > 1
    LOOP
        PERFORM pg_temp.merge_wishlist_duplicate(
            duplicate.canonical_id,
            duplicate.duplicate_id
        );
    END LOOP;
END;
$$;

-- Then consolidate TMDB identities. Missing media type historically meant movie.
DO $$
DECLARE
    duplicate record;
BEGIN
    FOR duplicate IN
        SELECT duplicate_id, canonical_id
        FROM (
            SELECT id AS duplicate_id,
                   first_value(id) OVER identity_rows AS canonical_id,
                   row_number() OVER identity_rows AS identity_rank
            FROM wishlist_items
            WHERE NULLIF(btrim(snapshot->>'tmdb_id'), '') IS NOT NULL
              AND NULLIF(btrim(movievault_id), '') IS NULL
            WINDOW identity_rows AS (
                PARTITION BY
                    user_id,
                    snapshot->>'tmdb_id',
                    COALESCE(NULLIF(snapshot->>'tmdb_media_type', ''), 'movie')
                ORDER BY added_at, id
            )
        ) AS ranked
        WHERE identity_rank > 1
    LOOP
        PERFORM pg_temp.merge_wishlist_duplicate(
            duplicate.canonical_id,
            duplicate.duplicate_id
        );
    END LOOP;
END;
$$;

DROP FUNCTION pg_temp.merge_wishlist_duplicate(uuid, uuid);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wishlist_items_user_movievault
    ON wishlist_items (user_id, movievault_id)
    WHERE NULLIF(btrim(movievault_id), '') IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wishlist_items_user_tmdb
    ON wishlist_items (
        user_id,
        (snapshot->>'tmdb_id'),
        (COALESCE(NULLIF(snapshot->>'tmdb_media_type', ''), 'movie'))
    )
    WHERE NULLIF(btrim(snapshot->>'tmdb_id'), '') IS NOT NULL
      AND NULLIF(btrim(movievault_id), '') IS NULL;
