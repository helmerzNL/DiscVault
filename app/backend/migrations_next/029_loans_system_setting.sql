-- Loans System toggle (admin-only) + instance-wide enablement flag.
-- New permission gates who can see/flip the "Loans System" switch in
-- Preferences -> Collectors. The switch itself is stored as an instance-wide
-- app_setting so individual users cannot override it.

INSERT INTO permissions (key, domain, description) VALUES
    ('security.manage_loans_system', 'security', 'Enable or disable the Loans System instance-wide')
ON CONFLICT (key) DO UPDATE SET
    domain = EXCLUDED.domain,
    description = EXCLUDED.description;

-- Grant to owner and admin roles (both are administrators). This permission is
-- intentionally NOT in the admin exclusion list, so the admin role holds it too.
WITH owner_role AS (
    SELECT id FROM roles WHERE key = 'owner'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT owner_role.id, 'security.manage_loans_system'
FROM owner_role
ON CONFLICT (role_id, permission_key) DO NOTHING;

WITH admin_role AS (
    SELECT id FROM roles WHERE key = 'admin'
)
INSERT INTO role_permissions (role_id, permission_key)
SELECT admin_role.id, 'security.manage_loans_system'
FROM admin_role
ON CONFLICT (role_id, permission_key) DO NOTHING;

-- Seed the instance-wide flag. Existing installs that already have loan data
-- keep the feature ON; fresh installs default to OFF. The loans table is
-- created by migration 026 (always present before this migration runs).
INSERT INTO app_settings (key, value, is_secret)
SELECT
    'loans_system_enabled',
    (EXISTS (SELECT 1 FROM loans))::text::jsonb,
    false
ON CONFLICT (key) DO NOTHING;
