-- Borrow requests: a group member asks a movie's owner to lend them a disc.
--
-- Within a MemberGroup a user can see discs owned by OTHER members. They cannot
-- lend someone else's disc, so instead they raise a `loan_request` with a
-- desired borrow-from date and return-by date. The owner approves or declines.
-- On approval we materialize an outbound `loans` row (borrower = requester,
-- due_at = return_by) and link it back via `resulting_loan_id`.
--
-- Like `loans` (migration 026) each request carries a denormalized `snapshot`
-- jsonb (title/year/format/poster/barcode) so the request survives movie
-- deletion. The readiness-report probe in next_app.py looks for the
-- "loan_requests" table to flip the borrow-request feature from "planned" to
-- "ready" once this migration runs.

CREATE TABLE IF NOT EXISTS loan_requests (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    movie_id           uuid REFERENCES movies(id) ON DELETE SET NULL,
    requester_user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    owner_user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    snapshot           jsonb NOT NULL DEFAULT '{}'::jsonb,
    borrow_from        date NOT NULL,
    return_by          date NOT NULL,
    status             text NOT NULL DEFAULT 'pending',
    note               text,
    resulting_loan_id  uuid REFERENCES loans(id) ON DELETE SET NULL,
    decided_at         timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT loan_requests_status_valid
        CHECK (status IN ('pending', 'approved', 'declined', 'cancelled')),
    CONSTRAINT loan_requests_dates_ordered
        CHECK (return_by >= borrow_from)
);

-- Owner inbox: pending requests to decide, newest first.
CREATE INDEX IF NOT EXISTS idx_loan_requests_owner_status
    ON loan_requests(owner_user_id, status, created_at DESC);

-- Requester outbox: own requests and their status, newest first.
CREATE INDEX IF NOT EXISTS idx_loan_requests_requester_status
    ON loan_requests(requester_user_id, status, created_at DESC);

-- At most one pending request per (movie, requester) so a member cannot spam
-- the owner with duplicate open requests for the same disc.
CREATE UNIQUE INDEX IF NOT EXISTS uq_loan_requests_pending_movie_requester
    ON loan_requests(movie_id, requester_user_id)
    WHERE status = 'pending' AND movie_id IS NOT NULL;
