-- DiscVault Next: admit distribution-5 to the sync-state contract allow-list.
--
-- `movievault_v2_sync_state.contract_version` carries a CHECK that enumerates
-- the contracts this repository has seen. 035 created it at distribution-2,
-- 038 widened it to -3 and 039 to -4. Raising the plugin manifest to
-- distribution-5 without widening it here made the *successful* bootstrap fail
-- on its very last statement: every record was downloaded, parsed and written,
-- and then the row recording that success was rejected, rolling the whole
-- transaction back. The sync could therefore never complete, while the feed,
-- the parser and the schema were all correct.
--
-- The failure was invisible for the same reason MovieVault's was (#211): the
-- CheckViolation aborted the transaction, and the `SELECT pg_advisory_unlock`
-- in run_sync's `finally` then raised "current transaction is aborted" on top
-- of it, so only the cleanup error reached the job record.

ALTER TABLE movievault_v2_sync_state
    DROP CONSTRAINT IF EXISTS movievault_v2_sync_state_contract_version_check;

ALTER TABLE movievault_v2_sync_state
    ADD CONSTRAINT movievault_v2_sync_state_contract_version_check
    CHECK (
        contract_version IS NULL
        OR contract_version IN (
            'distribution-2',
            'distribution-3',
            'distribution-4',
            'distribution-5'
        )
    );
