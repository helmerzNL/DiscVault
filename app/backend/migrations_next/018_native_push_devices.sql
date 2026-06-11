CREATE TABLE IF NOT EXISTS native_push_devices (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform         text NOT NULL DEFAULT 'ios',
    device_token     text NOT NULL,
    apns_environment text NOT NULL DEFAULT 'production',
    app_bundle_id    text,
    device_label     text,
    app_version      text,
    user_agent       text,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz,
    CHECK (platform IN ('ios')),
    CHECK (apns_environment IN ('sandbox', 'production'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_native_push_devices_token_active
    ON native_push_devices(platform, apns_environment, device_token)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_native_push_devices_user_active
    ON native_push_devices(user_id, platform, revoked_at, updated_at DESC);
