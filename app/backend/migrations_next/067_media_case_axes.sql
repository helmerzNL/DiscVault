-- DiscVault Next: split movie_technical_specs.packaging into two axes plus
-- finish tags.
--
-- 053 widened packaging from a scalar to a jsonb list so a release could be
-- both a steelbook AND carry a slipcover. That fixed the cardinality but not
-- the vocabulary: one flat list still mixes the case itself (steelbook,
-- digipak) with the cardboard around it (slipcover, slipcase) with box-set-ness
-- (box), so nothing prevents a record claiming both `steelbook` and `amaray`,
-- and the terms collectors use - FuturePak, hardbox, o-card, fullslip,
-- one-click - have nowhere to live. Surface finish (spot UV, lenticular, foil,
-- embossing) was never representable at all.
--
--   carrier_type     - what the disc sits in. One per title.
--   outer_packaging  - cardboard around it. Several may apply; a boutique
--                      release can carry an o-card inside a slipcase.
--   finishes         - surface treatment, orthogonal to both axes.
--   steelbook_format - Scanavo generation (g1/g2/g3), metal carriers only.
--
-- `packaging` is deliberately NOT dropped. It stays as a derived mirror of the
-- two axes so backup/restore, import, the MCP server and the MovieVault
-- distribution-4 ingest - which still sends the flat list - keep working.
-- Retiring it is a separate, later change. See app/backend/next_packaging.py
-- for the vocabulary and the mapping in both directions.
--
-- The backfill matches case-insensitively on purpose: 053 preserved manually
-- entered scalars verbatim ("Steelbook") and the movievault_26 plugin emits
-- TitleCase, so a case-sensitive match would silently drop exactly the rows
-- that were already rendering untranslated.

ALTER TABLE movie_technical_specs
    ADD COLUMN IF NOT EXISTS carrier_type     text,
    ADD COLUMN IF NOT EXISTS steelbook_format text,
    ADD COLUMN IF NOT EXISTS outer_packaging  jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS finishes         jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Carrier: the first recognized value wins. A well-formed row names at most
-- one, and ordinality keeps the choice deterministic for rows that do not.
WITH mapped AS (
    SELECT
        t.movie_id,
        (ARRAY_AGG(m.carrier ORDER BY e.ord))[1] AS carrier
    FROM movie_technical_specs t
    CROSS JOIN LATERAL jsonb_array_elements_text(t.packaging)
        WITH ORDINALITY AS e(value, ord)
    JOIN (VALUES
        ('keep_case', 'keep_case'),
        ('amaray',    'keep_case'),
        ('steelbook', 'steelbook'),
        ('digipak',   'digipak'),
        ('digibook',  'digibook'),
        ('mediabook', 'mediabook')
    ) AS m(legacy, carrier)
      ON m.legacy = lower(btrim(e.value))
    WHERE jsonb_typeof(t.packaging) = 'array'
    GROUP BY t.movie_id
)
UPDATE movie_technical_specs t
SET carrier_type = mapped.carrier
FROM mapped
WHERE mapped.movie_id = t.movie_id
  AND t.carrier_type IS NULL;

-- Outer packaging: every recognized value is kept, de-duplicated, in the order
-- it appeared.
WITH mapped AS (
    SELECT
        t.movie_id,
        jsonb_agg(DISTINCT m.outer_value) AS outer_packaging
    FROM movie_technical_specs t
    CROSS JOIN LATERAL jsonb_array_elements_text(t.packaging) AS e(value)
    JOIN (VALUES
        ('slipcover', 'slipcover'),
        ('slipcase',  'slipcase'),
        ('box',       'rigid_box')
    ) AS m(legacy, outer_value)
      ON m.legacy = lower(btrim(e.value))
    WHERE jsonb_typeof(t.packaging) = 'array'
    GROUP BY t.movie_id
)
UPDATE movie_technical_specs t
SET outer_packaging = mapped.outer_packaging
FROM mapped
WHERE mapped.movie_id = t.movie_id
  AND t.outer_packaging = '[]'::jsonb;

-- Rows whose packaging held only unrecognized text keep a NULL carrier and
-- empty arrays rather than being forced to `other`: "we do not know" and "it is
-- something else" are different answers, and the raw value is still readable in
-- the retained `packaging` column.

CREATE INDEX IF NOT EXISTS movie_technical_specs_carrier_type_idx
    ON movie_technical_specs (carrier_type)
    WHERE carrier_type IS NOT NULL;
