"""A MovieVault v2 poster URL must survive the server-side image allow-list.

`media_asset_public_url` (DiscVault #637) is one place a poster value becomes a
client URL; `server_usable_image` is the other. It gates every poster that
reaches a client through the *metadata fallback* -- `metadata.poster_url` and
its siblings -- via `first_usable_image`. A provisional fan-submitted poster
arrives there as a `/api/next/movievault-v2/posters/<id>` route (there is no
suppression path: the poster must be shown, per
`docs/projects/discvault/changes/2026-08-16-movievault-v2-fanart-poster.md` §5
and `docs/projects/discvault/specs/movievault-release-details-posters.md` §7).

The server allow-list accepted only `http(s)://` and `/api/next/media/`, so it
stripped that route to `""` and the PWA rendered a blank tile -- even though the
two client-side `usableImage` twins (next_views_ui.py, next_views_collection.py)
already accept the prefix. These tests pin the helper, the metadata-fallback
projections that feed clients through it, and the parity that was the root
cause: the server allow-list and the JS twins must accept the same prefixes.
"""

from __future__ import annotations

import os
import re
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app


ASSET_ID = "3f1d2c7e-5a4b-4c3d-9e8f-1a2b3c4d5e6f"
POSTER_ROUTE = f"/api/next/movievault-v2/posters/{ASSET_ID}"


class ServerUsableImageTests(unittest.TestCase):
    def test_movievault_v2_poster_route_passes_through_unchanged(self):
        self.assertEqual(next_app.server_usable_image(POSTER_ROUTE), POSTER_ROUTE)

    def test_generic_media_route_still_passes_through(self):
        route = f"/api/next/media/assets/{ASSET_ID}"
        self.assertEqual(next_app.server_usable_image(route), route)

    def test_absolute_urls_still_pass_through(self):
        self.assertEqual(
            next_app.server_usable_image("https://movievault.example/p.jpg"),
            "https://movievault.example/p.jpg",
        )
        self.assertEqual(
            next_app.server_usable_image("http://movievault.example/p.jpg"),
            "http://movievault.example/p.jpg",
        )

    def test_genuinely_unusable_values_still_return_empty(self):
        for value in (
            None,
            "",
            "not-a-url",
            "posters/godfather.jpg",
            "/api/next/other/thing",
            "ftp://example/p.jpg",
            # Guard against a too-loose prefix: a look-alike path that is not the
            # poster route must not slip through.
            "/api/next/movievault-v2/backdrops/1",
        ):
            with self.subTest(value=value):
                self.assertEqual(next_app.server_usable_image(value), "")

    def test_first_usable_image_selects_the_poster_route_from_metadata(self):
        """`first_usable_image` is what the metadata-fallback projections call;
        it must now accept the provisional poster route rather than skip it."""
        self.assertEqual(
            next_app.first_usable_image(None, "", POSTER_ROUTE),
            POSTER_ROUTE,
        )


class MetadataFallbackProjectionTests(unittest.TestCase):
    """A poster that lives only in `metadata.poster_url` as the provisional
    MovieVault route must survive every projection that feeds it to a client
    through the metadata fallback."""

    def test_with_preview_media_urls_keeps_metadata_poster_route(self):
        data = next_app.with_preview_media_urls(
            {
                "id": "movie-1",
                # No media_asset columns: the value can only come from metadata.
                "metadata": {"poster_url": POSTER_ROUTE},
            }
        )
        self.assertEqual(data["poster_url"], POSTER_ROUTE)

    def test_movie_detail_image_keeps_metadata_poster_route(self):
        url = next_app.movie_detail_image([], {"poster_url": POSTER_ROUTE}, "poster")
        self.assertEqual(url, POSTER_ROUTE)

    def test_container_detail_image_keeps_metadata_poster_route(self):
        url = next_app.container_detail_image([], {"poster_url": POSTER_ROUTE}, "poster")
        self.assertEqual(url, POSTER_ROUTE)

    def test_fold_container_member_poster_keeps_metadata_poster_route(self):
        # The borrowed-cover fold reads the poster only from the joined
        # `member_poster_metadata_url` column and emits `member_poster_url`.
        row = next_app.fold_container_member_poster(
            {
                "id": "member-1",
                "member_poster_metadata_url": POSTER_ROUTE,
            }
        )
        self.assertEqual(row.get("member_poster_url"), POSTER_ROUTE)


class ServerJsAllowListParityTests(unittest.TestCase):
    """The root cause: the server allow-list drifted from its JS `usableImage`
    twins. Pin them to the same set of accepted prefixes so a future edit to one
    side that forgets the other fails here instead of on a user's blank tile."""

    EXPECTED_PREFIXES = frozenset(
        {
            "http://",
            "https://",
            "/api/next/media/",
            "/api/next/movievault-v2/posters/",
        }
    )

    def _server_accepted_prefixes(self):
        accepted = set()
        for prefix in self.EXPECTED_PREFIXES:
            sample = prefix if prefix.endswith("/") else prefix + "example/p.jpg"
            if next_app.server_usable_image(sample):
                accepted.add(prefix)
        return accepted

    def _js_accepted_prefixes(self, source_path):
        text = open(source_path, encoding="utf-8").read()
        found = set()
        for prefix in self.EXPECTED_PREFIXES:
            # The twins spell the prefix as a string literal in a startsWith call.
            if re.search(r'startsWith\(\s*"' + re.escape(prefix) + r'"', text):
                found.add(prefix)
        return found

    def test_server_accepts_the_expected_prefix_set(self):
        self.assertEqual(self._server_accepted_prefixes(), set(self.EXPECTED_PREFIXES))

    def test_server_matches_next_views_ui_twin(self):
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(next_app.__file__)))
        js_prefixes = self._js_accepted_prefixes(
            os.path.join(backend_dir, "backend", "next_views_ui.py")
        )
        self.assertEqual(js_prefixes, self._server_accepted_prefixes())

    def test_server_matches_next_views_collection_twin(self):
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(next_app.__file__)))
        js_prefixes = self._js_accepted_prefixes(
            os.path.join(backend_dir, "backend", "next_views_collection.py")
        )
        self.assertEqual(js_prefixes, self._server_accepted_prefixes())


if __name__ == "__main__":
    unittest.main()
