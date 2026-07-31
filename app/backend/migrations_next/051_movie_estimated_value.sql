-- DiscVault Next: a user-entered estimated value per movie, distinct from
-- purchase_price (what was paid). Local-only, never populated by a
-- metadata plugin (see METADATA_LOCAL_ONLY_FIELDS in next_metadata.py) -
-- it syncs through the same personal-library sync surface (bootstrap,
-- delta, mutations) that already carries purchase_price/purchase_date to
-- DiscVaultApp and DiscVault-AndroidApp.

ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS estimated_value numeric(10, 2);
