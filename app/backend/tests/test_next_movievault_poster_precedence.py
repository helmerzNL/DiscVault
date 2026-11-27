"""A film's poster comes from MovieVault before TMDB, and a release without one borrows another edition's.

The artwork rule (App-Guidance `projects/discvault/specs/artwork-precedence.md`) settled this for the
clients on 2026-11-17 and left the server unchecked. On a real server the four members of a box set kept
the cinema posters TMDB had stored, although MovieVault held a cover for the film: the MovieVault answer
carried the cover as the instance's own address (`/api/next/movievault-v2/posters/<id>`), and the media
pipeline only follows absolute `http(s)` addresses, so the cover never became a media update at all.

What these tests pin, without a database (the same rules were also run against PostgreSQL 16, see the
change that added them):

* the one relative address the media pipeline may follow, and that no other relative address passes;
* the merge, in the pipeline's own order, puts the MovieVault cover first and keeps TMDB's as an option;
* a release without a cover borrows one only for `movie_details`, from a copy of the answer, and a
  failed lookup never fails the refresh.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import uuid
from contextlib import contextmanager
from typing import Any
from unittest import mock


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_metadata as metadata
from app.backend import next_movievault_v2_artwork as artwork

_PLUGIN_PATH = os.path.join(repo_root, "app", "backend", "next_plugins", "movievault_v2", "plugin.py")
_spec = importlib.util.spec_from_file_location("movievault_v2_plugin_poster_test", _PLUGIN_PATH)
assert _spec is not None and _spec.loader is not None
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

MEDIA_ID = "4f1b9a52-2f0f-4c52-9a3a-0a6e9e0f6a11"
LOCAL = f"/api/next/movievault-v2/posters/{MEDIA_ID}"
TMDB_POSTER = "https://image.tmdb.org/t/p/original/raayaQ9WTUGolsYHPWc2bGonu58.jpg"
RELEASE_ID = "5c8b5b9e-3323-4d82-821b-f25f29dabcd2"


class LocalPosterAddressTests(unittest.TestCase):
    def test_a_cached_movievault_cover_is_an_image(self) -> None:
        self.assertEqual(metadata.image_url_options(LOCAL), [LOCAL])

    def test_absolute_addresses_still_pass(self) -> None:
        self.assertEqual(metadata.image_url_options(TMDB_POSTER), [TMDB_POSTER])

    def test_no_other_relative_address_is_an_image(self) -> None:
        for value in (
            "/some/other/path.jpg",
            "/api/next/movievault-v2/posters/not-a-uuid",
            f"/api/next/movievault-v2/posters/{MEDIA_ID}/extra",
            f"/api/next/media/assets/{MEDIA_ID}",
            f"https-less/api/next/movievault-v2/posters/{MEDIA_ID}",
            "",
        ):
            self.assertEqual(metadata.image_url_options(value), [], value)

    def test_the_media_asset_is_read_from_the_address(self) -> None:
        self.assertEqual(artwork.media_asset_id_from_local_poster_url(LOCAL), uuid.UUID(MEDIA_ID))
        self.assertIsNone(artwork.media_asset_id_from_local_poster_url(TMDB_POSTER))
        self.assertIsNone(artwork.media_asset_id_from_local_poster_url(None))
        self.assertEqual(artwork.local_poster_url(MEDIA_ID), LOCAL)


def merged(movievault: dict[str, Any] | None, tmdb: dict[str, Any] | None) -> dict[str, Any]:
    """The merge of one MovieVault and one TMDB answer, in the pipeline's order and with its policy."""
    results = []
    if movievault is not None:
        results.append(
            metadata.metadata_source_policy_result(
                metadata.canonicalize_plugin_result("movievault_v2", "movie_details", {"status": "hit", **movievault})
            )
        )
    if tmdb is not None:
        results.append(
            metadata.metadata_source_policy_result(
                metadata.canonicalize_plugin_result("tmdb", "movie_details", {"status": "hit", **tmdb})
            )
        )
    return metadata.merge_metadata_results(
        current={"format": "4K UHD", "metadata": {"poster_url": TMDB_POSTER}},
        technical_current={},
        results=results,
        overwrite_enabled=False,
        target_format=metadata.normalize_media_format("4K UHD"),
    )


def movievault_answer(poster_url: str | None) -> dict[str, Any]:
    record = {
        "releaseId": RELEASE_ID,
        "filmId": str(uuid.uuid4()),
        "canonicalTitle": "Rocky",
        "releaseTitle": "Rocky 4K Blu-ray (SteelBook) (Italy)",
        "format": "4K UHD",
    }
    if poster_url:
        record["posterUrl"] = poster_url
        record["posterStatus"] = "ready"
    return plugin._release(record)


