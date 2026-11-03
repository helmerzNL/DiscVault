"""The series screen's season correction: every release of a series at once.

Same posture as the release sheet's routes (`test_next_movievault_correction_
routes.py`): what matters is what the endpoints refuse to take from the caller.
The diff is recomputed per release on the server, a release the actor may not
change is left out rather than failing the series, and each release travels as
its own seasons-only correction.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend.next_app import create_app
    from app.backend.next_common import NextApiError
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    create_app = None
    NextApiError = None

SERIES_ID = "00000000-0000-0000-0000-0000000000d1"
FIRST = "00000000-0000-0000-0000-0000000000a1"
SECOND = "00000000-0000-0000-0000-0000000000a2"
THIRD = "00000000-0000-0000-0000-0000000000a3"
TARGET = {"entityType": "release", "entityId": "c0000000-0000-0000-0000-000000000001", "baseRevision": 7}


def _item(movie_id, state, proposed=None):
    change = (
        {"field": "seasons", "expected": [], "proposed": proposed}
        if state == "changed"
        else None
    )
    return {
        "movieId": movie_id,
        "title": f"Box {movie_id[-2:]}",
        "state": state,
        "localSeasons": proposed or [],
        "catalogueSeasons": [] if change else None,
        "withheld": "needs_live_catalogue" if state == "withheld" else None,
        "change": change,
        "target": TARGET if state != "not_in_catalogue" else None,
        "comparedAgainst": "catalogue",
        "lastContribution": None,
    }


ITEMS = {
    FIRST: _item(FIRST, "changed", [1]),
    SECOND: _item(SECOND, "in_sync"),
    THIRD: _item(THIRD, "changed", [3]),
}


@unittest.skipIf(create_app is None, "Flask is not installed in this test environment")
class SeriesSeasonRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.actor = {"id": "00000000-0000-0000-0000-0000000000c1", "permissions": ["collection.edit_all"]}
        self.conn = MagicMock()
        self.connect_context = MagicMock()
        self.connect_context.__enter__.return_value = self.conn
        self.connect_context.__exit__.return_value = False
        self.queued = []
        self.recorded = []

    def _patches(self, *, enabled=True, editable=(FIRST, SECOND, THIRD), items=None, series=True):
        items = items if items is not None else ITEMS

        def _record(conn, actor, entity, entity_id):
            if entity_id not in editable:
                raise NextApiError("Permission required: collection.edit_all", 403)
            return {"id": entity_id, "metadata": {}}, {}

        def _queue(conn, payload, *, actor=None, reason):
            self.queued.append((payload, reason))
            return {"id": f"job-{len(self.queued)}"}

        def _log(conn, **kwargs):
            self.recorded.append(kwargs)

        return (
            patch("app.backend.next_app.connect", return_value=self.connect_context),
            patch("app.backend.next_app.next_auth_effective_enabled", return_value=False),
            patch("app.backend.next_app.require_next_permission", return_value=self.actor),
            patch("app.backend.next_app.series_tables_available", return_value=True),
            patch("app.backend.next_app.series_entity", return_value={"id": SERIES_ID} if series else None),
            patch(
                "app.backend.next_app.series_disc_entities",
                return_value=[{"id": FIRST}, {"id": SECOND}, {"id": THIRD}],
            ),
            patch("app.backend.next_app.movie_entity", side_effect=lambda conn, uuid: {"id": str(uuid), "metadata": {}}),
            patch(
                "app.backend.next_app.actor_can_edit_visible_movie",
                side_effect=lambda conn, actor, movie: str(movie["id"]) in editable,
            ),
            patch("app.backend.next_app.field_correction_enabled", return_value=enabled),
            patch("app.backend.next_app.field_correction_gate", return_value={"owner": True, "user": enabled}),
            patch("app.backend.next_app.catalogue_synced_at", return_value="2026-11-03T10:00:00+00:00"),
            patch(
                "app.backend.next_app.series_season_item",
                side_effect=lambda conn, record, metadata, **kwargs: items[str(record["id"])],
            ),
            patch("app.backend.next_app.queue_field_correction_job", side_effect=_queue),
            patch("app.backend.next_app.record_contribution", side_effect=_log),
        )

    def _run(self, call, **kwargs):
        patches = self._patches(**kwargs)
        for item in patches:
            item.start()
        try:
            return call()
        finally:
            for item in patches:
                item.stop()

    def _submit(self, body, **kwargs):
        return self._run(
            lambda: self.client.post(
                "/api/next/movievault/contributions/series-seasons/submit", json=body
            ),
            **kwargs,
        )

    # ---- the overview --------------------------------------------------

    def test_the_overview_states_every_release_and_counts_what_can_be_sent(self):
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/series-seasons?seriesId={SERIES_ID}")
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([item["movieId"] for item in payload["releases"]], [FIRST, SECOND, THIRD])
        self.assertEqual(payload["changedCount"], 2)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["catalogueSyncedAt"], "2026-11-03T10:00:00+00:00")

    def test_a_release_the_actor_may_not_change_is_left_out_not_fatal(self):
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/series-seasons?seriesId={SERIES_ID}"),
            editable=(FIRST, THIRD),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["movieId"] for item in response.get_json()["releases"]], [FIRST, THIRD])

    def test_an_unknown_series_is_a_404(self):
        response = self._run(
            lambda: self.client.get(f"/api/next/movievault/contributions/series-seasons?seriesId={SERIES_ID}"),
            series=False,
        )
        self.assertEqual(response.status_code, 404)

    def test_a_series_id_is_required(self):
        response = self._run(lambda: self.client.get("/api/next/movievault/contributions/series-seasons"))
        self.assertEqual(response.status_code, 400)

    # ---- sending -------------------------------------------------------

    def test_every_differing_release_travels_as_its_own_seasons_only_correction(self):
        """Upstream reviews one record per correction; bundling the boxes would
        force one verdict on releases a moderator may judge apart."""
        response = self._submit({"seriesId": SERIES_ID})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["queued"], 2)
        self.assertEqual(len(self.queued), 2)
        for (correction, reason), proposed in zip(self.queued, ([1], [3])):
            self.assertEqual(reason, "series_season_correction")
            self.assertEqual(correction["target"], TARGET)
            self.assertEqual(
                correction["changes"], [{"field": "seasons", "expected": [], "proposed": proposed}]
            )
        self.assertEqual(len(self.recorded), 2)
        states = {item["movieId"]: item for item in payload["results"]}
        self.assertFalse(states[SECOND]["queued"])
        self.assertEqual(states[SECOND]["state"], "in_sync")

    def test_the_diff_is_never_taken_from_the_caller(self):
        """`expected` is the whole conflict check upstream; a client that sent
        its own could claim to have seen a catalogue it never saw."""
        self._submit(
            {
                "seriesId": SERIES_ID,
                "movieIds": [FIRST],
                "changes": [{"field": "seasons", "expected": [9], "proposed": [9]}],
            }
        )
        self.assertEqual(len(self.queued), 1)
        self.assertEqual(self.queued[0][0]["changes"][0]["proposed"], [1])

    def test_only_the_ticked_releases_are_sent(self):
        payload = self._submit({"seriesId": SERIES_ID, "movieIds": [THIRD]}).get_json()

        self.assertEqual(payload["queued"], 1)
        self.assertEqual(self.queued[0][0]["changes"][0]["proposed"], [3])
        self.assertEqual([item["movieId"] for item in payload["results"]], [THIRD])

    def test_a_ticked_release_outside_the_series_is_named(self):
        stranger = "00000000-0000-0000-0000-0000000000ff"
        payload = self._submit({"seriesId": SERIES_ID, "movieIds": [FIRST, stranger]}).get_json()

        states = {item["movieId"]: item["state"] for item in payload["results"]}
        self.assertEqual(states[stranger], "not_found")
        self.assertEqual(payload["queued"], 1)

    def test_a_ticked_release_with_nothing_to_send_says_so(self):
        items = {**ITEMS, FIRST: _item(FIRST, "withheld", [1])}
        payload = self._submit({"seriesId": SERIES_ID, "movieIds": [FIRST]}, items=items).get_json()

        self.assertEqual(payload["queued"], 0)
        self.assertEqual(payload["results"][0]["state"], "withheld")
        self.assertEqual(payload["results"][0]["withheld"], "needs_live_catalogue")

    def test_sending_needs_the_gate_open(self):
        response = self._submit({"seriesId": SERIES_ID}, enabled=False)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.queued, [])

    def test_movie_ids_must_be_a_list_of_ids(self):
        response = self._submit({"seriesId": SERIES_ID, "movieIds": FIRST})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
