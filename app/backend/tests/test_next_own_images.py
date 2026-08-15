"""The rules around the user's own images that need no database.

The interesting half of this feature is what happens when two devices touch the
same set, and that is covered against a real database in
`test_next_own_images_postgres.py`. What is pinned here is the handful of
decisions a fake connection can prove: how a label is normalised, how the
ceiling reads its setting, and -- the one worth a test on its own -- that the
wire shape carries the fields a client needs to avoid re-downloading a
photograph it already holds.
"""

import os
import sys
import unittest
from unittest.mock import patch


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


class ScanLabelTests(unittest.TestCase):
    def test_a_label_is_lower_cased_and_trimmed(self):
        self.assertEqual(next_app.clean_scan_label("  Front "), "front")
        self.assertEqual(next_app.clean_scan_label("SPINE"), "spine")

    def test_an_unknown_label_survives_rather_than_being_rejected(self):
        """`SCAN_LABELS` is what the picker offers, not a whitelist.

        A client sending a word this build has not heard of should end up with a
        photograph carrying an odd caption. Refusing it would lose the one image
        in the app that no source can supply a second time -- over a display
        string.
        """
        self.assertEqual(next_app.clean_scan_label("obi-strip"), "obi-strip")

    def test_a_long_label_is_truncated_not_refused(self):
        self.assertEqual(
            len(next_app.clean_scan_label("x" * 400)), next_app.MAX_SCAN_LABEL_LENGTH
        )

    def test_no_label_is_the_empty_string(self):
        self.assertEqual(next_app.clean_scan_label(None), "")


class ScanLimitTests(unittest.TestCase):
    def _limit(self, stored):
        with patch.object(next_app, "app_setting_value", return_value=stored):
            return next_app.scan_limit_per_entity(object())

    def test_the_default_is_ten(self):
        self.assertEqual(self._limit(next_app.DEFAULT_SCAN_LIMIT_PER_ENTITY), 10)

    def test_a_setting_that_is_not_a_number_falls_back_to_the_default(self):
        """A malformed setting must not read as zero.

        Zero is a meaningful value here -- it turns the feature off -- so
        inheriting it from a typo would silently stop an instance accepting
        images with nothing to say why.
        """
        self.assertEqual(self._limit("plenty"), 10)
        self.assertEqual(self._limit(None), 10)

    def test_a_negative_setting_reads_as_zero(self):
        self.assertEqual(self._limit(-4), 0)

    def test_the_setting_is_clamped(self):
        self.assertEqual(self._limit(10_000), next_app.MAX_SCAN_LIMIT_PER_ENTITY)


class ScanKindTests(unittest.TestCase):
    def test_a_scan_is_its_own_media_kind(self):
        """Not a poster with a flag.

        A poster is a candidate for the shelf cover and competes for that slot;
        a scan is a record of the object and never enters that competition.
        Sharing the kind would make every "list the posters" query answer with
        somebody's photograph of a spine.
        """
        self.assertNotIn(next_app.SCAN_MEDIA_KIND, next_app.MOVIE_ARTWORK_KINDS)
        self.assertIn(next_app.SCAN_MEDIA_KIND, next_app.UPLOADABLE_ARTWORK_KINDS)
        self.assertTrue(next_app.MOVIE_ARTWORK_KINDS <= next_app.UPLOADABLE_ARTWORK_KINDS)

    def test_every_entity_that_can_hold_images_has_a_sync_entity_type(self):
        """A missing entry is not an error anywhere -- `emit_entity_scan_change`
        returns 0 and the change is simply never published. The images would
        upload, appear in the PWA, and reach no other device."""
        self.assertEqual(
            sorted(next_app.SCAN_SYNC_ENTITY_TYPES),
            ["container", "movie", "series"],
        )


