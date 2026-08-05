"""What the location sync publishes, and what it deliberately withholds.

Sync-contract §4c. Locations existed server-side for a long time before they
reached a client: the tree, the QR codes and the assignments were all there, and
none of it was on the sync wire. A movie whose location was set in the PWA
through the picker showed blank on the phone, because `location_id` rode the
delta but not the bootstrap and there was no `locations` array to resolve it
against.

The guards below are structural on purpose. Every bug in this area was a
*relationship* between two builders quietly coming apart — a column added to one
SELECT and not its twin, a key published on one endpoint and not the other — and
none of them made anything fail visibly.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app, next_preferences
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    next_app = None
    next_preferences = None


def _fake_location_rows():
    """What `location_list_entities` returns: wire fields plus server-only extras."""
    return [
        {
            "id": "k1",
            "public_id": "next-location-k1",
            "parent_id": None,
            "name": "Kast 01",
            "description": None,
            "qr_token": "secret-token-k1",
            "sort_order": 0,
            "metadata": {},
            "created_at": "2026-08-05T00:00:00Z",
            "updated_at": "2026-08-05T00:00:00Z",
            "depth": 1,
            "name_path": ["Kast 01"],
            "path": ["Kast 01"],
            "path_label": "Kast 01",
            "movie_count": 3,
            "container_count": 1,
            "backdrop_url": "",
        },
        {
            "id": "l1",
            "public_id": "next-location-l1",
            "parent_id": "k1",
            "name": "Lade 01",
            "description": "Top drawer",
            "qr_token": "secret-token-l1",
            "sort_order": 0,
            "metadata": {},
            "created_at": "2026-08-05T00:00:00Z",
            "updated_at": "2026-08-05T00:00:00Z",
            "depth": 2,
            "name_path": ["Kast 01", "Lade 01"],
            "path": ["Kast 01", "Lade 01"],
            "path_label": "Kast 01 / Lade 01",
            "movie_count": 0,
            "container_count": 0,
            "backdrop_url": "",
        },
    ]


@unittest.skipIf(next_app is None, "Flask is not installed in this test environment")
class LocationSyncShapeTests(unittest.TestCase):
    """The `locations` array as §4c.1 defines it. No database."""

    def setUp(self):
        self._original = next_app.location_list_entities
        next_app.location_list_entities = lambda conn: _fake_location_rows()
        self.addCleanup(setattr, next_app, "location_list_entities", self._original)

    def test_publishes_exactly_the_contract_keys(self):
        expected = {
            "id",
            "public_id",
            "parent_id",
            "name",
            "description",
            "sort_order",
            "depth",
            "path",
            "path_label",
            "backdrop_url",
            "metadata",
            "created_at",
            "updated_at",
        }
        for row in next_app.location_sync_entities(None):
            self.assertEqual(expected, set(row), "the locations array drifted from §4c.1")

    def test_qr_token_never_reaches_a_client(self):
        """A capability token has no business on every device.

        `public_id` is on the wire, and a scan resolves to
        `discvault://locations/<public_id>`, so the scan flow is complete
        without it (§4c.1).
        """
        for row in next_app.location_sync_entities(None):
            self.assertNotIn("qr_token", row)

    def test_counts_are_not_published(self):
        # A client counts from its own copy. A server tally that drifts out of
        # step with that copy is worse than no tally.
        for row in next_app.location_sync_entities(None):
            self.assertNotIn("movie_count", row)
            self.assertNotIn("container_count", row)

    def test_ordering_is_preserved(self):
        """§4c.1 promises pre-order, so no client has to sort for itself."""
        ids = [row["id"] for row in next_app.location_sync_entities(None)]
        self.assertEqual(["k1", "l1"], ids)

    def test_single_entity_matches_the_array_entry(self):
        """The delta and the bootstrap must describe a location identically.

        `depth`, `path` and `path_label` only exist relative to the rest of the
        tree, so a second SELECT would be a second chance to disagree.
        """
        from_array = next(
            row for row in next_app.location_sync_entities(None) if row["id"] == "l1"
        )
        self.assertEqual(from_array, next_app.single_location_sync_entity(None, "l1"))

    def test_single_entity_is_none_for_an_unknown_id(self):
        self.assertIsNone(next_app.single_location_sync_entity(None, "does-not-exist"))


@unittest.skipIf(next_app is None, "Flask is not installed in this test environment")
class LocationColumnParityTests(unittest.TestCase):
    """The bootstrap-versus-delta drift that hid this gap. No database."""

    def test_movies_publish_location_id_on_the_bootstrap(self):
        self.assertIn("location_id", next_app._MOVIE_SYNC_COLUMNS)

    def test_location_id_is_not_delta_only(self):
        # It used to sit here, which made a fresh install unable to resolve a
        # location the delta had happily been carrying.
        self.assertNotIn("location_id", next_app._MOVIE_ENTITY_ONLY_COLUMNS)

    def test_both_container_sync_builders_select_location_id(self):
        """`all_container_entities` (bootstrap) and `single_container_sync_entity`
        (delta) are two hand-maintained SELECT lists, and they had drifted the
        same way the movie ones did.
        """
        import inspect

        for builder in (next_app.all_container_entities, next_app.single_container_sync_entity):
            source = inspect.getsource(builder)
            self.assertIn("c.location_id", source, builder.__name__)

    def test_location_is_a_supported_entity_type_but_not_a_mutation(self):
        """§4c.2: locations travel down; assignments travel up on the existing
        movie/container upserts. A `location.upsert` would need identity-ladder
        rung 1, and the table has no per-record client id to provide it.
        """
        import inspect

        source = inspect.getsource(next_app.register_routes)
        state_block = source[source.index('"supportedEntityTypes"') : source.index('"userEntityTypes"')]
        self.assertIn('"location"', state_block)
        self.assertNotIn("location.upsert", state_block)
        self.assertNotIn("location.delete", state_block)


@unittest.skipIf(next_app is None, "Flask is not installed in this test environment")
class LocationDetachmentTests(unittest.TestCase):
    """Deleting a location is a change to the rows it detached (§4c.5)."""

    def test_every_detached_row_gets_its_own_change(self):
        emitted = []
        original_movie = next_app.emit_movie_change
        original_container = next_app.emit_container_change
        next_app.emit_movie_change = lambda conn, movie_id, **kw: emitted.append(("movie", movie_id)) or 1
        next_app.emit_container_change = lambda conn, container_id, **kw: emitted.append(("container", container_id)) or 1
        self.addCleanup(setattr, next_app, "emit_movie_change", original_movie)
        self.addCleanup(setattr, next_app, "emit_container_change", original_container)

        count = next_app.emit_location_detachments(None, ["m1", "m2"], ["c1"])

        self.assertEqual(3, count)
        self.assertEqual([("movie", "m1"), ("movie", "m2"), ("container", "c1")], emitted)

    def test_nothing_detached_emits_nothing(self):
        self.assertEqual(0, next_app.emit_location_detachments(None, [], []))


@unittest.skipIf(next_app is None, "Flask is not installed in this test environment")
class MovieLocationAssignmentTests(unittest.TestCase):
    """The write path a client uses to shelve a movie (§4c.2, §4c.3).

    `container_payload` handled `locationId` from the day locations existed;
    `movie_payload_fields` — the map behind `movie.upsert`, which is the route a
    phone actually takes — did not mention it at all. The key was accepted,
    dropped, and answered with `isApplied`, so a client booked an assignment
    that never happened.
    """

    def test_an_untouched_field_asserts_nothing(self):
        # kotlinx omits defaults and Swift uses encodeIfPresent, so this is the
        # shape of every upsert that is not about a location.
        self.assertEqual({}, next_app.movie_location_assignment({"title": "Heat"}))

    def test_an_explicit_null_is_the_same_as_absent(self):
        # §4.8: the two are indistinguishable by construction, so they must mean
        # the same thing. Keep.
        self.assertEqual({}, next_app.movie_location_assignment({"locationId": None}))

    def test_an_explicit_empty_value_erases_the_assignment(self):
        # The only way a user takes a movie off its shelf from a phone.
        self.assertEqual({"location_id": None}, next_app.movie_location_assignment({"locationId": ""}))
        self.assertEqual({"location_id": None}, next_app.movie_location_assignment({"locationId": "   "}))

    def test_a_uuid_is_parsed(self):
        assignment = next_app.movie_location_assignment(
            {"locationId": "6f1b0f2e-0f2e-4f2e-8f2e-0f2e4f2e8f2e"}
        )
        self.assertEqual(
            "6f1b0f2e-0f2e-4f2e-8f2e-0f2e4f2e8f2e", str(assignment["location_id"])
        )

    def test_the_snake_case_alias_is_accepted(self):
        assignment = next_app.movie_location_assignment(
            {"location_id": "6f1b0f2e-0f2e-4f2e-8f2e-0f2e4f2e8f2e"}
        )
        self.assertIn("location_id", assignment)

    def test_the_upsert_field_map_carries_the_assignment(self):
        fields = next_app.movie_payload_fields({"locationId": ""})
        self.assertEqual({"location_id": None}, fields["location_assignment"])

    def test_the_free_text_field_is_untouched_by_an_assignment(self):
        """§4c.6: two fields that never overwrite each other."""
        fields = next_app.movie_payload_fields(
            {"location": "Bottom shelf", "locationId": "6f1b0f2e-0f2e-4f2e-8f2e-0f2e4f2e8f2e"}
        )
        self.assertEqual("Bottom shelf", fields["location"])
        self.assertIn("location_id", fields["location_assignment"])

    def test_nothing_is_written_when_the_client_said_nothing(self):
        calls = []

        class _Cur:
            def execute(self, sql, params=None):
                calls.append(sql)

            def fetchone(self):
                return None

        next_app.apply_movie_location_assignment(_Cur(), "movie-1", {})
        self.assertEqual([], calls)

    def test_an_unknown_location_is_rejected_rather_than_stored_as_null(self):
        """A failure that comes back as success is worse than a failure."""

        class _Cur:
            def execute(self, sql, params=None):
                self.last = sql

            def fetchone(self):
                return None  # the location does not exist

        with self.assertRaises(next_app.NextApiError):
            next_app.apply_movie_location_assignment(
                _Cur(), "movie-1", {"location_id": "6f1b0f2e-0f2e-4f2e-8f2e-0f2e4f2e8f2e"}
            )

    def test_erasing_needs_no_existence_check(self):
        statements = []

        class _Cur:
            def execute(self, sql, params=None):
                statements.append(sql)

            def fetchone(self):
                raise AssertionError("an erase must not look a location up")

        next_app.apply_movie_location_assignment(_Cur(), "movie-1", {"location_id": None})
        self.assertEqual(1, len(statements))
        self.assertIn("UPDATE movies", statements[0])


@unittest.skipIf(next_preferences is None, "Flask is not installed in this test environment")
class LocationMobileContractTests(unittest.TestCase):
    def test_the_endpoint_contract_names_the_location_routes(self):
        contract = next_preferences.mobile_endpoint_contract_payload()
        self.assertEqual("/api/next/locations", contract["locations"]["list"])
        self.assertIn("{locationPublicId}", contract["locations"]["open"])

    def test_the_served_contract_names_every_sync_route(self):
        """A second, drifted copy of this payload used to live in `next_app`.

        It was dead code — the import binding won — so the routes it named were
        missing from what clients actually received.
        """
        sync = next_preferences.mobile_endpoint_contract_payload()["sync"]
        for key in ("state", "bootstrap", "delta", "mutations", "reconcile", "userBootstrap", "userDelta"):
            self.assertIn(key, sync)

    def test_managing_the_tree_is_not_offered_as_a_client_capability(self):
        import inspect

        source = inspect.getsource(next_preferences.mobile_feature_capabilities)
        block = source[source.index('"locations"') :]
        self.assertIn('"manage": False', block)
