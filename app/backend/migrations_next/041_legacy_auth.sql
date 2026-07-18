-- Opt-in database-backed password and TOTP authentication.
CREATE TABLE IF NOT EXISTS legacy_password_credentials (
    user_id                         uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    password_hash                   text NOT NULL,
    hash_version                    integer NOT NULL DEFAULT 1,
    must_change_password            boolean NOT NULL DEFAULT false,
    mfa_required                    boolean NOT NULL DEFAULT true,
    passkey_registration_allowed    boolean NOT NULL DEFAULT true,
    failed_attempt_count            integer NOT NULL DEFAULT 0,
    first_failed_at                 timestamptz,
    locked_until                    timestamptz,
    credential_expires_at           timestamptz,
    password_changed_at             timestamptz NOT NULL DEFAULT now(),
    last_login_at                   timestamptz,
    created_by                      uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at                      timestamptz NOT NULL DEFAULT now(),
    updated_at                      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_legacy_password_credentials_locked
    ON legacy_password_credentials(locked_until)
    WHERE locked_until IS NOT NULL;

CREATE TABLE IF NOT EXISTS legacy_totp_credentials (
    user_id                    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    encrypted_secret           bytea NOT NULL,
    nonce                      bytea NOT NULL,
    confirmed_at               timestamptz,
    last_accepted_step         bigint,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS legacy_mfa_recovery_codes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash   text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    used_at     timestamptz
);

CREATE INDEX IF NOT EXISTS idx_legacy_mfa_recovery_codes_user
    ON legacy_mfa_recovery_codes(user_id, used_at);

CREATE TABLE IF NOT EXISTS legacy_auth_flows (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash  text NOT NULL UNIQUE,
    flow_type   text NOT NULL CHECK (
        flow_type IN (
            'bootstrap_totp',
            'bootstrap_recovery_ack',
            'password_change',
            'mfa_enrollment',
            'mfa_recovery_ack',
            'mfa_challenge'
        )
    ),
    user_id     uuid REFERENCES users(id) ON DELETE CASCADE,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz
);

CREATE INDEX IF NOT EXISTS idx_legacy_auth_flows_expiry
    ON legacy_auth_flows(expires_at);

CREATE TABLE IF NOT EXISTS legacy_auth_attempts (
    id               bigserial PRIMARY KEY,
    identity_digest  text NOT NULL,
    ip_digest        text NOT NULL,
    succeeded        boolean NOT NULL DEFAULT false,
    occurred_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_legacy_auth_attempts_identity
    ON legacy_auth_attempts(identity_digest, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_legacy_auth_attempts_ip
    ON legacy_auth_attempts(ip_digest, occurred_at DESC);

INSERT INTO app_settings (key, value, is_secret, updated_at)
VALUES ('legacy_password_login_enabled', 'false'::jsonb, false, now())
ON CONFLICT (key) DO NOTHING;
