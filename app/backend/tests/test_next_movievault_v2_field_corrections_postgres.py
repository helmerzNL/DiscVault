"""Field-correction target resolution and diffing, against a real PostgreSQL.

What matters here cannot be asserted against a fake cursor: which of two ways of
naming a record wins, that a barcode resolving to more than one entity is not a
target at all, and that `expected` comes from the mirror rather than from the
row being corrected. All three are about what the database actually returns.

Point `DATABASE_URL` at a database with every migration applied; without it
these skip.
"""

import hashlib
import os
import sys
import unittest
import uuid


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ModuleNotFoundError:
    psycopg = None
    dict_row = None
    Jsonb = None

from app.backend import next_movievault_v2_field_corrections as corrections


DATABASE_URL = os.environ.get("DATABASE_URL")

PREFIX = "field-correction-test"
BARCODE = "4006381333931"
OTHER_BARCODE = "0717951008572"


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "PostgreSQL test database is not configured")
class FieldCorrectionResolutionPostgresTests(unittest.TestCase):
    def connect(self):
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)

    def setUp(self):
        self.generation = uuid.uuid4()
        self.release_id = uuid.uuid4()
        self.box_set_id = uuid.uuid4()
        self.conn = self.connect()
        self.addCleanup(self.conn.close)
        self.addCleanup(self._cleanup)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_sync_state (plugin_id, origin, active_generation)
                VALUES ('movievault_v2', 'https://example.test', %s)
                ON CONFLICT (plugin_id) DO UPDATE SET active_generation = EXCLUDED.active_generation
                """,
                (self.generation,),
            )
            cur.execute(
                """
                INSERT INTO movievault_v2_releases (
                    generation, release_id, film_id, canonical_title, release_title,
                    edition, format, country_code, language_code, runtime_minutes,
                    distributor, revision
                )
                VALUES (%s, %s, %s, 'Mirror Film', 'Mirror Film', 'Theatrical',
                        'Blu-ray', 'NL', 'nl', 118, 'Mirror Distribution', 43)
                """,
                (self.generation, self.release_id, uuid.uuid4()),
            )
            cur.execute(
                """
                INSERT INTO movievault_v2_box_sets (generation, box_set_id, title, revision)
                VALUES (%s, %s, 'Mirror Box Set', 7)
                """,
                (self.generation, self.box_set_id),
            )

    def _cleanup(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM movies WHERE title LIKE %s", (f"{PREFIX}%",))
            cur.execute("DELETE FROM movievault_v2_lookup_hashes WHERE generation = %s", (self.generation,))
            cur.execute("DELETE FROM movievault_v2_releases WHERE generation = %s", (self.generation,))
            cur.execute("DELETE FROM movievault_v2_box_sets WHERE generation = %s", (self.generation,))
            cur.execute("DELETE FROM movievault_v2_sync_state WHERE active_generation = %s", (self.generation,))

    def _movie(self, **overrides):
        movie_id = uuid.uuid4()
        row = {
            "id": movie_id,
            "public_id": f"{PREFIX}-{movie_id}",
            "title": f"{PREFIX} local title",
            "barcode": None,
            "edition": "Director's Cut",
            "format": "4K UHD",
            "country": "NL",
            "language": "nl",
            "release_date": None,
            "runtime_minutes": 120,
        }
        row.update(overrides)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movies (id, public_id, title, barcode, edition, format,
                                    country, language, release_date, runtime_minutes)
                VALUES (%(id)s, %(public_id)s, %(title)s, %(barcode)s, %(edition)s,
                        %(format)s, %(country)s, %(language)s, %(release_date)s,
                        %(runtime_minutes)s)
                """,
                row,
            )
        return row

    def _lookup(self, lookup_hash, entity_type, entity_id, source_type="release_ean", position=0):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_lookup_hashes (
                    generation, lookup_hash, entity_type, entity_id, source_type, member_position
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (self.generation, lookup_hash, entity_type, entity_id, source_type, position),
            )

    def _identifier(self, movie_id, value, provider="movievault_v2"):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movie_identifiers (movie_id, provider_id, identifier_type, identifier)
                VALUES (%s, %s, 'movie_id', %s)
                """,
                (movie_id, provider, str(value)),
            )

    def test_the_stored_identifier_is_preferred_over_the_barcode(self):
        """The id says "this is that release"; a barcode says "something with
        this barcode". When a record carries both, the id is the better claim --
        and it is the only one that survives a barcode a moderator declined."""
        other_release = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_releases (
                    generation, release_id, film_id, canonical_title, release_title, revision
                )
                VALUES (%s, %s, %s, 'Other', 'Other', 9)
                """,
                (self.generation, other_release, uuid.uuid4()),
            )
        movie = self._movie(barcode=BARCODE)
        self._identifier(movie["id"], self.release_id)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", other_release)

        target = corrections.resolve_target(self.conn, entity="movie", record=movie)

        self.assertEqual(target["entityId"], str(self.release_id))
        self.assertEqual(target["matchedBy"], "identifier")
        self.assertEqual(target["baseRevision"], 43)

    def test_the_barcode_resolves_when_no_identifier_is_stored(self):
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        target = corrections.resolve_target(self.conn, entity="movie", record=movie)

        self.assertEqual(target["matchedBy"], "barcode")
        self.assertEqual(target["baseRevision"], 43)

    def test_an_identifier_from_another_generation_is_not_a_target(self):
        """A `movievault_26` id is a UUID-shaped value in a different namespace,
        and an imported film carries a locally derived UUIDv5 that names
        nothing. Both would resolve on shape alone."""
        movie = self._movie()
        self._identifier(movie["id"], uuid.uuid4(), provider="movievault_26")

        self.assertIsNone(corrections.resolve_target(self.conn, entity="movie", record=movie))

    def test_an_ambiguous_barcode_is_not_a_target(self):
        """Two records behind one barcode is the ambiguity the fallback picker
        exists for. Picking one here would attach a correction to a record
        nobody chose."""
        second = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_releases (
                    generation, release_id, film_id, canonical_title, release_title, revision
                )
                VALUES (%s, %s, %s, 'Second', 'Second', 3)
                """,
                (self.generation, second, uuid.uuid4()),
            )
        movie = self._movie(barcode=BARCODE)
        lookup_hash = corrections.barcode_lookup_hash(BARCODE)
        self._lookup(lookup_hash, "release", self.release_id)
        self._lookup(lookup_hash, "release", second)

        self.assertIsNone(corrections.resolve_target(self.conn, entity="movie", record=movie))

    def test_a_movie_may_not_correct_a_box_set(self):
        """A box-set barcode on a movie row is a real shape -- it means the disc
        is part of a set -- and it must not turn a film edit into a box-set
        correction."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "box_set", self.box_set_id, "box_set_ean")

        self.assertIsNone(corrections.resolve_target(self.conn, entity="movie", record=movie))

    def test_expected_comes_from_the_mirror_and_never_from_the_local_row(self):
        movie = self._movie(barcode=BARCODE, edition="Director's Cut")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertEqual(preview["mode"], "correction")
        edition = next(item for item in preview["changes"] if item["field"] == "edition")
        self.assertEqual(edition["expected"], "Theatrical")
        self.assertEqual(edition["proposed"], "Director's Cut")

    def test_a_field_the_catalogue_already_agrees_with_is_not_a_change(self):
        """A submission is bounded at 25 fields. Spending one on agreement costs
        a moderator a decision and teaches nobody anything."""
        movie = self._movie(barcode=BARCODE, format="Blu-ray")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertNotIn("format", {item["field"] for item in preview["changes"]})

    def test_a_locked_field_never_travels(self):
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(
            self.conn,
            entity="movie",
            record=movie,
            metadata={"field_locks": ["edition"]},
        )

        self.assertNotIn("edition", {item["field"] for item in preview["changes"]})
        self.assertEqual(preview["withheld"]["edition"], "locked_locally")

    def test_a_country_that_is_not_a_code_is_withheld_rather_than_rejected_upstream(self):
        movie = self._movie(barcode=BARCODE, country="Netherlands")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertEqual(
            preview["withheld"]["countryCode"], "local_value_is_not_a_country_code"
        )
        self.assertNotIn("countryCode", {item["field"] for item in preview["changes"]})

    def test_a_language_that_is_not_a_code_is_withheld(self):
        """MovieVault puts no pattern on `language_code`, so it would accept
        "Dutch" and poison a shared catalogue. Refusing here is the only guard."""
        movie = self._movie(barcode=BARCODE, language="Dutch")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertEqual(
            preview["withheld"]["languageCode"], "local_value_is_not_a_language_code"
        )

    def test_an_unresolvable_movie_is_a_proposal_and_a_box_set_is_unavailable(self):
        movie = self._movie(barcode=OTHER_BARCODE)
        self.assertEqual(
            corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})["mode"],
            "proposal",
        )
        self.assertEqual(
            corrections.correction_preview(
                self.conn, entity="container", record={"id": uuid.uuid4(), "title": "x"}
            )["mode"],
            "unavailable",
        )

    def test_a_box_set_offers_only_its_title(self):
        """Not a limitation of the mechanism: `containers` holds nothing else
        MovieVault also holds for a box set."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movievault_v2_lookup_hashes (
                    generation, lookup_hash, entity_type, entity_id, source_type, member_position
                )
                VALUES (%s, %s, 'box_set', %s, 'box_set_ean', 0)
                """,
                (self.generation, corrections.barcode_lookup_hash(BARCODE), self.box_set_id),
            )
        container = {"id": uuid.uuid4(), "title": "Corrected Set", "barcode": BARCODE}

        preview = corrections.correction_preview(self.conn, entity="container", record=container)

        self.assertEqual(preview["mode"], "correction")
        self.assertEqual([item["field"] for item in preview["changes"]], ["title"])
        self.assertEqual(preview["target"]["baseRevision"], 7)

    def test_the_barcode_hash_matches_what_the_index_is_keyed_by(self):
        """Computed differently is simply a different hash, and would miss in
        silence. Pinned against the same normalisation both native clients use."""
        self.assertEqual(
            corrections.barcode_lookup_hash("4006-3813-33931"),
            hashlib.sha256(b"4006381333931").hexdigest(),
        )
        self.assertIsNone(corrections.barcode_lookup_hash("123"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
