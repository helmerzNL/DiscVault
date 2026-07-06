-- DiscVault Next: physical storage locations (Locaties)
-- A location is a cabinet/rack/box/slot (Kast/rek/doos) where films are stored.
-- Self-referencing tree, max 4 levels deep. Films and containers can be tagged
-- with a single location.

CREATE TABLE IF NOT EXISTS locations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id   text UNIQUE NOT NULL,
    parent_id   uuid REFERENCES locations(id) ON DELETE CASCADE,
    name        text NOT NULL,
    description text,
    qr_token    text UNIQUE NOT NULL,
    sort_order  integer NOT NULL DEFAULT 0,
    metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_id, sort_order);

-- 4-level depth guard. Enforced on INSERT and on parent_id UPDATE. Considers the
-- height of the moved node's own subtree so a reparent cannot push any descendant
-- past level 4, and rejects cycles (moving a node under one of its descendants).
CREATE OR REPLACE FUNCTION locations_enforce_depth() RETURNS trigger AS $$
DECLARE
    parent_depth   integer := 0;
    subtree_height integer;
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        IF NEW.parent_id = NEW.id THEN
            RAISE EXCEPTION 'location cannot be its own parent';
        END IF;

        WITH RECURSIVE ancestors AS (
            SELECT id, parent_id, 1 AS depth
            FROM locations WHERE id = NEW.parent_id
            UNION ALL
            SELECT l.id, l.parent_id, a.depth + 1
            FROM locations l JOIN ancestors a ON l.id = a.parent_id
        )
        SELECT max(depth) INTO parent_depth FROM ancestors;

        IF parent_depth IS NULL THEN
            parent_depth := 0;
        END IF;

        IF EXISTS (
            WITH RECURSIVE descendants AS (
                SELECT id FROM locations WHERE id = NEW.id
                UNION ALL
                SELECT l.id FROM locations l JOIN descendants d ON l.parent_id = d.id
            )
            SELECT 1 FROM descendants WHERE id = NEW.parent_id
        ) THEN
            RAISE EXCEPTION 'location cannot be moved under its own descendant';
        END IF;
    END IF;

    WITH RECURSIVE subtree AS (
        SELECT id, parent_id, 1 AS lvl
        FROM locations WHERE id = NEW.id
        UNION ALL
        SELECT l.id, l.parent_id, s.lvl + 1
        FROM locations l JOIN subtree s ON l.parent_id = s.id
    )
    SELECT max(lvl) INTO subtree_height FROM subtree;

    IF subtree_height IS NULL THEN
        subtree_height := 1;
    END IF;

    IF (parent_depth + subtree_height) > 4 THEN
        RAISE EXCEPTION 'location depth exceeds maximum of 4 levels';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_locations_enforce_depth ON locations;
CREATE TRIGGER trg_locations_enforce_depth
    BEFORE INSERT OR UPDATE ON locations
    FOR EACH ROW EXECUTE FUNCTION locations_enforce_depth();

ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS location_id uuid REFERENCES locations(id) ON DELETE SET NULL;
ALTER TABLE containers
    ADD COLUMN IF NOT EXISTS location_id uuid REFERENCES locations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_movies_location     ON movies(location_id);
CREATE INDEX IF NOT EXISTS idx_containers_location ON containers(location_id);
