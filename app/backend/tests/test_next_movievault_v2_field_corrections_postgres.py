"""Field-correction target resolution and diffing, against a real PostgreSQL.

What matters here cannot be asserted against a fake cursor: which of two ways of
naming a record wins, that a barcode resolving to more than one entity is not a
target at all, and that `expected` comes from the mirror rather than from the
row being corrected. All three are about what the database actually returns.

Point `DATABASE_URL` at a database with every migration applied; without it
these skip.
"""

import hashlib
import json
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
#: A second real EAN-13, and a real UPC-A. Both check-digit valid: a fabricated
#: code passes the length test, fails the checksum, and asserts nothing.
OTHER_EAN = "5051890013279"
UPC = "012569828827"


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
                VALUES (%s, %s, %s, 'Mirror Film',
                        'Mirror Film Blu-ray (Spiegelfilm) (Germany)', 'Theatrical',
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
            cur.execute(
                "DELETE FROM movievault_v2_contributions WHERE movie_id IN "
                "(SELECT id FROM movies WHERE title LIKE %s)",
                (f"{PREFIX}%",),
            )
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

    def test_a_release_title_is_not_the_film_title_and_is_never_offered(self):
        """The most misleading of the withheld fields, because the two values
        look comparable.

        `content.releases.title` names a pressing -- "Spider-Man: Into the
        Spider-Verse Blu-ray (Spider-Man: A New Universe) (Germany)" -- while
        DiscVault's `movies.title` names the film. Offering it made every
        correctly titled film read as a disagreement on every release, and
        accepting one would have flattened a product name into a film name,
        losing the edition, the alternate title and the country with it.
        """
        # The film title is correct; the mirror holds a product name. Before
        # this, that pairing produced a proposed "correction" on every release.
        movie = self._movie(barcode=BARCODE, title=f"{PREFIX} Mirror Film")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        mirror = corrections.mirror_values(
            self.conn, corrections.resolve_target(self.conn, entity="movie", record=movie)
        )
        self.assertNotEqual(mirror["title"], "Mirror Film")

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertNotIn("title", {item["field"] for item in preview["changes"]})
        self.assertEqual(preview["withheld"]["title"], "different_field_upstream")
        self.assertNotIn("title", corrections.RELEASE_FIELD_SOURCES)

    def test_a_box_set_title_is_still_offered(self):
        """A box set's title on both sides names the same product, so the
        release-title reasoning does not carry over to containers -- and it is
        the only field a box set has."""
        self.assertIn("title", corrections.BOX_SET_FIELD_SOURCES)
        self.assertNotIn("title", corrections.BOX_SET_FIELDS_WITHHELD)

    def test_a_country_that_is_not_a_code_is_withheld_rather_than_rejected_upstream(self):
        movie = self._movie(barcode=BARCODE, country="Netherlands")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertEqual(
            preview["withheld"]["countryCode"], "local_value_is_not_a_country_code"
        )
        self.assertNotIn("countryCode", {item["field"] for item in preview["changes"]})

    def test_a_lower_case_country_code_is_normalised_rather_than_withheld(self):
        """`movies.country` is free text, so the same country arrives written
        several ways. Two ASCII letters name a country whatever their case, so
        upper-casing is a normalisation and not a guess -- and without it a user
        was told their value is not a country code about a value that is one.
        """
        # The mirror holds "NL", so a local "de" is a real disagreement and has
        # to survive as one; a local "nl" would simply agree.
        movie = self._movie(barcode=BARCODE, country="de")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertNotIn("countryCode", preview["withheld"])
        change = next(item for item in preview["changes"] if item["field"] == "countryCode")
        self.assertEqual(change["proposed"], "DE")
        self.assertEqual(change["expected"], "NL")

    def test_a_country_code_that_already_agrees_in_another_case_is_not_a_correction(self):
        """The normalisation must run before the diff, or "nl" against a mirror
        holding "NL" reads as a change and proposes a correction that corrects
        nothing."""
        movie = self._movie(barcode=BARCODE, country="nl")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})

        self.assertNotIn("countryCode", preview["withheld"])
        self.assertNotIn("countryCode", {item["field"] for item in preview["changes"]})

    def test_a_country_that_needs_a_lookup_table_stays_withheld(self):
        """The line is case-folding, not derivation. "NLD" and "nl-NL" name the
        Netherlands to a reader and to nothing else here; mapping them needs a
        table, which is a data decision with a silent wrong-answer mode."""
        for value in ("NLD", "nl-NL", "Nederland"):
            with self.subTest(country=value):
                # Resolved through the stored identifier rather than a barcode:
                # `movies.barcode` is UNIQUE, so several movies in one test
                # cannot each carry the same one.
                movie = self._movie(country=value)
                self._identifier(movie["id"], self.release_id)

                preview = corrections.correction_preview(
                    self.conn, entity="movie", record=movie, metadata={}
                )

                self.assertEqual(
                    preview["withheld"]["countryCode"], "local_value_is_not_a_country_code"
                )

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

    # ---- the contribution log -------------------------------------------

    def test_the_outcome_survives_the_correction_becoming_unnecessary(self):
        """The reason the log exists at all.

        An accepted correction makes the local row and the catalogue agree, so
        the diff is empty and the Contribute button goes -- and with it the one
        place the outcome could have appeared. The answer to "what happened to
        my correction" must not depend on there being a next one to send.
        """
        # Every correctable field now agrees with the mirror, which is what an
        # accepted correction leaves behind.
        movie = self._movie(
            barcode=BARCODE,
            edition="Theatrical",
            format="Blu-ray",
            runtime_minutes=118,
        )
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        target = corrections.resolve_target(self.conn, entity="movie", record=movie)
        job_id = uuid.uuid4()

        corrections.record_contribution(
            self.conn,
            entity="movie",
            record=movie,
            target=target,
            changes=[{"field": "edition", "expected": "Theatrical", "proposed": "Director's Cut"}],
            job_id=job_id,
        )
        corrections.update_contribution_by_job(
            self.conn, job_id, status="pending", contribution_id="c-1"
        )
        corrections.update_contribution_by_contribution_id(
            self.conn, "c-1", status="accepted", canonical_target_id="t-1"
        )

        # Nothing left to correct - the local row now matches the mirror.
        preview = corrections.correction_preview(self.conn, entity="movie", record=movie, metadata={})
        self.assertEqual([item["field"] for item in preview["changes"]], [])

        latest = corrections.latest_contribution(self.conn, entity="movie", record=movie)
        self.assertEqual(latest["status"], "accepted")
        self.assertTrue(latest["settled"])
        self.assertEqual(latest["fields"], ["edition"])
        self.assertEqual(latest["canonicalTargetId"], "t-1")

    def test_only_the_latest_contribution_is_reported(self):
        """The screen answers "did my correction land". A history of every
        attempt is a different feature with a different surface."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        target = corrections.resolve_target(self.conn, entity="movie", record=movie)
        for status in ("rejected", "accepted"):
            job_id = uuid.uuid4()
            corrections.record_contribution(
                self.conn,
                entity="movie",
                record=movie,
                target=target,
                changes=[{"field": "format", "expected": "Blu-ray", "proposed": "4K UHD"}],
                job_id=job_id,
            )
            corrections.update_contribution_by_job(self.conn, job_id, status=status)

        latest = corrections.latest_contribution(self.conn, entity="movie", record=movie)
        self.assertEqual(latest["status"], "accepted")

    def test_a_job_updates_exactly_one_row(self):
        """Without the unique index a re-queued job could update two rows and
        silently halve the history."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        target = corrections.resolve_target(self.conn, entity="movie", record=movie)
        job_id = uuid.uuid4()
        changes = [{"field": "format", "expected": "Blu-ray", "proposed": "4K UHD"}]

        corrections.record_contribution(
            self.conn, entity="movie", record=movie, target=target, changes=changes, job_id=job_id
        )
        # A second insert for the same job is the retry case, not a new
        # contribution.
        corrections.record_contribution(
            self.conn, entity="movie", record=movie, target=target, changes=changes, job_id=job_id
        )

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS total FROM movievault_v2_contributions WHERE job_id = %s",
                (job_id,),
            )
            self.assertEqual(dict(cur.fetchone())["total"], 1)

    def test_an_unsettled_status_is_reported_as_unsettled(self):
        """`queued` and `pending` are the two states a user is waiting in, and
        the screen has to be able to tell them apart from a decision."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        target = corrections.resolve_target(self.conn, entity="movie", record=movie)
        job_id = uuid.uuid4()
        corrections.record_contribution(
            self.conn,
            entity="movie",
            record=movie,
            target=target,
            changes=[{"field": "format", "expected": "Blu-ray", "proposed": "4K UHD"}],
            job_id=job_id,
        )

        latest = corrections.latest_contribution(self.conn, entity="movie", record=movie)
        self.assertEqual(latest["status"], "queued")
        self.assertFalse(latest["settled"])

        corrections.update_contribution_by_job(self.conn, job_id, status="pending")
        self.assertFalse(
            corrections.latest_contribution(self.conn, entity="movie", record=movie)["settled"]
        )

    def test_a_record_with_no_contribution_reports_nothing(self):
        movie = self._movie(barcode=BARCODE)
        self.assertIsNone(
            corrections.latest_contribution(self.conn, entity="movie", record=movie)
        )

    # ---- the conflict pre-flight -----------------------------------------

    def _live(self, payload):
        """Stand in for `GET /v2/films/{id}/releases`."""
        import unittest.mock as mock

        return mock.patch.object(
            corrections,
            "live_release_values",
            lambda film_id, release_id, **k: payload,
        )

    def test_the_preflight_beats_a_stale_mirror(self):
        """`expected` is the conflict check upstream, so a value read from a
        snapshot that has since moved gets the correction refused for a field
        the contributor read correctly at the time."""
        movie = self._movie(barcode=BARCODE, edition="Director's Cut")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        with self._live({"edition": "Extended", "format": "Blu-ray"}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        edition = next(item for item in preview["changes"] if item["field"] == "edition")
        # The mirror says "Theatrical"; the catalogue has since moved on.
        self.assertEqual(edition["expected"], "Extended")
        self.assertEqual(preview["comparedAgainst"], "catalogue")

    def test_a_field_the_catalogue_has_already_caught_up_on_is_not_a_change(self):
        """Someone else corrected it first. Sending it again would put a
        no-op in front of a moderator."""
        movie = self._movie(barcode=BARCODE, edition="Director's Cut")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        with self._live({"edition": "Director's Cut"}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertNotIn("edition", {item["field"] for item in preview["changes"]})

    def test_an_unreachable_catalogue_falls_back_rather_than_agreeing(self):
        """A failed check is not "nothing changed". An offline instance must
        still be able to compose a correction, and the answer says which of the
        two it was computed against."""
        movie = self._movie(barcode=BARCODE, edition="Director's Cut")
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        with self._live(None):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        edition = next(item for item in preview["changes"] if item["field"] == "edition")
        self.assertEqual(edition["expected"], "Theatrical")
        self.assertEqual(preview["comparedAgainst"], "mirror")

    def test_a_release_the_catalogue_no_longer_serves_is_unavailable(self):
        """Merged, retired or deleted upstream. Correcting a record that is no
        longer served is not something to guess at."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        with self._live({"_gone": True}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertEqual(preview["mode"], "unavailable")

    def test_eligibility_can_skip_the_preflight(self):
        """It renders on every detail screen and only needs to know whether
        anything differs; a network round trip there would be paid constantly
        to sharpen a number nobody has looked at."""
        import unittest.mock as mock

        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        called = []
        with mock.patch.object(
            corrections,
            "live_release_values",
            lambda *a, **k: called.append(a) or None,
        ):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}, preflight=False
            )

        self.assertEqual(called, [])
        self.assertEqual(preview["comparedAgainst"], "mirror")

    def test_a_box_set_is_checked_against_its_own_route(self):
        """The box-set half. It reads `/v2/box-sets/{id}` rather than the film
        route, because the two return different shapes -- one an object, one a
        list to search."""
        import unittest.mock as mock

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

        with mock.patch.object(
            corrections, "live_box_set_values", lambda box_set_id, **k: {"title": "Moved Since"}
        ):
            preview = corrections.correction_preview(
                self.conn, entity="container", record=container
            )

        title = next(item for item in preview["changes"] if item["field"] == "title")
        self.assertEqual(title["expected"], "Moved Since")
        self.assertEqual(preview["comparedAgainst"], "catalogue")

    def test_a_box_set_falls_back_to_the_mirror_too(self):
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

        import unittest.mock as mock

        with mock.patch.object(corrections, "live_box_set_values", lambda *a, **k: None):
            preview = corrections.correction_preview(
                self.conn, entity="container", record=container
            )

        self.assertEqual(preview["comparedAgainst"], "mirror")
        self.assertEqual(
            next(item for item in preview["changes"] if item["field"] == "title")["expected"],
            "Mirror Box Set",
        )

    # ---- settling, and telling the contributor ---------------------------

    def _logged(self, actor_id=None):
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        target = corrections.resolve_target(self.conn, entity="movie", record=movie)
        job_id = uuid.uuid4()
        corrections.record_contribution(
            self.conn,
            entity="movie",
            record=movie,
            target=target,
            changes=[{"field": "format", "expected": "Blu-ray", "proposed": "4K UHD"}],
            job_id=job_id,
            actor_id=actor_id,
        )
        corrections.update_contribution_by_job(
            self.conn, job_id, status="pending", contribution_id=f"c-{job_id}"
        )
        return movie, f"c-{job_id}"

    def test_a_verdict_settles_exactly_once(self):
        """The caller turns a non-None into a notification, and a verdict
        announced twice is worse than one announced late. Two workers racing
        the same answer must produce one winner."""
        actor = uuid.uuid4()
        _, contribution_id = self._logged(actor_id=actor)

        first = corrections.update_contribution_by_contribution_id(
            self.conn, contribution_id, status="accepted", canonical_target_id="t-1"
        )
        second = corrections.update_contribution_by_contribution_id(
            self.conn, contribution_id, status="accepted", canonical_target_id="t-1"
        )

        self.assertIsNotNone(first)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(first["submittedBy"], str(actor))
        self.assertEqual(first["fields"], ["format"])
        self.assertIsNone(second)

    def test_a_status_that_is_not_a_decision_settles_nothing(self):
        """`pending` and `quarantined` are states a contributor waits in.
        Announcing them as verdicts would be a notification per poll."""
        _, contribution_id = self._logged()
        for status in ("pending", "quarantined"):
            self.assertIsNone(
                corrections.update_contribution_by_contribution_id(
                    self.conn, contribution_id, status=status
                )
            )

    def test_a_later_verdict_does_not_reopen_a_settled_one(self):
        """A poll that somehow runs after the decision must not announce a
        second, different outcome for the same contribution."""
        _, contribution_id = self._logged()
        corrections.update_contribution_by_contribution_id(
            self.conn, contribution_id, status="rejected"
        )
        self.assertIsNone(
            corrections.update_contribution_by_contribution_id(
                self.conn, contribution_id, status="accepted"
            )
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM movievault_v2_contributions WHERE contribution_id = %s",
                (contribution_id,),
            )
            self.assertEqual(dict(cur.fetchone())["status"], "rejected")

    def test_settling_keeps_a_canonical_id_it_already_has(self):
        """`COALESCE`, not overwrite: a second answer that omits the id must
        not erase the one the first carried."""
        _, contribution_id = self._logged()
        corrections.update_contribution_by_contribution_id(
            self.conn, contribution_id, status="accepted", canonical_target_id="t-1"
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT canonical_target_id FROM movievault_v2_contributions WHERE contribution_id = %s",
                (contribution_id,),
            )
            self.assertEqual(dict(cur.fetchone())["canonical_target_id"], "t-1")

    # ---- the history ------------------------------------------------------

    def test_the_history_is_newest_first_and_carries_the_record_title(self):
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        target = corrections.resolve_target(self.conn, entity="movie", record=movie)
        for status in ("rejected", "accepted"):
            job_id = uuid.uuid4()
            corrections.record_contribution(
                self.conn,
                entity="movie",
                record=movie,
                target=target,
                changes=[{"field": "format", "expected": "Blu-ray", "proposed": "4K UHD"}],
                job_id=job_id,
            )
            corrections.update_contribution_by_job(self.conn, job_id, status=status)

        history = corrections.contribution_history(self.conn, limit=10)
        mine = [item for item in history if item["recordId"] == str(movie["id"])]

        self.assertEqual([item["status"] for item in mine], ["accepted", "rejected"])
        self.assertEqual(mine[0]["title"], movie["title"])
        self.assertEqual(mine[0]["entity"], "movie")
        self.assertEqual(mine[0]["fields"], ["format"])

    def test_the_history_can_be_scoped_to_one_contributor(self):
        """A contribution is attributable, and one user reading another's is
        not something a shared instance should offer by default."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        target = corrections.resolve_target(self.conn, entity="movie", record=movie)
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        for actor in (mine, theirs):
            corrections.record_contribution(
                self.conn,
                entity="movie",
                record=movie,
                target=target,
                changes=[{"field": "format", "expected": "Blu-ray", "proposed": "4K UHD"}],
                job_id=uuid.uuid4(),
                actor_id=actor,
            )

        scoped = corrections.contribution_history(self.conn, actor_id=mine, limit=10)
        self.assertEqual(len(scoped), 1)
        self.assertEqual(len(corrections.contribution_history(self.conn, limit=10)), 2)

    # ---- disc regions ----------------------------------------------------

    def _technical(self, movie_id, regions):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movie_technical_specs (movie_id, regions)
                VALUES (%s, %s)
                ON CONFLICT (movie_id) DO UPDATE SET regions = EXCLUDED.regions
                """,
                (movie_id, Jsonb(regions) if Jsonb else json.dumps(regions)),
            )

    def test_disc_regions_are_the_one_region_shaped_field_that_travels(self):
        """MovieVault's pulldown and DiscVault's `movie_technical_specs.regions`
        hold the same normalised vocabulary, so this pair is genuinely the same
        field -- unlike `region`, which is free-text market region."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        self._technical(movie["id"], ["B", "A"])

        with self._live({"discRegions": ["A"]}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        change = next(item for item in preview["changes"] if item["field"] == "discRegions")
        # Sorted on both sides: two orderings of one answer are not a
        # disagreement about which discs play where.
        self.assertEqual(change["proposed"], ["A", "B"])
        self.assertEqual(change["expected"], ["A"])
        self.assertNotIn("discRegions", preview["withheld"])
        self.assertEqual(preview["withheld"]["region"], "different_field_upstream")

    def test_a_region_value_outside_the_vocabulary_never_travels(self):
        """DiscVault keeps these in a jsonb column with no constraint, so an
        unrecognised value is a local possibility. Upstream would refuse the
        whole envelope rather than the one field, which would take every other
        correction on the record down with it."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        self._technical(movie["id"], ["Region 2", "PAL"])

        with self._live({"discRegions": ["A"]}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertNotIn("discRegions", {item["field"] for item in preview["changes"]})

    def test_nothing_recorded_locally_is_not_a_proposal_to_clear_them(self):
        """An empty local set means "nobody said", and upstream reads an empty
        list as "this release plays nowhere"."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        self._technical(movie["id"], [])

        with self._live({"discRegions": ["A"]}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertNotIn("discRegions", {item["field"] for item in preview["changes"]})

    def test_a_locked_region_set_is_not_published(self):
        """The local lock is spelled `regions` and the wire field is
        `discRegions`. Without the mapping a personal override pinned against
        metadata refresh would be pushed into a shared catalogue."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        self._technical(movie["id"], ["B"])

        with self._live({"discRegions": ["A"]}):
            preview = corrections.correction_preview(
                self.conn,
                entity="movie",
                record=movie,
                metadata={"field_locks": ["regions"]},
            )

        self.assertEqual(preview["withheld"]["discRegions"], "locked_locally")
        self.assertNotIn("discRegions", {item["field"] for item in preview["changes"]})

    # ---- the EAN list ----------------------------------------------------

    def _identifier_row(self, movie_id, identifier_type, value):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO movie_product_identifiers (movie_id, identifier_type, identifier_value)
                VALUES (%s, %s, %s)
                """,
                (movie_id, identifier_type, value),
            )

    def test_eans_travels_once_discvault_can_hold_more_than_one_code(self):
        """The field was withheld because a single `movies.barcode` cannot
        express a complete replacement list -- sending `[barcode]` would delete
        every other EAN the release has. With a typed-identifier table there is
        a real list to send."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)
        self._identifier_row(movie["id"], "ean", OTHER_EAN)
        self._identifier_row(movie["id"], "upc", UPC)

        with self._live({"edition": "Theatrical", "eans": [OTHER_EAN]}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertNotIn("eans", preview["withheld"])
        change = next(item for item in preview["changes"] if item["field"] == "eans")
        # The scanned barcode joins the typed one; the UPC does not, because
        # upstream writes this field into `identifier_type = 'ean'` alone.
        self.assertEqual(change["proposed"], sorted([BARCODE, OTHER_EAN]))
        self.assertEqual(change["expected"], [OTHER_EAN])
        self.assertNotIn(UPC, change["proposed"])

    def test_eans_is_refused_out_loud_when_the_mirror_is_all_there_is(self):
        """`movievault_v2_releases` has no barcode column and the lookup index
        holds hashes by design, so there is no honest `expected` without a live
        read. A replacement list proposed against an unknown current state is
        the deletion this field was withheld to prevent."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        with self._live(None):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertEqual(preview["comparedAgainst"], "mirror")
        self.assertEqual(preview["withheld"]["eans"], "needs_live_catalogue")
        self.assertNotIn("eans", {item["field"] for item in preview["changes"]})

    def test_a_live_read_without_a_barcode_list_is_refused_too(self):
        """"The catalogue did not say" is not "the release has none". Treating
        the two the same proposes replacing a list nobody read."""
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        with self._live({"edition": "Theatrical"}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertEqual(preview["comparedAgainst"], "catalogue")
        self.assertEqual(preview["withheld"]["eans"], "needs_live_catalogue")

    def test_a_release_that_already_holds_the_same_list_is_not_a_change(self):
        movie = self._movie(barcode=BARCODE)
        self._lookup(corrections.barcode_lookup_hash(BARCODE), "release", self.release_id)

        with self._live({"eans": [BARCODE]}):
            preview = corrections.correction_preview(
                self.conn, entity="movie", record=movie, metadata={}
            )

        self.assertNotIn("eans", {item["field"] for item in preview["changes"]})
        self.assertNotIn("eans", preview["withheld"])

    def test_the_scannable_types_stay_unique_across_movies(self):
        """One scan resolves to one film -- the promise `movies.barcode` makes,
        extended to the codes a scanner can produce."""
        first = self._movie()
        second = self._movie()
        self._identifier_row(first["id"], "ean", OTHER_EAN)
        with self.assertRaises(Exception):
            self._identifier_row(second["id"], "ean", OTHER_EAN)

    def test_a_catalogue_number_may_repeat_because_it_legitimately_does(self):
        """Two members of one box set share a catalogue number. Constraining it
        would refuse true data to protect a lookup that never consults it."""
        first = self._movie()
        second = self._movie()
        self._identifier_row(first["id"], "catalog_number", "SPHE 1234")
        self._identifier_row(second["id"], "catalog_number", "SPHE 1234")

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
