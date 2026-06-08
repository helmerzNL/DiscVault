INSERT INTO roles (key, name, description, system) VALUES
    ('member_groups', 'Member Groups', 'Can create personal media groups and invite users by username', true)
ON CONFLICT (key) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    system = true;

WITH member_groups_role AS (
    SELECT id FROM roles WHERE key = 'member_groups'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT member_groups_role.id, permissions.key
FROM member_groups_role, permissions
WHERE permissions.key IN (
    'groups.view',
    'groups.create',
    'groups.invite',
    'collection.view_own',
    'collection.view_group'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH media_fan_role AS (
    SELECT id FROM roles WHERE key = 'media_fan'
)
DELETE FROM role_permissions rp
USING media_fan_role
WHERE rp.role_id = media_fan_role.id
  AND rp.permission_key IN ('groups.create', 'groups.invite');
