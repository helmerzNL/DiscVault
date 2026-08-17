"""The coupled iOS release-selection route.

`POST /api/next/movievault/contributions/release-selection` is the import/edit
side-effect given its own front door, for the client that cannot use either
route: iOS writes films through sync mutations, which never read
`releaseCandidate`, so the pressing the user picked never reached the catalogue
(change spec `2026-08-16-ios-contributions.md` §3.5).

The route is deliberately thin -- it invents no contribution logic. It composes
the gate, the `id` binding, and the payload builder that the PWA path already
proves. So what these tests hold is mostly what the route refuses to queue: a
selection when the gate is off, for a film that is not here yet, or with nothing
of substance to say.
"""

import os
import sys
import unittest
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None

MOVIE_ID = "00000000-0000-0000-0000-0000000000a1"
ROUTE = "/api/next/movievault/contributions/release-selection"

# A candidate with real technical substance -- what the payload builder would
# accept. The route never inspects it; it hands it to the builder verbatim.
CANDIDATE = {
    "releaseRef": "mv:release:abc123",
    "source": "resolver",
    "edition": "Director's Cut",
    "format": "4K UHD",
    "discCount": 2,
    "regions": ["B"],
    "video": {"resolution": "2160p", "codecs": ["HEVC"], "hdrFormats": ["HDR10"]},
}
CANDIDATE_FILM = {"title": "Annihilation", "year": 2018, "workType": "movie", "tmdbMovieId": 324857}

