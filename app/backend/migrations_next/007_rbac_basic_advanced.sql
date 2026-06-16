INSERT INTO permissions (key, domain, description) VALUES
    ('collection.add_own', 'collection', 'Add self-owned media'),
    ('collection.edit_own', 'collection', 'Edit self-owned media'),
    ('collection.delete_own', 'collection', 'Delete self-owned media'),
    ('collection.export_functional', 'collection', 'Export functional collection data'),
    ('containers.view', 'containers', 'View containers, box sets and groups'),
    ('containers.create', 'containers', 'Create containers, box sets and groups'),
    ('containers.edit', 'containers', 'Edit containers, box sets and groups'),
    ('containers.delete', 'containers', 'Delete containers, box sets and groups'),
    ('groups.view', 'groups', 'View groups the user can access'),
    ('groups.create', 'groups', 'Create own media groups'),
    ('groups.invite', 'groups', 'Invite users to own media groups'),
    ('watchlist.manage', 'personal', 'Manage personal watchlists'),
    ('lending.request', 'personal', 'Request or register borrowing media'),
    ('metadata.view_debug_payloads', 'metadata', 'View metadata debug payloads'),
    ('metadata.manage_receivers', 'metadata', 'Manage metadata receiver integrations'),
    ('users.manage_passkeys', 'users', 'Manage user passkeys'),
    ('roles.view', 'roles', 'View role and permission catalog'),
    ('roles.manage', 'roles', 'Create, edit and delete custom roles'),
    ('security.manage_rbac_mode', 'security', 'Switch Basic and Advanced RBAC mode'),
    ('admin.clear_logs', 'admin', 'Clear logs'),
    ('admin.manage_pwa', 'admin', 'Manage PWA settings'),
    ('digital_sources.view', 'digital_sources', 'View digital media source integrations'),
    ('digital_sources.connect', 'digital_sources', 'Connect digital media source integrations'),
    ('digital_sources.sync', 'digital_sources', 'Synchronize digital media source integrations'),
    ('digital_sources.manage', 'digital_sources', 'Manage digital media source integrations')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

INSERT INTO roles (key, name, description, system) VALUES
    ('media_editor', 'Media Editor', 'Can add, edit, delete, import and refresh media', true),
    ('media_fan', 'Media Fan', 'Can view group media, add own media, create groups and invite by username', true),
    ('media_viewer', 'Media Viewer', 'Can view group media and use personal watchlist or borrowing features', true)
ON CONFLICT (key) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    system = true,
    updated_at = now();

UPDATE roles
SET
    name = 'Beheerder',
    description = 'Broad administration access without protected owner-only controls',
    system = true,
    updated_at = now()
WHERE key = 'admin';

INSERT INTO app_settings (key, value, is_secret)
VALUES ('rbac_mode', '"basic"'::jsonb, false)
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
WHERE permissions.key NOT IN (
    'security.manage_rbac_mode',
    'security.export_security',
    'security.restore_security',
    'admin.restore_full'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH media_editor_role AS (
    SELECT id FROM roles WHERE key = 'media_editor'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT media_editor_role.id, permissions.key
FROM media_editor_role, permissions
WHERE permissions.key IN (
    'collection.view',
    'collection.add',
    'collection.add_own',
    'collection.edit_own',
    'collection.edit_all',
    'collection.delete_own',
    'collection.delete_all',
    'collection.import',
    'collection.bulk_edit',
    'containers.view',
    'containers.create',
    'containers.edit',
    'containers.delete',
    'metadata.search',
    'metadata.refresh_one',
    'metadata.refresh_bulk',
    'watchlist.manage',
    'lending.request'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH media_fan_role AS (
    SELECT id FROM roles WHERE key = 'media_fan'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT media_fan_role.id, permissions.key
FROM media_fan_role, permissions
WHERE permissions.key IN (
    'collection.view',
    'collection.add_own',
    'collection.edit_own',
    'containers.view',
    'groups.view',
    'groups.create',
    'groups.invite',
    'metadata.search',
    'watchlist.manage',
    'lending.request'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH media_viewer_role AS (
    SELECT id FROM roles WHERE key = 'media_viewer'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT media_viewer_role.id, permissions.key
FROM media_viewer_role, permissions
WHERE permissions.key IN (
    'collection.view',
    'containers.view',
    'groups.view',
    'watchlist.manage',
    'lending.request'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

INSERT INTO feature_entitlements (feature_key, enabled, source, limits) VALUES
    ('rbac.advanced_mode', true, 'beta', '{}'::jsonb),
    ('rbac.basic_mode', true, 'beta', '{}'::jsonb)
ON CONFLICT (feature_key) DO UPDATE SET
    enabled = EXCLUDED.enabled,
    source = EXCLUDED.source,
    limits = EXCLUDED.limits,
    updated_at = now();
