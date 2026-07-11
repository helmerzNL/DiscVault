-- DiscVault Next: deterministic selector profiles for wishlist shop pricing.
--
-- Adds optional selector metadata on each wishlist shop source so extraction can
-- prefer a stable page fragment (CSS/regex) before generic schema.org/OG/regex
-- fallback. Also stores extraction_source in the history table for diagnostics.

ALTER TABLE wishlist_item_shops
    ADD COLUMN IF NOT EXISTS selector_type text,
    ADD COLUMN IF NOT EXISTS selector_value text,
    ADD COLUMN IF NOT EXISTS selector_options jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE wishlist_item_shop_prices
    ADD COLUMN IF NOT EXISTS extraction_source text;

CREATE INDEX IF NOT EXISTS idx_wishlist_item_shops_selector_type
    ON wishlist_item_shops (selector_type)
    WHERE selector_type IS NOT NULL;