# A representative built payload; its exact shape is the builder's concern, not
# the route's.
BUILT_PAYLOAD = {"release": {"title": "Annihilation", "edition": "Director's Cut"}, "film": {"title": "Annihilation"}}


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class ReleaseSelectionRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.actor = {"id": "00000000-0000-0000-0000-0000000000c1", "permissions": ["collection.edit_all"]}
        self.conn = _ConnContext()

    def _patches(self, *, enabled=True, movie=None, payload=None, job=None):
        """Every collaborator the route reaches, stubbed.

        `payload=None` means the builder returns the substantial `BUILT_PAYLOAD`;
        pass `{}` to simulate the substance floor refusing a thin selection.
        """
        return (
            patch("app.backend.next_app.connect", return_value=self.conn),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.release_contribution_enabled", return_value=enabled),
            patch(
                "app.backend.next_app.movie_entity",
                return_value=movie if movie is not None else {"id": MOVIE_ID, "title": "Annihilation", "year": 2018, "metadata": {}},
            ),
            patch("app.backend.next_app.actor_can_edit_visible_movie", return_value=True),
            patch(
                "app.backend.next_app.release_technical_contribution_payload",
                return_value=BUILT_PAYLOAD if payload is None else payload,
            ),
            patch("app.backend.next_app.queue_release_contribution_job", return_value=job),
        )

    def _run(self, call, **kwargs):
        started = self._patches(**kwargs)
        for item in started:
            item.start()
        try:
            return call()
        finally:
            for item in started:
                item.stop()

    def _post(self, body):
        return self.client.post(ROUTE, json=body)

    # ---- the happy path ------------------------------------------------

    def test_a_selection_is_queued_and_returns_the_job_handle(self):
        """The whole point: a chosen pressing becomes a queued contribution,
        and the caller gets the same job handle `/status` reads."""
        response = self._run(
            lambda: self._post({"id": MOVIE_ID, "scannedBarcode": "5051890319025", "releaseCandidate": CANDIDATE, "releaseCandidateFilm": CANDIDATE_FILM}),
            job={"id": "0c2f7b16-0000-0000-0000-000000000001"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["queued"])
        self.assertEqual(payload["jobId"], "0c2f7b16-0000-0000-0000-000000000001")

    def test_the_candidate_is_handed_to_the_builder_as_a_candidate_selection(self):
        """The field boundary and the substance floor live in the builder, so
        the route must reach it -- with the scanned barcode as evidence and the
        candidate-selection provenance, never trusting the client to pre-judge."""
        with patch("app.backend.next_app.connect", return_value=self.conn), patch(
            "app.backend.next_app.next_auth_effective_enabled", return_value=False
        ), patch("app.backend.next_app.require_next_permission", return_value=self.actor), patch(
            "app.backend.next_app.release_contribution_enabled", return_value=True
        ), patch(
            "app.backend.next_app.movie_entity", return_value={"id": MOVIE_ID, "title": "Annihilation", "year": 2018, "metadata": {}}
        ), patch(
            "app.backend.next_app.actor_can_edit_visible_movie", return_value=True
        ), patch(
            "app.backend.next_app.release_technical_contribution_payload", return_value=BUILT_PAYLOAD
        ) as builder, patch(
            "app.backend.next_app.queue_release_contribution_job", return_value={"id": "job-1"}
        ):
            response = self._post({"id": MOVIE_ID, "scannedBarcode": "5051890319025", "releaseCandidate": CANDIDATE, "releaseCandidateFilm": CANDIDATE_FILM})

        self.assertEqual(response.status_code, 200)
        args, kwargs = builder.call_args
        self.assertEqual(args[0], CANDIDATE)
        self.assertEqual(kwargs["scanned_barcode"], "5051890319025")
        self.assertEqual(kwargs["film"], CANDIDATE_FILM)
        self.assertEqual(kwargs["provenance"], "candidate_selection")

    def test_the_film_falls_back_to_the_local_record_when_none_is_sent(self):
        """`releaseCandidateFilm` is optional: the film iOS just wrote is here
        server-side, so its title and year stand in when the client omits it."""
        with patch("app.backend.next_app.connect", return_value=self.conn), patch(
            "app.backend.next_app.next_auth_effective_enabled", return_value=False
        ), patch("app.backend.next_app.require_next_permission", return_value=self.actor), patch(
            "app.backend.next_app.release_contribution_enabled", return_value=True
        ), patch(
            "app.backend.next_app.movie_entity", return_value={"id": MOVIE_ID, "title": "Annihilation", "year": 2018, "metadata": {}}
        ), patch(
            "app.backend.next_app.actor_can_edit_visible_movie", return_value=True
        ), patch(
            "app.backend.next_app.release_technical_contribution_payload", return_value=BUILT_PAYLOAD
        ) as builder, patch(
            "app.backend.next_app.queue_release_contribution_job", return_value={"id": "job-1"}
        ):
            response = self._post({"id": MOVIE_ID, "scannedBarcode": "5051890319025", "releaseCandidate": CANDIDATE})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(builder.call_args.kwargs["film"], {"title": "Annihilation", "year": 2018})

    # ---- the rejections ------------------------------------------------

    def test_a_selection_is_refused_when_the_gate_is_off(self):
        """Owner setting or the user preference off: a 403 the client reads as
        'selections off', not a failure."""
        response = self._run(
            lambda: self._post({"id": MOVIE_ID, "scannedBarcode": "5051890319025", "releaseCandidate": CANDIDATE}),
            enabled=False,
        )
        self.assertEqual(response.status_code, 403)

    def test_a_film_not_yet_synced_is_a_404_the_client_retries(self):
        """iOS calls after the sync ack; if the mutation has not landed here yet
        the id does not resolve, and a 404 tells it to retry -- not to give up."""
        response = self._run(
            lambda: self._post({"id": MOVIE_ID, "scannedBarcode": "5051890319025", "releaseCandidate": CANDIDATE}),
            movie=None,
        )
        self.assertEqual(response.status_code, 404)

    def test_a_thin_selection_is_refused_before_it_is_queued(self):
        """The substance floor: a candidate with nothing but a title and a
        barcode is a lookup, not a contribution. The builder returns {} and the
        route refuses rather than cost a moderator a review."""
        with patch("app.backend.next_app.queue_release_contribution_job") as queue:
            response = self._run(
                lambda: self._post({"id": MOVIE_ID, "scannedBarcode": "5051890319025", "releaseCandidate": {"releaseRef": "mv:release:thin"}}),
                payload={},
            )
            self.assertEqual(response.status_code, 422)
            queue.assert_not_called()

    def test_a_missing_candidate_is_a_400(self):
        response = self._run(lambda: self._post({"id": MOVIE_ID, "scannedBarcode": "5051890319025"}))
        self.assertEqual(response.status_code, 400)

    def test_a_non_object_body_is_a_400(self):
        response = self._run(lambda: self.client.post(ROUTE, json=[1, 2, 3]))
        self.assertEqual(response.status_code, 400)

    def test_a_missing_id_is_refused(self):
        """No `id` means no film to bind the selection to."""
        response = self._run(lambda: self._post({"scannedBarcode": "5051890319025", "releaseCandidate": CANDIDATE}))
        self.assertIn(response.status_code, (400, 404))


class _ConnContext:
    """A stand-in connection usable as a `with connect() as conn` context.

    The route never touches the connection directly -- every collaborator that
    would is patched -- so it only has to be a working context manager.
    """

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
