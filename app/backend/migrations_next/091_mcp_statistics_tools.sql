-- The five statistics tools reached app/mcp-server/server.py in #654 but never
-- got a permission. The MCP server checks every call against
-- /api/next/mcp/catalog, which is built from the permissions table, so all five
-- answered "Permission denied" for every role -- owner included -- and no
-- setting could grant them because the keys did not exist.
INSERT INTO permissions (key, domain, description) VALUES
    ('mcp.tool.get_collection_stats_detailed', 'mcp', 'MCP: read detailed collection statistics'),
    ('mcp.tool.get_top_formats', 'mcp', 'MCP: read the format breakdown'),
    ('mcp.tool.get_top_genres', 'mcp', 'MCP: read the genre breakdown'),
    ('mcp.tool.get_top_directors', 'mcp', 'MCP: read the top directors'),
    ('mcp.tool.get_top_actors', 'mcp', 'MCP: read the top actors')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

-- Mirrors 016_mcp_api_access.sql: owner, admin and mcp_user hold every mcp.%
-- permission. Re-running the same grant is what carries the new keys to the
-- roles on an existing install; ON CONFLICT keeps it idempotent.
WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, permissions.key
FROM owner_role, permissions
WHERE permissions.key LIKE 'mcp.%'
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, permissions.key
FROM admin_role, permissions
WHERE permissions.key LIKE 'mcp.%'
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH mcp_role AS (
    SELECT id FROM roles WHERE key = 'mcp_user'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT mcp_role.id, permissions.key
FROM mcp_role, permissions
WHERE permissions.key LIKE 'mcp.%'
ON CONFLICT (role_id, permission_key) DO NOTHING;
