-- Uninstall the MovieVault 26 plugin and leave MovieVault v2 as the top source.
--
-- `app/backend/next_plugins/movievault_26/` no longer ships with the image, and
-- neither does the v25 `movievault` plugin it replaced. Removing the files alone
-- is not an uninstall: sync_plugin_registry() only flips a vanished plugin to
-- installed=false, enabled=false and leaves the row, its stored settings, its
-- cached lookups and its MovieVault credentials behind. This migration removes
-- them the same way the App Admin "delete plugin" route does
-- (`delete_plugin_records` in next_app.py), and in the same order, because
-- metadata_field_provenance references metadata_plugins ON DELETE RESTRICT and
-- would otherwise refuse the delete.
--
-- Three consequences worth stating plainly, all repeated in the release notes:
--
-- 1. FIELD PROVENANCE ATTRIBUTED TO MOVIEVAULT 26 IS DROPPED. The movie data
--    those rows describe is untouched - only the record of which plugin
--    supplied a given field goes. That is what uninstalling a metadata source
--    has always done here; keeping the rows is not an option while the foreign
--    key is RESTRICT.
-- 2. DISCVAULT NO LONGER HAS A METADATA RECEIVER. movievault_26 was the only
--    plugin carrying the `metadata_receiver` category, so nothing contributes
--    back over the v1 receiver route any more. The separate movievault_v2
--    release-contribution path (`movievault_v2_contribution_enabled`) is
--    untouched and keeps working.
-- 3. THE STORED MOVIEVAULT CONNECTION IS DESTROYED. The instance keypair, the
--    v1 and v3 API tokens and the handshake state were credentials for an
--    integration that no longer exists, so they are deleted rather than left at
--    rest. There is nothing left that could re-use them: reinstating MovieVault
--    26 would bootstrap a fresh instance identity.
--
-- Both registry tables are written for every step. sync_plugin_registry()
-- mirrors plugins <- metadata_plugins, so touching only one is either reverted
-- at the next sync or stale until it happens. Every table access is guarded so
-- this is a no-op on an install that never had the tables, and every statement
-- is a plain DELETE/conditional UPDATE so re-running changes nothing.

-- 1. Retire work still queued for the removed plugins. A pending
--    `plugin.execute` job naming a plugin that no longer exists can only fail,
--    and it would fail after the rows it needs are gone - reporting a missing
--    plugin rather than the removal that caused it.
--
--    Failed rather than a new "cancelled" status: `background_jobs.status` has
--    no CHECK constraint, but every reader in the app knows exactly
--    pending/running/completed/failed, and inventing a fifth value here would
--    show up as an unrecognised state in the jobs list. Only `pending` is
--    touched - a `running` job is already claimed by a worker, and rewriting it
--    underneath that worker is a race this migration has no need to enter.
DO $$
BEGIN
    IF to_regclass('public.background_jobs') IS NOT NULL THEN
        UPDATE background_jobs
        SET status = 'failed',
            finished_at = now(),
            error = 'MovieVault 26 was removed from DiscVault'
        WHERE status = 'pending'
          AND payload->>'pluginId' IN ('movievault_26', 'movievault');
    END IF;
END $$;

-- 2. Delete the plugin records. Provenance first (ON DELETE RESTRICT), then the
--    per-plugin settings and cache, then the registry rows themselves.
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

-- 3. Purge the MovieVault connection at rest: the instance keypair (encrypted or
--    legacy plaintext), both API tokens, the handshake/bootstrap state and the
--    v1 receiver gate. These were read only by `next_movievault_connection.py`,
--    which is removed in the same change.
--
--    Two keys are deliberately absent. `movievault_v2_*` belongs to the plugin
--    that stays. `movievault_enabled` is a v25 legacy flag read by the
--    once-only legacy import reconciliation (`legacy_metadata_plugin_plan` in
--    next_import.py), not by the connection: deleting it would change whether
--    that reconciliation runs at all on an install that has not done it yet.
DO $$
BEGIN
    IF to_regclass('public.app_settings') IS NOT NULL THEN
        DELETE FROM app_settings
        WHERE key IN (
            'plugin_secret:movievault:token',
            'plugin_secret:movievault_26:token',
            'movievault_api_token',
            'movievault_api_key',
            'movievault_instance_id',
            'movievault_instance_name',
            'movievault_instance_private_key',
            'movievault_instance_public_key',
            'movievault_instance_public_key_id',
            'movievault_token_prefix',
            'movievault_token_scopes',
            'movievault_last_bootstrap_at',
            'movievault_last_handshake_at',
            'movievault_link_status',
            'movievault_auth_method',
            'movievault_sharing_mode',
            'movievault_search_url',
            'movievault_ingest_url',
            'movievault_contribution_url',
            'movievault_contribution_enabled',
            'movievault_discvault_handshake_secret',
            'movievault_v3_api_token',
            'movievault_v3_key_id',
            'movievault_v3_instance_id',
            'movievault_v3_token_prefix',
            'movievault_v3_scopes',
            'movievault_v3_last_bootstrap_at'
        );
    END IF;
END $$;

-- 4. MovieVault v2 becomes the highest-priority metadata source.
--
--    metadata_source_plugins() orders by (order_index, lower(name)), lowest
--    first, and the bundled manifests ship tmdb at 10 - so 5 is what "above
--    everything DiscVault ships" costs. The manifest carries the same 5, but
--    sync_plugin_registry()'s ON CONFLICT clauses deliberately omit `enabled`
--    and `order_index` so that a manifest change can never silently re-enable
--    or re-rank a plugin an operator had set by hand. Existing installs would
--    therefore never see it, which is what this step is for.
--
--    Like migration 058 before it, this is a ONE-SHOT OVERRIDE OF OPERATOR
--    INTENT: an admin who had deliberately disabled movievault_v2 or moved it
--    down is flipped once. The schema carries no prior-intent signal to tell a
--    deliberate choice from an untouched default, and the migration ledger
--    guarantees this runs exactly once.
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
