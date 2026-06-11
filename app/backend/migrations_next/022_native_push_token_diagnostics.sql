ALTER TABLE native_push_devices
    ADD COLUMN IF NOT EXISTS token_hash text,
    ADD COLUMN IF NOT EXISTS token_suffix text;

UPDATE native_push_devices
SET
    token_hash = COALESCE(token_hash, encode(digest(device_token, 'sha256'), 'hex')),
    token_suffix = COALESCE(token_suffix, right(device_token, 6))
WHERE device_token IS NOT NULL
  AND (token_hash IS NULL OR token_suffix IS NULL);

DROP INDEX IF EXISTS idx_native_push_devices_token_active;

CREATE UNIQUE INDEX IF NOT EXISTS idx_native_push_devices_user_token_active
    ON native_push_devices(user_id, token_hash, apns_environment, COALESCE(app_bundle_id, ''))
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_native_push_devices_token_hash
    ON native_push_devices(token_hash);
