-- DiscVault Next: price & deal alert fields on wishlist items.
--
-- Users can set a target price and optionally a shop URL on any wishlist entry.
-- A background sweep job periodically fetches the shop URL, extracts the current
-- price (via schema.org JSON-LD, Open Graph, or regex heuristics), and fires a
-- notification when the price drops at or below the target.
--
-- The `price_alerts` notification preference key gates delivery; users can opt
-- out per-preference without touching the alert settings on each item.
--
-- Design notes:
--   • alert_enabled=false by default — opt-in only.
--   • last_alerted_at is compared against a server-side 24 h cooldown so the
--     same drop does not flood the inbox.
--   • price_currency stores the ISO-4217 code extracted alongside the price so
--     cross-currency comparisons can be detected and surfaced in the UI.
--   • Columns are nullable where appropriate: a user can enable an alert with
--     just a target_price (and rely on a plugin providing the current price via
--     the price_check capability) even without a price_url.

ALTER TABLE wishlist_items
    ADD COLUMN IF NOT EXISTS alert_enabled        boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS target_price         numeric(10, 2),
    ADD COLUMN IF NOT EXISTS price_url            text,
    ADD COLUMN IF NOT EXISTS last_seen_price      numeric(10, 2),
    ADD COLUMN IF NOT EXISTS price_currency       text NOT NULL DEFAULT 'EUR',
    ADD COLUMN IF NOT EXISTS last_price_checked_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_alerted_at      timestamptz;

-- Partial index for the sweep query: only active alerts that are due for a check.
CREATE INDEX IF NOT EXISTS idx_wishlist_price_alerts_sweep
    ON wishlist_items (last_price_checked_at ASC NULLS FIRST)
    WHERE alert_enabled = true;
