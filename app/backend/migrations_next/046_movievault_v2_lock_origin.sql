-- Lock the MovieVault v2 endpoint (origin) so it is no longer user-editable.
--
-- The origin is now enforced server-side by the backend (a fixed default plus
-- the optional MOVIEVAULT_V2_ORIGIN env override, resolved via enforced_origin()).
-- Plugin registry sync already strips the "origin" field from the plugin's
-- settingsSchema, but existing installs still hold the old schema (with an
-- editable/required origin) and a stored operator value until the next sync.
--
-- This migration brings existing rows into the locked state immediately and is
-- fully idempotent: it only rewrites rows that still contain an "origin" entry,
-- and every table access is guarded so it is a no-op where the plugin tables do
-- not exist yet.

-- 1. Drop the "origin" field from the stored settingsSchema of the installed
--    plugin rows (both the generic plugins table and the metadata_plugins view
--    table). settingsSchema.settings is a JSON array of field objects; a field
--    is identified by its "name" (falling back to "key").
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
                    WHERE COALESCE(field ->> 'name', field ->> 'key') <> 'origin'
                ), '[]'::jsonb)
            ),
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND jsonb_typeof(settings_schema -> 'settings') = 'array'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(settings_schema -> 'settings') AS field
              WHERE COALESCE(field ->> 'name', field ->> 'key') = 'origin'
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
                    WHERE COALESCE(field ->> 'name', field ->> 'key') <> 'origin'
                ), '[]'::jsonb)
            ),
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND jsonb_typeof(settings_schema -> 'settings') = 'array'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(settings_schema -> 'settings') AS field
              WHERE COALESCE(field ->> 'name', field ->> 'key') = 'origin'
          );
    END IF;
END $$;

-- 2. Remove any stored operator-supplied "origin" value. Runtime ignores it
--    (enforced_origin() always wins), and with the schema field gone it can no
--    longer render anywhere; dropping it avoids a stale value lingering in the
--    settings blob.
DO $$
BEGIN
    IF to_regclass('public.plugin_settings') IS NOT NULL THEN
        UPDATE plugin_settings
        SET settings = settings - 'origin',
            updated_at = now()
        WHERE plugin_id = 'movievault_v2'
          AND settings ? 'origin';
    END IF;

    IF to_regclass('public.metadata_plugin_settings') IS NOT NULL THEN
        UPDATE metadata_plugin_settings
        SET settings = settings - 'origin',
            updated_at = now()
        WHERE plugin_id = 'movievault_v2'
          AND settings ? 'origin';
    END IF;
END $$;
