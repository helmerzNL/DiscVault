CREATE TABLE IF NOT EXISTS mobile_auth_flows (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    callback_scheme       text NOT NULL,
    state                 text,
    code_challenge        text NOT NULL,
    code_challenge_method text NOT NULL DEFAULT 'S256',
    created_at            timestamptz NOT NULL DEFAULT now(),
    expires_at            timestamptz NOT NULL,
    used_at               timestamptz
);

CREATE INDEX IF NOT EXISTS idx_mobile_auth_flows_active
    ON mobile_auth_flows(expires_at, used_at);

CREATE TABLE IF NOT EXISTS mobile_auth_codes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code_hash       text NOT NULL UNIQUE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mobile_flow_id  uuid NOT NULL REFERENCES mobile_auth_flows(id) ON DELETE CASCADE,
    code_challenge  text NOT NULL,
    state           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    used_at         timestamptz
);

CREATE INDEX IF NOT EXISTS idx_mobile_auth_codes_active
    ON mobile_auth_codes(expires_at, used_at);
