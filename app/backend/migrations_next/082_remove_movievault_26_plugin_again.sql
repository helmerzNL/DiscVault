-- Delete the MovieVault 26 registry rows that plugin discovery re-created.
--
-- Migration 080 deleted them correctly and they came back, which is worth
-- stating plainly rather than filing as a flake. 080 removed the rows; it did
-- not remove the plugin's *files*, and on an existing install those do not live
-- in the image at all.
--
-- The first boot of any DiscVault install copies every bundled plugin into the
-- writable install directory (normally `/data/plugins`) and writes an
-- `.initialized` marker, after which `plugin_paths()` treats that copy as the
-- authoritative source and stops reading the image. Dropping
-- `next_plugins/movievault_26/` from the image therefore changed nothing for an
-- install that had ever run an older version: its own copy sat in the data
-- volume, discovery still found it, and the first `sync_plugin_registry()` after
-- this migration re-INSERTed the row - disabled and at order 55, exactly as the
-- manifest in that stale copy describes it.
--
-- The durable half of the fix is in code, not here: `WITHDRAWN_PLUGIN_IDS` in
-- `next_plugin_runtime.py` makes discovery skip these ids wherever their files
-- are, and `remove_withdrawn_plugin_dirs()` deletes the stale copy from the
-- install directory. With discovery no longer producing the plugin, nothing
-- re-creates the rows, so this migration is the last time they need deleting.
--
-- Numbered 082 rather than 081: `081_entity_media_user_scans.sql` landed on
-- beta first, and two files sharing a version make `next_database.py migrate`
-- refuse to run at all - which, in a container whose command is
-- `migrate && exec gunicorn`, is a deployment that never starts.
--
-- 080 is left exactly as it is. It ran, it is in the ledger, and rewriting an
-- applied migration would not re-run it for anybody. This is deliberately a
-- narrower repeat: rows only. The credentials and app_settings keys 080 purged
-- cannot have returned - the code that wrote them was deleted in the same
-- change - so there is nothing to purge twice.

DO $$
BEGIN
    IF to_regclass('public.metadata_field_provenance') IS NOT NULL THEN
        DELETE FROM metadata_field_provenance
        WHERE plugin_id IN ('movievault_26', 'movievault');
    END IF;

    IF to_regclass('public.metadata_lookup_cache') IS NOT NULL THEN
        DELETE FROM metadata_lookup_cache
        WHERE plugin_id IN ('movievault_26', 'movievault');
    END IF;

    IF to_regclass('public.metadata_plugin_settings') IS NOT NULL THEN
        DELETE FROM metadata_plugin_settings
        WHERE plugin_id IN ('movievault_26', 'movievault');
    END IF;

    IF to_regclass('public.plugin_settings') IS NOT NULL THEN
        DELETE FROM plugin_settings
        WHERE plugin_id IN ('movievault_26', 'movievault');
    END IF;

    IF to_regclass('public.metadata_plugins') IS NOT NULL THEN
        DELETE FROM metadata_plugins
        WHERE id IN ('movievault_26', 'movievault');
    END IF;

    IF to_regclass('public.plugins') IS NOT NULL THEN
        DELETE FROM plugins
        WHERE id IN ('movievault_26', 'movievault');
    END IF;
END $$;

-- Re-assert MovieVault v2's rank as well. `reconcile_plugin_replacements()` runs
-- on every registry sync and, while a `movievault_26` row existed carrying
-- `replacesPlugins: ["movievault"]`, could write an order inherited from a
-- legacy `movievault` row. Cheap to redo, and conditional so it is a no-op where
-- 080's value already stands.
DO $$
BEGIN
    IF to_regclass('public.metadata_plugins') IS NOT NULL THEN
        UPDATE metadata_plugins
        SET enabled = true,
            order_index = 5,
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND (enabled IS DISTINCT FROM true OR order_index IS DISTINCT FROM 5);
    END IF;

    IF to_regclass('public.plugins') IS NOT NULL THEN
        UPDATE plugins
        SET enabled = true,
            order_index = 5,
            updated_at = now()
        WHERE id = 'movievault_v2'
          AND (enabled IS DISTINCT FROM true OR order_index IS DISTINCT FROM 5);
    END IF;
END $$;
