CREATE TABLE IF NOT EXISTS audit_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      text NOT NULL,
    category        text NOT NULL DEFAULT 'system',
    actor_user_id   uuid REFERENCES users(id) ON DELETE SET NULL,
    actor_username  text,
    actor_role      text,
    target_type     text,
    target_id       text,
    summary         text,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    request_ip      text,
    user_agent      text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_category_created_at
    ON audit_events(category, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_actor_user
    ON audit_events(actor_user_id, created_at DESC);

INSERT INTO permissions (key, domain, description) VALUES
    ('admin.view_audit', 'admin', 'View audit log events')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, 'admin.view_audit'
FROM owner_role
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, 'admin.view_audit'
FROM admin_role
ON CONFLICT (role_id, permission_key) DO NOTHING;
