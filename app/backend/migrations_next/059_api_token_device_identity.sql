-- Device identity for native API tokens.
--
-- Until now every native login inserted a fresh api_access_tokens row named
-- "DiscVault iOS": there was no refresh endpoint, so the app re-logs in to get a
-- token, and the row carried nothing to recognise the device by. The API & MCP
-- page therefore grew one entry per login instead of one per device.
--
-- These columns give a token an identity. device_id is the stable per-install
-- identifier the client sends; the partial unique index makes it the dedupe key
-- for active tokens, mirroring idx_native_push_devices_user_token_active from
-- 022_native_push_token_diagnostics.sql.

ALTER TABLE api_access_tokens
    ADD COLUMN IF NOT EXISTS client_kind text,
    ADD COLUMN IF NOT EXISTS device_id text,
    ADD COLUMN IF NOT EXISTS device_label text,
    ADD COLUMN IF NOT EXISTS device_model text,
    ADD COLUMN IF NOT EXISTS user_agent text;

-- Existing rows predate client_kind. The mobile issuer hardcoded this name, so
-- it is the only signal available for a backfill; user-created keys are left
-- alone and keep a NULL client_kind.
UPDATE api_access_tokens
SET client_kind = 'ios'
WHERE client_kind IS NULL
  AND name = 'DiscVault iOS';

-- Deliberately no backfill of device_id: the rows are indistinguishable, and
-- inventing a shared id would make one device's login revoke another's token.
-- Legacy duplicates stay valid and are collapsed for display instead.

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_access_tokens_user_device_active
    ON api_access_tokens(user_id, device_id)
    WHERE device_id IS NOT NULL AND revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_api_access_tokens_user_client_kind
    ON api_access_tokens(user_id, client_kind);
