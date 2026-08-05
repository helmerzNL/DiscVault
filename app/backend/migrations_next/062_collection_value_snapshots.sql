-- DiscVault Next: daily snapshots of what a collection is worth.
--
-- The per-movie and per-box-set estimated values answer "what is this worth now".
-- A collection's worth over time cannot be reconstructed from them afterwards:
-- the amounts are overwritten in place and membership moves around. So the total
-- is recomputed on every price-affecting write and written here.
--
-- One row per user, scope and calendar day: the last write of a day wins, which
-- keeps the series one point per day instead of one point per edit. Scopes are
-- the whole collection ('total', scope_id NULL) plus each vault and each
-- collection the user can see, so a chart can be filtered later.
--
-- total_value is expressed in base_currency, converted with the rates the price
-- display already uses (price_display_exchange_rates). by_currency keeps the raw
-- per-currency sums untouched, including the "" bucket for amounts stored without
-- a currency, so a later reader can re-express the point without inheriting the
-- exchange rate that happened to apply on the day it was captured.

CREATE TABLE IF NOT EXISTS collection_value_snapshots (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope_type     text NOT NULL CHECK (scope_type IN ('total', 'vault', 'collection')),
    scope_id       uuid REFERENCES containers(id) ON DELETE CASCADE,
    captured_on    date NOT NULL,
    base_currency  text NOT NULL,
    total_value    numeric(14, 2) NOT NULL DEFAULT 0,
    by_currency    jsonb NOT NULL DEFAULT '{}'::jsonb,
    priced_count   integer NOT NULL DEFAULT 0,
    unpriced_count integer NOT NULL DEFAULT 0,
    captured_at    timestamptz NOT NULL DEFAULT now()
);

-- scope_id is NULL for the 'total' scope, and NULL never equals NULL in a unique
-- index, so the two scope shapes need two partial indexes to get one row per day.
CREATE UNIQUE INDEX IF NOT EXISTS uq_collection_value_snapshots_total
    ON collection_value_snapshots(user_id, captured_on)
    WHERE scope_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_collection_value_snapshots_scope
    ON collection_value_snapshots(user_id, scope_type, scope_id, captured_on)
    WHERE scope_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_collection_value_snapshots_series
    ON collection_value_snapshots(user_id, scope_type, captured_on);
