-- DiscVault Next: accept distribution-6 -- the contract that carries discs.
--
-- Two halves, deliberately in one migration because they are one act: the
-- consumer-side readiness for a contract version, done before the origin can
-- ever serve it (the deployment-order rule in
-- adding-a-distribution-contract-version.md).
--
-- First: a column for the per-disc breakdown the v6 feed publishes. jsonb and
-- verbatim, exactly like `seasons` (072) and `finishes` (071) on this table:
-- movievault_v2_releases is a mirror of what MovieVault published, not a
-- modelled entity. The modelled shape lives in `movie_discs` (075), and
-- enrichment reads from this mirror to build it -- in a later change, never in
-- this one; a column that lands together with its consumer cannot be deployed
-- ahead of the feed.
--
-- NULL means this generation's record never carried the key (a pre-v6
-- contract, or a release nobody has broken down -- the producer omits the key
-- then). `[]` would be a statement; the parser never fabricates one.

ALTER TABLE movievault_v2_releases
    ADD COLUMN IF NOT EXISTS discs jsonb;

-- Second: admit distribution-6 to the sync-state allow-list. 073 records why
-- this cannot be forgotten: raising the plugin manifest without widening this
-- CHECK made a *successful* bootstrap fail on its very last statement, rolling
-- the whole download back while feed, parser and schema were all correct.
-- The regression test iterates SUPPORTED_CONTRACTS, so listing the contract in
-- code without this constraint is caught before it can reach an instance.

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
            'distribution-5',
            'distribution-6'
        )
    );
