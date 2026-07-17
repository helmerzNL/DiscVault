-- DiscVault Next: MovieVault distribution-3 canonical metadata.

ALTER TABLE movievault_v2_sync_state
    DROP CONSTRAINT IF EXISTS movievault_v2_sync_state_contract_version_check;

ALTER TABLE movievault_v2_sync_state
    ADD CONSTRAINT movievault_v2_sync_state_contract_version_check
    CHECK (
        contract_version IS NULL
        OR contract_version IN ('distribution-2', 'distribution-3')
    );

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS studio text,
    ADD COLUMN IF NOT EXISTS distributor text,
    ADD COLUMN IF NOT EXISTS runtime_minutes integer;

ALTER TABLE movievault_v2_releases
    DROP CONSTRAINT IF EXISTS movievault_v2_releases_runtime_minutes_check;

ALTER TABLE movievault_v2_releases
    ADD CONSTRAINT movievault_v2_releases_runtime_minutes_check
    CHECK (runtime_minutes IS NULL OR runtime_minutes BETWEEN 1 AND 10000);

ALTER TABLE movievault_v2_box_set_members
    ADD COLUMN IF NOT EXISTS studio text,
    ADD COLUMN IF NOT EXISTS distributor text,
    ADD COLUMN IF NOT EXISTS runtime_minutes integer;

ALTER TABLE movievault_v2_box_set_members
    DROP CONSTRAINT IF EXISTS movievault_v2_box_set_members_runtime_minutes_check;

ALTER TABLE movievault_v2_box_set_members
    ADD CONSTRAINT movievault_v2_box_set_members_runtime_minutes_check
    CHECK (runtime_minutes IS NULL OR runtime_minutes BETWEEN 1 AND 10000);