class ScanRouteRegistrationTests(unittest.TestCase):
    def test_all_three_entities_expose_the_same_seven_routes(self):
        """Registered in a loop, so a missing one is a typo in a name rather
        than an absent block of code -- which is exactly the kind of gap that
        shows up as "images work on films and not on box sets"."""
        rules: dict[str, set[str]] = {}
        # Merged rather than assigned: each method is its own `Rule` object with
        # the same path, so keeping the last one silently drops the others.
        for rule in next_app.app.url_map.iter_rules():
            rules.setdefault(rule.rule, set()).update(rule.methods or set())
        for segment in ("movies", "containers", "series"):
            base = f"/api/next/{segment}/<entity_id>/images"
            self.assertIn(base, rules, segment)
            self.assertLessEqual({"GET", "POST"}, rules[base])
            self.assertLessEqual({"PATCH", "DELETE"}, rules[f"{base}/<media_id>"])
            for action in ("primary", "hide", "unhide"):
                self.assertIn(f"{base}/<media_id>/{action}", rules, f"{segment}:{action}")

    def test_the_poster_routes_are_untouched(self):
        """Own images got a namespace of their own precisely so the artwork
        routes -- which write `{kind}_url` and `{kind}_locked` back onto the
        entity -- keep behaving as they did."""
        rules = {rule.rule for rule in next_app.app.url_map.iter_rules()}
        self.assertIn("/api/next/movies/<movie_id>/media/upload", rules)
        self.assertIn("/api/next/movies/<movie_id>/media/primary", rules)


class ScanSyncRowTests(unittest.TestCase):
    def test_the_wire_row_lets_a_client_skip_a_download_it_already_has(self):
        """`sha256` and `sizeBytes` are not decoration.

        These images live on phones. Without a content hash on the wire a client
        cannot tell "I already hold these bytes" from "I must fetch them", and
        every reconciliation re-downloads the whole set over mobile data.
        """
        row = {
            "id": "11111111-1111-1111-1111-111111111111",
            "client_id": "device-token",
            "label": "spine",
            "url": "/api/next/media/assets/11111111-1111-1111-1111-111111111111",
            "sha256": "abc",
            "size_bytes": 4096,
            "width": 800,
            "height": 1200,
            "content_type": "image/jpeg",
            "is_primary": True,
            "sort_order": 2,
            "hidden_at": None,
            "deleted_at": None,
            "created_at": None,
            "updated_at": None,
        }
        with patch.object(next_app, "entity_scan_entities", return_value=[row]):
            published = next_app.entity_scan_sync_rows(object(), "movie", "movie-id", revision=7)

        self.assertEqual(len(published), 1)
        entry = published[0]
        self.assertEqual(entry["sha256"], "abc")
        self.assertEqual(entry["sizeBytes"], 4096)
        self.assertEqual(entry["clientId"], "device-token")
        self.assertEqual(entry["revision"], 7)
        self.assertFalse(entry["deleted"])
        self.assertTrue(entry["isPrimary"])

    def test_the_published_url_stays_a_server_relative_path(self):
        """Absolutising here would bake whichever hostname this request arrived
        on into a value several clients share, and it survives exactly until the
        server is reached by another name. Resolving belongs at the edge that
        knows which server it is talking to."""
        row = {
            "id": "22222222-2222-2222-2222-222222222222",
            "url": "/api/next/media/assets/22222222-2222-2222-2222-222222222222",
            "sha256": "d",
            "hidden_at": None,
            "deleted_at": None,
        }
        with patch.object(next_app, "entity_scan_entities", return_value=[row]):
            published = next_app.entity_scan_sync_rows(object(), "movie", "movie-id")
        self.assertTrue(published[0]["url"].startswith("/api/next/media/assets/"))

    def test_a_deleted_image_travels_as_a_tombstone(self):
        """A delete that travels as an absence cannot be told apart from a
        change still in flight, and the receiver puts the image straight back."""
        row = {
            "id": "33333333-3333-3333-3333-333333333333",
            "url": "",
            "sha256": "e",
            "hidden_at": None,
            "deleted_at": "2026-08-14T10:00:00Z",
        }
        with patch.object(next_app, "entity_scan_entities", return_value=[row]):
            published = next_app.entity_scan_sync_rows(object(), "movie", "movie-id")
        self.assertTrue(published[0]["deleted"])
        self.assertEqual(published[0]["deletedAt"], "2026-08-14T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
