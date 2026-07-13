-- DiscVault Next: optional provider metadata for wishlist shop price checks.
--
-- Enables provider-backed pricing (e.g. Keepa/PriceAPI plugins) per shop
-- source without requiring provider credentials in the client.

ALTER TABLE wishlist_item_shops
    ADD COLUMN IF NOT EXISTS provider_plugin_id text,
    ADD COLUMN IF NOT EXISTS provider_product_ref text,
    ADD COLUMN IF NOT EXISTS provider_last_status text,
    ADD COLUMN IF NOT EXISTS provider_last_error text;

ALTER TABLE wishlist_item_shop_prices
    ADD COLUMN IF NOT EXISTS provider_id text,
    ADD COLUMN IF NOT EXISTS source_detail text,
    ADD COLUMN IF NOT EXISTS confidence numeric(5, 2);

CREATE INDEX IF NOT EXISTS idx_wishlist_item_shops_provider_plugin
    ON wishlist_item_shops (provider_plugin_id)
    WHERE provider_plugin_id IS NOT NULL;

