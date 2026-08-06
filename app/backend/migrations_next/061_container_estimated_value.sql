-- DiscVault Next: a user-entered estimated value for a box set as a whole.
--
-- Mirrors movies.estimated_value / estimated_value_currency (051, 057). A box set
-- is bought as one product for one price, so pricing its member discs individually
-- either double-counts the shelf or leaves the set unpriced. The set carries the
-- money; a movie that belongs to a box set has its own value suppressed while the
-- membership lasts (see next_collection_value.movie_value_locked_sql).
--
-- Only meaningful for container_type = 'box_set'. Deliberately no CHECK tying the
-- column to the type: box_set <-> vault conversion is allowed (container_type_change)
-- and must never fail on a row that once carried a value. Application code refuses
-- to *write* the field for a vault or a collection.
--
-- Currency nullable for the same reason as 057: NULL means "not recorded", and
-- defaulting it to EUR would be a guess that misstates money.

ALTER TABLE containers
    ADD COLUMN IF NOT EXISTS estimated_value numeric(10, 2);

ALTER TABLE containers
    ADD COLUMN IF NOT EXISTS estimated_value_currency text;

COMMENT ON COLUMN containers.estimated_value IS
    'User-entered value of the container as a whole. Only written for container_type=box_set; local-only, never populated by a metadata plugin.';

COMMENT ON COLUMN containers.estimated_value_currency IS
    'ISO 4217 code the estimated_value is expressed in. NULL means not recorded; do not assume a default.';