TMDB_ANSWER = {"movie": {"title": "Rocky"}, "posterUrl": TMDB_POSTER, "posters": [TMDB_POSTER]}


class MergePrecedenceTests(unittest.TestCase):
    def test_the_movievault_cover_is_the_primary_poster_and_tmdbs_stays_an_option(self) -> None:
        out = merged(movievault_answer(LOCAL), TMDB_ANSWER)
        poster = out["mediaUpdates"]["poster"]
        self.assertEqual(poster["sourceUrl"], LOCAL)
        self.assertEqual(poster["providerId"], "movievault_v2")
        self.assertIn(TMDB_POSTER, poster["options"])

    def test_without_a_movievault_cover_tmdbs_poster_is_used(self) -> None:
        out = merged(movievault_answer(None), TMDB_ANSWER)
        self.assertEqual(out["mediaUpdates"]["poster"]["sourceUrl"], TMDB_POSTER)

    def test_a_movievault_cover_alone_is_enough(self) -> None:
        out = merged(movievault_answer(LOCAL), None)
        self.assertEqual(out["mediaUpdates"]["poster"]["sourceUrl"], LOCAL)

    def test_nothing_found_proposes_no_artwork_so_nothing_is_cleared(self) -> None:
        out = merged(movievault_answer(None), None)
        self.assertNotIn("poster", out["mediaUpdates"])


class FilmPosterFallbackTests(unittest.TestCase):
    def execution(self, **result: Any) -> dict[str, Any]:
        return {"status": "ok", "result": {"status": "hit", "releaseId": RELEASE_ID, "posterUrl": None, **result}}

    class Conn:
        @contextmanager
        def transaction(self):
            yield

    def borrowed(self) -> dict[str, Any]:
        return {"mediaAssetId": MEDIA_ID, "releaseId": "99999999-0000-0000-0000-000000000009", "source": "film", "url": LOCAL}

    def test_a_release_without_a_cover_gets_the_borrowed_one_on_a_copy(self) -> None:
        original = self.execution()
        with mock.patch.object(metadata, "film_poster_for_release", return_value=self.borrowed()):
            out = metadata.with_film_poster_fallback(self.Conn(), "movievault_v2", "movie_details", original)
        self.assertEqual(out["result"]["posterUrl"], LOCAL)
        self.assertEqual(out["result"]["posterSource"], "film")
        self.assertIsNone(original["result"]["posterUrl"], "a cached execution is shared and must not be edited")

    def test_only_movie_details_is_asked(self) -> None:
        with mock.patch.object(metadata, "film_poster_for_release", return_value=self.borrowed()) as asked:
            for entrypoint in ("search_barcode", "search_title", "box_set_candidates"):
                out = metadata.with_film_poster_fallback(self.Conn(), "movievault_v2", entrypoint, self.execution())
                self.assertIsNone(out["result"]["posterUrl"], entrypoint)
        asked.assert_not_called()

    def test_another_plugin_is_untouched(self) -> None:
        with mock.patch.object(metadata, "film_poster_for_release", return_value=self.borrowed()) as asked:
            out = metadata.with_film_poster_fallback(self.Conn(), "tmdb", "movie_details", self.execution())
        self.assertIsNone(out["result"]["posterUrl"])
        asked.assert_not_called()

    def test_a_cover_the_release_already_has_is_kept(self) -> None:
        with mock.patch.object(metadata, "film_poster_for_release", return_value=self.borrowed()) as asked:
            out = metadata.with_film_poster_fallback(
                self.Conn(), "movievault_v2", "movie_details", self.execution(posterUrl="/api/next/movievault-v2/posters/own")
            )
        self.assertEqual(out["result"]["posterUrl"], "/api/next/movievault-v2/posters/own")
        asked.assert_not_called()

    def test_a_miss_or_a_pending_cover_changes_nothing(self) -> None:
        with mock.patch.object(metadata, "film_poster_for_release", return_value=None):
            out = metadata.with_film_poster_fallback(self.Conn(), "movievault_v2", "movie_details", self.execution())
        self.assertIsNone(out["result"]["posterUrl"])

    def test_a_failed_lookup_never_fails_the_refresh(self) -> None:
        original = self.execution()
        with mock.patch.object(metadata, "film_poster_for_release", side_effect=RuntimeError("catalogue copy mid-sync")):
            out = metadata.with_film_poster_fallback(self.Conn(), "movievault_v2", "movie_details", original)
        self.assertIs(out, original)

    def test_an_answer_that_names_no_release_is_left_alone(self) -> None:
        execution = {"status": "ok", "result": {"status": "hit", "posterUrl": None}}
        with mock.patch.object(metadata, "film_poster_for_release") as asked:
            out = metadata.with_film_poster_fallback(self.Conn(), "movievault_v2", "movie_details", execution)
        self.assertIs(out, execution)
        asked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
