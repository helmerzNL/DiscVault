"""The pinned-poster marker on the sync wire (sync-contract 1.29, §4.8b).

`poster_url` carries two incompatible assertions in one field — "this is what
the provider supplied" and "this is what I chose" — which want opposite
handling. §4.8b separates them with a marker beside the URL, so a client can
apply a pinned poster *over* an existing one while a provider-supplied poster
still overwrites nothing.

The fact itself is not new here. The server has recorded a pinned primary as
`{kind}_locked` since long before §4.8b existed, and `movies.metadata` is
published wholesale, so it was already on the wire under a name no client knew
to read. These tests pin the translation in both directions and, above all, the
two rules that fail silently when they are wrong:

- a marker is never published without a usable URL behind it (the §4.8b
  pitfall — a client that believes a hollow marker stops falling back);
- an absent write key is "no opinion", not an unpin, so a build predating the
  section cannot unpin a collection by staying quiet.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


class PinMarkerPublicationTests(unittest.TestCase):
    """`movie_pin_markers` — what the server puts on the wire."""

    def test_a_locked_poster_with_a_url_publishes_the_marker(self):
        markers = next_app.movie_pin_markers(
            {"poster_locked": True, "poster_url": "https://example.test/p.jpg"}
        )
        self.assertEqual(markers, {"poster_is_pinned": True})

    def test_a_server_hosted_asset_path_counts_as_a_usable_url(self):
        """The common case: the PWA pins an uploaded or proxied asset.

        `media_asset_public_url` returns a server-relative path for those, and
        it is the value that actually reaches clients — treating it as unusable
        would suppress the marker on nearly every real pin.
        """
        markers = next_app.movie_pin_markers(
            {"poster_locked": True, "poster_url": "/api/next/media/assets/abc"}
        )
        self.assertEqual(markers, {"poster_is_pinned": True})

    def test_an_unpinned_movie_carries_no_key_at_all(self):
        """Absent means "not pinned" in §4.8b, explicitly not "unknown"."""
        self.assertEqual(next_app.movie_pin_markers({"poster_url": "https://x.test/p.jpg"}), {})
        self.assertEqual(next_app.movie_pin_markers({"poster_locked": False}), {})

    def test_a_lock_without_a_usable_url_is_not_published(self):
        """The §4.8b pitfall, enforced at the producer.

        `media_asset_public_url` returns "" for an asset it cannot address, so
        this combination is reachable rather than hypothetical. A marker with
        nothing behind it is worse than no marker.
        """
        self.assertEqual(next_app.movie_pin_markers({"poster_locked": True}), {})
        self.assertEqual(next_app.movie_pin_markers({"poster_locked": True, "poster_url": ""}), {})

    def test_a_hostless_reference_does_not_count_as_a_url(self):
        """`server_usable_image` accepts an absolute URL or a media path, nothing else.

        A bare filename left by an old import is not something a client can
        load, and a marker pointing at one strands the field.
        """
        self.assertEqual(
            next_app.movie_pin_markers({"poster_locked": True, "poster_url": "poster.jpg"}),
            {},
        )

    def test_the_two_kinds_are_independent(self):
        markers = next_app.movie_pin_markers(
            {
                "poster_locked": True,
                "poster_url": "https://example.test/p.jpg",
                "backdrop_locked": True,
                "backdrop_url": "https://example.test/b.jpg",
            }
        )
        self.assertEqual(
            markers, {"poster_is_pinned": True, "backdrop_is_pinned": True}
        )

    def test_a_string_lock_value_is_read_as_a_flag(self):
        """Stored locks are not uniformly booleans — the refresh guards read
        `poster_locked` with a `('true','1','yes')` SQL comparison, so the
        publication path has to agree with them."""
        markers = next_app.movie_pin_markers(
            {"poster_locked": "true", "poster_url": "https://example.test/p.jpg"}
        )
        self.assertEqual(markers, {"poster_is_pinned": True})

    def test_non_dict_metadata_is_tolerated(self):
        self.assertEqual(next_app.movie_pin_markers(None), {})
        self.assertEqual(next_app.movie_pin_markers("nonsense"), {})


class PinMarkerAttachTests(unittest.TestCase):
    """`attach_movie_pin_markers` — folding the marker into a payload row."""

    def test_the_marker_joins_the_published_metadata(self):
        rows = [
            {
                "id": "1",
                "metadata": {
                    "poster_locked": True,
                    "poster_url": "https://example.test/p.jpg",
                },
            }
        ]
        next_app.attach_movie_pin_markers(rows)
        self.assertTrue(rows[0]["metadata"]["poster_is_pinned"])
        # The stored truth stays beside it; this is a rename on the wire, not a
        # replacement, and the PWA still reads `poster_locked`.
        self.assertTrue(rows[0]["metadata"]["poster_locked"])

    def test_an_unpinned_row_is_left_exactly_as_it_was(self):
        metadata = {"poster_url": "https://example.test/p.jpg"}
        rows = [{"id": "1", "metadata": metadata}]
        next_app.attach_movie_pin_markers(rows)
        self.assertNotIn("poster_is_pinned", rows[0]["metadata"])


class PinMarkerWriteTests(unittest.TestCase):
    """`movie_payload_fields` — what the server accepts from a client."""

    def _metadata(self, payload):
        return next_app.movie_payload_fields(payload)["metadata"]

    def test_the_camel_case_write_key_lands_on_the_stored_key(self):
        metadata = self._metadata({"metadata": {"posterIsPinned": True}})
        self.assertIs(metadata["poster_locked"], True)

    def test_the_wire_spelling_never_reaches_storage(self):
        """`metadata` is merged into the column as-is, so an untranslated key
        would sit in `movies.metadata` looking authoritative while every reader
        of the pin went on consulting `poster_locked`."""
        metadata = self._metadata({"metadata": {"posterIsPinned": True}})
        self.assertNotIn("posterIsPinned", metadata)
        self.assertNotIn("poster_is_pinned", metadata)

    def test_the_published_read_key_is_accepted_and_translated_too(self):
        """A client that echoes back what it was sent must not create a second
        spelling of the same fact."""
        metadata = self._metadata({"metadata": {"poster_is_pinned": True}})
        self.assertIs(metadata["poster_locked"], True)
        self.assertNotIn("poster_is_pinned", metadata)

    def test_an_absent_key_is_no_opinion_rather_than_an_unpin(self):
        """The client metadata object is sparse (§4.8) and the upsert merges
        with `||`. A build predating §4.8b sends no key, and must not unpin a
        whole collection by staying quiet."""
        metadata = self._metadata({"metadata": {"distributor": "Arrow"}})
        self.assertNotIn("poster_locked", metadata)

    def test_an_explicit_false_is_a_real_unpin(self):
        metadata = self._metadata({"metadata": {"posterIsPinned": False}})
        self.assertIs(metadata["poster_locked"], False)

    def test_the_marker_is_accepted_at_the_top_level_as_well(self):
        """Tolerant in, exact out: the contract puts it inside `metadata`, but
        the poster URL is accepted in both places already."""
        metadata = self._metadata({"posterIsPinned": True})
        self.assertIs(metadata["poster_locked"], True)
        self.assertNotIn("posterIsPinned", metadata)

    def test_backdrop_travels_on_the_same_rules(self):
        metadata = self._metadata({"metadata": {"backdropIsPinned": True}})
        self.assertIs(metadata["backdrop_locked"], True)
        self.assertNotIn("backdropIsPinned", metadata)

    def test_a_pin_without_a_resent_url_is_still_accepted(self):
        """The pitfall guard belongs at publication, not here.

        A client may pin a poster whose URL it never changed, and §4.8's sparse
        metadata rule means it would omit `poster_url`. Refusing the write would
        drop a legitimate pin; `movie_pin_markers` withholds the marker instead
        if no usable URL turns out to back it.
        """
        metadata = self._metadata({"metadata": {"posterIsPinned": True}})
        self.assertIs(metadata["poster_locked"], True)


class PinMarkerRoundTripTests(unittest.TestCase):
    """Push then publish, the way a device actually experiences it."""

    def test_a_pushed_pin_comes_back_under_the_contract_name(self):
        stored = next_app.movie_payload_fields(
            {
                "posterUrl": "https://example.test/chosen.jpg",
                "metadata": {"posterIsPinned": True},
            }
        )["metadata"]
        rows = [{"id": "1", "metadata": stored}]
        next_app.attach_movie_pin_markers(rows)
        self.assertTrue(rows[0]["metadata"]["poster_is_pinned"])
        self.assertEqual(
            rows[0]["metadata"]["poster_url"], "https://example.test/chosen.jpg"
        )

    def test_an_unpin_round_trips_as_an_absent_key(self):
        stored = next_app.movie_payload_fields(
            {
                "posterUrl": "https://example.test/chosen.jpg",
                "metadata": {"posterIsPinned": False},
            }
        )["metadata"]
        rows = [{"id": "1", "metadata": stored}]
        next_app.attach_movie_pin_markers(rows)
        self.assertNotIn("poster_is_pinned", rows[0]["metadata"])


if __name__ == "__main__":
    unittest.main()
