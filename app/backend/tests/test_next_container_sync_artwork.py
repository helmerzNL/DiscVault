"""A box set's cover has to travel on the sync wire, resolved the same way.

The library and the container page have always resolved a container's artwork
server-side: its primary `entity_media` row first, its stored
`metadata.poster_url` second, and only then a cover borrowed from a member film.
The sync payload resolved none of it -- `containers` on the wire carried
`metadata` and nothing else.

Two symptoms came out of that single gap, and they look unrelated until you know
the cause:

* a box set whose cover lives only in `entity_media` arrived with **no** artwork
  at all, and its tile stayed empty;
* a box set whose stored `metadata.poster_url` points at a *member film's*
  poster -- an import that recorded the film's cover for the box -- arrived with
  the **wrong** artwork, while the PWA showed the right one. Not a different
  bug: the same missing resolution, seen through a row that happened to have
  something in it.

So the rule under test is the order, not merely the presence: the asset wins,
metadata is the fallback, and the resolved answer is written into the payload's
`metadata.poster_url` as well -- because §4b.9 tells clients to read the cover
there, and a build already installed reads that key and nothing else.

What is never folded in is the cover borrowed from a member film. §4b.9 makes a
pushed `metadata.poster_url` the container's own primary artwork, so shipping a
borrowed one would let a member's poster be laundered into the set's own on the
next push.
"""

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

try:
    from app.backend import next_app
except ModuleNotFoundError as exc:  # pragma: no cover - minimal environments
    if exc.name not in {"flask", "psycopg", "cbor2", "argon2", "jwt", "segno", "PIL"}:
        raise
    next_app = None


ASSET_URL = "https://cdn.example.test/box-set-cover.jpg"
METADATA_URL = "https://images.example.test/first-film-poster.jpg"


def row(**overrides):
    base = {
        "id": "c1",
        "title": "Back to the Future: The Complete Adventures",
        "metadata": {},
        "poster_asset_id": None,
        "poster_storage_backend": None,
        "poster_storage_key": None,
        "poster_source_url": None,
        "poster_provider_id": None,
        "backdrop_asset_id": None,
        "backdrop_storage_backend": None,
        "backdrop_storage_key": None,
        "backdrop_source_url": None,
        "backdrop_provider_id": None,
    }
    base.update(overrides)
    return base


def with_asset(**overrides):
    return row(
        poster_asset_id="a1",
        poster_storage_backend="remote",
        poster_storage_key=None,
        poster_source_url=ASSET_URL,
        poster_provider_id="movievault_26",
        **overrides,
    )


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class FoldContainerSyncArtworkTests(unittest.TestCase):
    def fold(self, value):
        return next_app.fold_container_sync_artwork(value)

    def test_own_asset_becomes_the_poster_url(self):
        folded = self.fold(with_asset())
        self.assertEqual(folded["poster_url"], ASSET_URL)

    def test_own_asset_wins_over_stored_metadata(self):
        # The reported symptom: metadata held a member film's poster, the PWA
        # preferred the asset and looked right, the wire did not resolve at all.
        folded = self.fold(with_asset(metadata={"poster_url": METADATA_URL}))
        self.assertEqual(folded["poster_url"], ASSET_URL)
        self.assertEqual(folded["metadata"]["poster_url"], ASSET_URL)

    def test_resolved_cover_is_written_into_metadata_for_installed_clients(self):
        # §4b.9 points clients at `metadata.poster_url`. A build already on a
        # phone reads that key and nothing else, so a top-level field alone
        # would fix nobody who has not updated.
        folded = self.fold(with_asset())
        self.assertEqual(folded["metadata"]["poster_url"], ASSET_URL)

    def test_metadata_is_the_fallback_when_there_is_no_asset(self):
        folded = self.fold(row(metadata={"poster_url": METADATA_URL}))
        self.assertEqual(folded["poster_url"], METADATA_URL)
        self.assertEqual(folded["metadata"]["poster_url"], METADATA_URL)

    def test_no_artwork_anywhere_adds_no_keys(self):
        folded = self.fold(row())
        self.assertNotIn("poster_url", folded)
        self.assertNotIn("backdrop_url", folded)
        self.assertEqual(folded["metadata"], {})

    def test_joined_columns_never_reach_the_client(self):
        # They are plumbing. Leaving them in the payload would put storage keys
        # and asset ids on the wire for every container.
        folded = self.fold(with_asset())
        leaked = [
            key
            for key in folded
            if key.startswith(("poster_", "backdrop_")) and key not in {"poster_url", "backdrop_url"}
        ]
        self.assertEqual(leaked, [])

    def test_backdrop_resolves_on_the_same_rule(self):
        folded = self.fold(
            row(
                backdrop_asset_id="b1",
                backdrop_storage_backend="remote",
                backdrop_source_url="https://cdn.example.test/back.jpg",
                metadata={"backdrop_url": "https://images.example.test/other.jpg"},
            )
        )
        self.assertEqual(folded["backdrop_url"], "https://cdn.example.test/back.jpg")
        self.assertEqual(folded["metadata"]["backdrop_url"], "https://cdn.example.test/back.jpg")

    def test_the_stored_row_is_not_mutated(self):
        original = with_asset(metadata={"poster_url": METADATA_URL})
        self.fold(original)
        self.assertEqual(original["metadata"], {"poster_url": METADATA_URL})
        self.assertIn("poster_asset_id", original)


@unittest.skipIf(next_app is None, "Flask/psycopg dependencies are not installed")
class ContainerSyncArtworkSqlTests(unittest.TestCase):
    """Both wire paths resolve, and an older schema still answers."""

    class FakeConn:
        def __init__(self, tables):
            self.tables = tables

        def cursor(self):  # pragma: no cover - never reached in these tests
            raise AssertionError("no query expected")

    def sql(self, tables):
        conn = self.FakeConn(tables)
        original = next_app.table_exists
        next_app.table_exists = lambda _c, name: name in tables
        try:
            return next_app.container_sync_artwork_sql(conn)
        finally:
            next_app.table_exists = original

    def test_resolves_the_containers_own_artwork_only(self):
        columns, joins = self.sql({"entity_media", "media_assets"})
        self.assertIn("em.entity_type='container'", joins)
        self.assertIn("ma.kind='poster'", joins)
        self.assertIn("ma.kind='backdrop'", joins)
        self.assertIn("poster_asset_id", columns)
        # The borrowed member cover must not appear on the wire at all.
        self.assertNotIn("container_movies", joins)
        self.assertNotIn("member_poster", joins)

    def test_hidden_and_deleted_artwork_is_skipped(self):
        _columns, joins = self.sql({"entity_media", "media_assets"})
        self.assertEqual(joins.count("em.deleted_at IS NULL"), 2)
        self.assertEqual(joins.count("em.hidden_at IS NULL"), 2)

    def test_primary_artwork_is_preferred(self):
        _columns, joins = self.sql({"entity_media", "media_assets"})
        self.assertEqual(joins.count("ORDER BY em.is_primary DESC, em.sort_order, ma.created_at"), 2)

    def test_an_older_schema_contributes_nothing(self):
        # The media tables arrived later than containers did; a database without
        # them must still answer a bootstrap rather than fail one.
        self.assertEqual(self.sql(set()), ("", ""))
        self.assertEqual(self.sql({"entity_media"}), ("", ""))

    def test_the_fragments_carry_no_parameters(self):
        # Both callers interpolate these around their own placeholders, so a
        # `%s` here would silently shift every parameter after it.
        columns, joins = self.sql({"entity_media", "media_assets"})
        self.assertNotIn("%s", columns)
        self.assertNotIn("%s", joins)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
