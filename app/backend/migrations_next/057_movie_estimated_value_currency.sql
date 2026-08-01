-- DiscVault Next: the currency an estimated value is expressed in.
--
-- `movies.estimated_value` (051) has been a bare number since it shipped, which
-- silently assumed every user thinks in one currency. A collection bought across
-- borders does not, and the amounts are meaningless without the unit.
--
-- Nullable on purpose, unlike the wishlist family's
-- `price_currency text NOT NULL DEFAULT 'EUR'` (031, 032). Those rows are always
-- created by a flow that knows the currency. Here there are already rows in the
-- wild carrying a value and no currency, and defaulting them to EUR would be a
-- guess that misstates money. NULL means "not recorded": the UI renders such a
-- value as a bare number, exactly as it does today, and offers the picker so the
-- user can say what it actually is.
--
-- No CHECK constraint on the value set. The picker offers the seven currencies
-- the price-display converter can actually fetch rates for
-- (PRICE_DISPLAY_SUPPORTED_CURRENCIES), but the column stays open so an existing
-- row is never rejected on read, and application code validates the shape.

ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS estimated_value_currency text;

COMMENT ON COLUMN movies.estimated_value_currency IS
    'ISO 4217 code the estimated_value is expressed in. NULL means not recorded; do not assume a default.';
