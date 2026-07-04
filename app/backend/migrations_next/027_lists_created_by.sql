-- Lists provenance: record which DiscVault account created each loan and each
-- wishlist entry, and speed up the inbound "borrowed by me" lookup.
--
-- `user_id` on both tables already identifies the owner/lender (loans) and the
-- list owner (wishlist). `created_by_user_id` is added as an explicit provenance
-- column so we can attribute who performed the action even when that differs
-- from the owner (e.g. an admin acting on behalf of a member), and so wishlist
-- entries can later be shared instance-wide with a known author. Both columns
-- are backfilled from the existing `user_id` for pre-existing rows.

-- Loans: who created the loan action.
ALTER TABLE loans
    ADD COLUMN IF NOT EXISTS created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL;

UPDATE loans
    SET created_by_user_id = user_id
    WHERE created_by_user_id IS NULL;

-- Wishlist: who placed the item on the wishlist (basis for instance-wide sharing).
ALTER TABLE wishlist_items
    ADD COLUMN IF NOT EXISTS created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL;

UPDATE wishlist_items
    SET created_by_user_id = user_id
    WHERE created_by_user_id IS NULL;

-- Inbound loan lookups: films lent TO a linked DiscVault account.
CREATE INDEX IF NOT EXISTS idx_loans_borrower_user
    ON loans(borrower_user_id, returned_at, loaned_at DESC);
