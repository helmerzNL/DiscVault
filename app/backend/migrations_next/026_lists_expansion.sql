-- Lists expansion: wishlist, per-user tags, and outbound loan tracker.
--
-- These four personal features ride the generic per-user sync stream introduced
-- in migration 025 (user_sync_state / user_sync_changes). No stream schema change
-- is needed; each new entity type simply appends rows with its own entity_type.
--
-- Table names are chosen deliberately to satisfy the readiness-report probes in
-- next_app.py (lending_center probes for "loans"; tags_smart_rules probes for
-- "tags"/"movie_tags"), so those features auto-flip from "planned" to "ready"
-- once this migration runs.
--
-- Owned-movie-linked tables carry a denormalized snapshot jsonb (title/year/
-- format/poster/barcode) so entries survive movie deletion, mirroring the
-- watchlist_items resilience pattern (migration 015). Wishlist is snapshot-native
-- by design: entries describe discs the user does NOT yet own.

-- Wishlist: metadata-only "want to acquire" entries (not tied to an owned movie row).
CREATE TABLE IF NOT EXISTS wishlist_items (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title             text NOT NULL,
    year              int,
    barcode           text,
    format            text,
    movievault_id     text,
    poster_url        text,
    note              text,
    snapshot          jsonb NOT NULL DEFAULT '{}'::jsonb,
    added_at          timestamptz NOT NULL DEFAULT now(),
    acquired_at       timestamptz,
    acquired_movie_id uuid REFERENCES movies(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_wishlist_items_user_added
    ON wishlist_items(user_id, added_at DESC);

-- Per-user private tags.
CREATE TABLE IF NOT EXISTS tags (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       text NOT NULL,
    slug       text NOT NULL,
    color      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, slug)
);

-- Tag assignments onto owned movies.
CREATE TABLE IF NOT EXISTS movie_tags (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag_id     uuid NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    movie_id   uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, tag_id, movie_id)
);

CREATE INDEX IF NOT EXISTS idx_movie_tags_user_movie
    ON movie_tags(user_id, movie_id);

CREATE INDEX IF NOT EXISTS idx_movie_tags_user_tag
    ON movie_tags(user_id, tag_id);

-- Outbound loan tracker: an owned disc lent to a borrower (freeform name or a
-- linked DiscVault account).
CREATE TABLE IF NOT EXISTS loans (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_id         uuid REFERENCES movies(id) ON DELETE SET NULL,
    snapshot         jsonb NOT NULL DEFAULT '{}'::jsonb,
    borrower_name    text,
    borrower_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    loaned_at        timestamptz NOT NULL DEFAULT now(),
    due_at           timestamptz,
    returned_at      timestamptz,
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT loans_borrower_present
        CHECK (borrower_name IS NOT NULL OR borrower_user_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_loans_user_active
    ON loans(user_id, returned_at, loaned_at DESC);
