INSERT INTO permissions (key, domain, description) VALUES
    ('collection.view_own', 'collection', 'View movies owned by the user'),
    ('collection.view_group', 'collection', 'View movies shared through media groups the user belongs to'),
    ('collection.view_all', 'collection', 'View all movies in the instance'),
    ('collection.edit_group', 'collection', 'Edit movies shared through managed media groups'),
    ('collection.delete_group', 'collection', 'Delete movies shared through managed media groups'),
    ('groups.view_all', 'groups', 'View all media groups')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, permissions.key
FROM owner_role, permissions
WHERE permissions.key IN (
    'collection.view_own',
    'collection.view_group',
    'collection.view_all',
    'collection.edit_group',
    'collection.delete_group',
    'groups.view_all'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, permissions.key
FROM admin_role, permissions
WHERE permissions.key IN (
    'collection.view_own',
    'collection.view_group',
    'collection.view_all',
    'collection.edit_group',
    'collection.delete_group',
    'groups.view_all'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH media_editor_role AS (
    SELECT id FROM roles WHERE key = 'media_editor'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT media_editor_role.id, permissions.key
FROM media_editor_role, permissions
WHERE permissions.key IN (
    'collection.view_own',
    'collection.view_group',
    'collection.edit_group',
    'collection.delete_group'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH media_fan_role AS (
    SELECT id FROM roles WHERE key = 'media_fan'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT media_fan_role.id, permissions.key
FROM media_fan_role, permissions
WHERE permissions.key IN (
    'collection.view_own',
    'collection.view_group'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH media_viewer_role AS (
    SELECT id FROM roles WHERE key = 'media_viewer'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT media_viewer_role.id, permissions.key
FROM media_viewer_role, permissions
WHERE permissions.key IN (
    'collection.view_own',
    'collection.view_group'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH api_reader_role AS (
    SELECT id FROM roles WHERE key = 'api_reader'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT api_reader_role.id, permissions.key
FROM api_reader_role, permissions
WHERE permissions.key IN ('collection.view_own', 'collection.view_group')
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH api_writer_role AS (
    SELECT id FROM roles WHERE key = 'api_writer'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT api_writer_role.id, permissions.key
FROM api_writer_role, permissions
WHERE permissions.key IN ('collection.view_own', 'collection.view_group', 'collection.edit_group', 'collection.delete_group')
ON CONFLICT (role_id, permission_key) DO NOTHING;
