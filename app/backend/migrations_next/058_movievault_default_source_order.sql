-- Make MovieVault v2 the default, highest-priority MovieVault metadata source.
--
-- The bundled manifests now ship movievault_v2 as defaultEnabled with
-- orderIndex 45, and movievault_26 disabled at orderIndex 55. Those manifest
-- values are applied by sync_plugin_registry() on INSERT only - the
-- ON CONFLICT DO UPDATE clauses deliberately omit "enabled" and "order_index" so
-- a manifest flip can never silently re-enable a plugin an operator switched
-- off. That protection also means existing installs would never pick up this
-- change, which is what this migration is for.
--
-- Two deliberate consequences, both called out in the release notes:
--
-- 1. This is a ONE-SHOT OVERRIDE OF OPERATOR INTENT. An admin who had
--    deliberately enabled movievault_26 or disabled movievault_v2 is flipped
--    once. There is no prior-intent signal in the schema to distinguish a
--    deliberate choice from an untouched default, and the migration ledger
--    guarantees this runs exactly once.
-- 2. movievault_26 is DiscVault's only metadata_receiver, so disabling it stops
--    contribution back to MovieVault until an admin re-enables it. That is the
--    intended behaviour of this change, not a side effect.
--
-- Order 45 (not a 51/52 swap): metadata_source_plugins() sorts by
-- (order_index, lower(name)), where "movievault 26" sorts before
-- "movievault v2" - a tie would hand the win to 26. 45 also sits below the
-- legacy "movievault" seed order of 50, so an order inherited through
-- reconcile_plugin_replacements() cannot outrank v2 either.
--
-- Every table access is guarded so this is a no-op where the plugin tables do
-- not exist yet, and every UPDATE is conditional so re-running changes nothing.

-- 1. Enabled state and priority. metadata_plugins is written first because it is
--    the master: sync_plugin_registry() mirrors plugins <- metadata_plugins, so
--    a write to plugins alone would be reverted at the next sync. Both tables are
--    written because the migration may run long before that sync, and
--    metadata_source_plugins() reads plugins.
DO $$
BEGIN
    IF to_regclass('public.metadata_plugins') IS NOT NULL THEN
        UPDATE metadata_plugins
        SET enabled = true,
            order_index = 45,
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND (enabled IS DISTINCT FROM true OR order_index IS DISTINCT FROM 45);

        UPDATE metadata_plugins
        SET enabled = false,
            order_index = 55,
            updated_at = now()
        WHERE id = 'movievault_26'
          AND (enabled IS DISTINCT FROM false OR order_index IS DISTINCT FROM 55);
    END IF;

    IF to_regclass('public.plugins') IS NOT NULL THEN
        UPDATE plugins
        SET enabled = true,
            order_index = 45,
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND (enabled IS DISTINCT FROM true OR order_index IS DISTINCT FROM 45);

        UPDATE plugins
        SET enabled = false,
            order_index = 55,
            updated_at = now()
        WHERE id = 'movievault_26'
          AND (enabled IS DISTINCT FROM false OR order_index IS DISTINCT FROM 55);
    END IF;
END $$;

-- 2. Drop the "bucketFallback" field from the stored settingsSchema of the
--    installed movievault_v2 rows. The anonymous bucket lookup is what resolves a
--    disc the locally synced index does not carry yet, so it is now enforced
--    server-side (enforced_bucket_fallback()) rather than being switchable.
--    Registry sync already strips the field via ENFORCED_PLUGIN_SETTINGS, but
--    existing installs hold the old schema until the next sync. settingsSchema.
--    settings is a JSON array of field objects; a field is identified by its
--    "name" (falling back to "key").
DO $$
BEGIN
    IF to_regclass('public.plugins') IS NOT NULL THEN
        UPDATE plugins
        SET settings_schema = jsonb_set(
                settings_schema,
                '{settings}',
                COALESCE((
                    SELECT jsonb_agg(field)
                    FROM jsonb_array_elements(settings_schema -> 'settings') AS field
                    WHERE COALESCE(field ->> 'name', field ->> 'key') <> 'bucketFallback'
                ), '[]'::jsonb)
            ),
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND jsonb_typeof(settings_schema -> 'settings') = 'array'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(settings_schema -> 'settings') AS field
              WHERE COALESCE(field ->> 'name', field ->> 'key') = 'bucketFallback'
          );
    END IF;

    IF to_regclass('public.metadata_plugins') IS NOT NULL THEN
        UPDATE metadata_plugins
        SET settings_schema = jsonb_set(
                settings_schema,
                '{settings}',
                COALESCE((
                    SELECT jsonb_agg(field)
                    FROM jsonb_array_elements(settings_schema -> 'settings') AS field
                    WHERE COALESCE(field ->> 'name', field ->> 'key') <> 'bucketFallback'
                ), '[]'::jsonb)
            ),
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND jsonb_typeof(settings_schema -> 'settings') = 'array'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(settings_schema -> 'settings') AS field
              WHERE COALESCE(field ->> 'name', field ->> 'key') = 'bucketFallback'
          );
    END IF;
END $$;

-- 3. Remove any stored "bucketFallback" value. Runtime ignores it
--    (enforced_bucket_fallback() always wins) and with the schema field gone it
--    can no longer render anywhere; a lingering "false" would be a trap for the
--    next reader.
DO $$
BEGIN
    IF to_regclass('public.plugin_settings') IS NOT NULL THEN
        UPDATE plugin_settings
        SET settings = settings - 'bucketFallback',
            updated_at = now()
        WHERE plugin_id = 'movievault_v2'
          AND settings ? 'bucketFallback';
    END IF;

    IF to_regclass('public.metadata_plugin_settings') IS NOT NULL THEN
        UPDATE metadata_plugin_settings
        SET settings = settings - 'bucketFallback',
            updated_at = now()
        WHERE plugin_id = 'movievault_v2'
          AND settings ? 'bucketFallback';
    END IF;
END $$;
