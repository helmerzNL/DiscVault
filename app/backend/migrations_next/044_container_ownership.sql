ALTER TABLE containers
    ADD COLUMN IF NOT EXISTS owner_id uuid REFERENCES users(id) ON DELETE SET NULL;

WITH instance_owner AS (
    SELECT u.id
    FROM users u
    JOIN user_roles ur ON ur.user_id = u.id
    JOIN roles r ON r.id = ur.role_id
    WHERE r.key = 'owner'
    ORDER BY
        CASE WHEN u.status = 'active' THEN 0 ELSE 1 END,
        u.created_at ASC,
        u.id ASC
    LIMIT 1
)
UPDATE containers c
SET owner_id = instance_owner.id
FROM instance_owner
WHERE c.owner_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_containers_owner_id
    ON containers(owner_id);
