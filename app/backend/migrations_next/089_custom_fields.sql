-- DiscVault Next: fields the owner of an instance defines at runtime.
--
-- Wire form is sync-contract.md 4e (proposed, v1.40); storage rationale is
-- projects/discvault/specs/custom-fields.md in App-Guidance. This header carries
-- only what a reader of the schema needs to not undo it.
--
-- WHY NOT movies.metadata
--
-- It is the obvious carrier and it is closed to this. The `||` merge that every
-- metadata write uses is shallow and CANNOT DELETE A KEY, so a value written
-- once could never be cleared over sync; the contract states that twice. And
-- `clears`, the escape hatch for emptying a field, is a closed compile-time list
-- that refuses an unknown field name with a 400 -- which a runtime-defined field
-- is, by definition, always. Rows do not have either problem.
--
-- WHY TYPED COLUMNS AND NOT ONE jsonb VALUE
--
-- Sorting and range questions are half the reason anyone asks for a number or a
-- date field: purchase price over 20, bought after 2024, newest first. Out of
-- jsonb every one of those is a cast, in every query, forever. The CHECK below
-- enforces row-locally that exactly one column is filled; that the filled column
-- matches the field's declared type needs a join and is validated in
-- next_custom_fields.py.
--
-- WHY archived_at AND NOT A DELETE
--
-- The reason is outside this database. Saved smart filters live only in each
-- browser's localStorage -- no table, no sync entity, no server route -- so an
-- operator can never repair them. And the filter normaliser rebuilds its object
-- on every read, supplying a default for anything missing: a clause naming a
-- field that no longer exists does not error, it silently becomes "any" and
-- shows the user their ENTIRE LIBRARY as though all of it matched the filter.
-- The ON DELETE RESTRICT below is the tooth behind that -- the database refuses
-- a hard delete even if a future route asks for one.

CREATE TABLE IF NOT EXISTS custom_field_definitions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key         text NOT NULL,
    name        text NOT NULL,
    field_type  text NOT NULL,
    options     jsonb NOT NULL DEFAULT '[]'::jsonb,
    sort_order  integer NOT NULL DEFAULT 0,
    archived_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- Lower-case slug. It is what a stored filter names and what the wire
    -- carries, so it has to survive a rename of the human-facing `name`.
    CONSTRAINT custom_field_definitions_key_shape CHECK (key ~ '^[a-z][a-z0-9_]{0,47}$'),
    CONSTRAINT custom_field_definitions_key_unique UNIQUE (key),
    -- Closed on input: this is the side we control, and a person picks from a
    -- list. Open at the edge is a client-side rule (contract 4e.4), not a
    -- storage one -- an instance never receives a type it did not define.
    CONSTRAINT custom_field_definitions_type CHECK (
        field_type IN ('text', 'number', 'date', 'boolean', 'select'))
);

CREATE INDEX IF NOT EXISTS idx_custom_field_definitions_live
    ON custom_field_definitions (sort_order, key)
    WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS movie_custom_field_values (
    movie_id      uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    -- RESTRICT, not CASCADE: a definition is archived, never removed, and the
    -- values outlive the archiving. See the header.
    field_id      uuid NOT NULL REFERENCES custom_field_definitions(id) ON DELETE RESTRICT,
    value_text    text,
    value_number  numeric,
    value_date    date,
    value_boolean boolean,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (movie_id, field_id),
    -- Exactly one. Not "at most one": an empty value is the absence of the row,
    -- the same presence-is-the-fact model watchlist_items, movie_tags and
    -- movie_user_ratings already use. A row of four NULLs would be a value that
    -- claims to exist and says nothing.
    CONSTRAINT movie_custom_field_values_one_value CHECK (
        num_nonnulls(value_text, value_number, value_date, value_boolean) = 1)
);

-- The wire entity is one change per FILM carrying that film's whole value set
-- (contract 4e.2), so the read is "give me every value for these movies" and the
-- movie is the leading column -- which the primary key already provides. This
-- index serves the other direction: "is this field still in use", which is what
-- the archive confirmation and any future option removal have to ask.
CREATE INDEX IF NOT EXISTS idx_movie_custom_field_values_field
    ON movie_custom_field_values (field_id);
