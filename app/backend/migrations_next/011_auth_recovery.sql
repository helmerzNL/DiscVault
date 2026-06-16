CREATE TABLE IF NOT EXISTS recovery_codes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash   text NOT NULL UNIQUE,
    label       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    used_at     timestamptz,
    expires_at  timestamptz
);

CREATE INDEX IF NOT EXISTS idx_recovery_codes_user
    ON recovery_codes(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_recovery_codes_active
    ON recovery_codes(user_id)
    WHERE used_at IS NULL;
