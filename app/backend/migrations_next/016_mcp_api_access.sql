CREATE TABLE IF NOT EXISTS api_access_tokens (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            text NOT NULL,
    token_hash      text NOT NULL UNIQUE,
    scopes          jsonb NOT NULL DEFAULT '[]'::jsonb,
    permission_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz,
    expires_at      timestamptz,
    revoked_at      timestamptz
);

CREATE INDEX IF NOT EXISTS idx_api_access_tokens_user
    ON api_access_tokens(user_id, revoked_at, created_at DESC);

INSERT INTO permissions (key, domain, description) VALUES
    ('api.read', 'api', 'Read collection data through the DiscVault API'),
    ('api.write', 'api', 'Create, update and delete collection data through the DiscVault API'),
    ('api.tokens.manage', 'api', 'Create and revoke personal API and MCP access tokens'),
    ('mcp.use', 'mcp', 'Use the DiscVault MCP server'),
    ('mcp.tool.search_collection', 'mcp', 'MCP: search the collection'),
    ('mcp.tool.get_collection_stats', 'mcp', 'MCP: read collection statistics'),
    ('mcp.tool.get_movie_details', 'mcp', 'MCP: read movie details'),
    ('mcp.tool.add_movie', 'mcp', 'MCP: add a movie'),
    ('mcp.tool.delete_movie', 'mcp', 'MCP: delete a movie'),
    ('mcp.tool.lookup_barcode', 'mcp', 'MCP: look up a barcode'),
    ('mcp.tool.list_all_movies', 'mcp', 'MCP: list all movies'),
    ('mcp.tool.get_watchlist', 'mcp', 'MCP: read personal watchlist'),
    ('mcp.tool.get_watch_history', 'mcp', 'MCP: read personal watch history'),
    ('mcp.tool.get_groups', 'mcp', 'MCP: read personal media groups')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

INSERT INTO roles (key, name, description, system) VALUES
    ('mcp_user', 'MCP User', 'Can use DiscVault through the MCP server with assigned MCP tool permissions', true),
    ('api_reader', 'API Reader', 'Can read DiscVault data through API tokens', true),
    ('api_writer', 'API Writer', 'Can read and write DiscVault data through API tokens', true)
ON CONFLICT (key) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    system = true,
    updated_at = now();

WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, permissions.key
FROM owner_role, permissions
WHERE permissions.key LIKE 'api.%'
   OR permissions.key LIKE 'mcp.%'
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, permissions.key
FROM admin_role, permissions
WHERE permissions.key LIKE 'api.%'
   OR permissions.key LIKE 'mcp.%'
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH mcp_role AS (
    SELECT id FROM roles WHERE key = 'mcp_user'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT mcp_role.id, permissions.key
FROM mcp_role, permissions
WHERE permissions.key = 'api.tokens.manage'
   OR permissions.key LIKE 'mcp.%'
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH api_reader_role AS (
    SELECT id FROM roles WHERE key = 'api_reader'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT api_reader_role.id, permissions.key
FROM api_reader_role, permissions
WHERE permissions.key IN ('api.read', 'api.tokens.manage', 'collection.view', 'containers.view', 'groups.view')
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH api_writer_role AS (
    SELECT id FROM roles WHERE key = 'api_writer'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT api_writer_role.id, permissions.key
FROM api_writer_role, permissions
WHERE permissions.key IN (
    'api.read',
    'api.write',
    'api.tokens.manage',
    'collection.view',
    'collection.add',
    'collection.add_own',
    'collection.edit_all',
    'collection.delete_all',
    'collection.bulk_edit',
    'containers.view',
    'containers.create',
    'containers.edit',
    'containers.delete',
    'groups.view'
)
ON CONFLICT (role_id, permission_key) DO NOTHING;

INSERT INTO feature_entitlements (feature_key, enabled, source, limits) VALUES
    ('api.access_tokens', true, 'beta', '{}'::jsonb),
    ('mcp.server', true, 'beta', '{}'::jsonb)
ON CONFLICT (feature_key) DO UPDATE SET
    enabled = EXCLUDED.enabled,
    source = EXCLUDED.source,
    limits = EXCLUDED.limits,
    updated_at = now();
