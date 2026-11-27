"""A scanned box set keeps the cover MovieVault supplied, and its page lists its barcode.

Two things a scan of a box set is expected to leave behind, both of which used to
go missing without any error:

* **The cover.** The MovieVault plugin forwards the *box set's* poster to the
  import as `posterUrl`. `import_box_set_proposal` read only `poster_url` and
  `poster`, so the container was created with no cover of its own and its page
  fell back to a member film's poster. A cover is supplementary, so nothing
  failed and nothing said so.
* **The barcode.** The container page's Metadata card listed only aggregate
  counts; the barcode the set was identified by appeared in the header chips and
  nowhere among the facts.

`import_box_set_proposal` is a closure inside the route registration and cannot
be called on its own, so the decisions it makes are the module-level helpers
tested directly below, and what is pinned about the closure itself is that it
goes through them. The container page is one inline script with no JS harness
here, so its test is a source assertion, the idiom the other container-page
tests use.
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


NEXT_APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "next_app.py"))
NEXT_VIEWS_UI_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py"))

MOVIEVAULT_COVER = "https://api.discvault.eu/v2/assets/3f1d2c7e-5a4b-4c3d-9e8f-1a2b3c4d5e6f/display"
LOCAL_COVER = "/api/next/movievault-v2/posters/3f1d2c7e-5a4b-4c3d-9e8f-1a2b3c4d5e6f"


class BoxSetProposalArtworkTests(unittest.TestCase):
    def test_the_plugins_camel_case_poster_is_read(self):
        """The regression: this is the spelling the MovieVault plugin emits."""
        artwork = next_app.box_set_proposal_artwork({"title": "Alien Anthology", "posterUrl": MOVIEVAULT_COVER})
        self.assertEqual(artwork, {"poster_url": MOVIEVAULT_COVER})

    def test_a_discvault_served_cover_is_read_too(self):
        artwork = next_app.box_set_proposal_artwork({"posterUrl": LOCAL_COVER})
        self.assertEqual(artwork, {"poster_url": LOCAL_COVER})

    def test_the_older_spellings_still_work(self):
        self.assertEqual(
            next_app.box_set_proposal_artwork({"poster_url": MOVIEVAULT_COVER}),
            {"poster_url": MOVIEVAULT_COVER},
        )
        self.assertEqual(
            next_app.box_set_proposal_artwork({"poster": MOVIEVAULT_COVER}),
            {"poster_url": MOVIEVAULT_COVER},
        )

    def test_the_containers_own_key_wins_over_the_plugins(self):
        """An edited or client-supplied proposal speaks in `poster_url`."""
        artwork = next_app.box_set_proposal_artwork(
            {"poster_url": "https://example.test/chosen.jpg", "posterUrl": MOVIEVAULT_COVER}
        )
        self.assertEqual(artwork["poster_url"], "https://example.test/chosen.jpg")

    def test_the_backdrop_is_read_under_each_spelling(self):
        for key in ("backdrop_url", "backdropUrl", "backdrop"):
            with self.subTest(key=key):
                artwork = next_app.box_set_proposal_artwork({key: "https://example.test/wide.jpg"})
                self.assertEqual(artwork, {"backdrop_url": "https://example.test/wide.jpg"})

    def test_a_value_a_browser_cannot_load_is_not_stored(self):
        """A relative path from an unknown origin would be kept and never drawn."""
        self.assertEqual(next_app.box_set_proposal_artwork({"posterUrl": "/images/cover.jpg"}), {})
        self.assertEqual(next_app.box_set_proposal_artwork({"posterUrl": "   "}), {})

    def test_an_unusable_first_spelling_does_not_hide_a_usable_second(self):
        artwork = next_app.box_set_proposal_artwork(
            {"poster_url": "/images/cover.jpg", "posterUrl": MOVIEVAULT_COVER}
        )
        self.assertEqual(artwork, {"poster_url": MOVIEVAULT_COVER})

    def test_no_artwork_is_an_empty_answer_not_a_null(self):
        self.assertEqual(next_app.box_set_proposal_artwork({"title": "Alien Anthology"}), {})
        self.assertEqual(next_app.box_set_proposal_artwork(None), {})


class BoxSetImportArtworkUpdatesTests(unittest.TestCase):
    ARTWORK = {"poster_url": MOVIEVAULT_COVER, "backdrop_url": "https://example.test/wide.jpg"}

    def test_a_box_set_with_no_cover_takes_the_supplied_one(self):
        updates = next_app.box_set_import_artwork_updates({}, self.ARTWORK)
        self.assertEqual(updates["poster_url"], MOVIEVAULT_COVER)
        self.assertIs(updates["poster_from_members"], False)
        self.assertEqual(updates["backdrop_url"], "https://example.test/wide.jpg")
        self.assertIs(updates["backdrop_from_members"], False)

    def test_a_cover_borrowed_from_a_member_is_replaced(self):
        """The set's own cover is the more specific answer; a borrowed one is a stand-in."""
        updates = next_app.box_set_import_artwork_updates(
            {"poster_url": "https://example.test/member.jpg", "poster_from_members": True},
            self.ARTWORK,
        )
        self.assertEqual(updates["poster_url"], MOVIEVAULT_COVER)
        self.assertIs(updates["poster_from_members"], False)

    def test_a_cover_the_container_already_owns_is_kept(self):
        updates = next_app.box_set_import_artwork_updates(
            {"poster_url": "https://example.test/mine.jpg", "poster_from_members": False},
            self.ARTWORK,
        )
        self.assertNotIn("poster_url", updates)
        self.assertNotIn("poster_from_members", updates)

    def test_a_locked_cover_is_never_replaced_even_when_empty(self):
        updates = next_app.box_set_import_artwork_updates({"poster_locked": True}, self.ARTWORK)
        self.assertNotIn("poster_url", updates)
        # The lock is per kind: the backdrop is still free.
        self.assertEqual(updates["backdrop_url"], "https://example.test/wide.jpg")

    def test_a_proposal_without_a_cover_writes_nothing(self):
        """Merging `{"poster_url": None}` is a JSON null, which blanked a stored poster."""
        self.assertEqual(
            next_app.box_set_import_artwork_updates({"poster_url": "https://example.test/mine.jpg"}, {}),
            {},
        )

    def test_unreadable_stored_metadata_is_treated_as_empty(self):
        updates = next_app.box_set_import_artwork_updates(None, self.ARTWORK)
        self.assertEqual(updates["poster_url"], MOVIEVAULT_COVER)


class ImportBoxSetProposalUsesTheHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_APP_PATH, encoding="utf-8") as handle:
            source = handle.read()
        match = re.search(
            r"    def import_box_set_proposal\(.*?(?=\n    def metadata_import_candidate\()",
            source,
            re.S,
        )
        assert match, "import_box_set_proposal is missing from next_app.py"
        cls.importer = match.group(0)

    def test_the_cover_is_read_through_the_shared_reader(self):
        self.assertIn("box_set_proposal_artwork(proposal)", self.importer)

    def test_the_raw_two_key_read_that_missed_posterurl_is_gone(self):
        self.assertNotIn('proposal.get("poster_url") or proposal.get("poster")', self.importer)

    def test_an_existing_box_set_is_updated_through_the_ownership_rule(self):
        self.assertIn("box_set_import_artwork_updates(", self.importer)

    def test_the_existing_box_sets_metadata_is_read_to_apply_that_rule(self):
        """The barcode lookup selected only `id`, so the rule had nothing to judge."""
        self.assertIn("SELECT id, metadata FROM containers WHERE container_type='box_set' AND barcode=%s", self.importer)

    def test_the_merged_payload_never_carries_a_null_cover(self):
        """The shared payload must not hold the cover keys at all: a None there is
        merged over the stored metadata as a JSON null."""
        payload = re.search(r"container_metadata = \{(.*?)\n        \}", self.importer, re.S)
        self.assertTrue(payload, "container_metadata payload not found")
        self.assertNotIn('"poster_url"', payload.group(1))
        self.assertNotIn('"backdrop_url"', payload.group(1))


class ContainerPageListsTheBarcodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        match = re.search(
            r'document\.getElementById\("containerDetailMetadata"\)\.innerHTML = detailFieldRows\(\[(.*?)\]\);',
            source,
            re.S,
        )
        assert match, "the container Metadata card is missing from the inline script"
        cls.rows = match.group(1)

    def test_the_metadata_card_has_a_barcode_row_from_the_container(self):
        self.assertIn('tNext("containerDetail.fieldBarcode", "Barcode"), container.barcode', self.rows)

    def test_the_barcode_is_the_first_fact(self):
        first_row = next(line for line in self.rows.splitlines() if line.strip().startswith("["))
        self.assertIn("containerDetail.fieldBarcode", first_row)

    def test_the_aggregate_rows_are_still_there(self):
        for key in ("aggregateMovieCount", "aggregateYearRange", "aggregateFormats", "aggregateArtwork", "aggregateVideos"):
            with self.subTest(key=key):
                self.assertIn(f"containerDetail.{key}", self.rows)


if __name__ == "__main__":
    unittest.main()
