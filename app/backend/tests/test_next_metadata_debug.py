import os
import sys
import unittest
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app as next_app_module
    from app.backend.next_app import movie_metadata_debug_entity
except ModuleNotFoundError as exc:  # Local minimal test environments may omit Flask.
    if exc.name != "flask":
        raise
    next_app_module = None
    movie_metadata_debug_entity = None


FETCHED_EVENT = {
    "createdAt": "2026-06-18T22:00:00+00:00",
    "metadata": {
        "sourceSummary": [
            {"pluginId": "tmdb", "name": "TMDb", "state": "hit", "reason": "provider returned data"},
            {"pluginId": "omdb", "name": "OMDb", "state": "retained_existing", "reason": "local values retained"},
        ],
        "providerResults": [
            {"pluginId": "tmdb", "sourceLabel": "TMDb", "sourceRef": "tmdb:603"},
            {"pluginId": "barcodelookup", "sourceLabel": "Barcode Lookup", "sourceRef": "bc:123"},
        ],
        "fieldDecisions": [
            {
                "target": "movie",
                "field": "title",
                "finalValue": "The Matrix",
                "candidates": [
                    {"pluginId": "tmdb", "sourceLabel": "TMDb", "sourceRef": "tmdb:603", "accepted": True, "winner": True, "value": "The Matrix"},
                    {"pluginId": "omdb", "sourceLabel": "OMDb", "sourceRef": "omdb:tt0133093", "accepted": False, "winner": False, "value": "Matrix"},
                ],
            },
            {
                "target": "metadata",
                "field": "overview",
                "finalValue": "A hacker learns the truth.",
                "candidates": [
                    {"pluginId": "tmdb", "sourceLabel": "TMDb", "sourceRef": "tmdb:603", "accepted": True, "winner": True, "value": "A hacker learns the truth."},
                ],
            },
        ],
    },
}

PUSHED_EVENT = {
    "createdAt": "2026-06-18T22:00:05+00:00",
    "metadata": {
        "receiverCount": 1,
        "receivers": [
            {
                "pluginId": "movievault_26",
                "name": "MovieVault",
                "status": "ok",
                "state": "delivered",
                "resultStatus": "accepted",
                "provider": "movievault_26",
                "reason": None,
            }
        ],
        "payloadSummary": {
            "entityType": "movie",
            "fieldCount": 3,
            "fields": ["originalTitle", "title", "year"],
            "sourceProviders": ["tmdb"],
        },
    },
}


@unittest.skipIf(movie_metadata_debug_entity is None, "Backend dependency missing")
class MovieMetadataDebugEntityTest(unittest.TestCase):
    def _run(self, fetched, pushed):
        def fake_latest(conn, movie_id, event_type):
            if event_type == "metadata.refresh_fetched":
                return fetched
            if event_type == "metadata.receiver_pushed":
                return pushed
            return None

        with mock.patch.object(next_app_module, "table_exists", return_value=True), mock.patch.object(
            next_app_module, "movie_metadata_debug_latest_audit_event", side_effect=fake_latest
        ):
            return movie_metadata_debug_entity(object(), "movie-1")

    def test_groups_fetched_per_plugin_with_accepted_markers(self):
        result = self._run(FETCHED_EVENT, PUSHED_EVENT)
        self.assertIsNotNone(result)
        self.assertEqual(result["fetchedAt"], "2026-06-18T22:00:00+00:00")

        fetched = {entry["pluginId"]: entry for entry in result["fetched"]}
        self.assertIn("tmdb", fetched)
        self.assertIn("omdb", fetched)
        self.assertIn("barcodelookup", fetched)

        tmdb = fetched["tmdb"]
        self.assertEqual(tmdb["state"], "hit")
        self.assertEqual(tmdb["sourceRef"], "tmdb:603")
        tmdb_fields = {f["field"]: f for f in tmdb["fields"]}
        self.assertEqual(tmdb_fields["title"]["value"], "The Matrix")
        self.assertTrue(tmdb_fields["title"]["accepted"])
        self.assertEqual(tmdb_fields["title"]["target"], "movie")
        self.assertTrue(tmdb_fields["overview"]["accepted"])

        omdb = fetched["omdb"]
        omdb_fields = {f["field"]: f for f in omdb["fields"]}
        self.assertFalse(omdb_fields["title"]["accepted"])
        self.assertEqual(omdb_fields["title"]["value"], "Matrix")

        # providerResults-only plugin appears with no field rows
        self.assertEqual(fetched["barcodelookup"]["fields"], [])

    def test_contributed_per_plugin_from_receiver_summary(self):
        result = self._run(FETCHED_EVENT, PUSHED_EVENT)
        self.assertEqual(len(result["contributed"]), 1)
        receiver = result["contributed"][0]
        self.assertEqual(receiver["pluginId"], "movievault_26")
        self.assertEqual(receiver["name"], "MovieVault")
        self.assertEqual(receiver["resultStatus"], "accepted")
        self.assertEqual(receiver["fields"], ["originalTitle", "title", "year"])
        self.assertEqual(receiver["sourceProviders"], ["tmdb"])

    def test_returns_none_when_no_events(self):
        self.assertIsNone(self._run(None, None))

    def test_handles_missing_receiver_event(self):
        result = self._run(FETCHED_EVENT, None)
        self.assertIsNotNone(result)
        self.assertEqual(result["contributed"], [])
        self.assertTrue(result["fetched"])


if __name__ == "__main__":
    unittest.main()
