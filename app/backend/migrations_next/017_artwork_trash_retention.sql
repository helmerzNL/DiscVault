ALTER TABLE entity_media
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz,
    ADD COLUMN IF NOT EXISTS deleted_by uuid REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS purge_after timestamptz,
    ADD COLUMN IF NOT EXISTS restore_metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_entity_media_deleted_at
    ON entity_media(deleted_at, purge_after)
    WHERE deleted_at IS NOT NULL;

INSERT INTO permissions (key, domain, description) VALUES
    ('metadata.manage_artwork_trash', 'metadata', 'Restore and purge deleted posters and backdrops')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, 'metadata.manage_artwork_trash'
FROM owner_role
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, 'metadata.manage_artwork_trash'
FROM admin_role
ON CONFLICT (role_id, permission_key) DO NOTHING;

INSERT INTO app_settings (key, value, is_secret) VALUES
    ('artwork_trash_purge_enabled', 'false'::jsonb, false),
    ('artwork_trash_retention', '"7d"'::jsonb, false)
ON CONFLICT (key) DO NOTHING;
