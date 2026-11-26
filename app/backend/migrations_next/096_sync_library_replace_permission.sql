-- The authority behind the link wizard's two destructive routes, POST
-- /api/next/sync/snapshot and POST /api/next/sync/retain (sync-contract §5f).
--
-- One permission, deliberately narrow, held by the owner and admin roles only.
-- No other role is granted it, so an ordinary user's token cannot reach those
-- routes even though a native token's key list carries the key: a token's keys
-- and its holder's role must both allow a permission, and the role half is
-- what stops everyone below admin.

INSERT INTO permissions (key, domain, description) VALUES
    ('sync.replace_library', 'sync', 'Replace the library with the one held by a linked app (snapshot and retain)')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, 'sync.replace_library'
FROM owner_role
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, 'sync.replace_library'
FROM admin_role
ON CONFLICT (role_id, permission_key) DO NOTHING;
