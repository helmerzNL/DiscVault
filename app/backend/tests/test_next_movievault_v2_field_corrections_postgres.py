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

    def test_a_box_set_has_no_preflight(self):
        """`/v2/films/{id}/releases` is about films. A box set has no
        equivalent, so its diff is always against the mirror -- stated rather
        than silently the same as a release's."""
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

        self.assertEqual(preview["comparedAgainst"], "mirror")

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
