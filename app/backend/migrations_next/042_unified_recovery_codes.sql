-- Use one recovery-code set for passkey account recovery and Legacy 2FA recovery.
ALTER TABLE recovery_codes
    ADD COLUMN IF NOT EXISTS legacy_code_hash text;

INSERT INTO recovery_codes (
    user_id,
    code_hash,
    legacy_code_hash,
    label,
    created_at,
    used_at
)
SELECT
    legacy.user_id,
    'legacy:' || legacy.id::text,
    legacy.code_hash,
    'Recovery code',
    legacy.created_at,
    legacy.used_at
FROM legacy_mfa_recovery_codes AS legacy
WHERE NOT EXISTS (
    SELECT 1
    FROM recovery_codes AS recovery
    WHERE recovery.user_id = legacy.user_id
      AND recovery.legacy_code_hash = legacy.code_hash
);

CREATE INDEX IF NOT EXISTS idx_recovery_codes_active_legacy_hash
    ON recovery_codes(user_id)
    WHERE used_at IS NULL AND legacy_code_hash IS NOT NULL;
