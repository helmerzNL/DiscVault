INSERT INTO permissions (key, domain, description) VALUES
    ('collection.view', 'collection', 'View the collection'),
    ('collection.add', 'collection', 'Add movies to the collection'),
    ('collection.edit_all', 'collection', 'Edit all movies'),
    ('collection.delete_all', 'collection', 'Delete all movies'),
    ('collection.import', 'collection', 'Import collection data'),
    ('collection.bulk_edit', 'collection', 'Bulk edit collection data'),
    ('metadata.search', 'metadata', 'Search metadata providers'),
    ('metadata.refresh_one', 'metadata', 'Refresh metadata for one movie'),
    ('metadata.refresh_bulk', 'metadata', 'Refresh metadata in bulk'),
    ('metadata.manage_plugins', 'metadata', 'Manage metadata plugins'),
    ('metadata.manage_plugin_order', 'metadata', 'Manage metadata plugin order'),
    ('metadata.manage_plugin_settings', 'metadata', 'Manage metadata plugin settings'),
    ('metadata.view_plugin_health', 'metadata', 'View metadata plugin health'),
    ('users.view', 'users', 'View users'),
    ('users.invite', 'users', 'Invite users'),
    ('users.disable', 'users', 'Disable users'),
    ('users.delete', 'users', 'Delete users'),
    ('users.assign_roles', 'users', 'Assign user roles'),
    ('security.toggle_auth', 'security', 'Toggle authentication'),
    ('security.manage_invite_only', 'security', 'Manage invite-only registration'),
    ('security.export_security', 'security', 'Export security data'),
    ('security.restore_security', 'security', 'Restore security data'),
    ('admin.view_settings', 'admin', 'View admin settings'),
    ('admin.edit_settings', 'admin', 'Edit admin settings'),
    ('admin.view_logs', 'admin', 'View logs'),
    ('admin.backup', 'admin', 'Create backups'),
    ('admin.restore_functional', 'admin', 'Restore functional data'),
    ('admin.restore_full', 'admin', 'Restore full data'),
    ('admin.view_jobs', 'admin', 'View background jobs'),
    ('admin.cancel_jobs', 'admin', 'Cancel background jobs'),
    ('admin.manage_push', 'admin', 'Manage push settings'),
    ('admin.manage_license', 'admin', 'Manage license state'),
    ('admin.view_entitlements', 'admin', 'View entitlements')
ON CONFLICT (key) DO NOTHING;

INSERT INTO roles (key, name, description, system) VALUES
    ('owner', 'Owner', 'Root administrator with full instance control', true),
    ('admin', 'Administrator', 'Broad administration access', true),
    ('collection_manager', 'Collection Manager', 'Can manage collection content', true),
    ('metadata_manager', 'Metadata Manager', 'Can manage metadata plugins and refreshes', true),
    ('user_manager', 'User Manager', 'Can invite users and manage roles', true),
    ('auditor', 'Auditor', 'Read-only audit and diagnostics access', true),
    ('viewer', 'Viewer', 'Read-only collection access', true),
    ('member', 'Member', 'Standard collection member', true)
ON CONFLICT (key) DO NOTHING;

WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, permissions.key
FROM owner_role, permissions
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, permissions.key
FROM admin_role, permissions
WHERE permissions.key NOT IN ('security.export_security', 'security.restore_security', 'admin.restore_full')
ON CONFLICT (role_id, permission_key) DO NOTHING;

INSERT INTO metadata_plugins (
    id,
    name,
    version,
    enabled,
    installed,
    order_index,
    manifest,
    settings_schema,
    premium_feature_key
) VALUES
    (
        'tmdb',
        'TMDb',
        '1.0.0',
        false,
        true,
        10,
        '{"capabilities":["search_title","lookup_external_id","movie_details","people","images","videos"]}'::jsonb,
        '{}'::jsonb,
        null
    ),
    (
        'omdb',
        'OMDb',
        '1.0.0',
        false,
        true,
        20,
        '{"capabilities":["search_title","lookup_external_id","movie_details","ratings"]}'::jsonb,
        '{}'::jsonb,
        null
    ),
    (
        'upcitemdb',
        'UPCItemDB',
        '1.0.0',
        false,
        true,
        30,
        '{"capabilities":["search_barcode","title_hint"]}'::jsonb,
        '{}'::jsonb,
        null
    ),
    (
        'bluray_com',
        'Blu-ray.com',
        '1.0.0',
        false,
        true,
        40,
        '{"capabilities":["search_barcode","movie_details","technical_specs","box_set_candidates"]}'::jsonb,
        '{}'::jsonb,
        null
    ),
    (
        'movievault',
        'MovieVault',
        '1.0.0',
        false,
        true,
        50,
        '{"capabilities":["search_barcode","search_title","movie_details","box_set_candidates","contribute_optional"]}'::jsonb,
        '{}'::jsonb,
        'metadata.plugin.movievault.use'
    )
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    version = EXCLUDED.version,
    installed = EXCLUDED.installed,
    manifest = EXCLUDED.manifest,
    settings_schema = EXCLUDED.settings_schema,
    premium_feature_key = EXCLUDED.premium_feature_key,
    updated_at = now();

INSERT INTO feature_entitlements (feature_key, enabled, source, limits) VALUES
    ('metadata.bulk_refresh', true, 'beta', '{}'::jsonb),
    ('metadata.plugin.movievault.use', true, 'beta', '{}'::jsonb),
    ('metadata.plugin.movievault.bulk', true, 'beta', '{}'::jsonb),
    ('metadata.plugin.movievault.contribute', true, 'beta', '{}'::jsonb),
    ('rbac.custom_roles', true, 'beta', '{}'::jsonb),
    ('admin.advanced_audit', true, 'beta', '{}'::jsonb)
ON CONFLICT (feature_key) DO NOTHING;
