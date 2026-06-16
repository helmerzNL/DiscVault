INSERT INTO permissions (key, domain, description) VALUES
    ('plugins.delete', 'plugins', 'Delete installed plugins')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, 'plugins.delete'
FROM owner_role
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, 'plugins.delete'
FROM admin_role
ON CONFLICT (role_id, permission_key) DO NOTHING;
