-- DiscVault Next: multi-shop price tracking for wishlist alerts.
--
-- Wishlist items can now track up to 10 shop sources, each with its own URL and
-- latest observed price. Every successful shop check appends a history row so
-- price trends can be visualized later.

CREATE TABLE IF NOT EXISTS wishlist_item_shops (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wishlist_item_id      uuid NOT NULL REFERENCES wishlist_items(id) ON DELETE CASCADE,
    shop_name             text NOT NULL,
    price_url             text NOT NULL,
    price_currency        text NOT NULL DEFAULT 'EUR',
    last_seen_price       numeric(10, 2),
    last_price_checked_at timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wishlist_item_shops_item
    ON wishlist_item_shops (wishlist_item_id, created_at);

CREATE INDEX IF NOT EXISTS idx_wishlist_item_shops_user
    ON wishlist_item_shops (user_id, wishlist_item_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wishlist_item_shops_item_url
    ON wishlist_item_shops (wishlist_item_id, price_url);

CREATE TABLE IF NOT EXISTS wishlist_item_shop_prices (
    id                    bigserial PRIMARY KEY,
    user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wishlist_item_id      uuid NOT NULL REFERENCES wishlist_items(id) ON DELETE CASCADE,
    wishlist_item_shop_id uuid NOT NULL REFERENCES wishlist_item_shops(id) ON DELETE CASCADE,
    observed_price        numeric(10, 2) NOT NULL,
    price_currency        text NOT NULL DEFAULT 'EUR',
    observed_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wishlist_item_shop_prices_shop_time
    ON wishlist_item_shop_prices (wishlist_item_shop_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_wishlist_item_shop_prices_item_time
    ON wishlist_item_shop_prices (wishlist_item_id, observed_at DESC);
