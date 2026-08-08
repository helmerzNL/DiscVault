-- DiscVault Next: record every code a pressing is sold under, not just the one
-- that was scanned.
--
-- `movies.barcode` is one `text` column with a UNIQUE constraint, and that is
-- deliberate: a scan has to resolve to exactly one film, and a list cannot make
-- that promise. But the same column is also the only place a product code can
-- be recorded at all, which forces one shape onto two jobs.
--
-- A single pressing routinely carries several codes. An EAN-13 for Europe and a
-- UPC-A for North America on the same disc; an Amazon ASIN that appears nowhere
-- in the barcode; a distributor catalogue number printed on the spine. Losing
-- them is not only a gap in the local record -- MovieVault models all five
-- (`content.release_identifiers`, keyed `(identifier_type, identifier_value)`),
-- and its `eans` correction field is a *complete replacement list*. An instance
-- that holds one value can therefore never contribute that field without
-- deleting the others, which is why `eans` has been withheld until now.
--
-- So resolution and description are split. `movies.barcode` keeps its UNIQUE
-- constraint and still decides what a scan matches; this table records the rest.
-- The scanned barcode is backfilled into it so the two never disagree on day
-- one, and its type is derived from the digit count -- 12 is a UPC, 13 in the
-- 978/979 range is an ISBN, 13 with a leading zero is a zero-padded UPC-A, and
-- everything else in the scheme is an EAN.
--
-- The vocabulary is MovieVault's, copied rather than invented: a sixth type
-- here would be one DiscVault could record and never contribute.

CREATE TABLE IF NOT EXISTS movie_product_identifiers (
    movie_id uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    identifier_type text NOT NULL
        CHECK (identifier_type IN ('ean', 'upc', 'isbn', 'asin', 'catalog_number')),
    identifier_value text NOT NULL
        CHECK (identifier_value <> '' AND length(identifier_value) <= 120),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (movie_id, identifier_type, identifier_value)
);

-- "Which film is this code?" is the read that matters, and it arrives without a
-- movie id -- so the index has to lead on the value rather than on `movie_id`,
-- which the primary key already covers.
CREATE INDEX IF NOT EXISTS idx_movie_product_identifiers_value
    ON movie_product_identifiers (identifier_type, identifier_value);

-- One scan, one film -- the same promise `movies.barcode` makes, extended to
-- the codes a scanner can actually produce. Deliberately partial: an ASIN or a
-- catalogue number is descriptive metadata that a person types, and two
-- editions of a box set legitimately share a catalogue number. Constraining
-- those would refuse true data to protect a lookup that never consults them.
CREATE UNIQUE INDEX IF NOT EXISTS uq_movie_product_identifiers_scannable
    ON movie_product_identifiers (identifier_type, identifier_value)
    WHERE identifier_type IN ('ean', 'upc', 'isbn');

-- Backfill, digits only and check-digit-validated, so a malformed legacy
-- barcode stays where it is rather than becoming a typed identifier that claims
-- to be verified. `ON CONFLICT DO NOTHING` covers the case where two movies
-- somehow carry the same code: `movies.barcode` is UNIQUE so it should not
-- happen, but a partial unique index failing a migration would be a poor way to
-- find out.
INSERT INTO movie_product_identifiers (movie_id, identifier_type, identifier_value)
SELECT
    m.id,
    CASE
        WHEN length(d.digits) = 12 THEN 'upc'
        WHEN length(d.digits) = 13 AND left(d.digits, 3) IN ('978', '979') THEN 'isbn'
        WHEN length(d.digits) = 13 AND left(d.digits, 1) = '0' THEN 'upc'
        ELSE 'ean'
    END,
    d.digits
FROM movies m
CROSS JOIN LATERAL (
    SELECT regexp_replace(coalesce(m.barcode, ''), '[^0-9]', '', 'g') AS digits
) d
WHERE length(d.digits) IN (8, 12, 13, 14)
  AND (
      -- GS1 mod-10: weight 3 and 1 alternating from the right, excluding the
      -- check digit itself, then the distance to the next multiple of ten.
      (10 - (
          SELECT sum(
              substring(d.digits FROM length(d.digits) - g.i FOR 1)::int
              * CASE WHEN g.i % 2 = 1 THEN 3 ELSE 1 END
          )
          FROM generate_series(1, length(d.digits) - 1) AS g(i)
      ) % 10) % 10
  ) = right(d.digits, 1)::int
ON CONFLICT DO NOTHING;
