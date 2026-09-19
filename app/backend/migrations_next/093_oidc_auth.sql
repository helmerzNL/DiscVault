CREATE TABLE IF NOT EXISTS oidc_identities (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    issuer             text NOT NULL CHECK (issuer <> ''),
    subject            text NOT NULL CHECK (subject <> ''),
    provider_name      text NOT NULL DEFAULT 'OIDC',
    email              text,
    preferred_username text,
    display_name       text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    last_login_at      timestamptz,
    UNIQUE (issuer, subject),
    UNIQUE (user_id, issuer)
);

CREATE INDEX IF NOT EXISTS idx_oidc_identities_user
    ON oidc_identities(user_id);

CREATE TABLE IF NOT EXISTS oidc_auth_transactions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    state_hash         text NOT NULL UNIQUE,
    nonce              text NOT NULL,
    code_verifier      text NOT NULL,
    mode               text NOT NULL CHECK (mode IN ('login', 'link')),
    initiating_user_id uuid REFERENCES users(id) ON DELETE CASCADE,
    return_path        text NOT NULL DEFAULT '/',
    redirect_uri       text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    used_at            timestamptz,
    CHECK (
        (mode = 'login' AND initiating_user_id IS NULL)
        OR (mode = 'link' AND initiating_user_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_oidc_auth_transactions_expiry
    ON oidc_auth_transactions(expires_at, used_at);
