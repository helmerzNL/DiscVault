"""An imported box-set member has to keep the release it came from.

Adding the Back to the Future box set created its three members and queued a
full metadata refresh for each. The refresh reported success and applied
nothing, on every member, every time -- and kept doing so until the TMDB id was
typed in by hand, after which the same refresh filled everything in.

The reason is that a member arrives identified only by the MovieVault release
the box-set proposal names. It has no year (the box-set feed states none for its
members), and the import mints it a synthetic barcode -- `IMPORT-...-BOX-01` --
which no catalog will ever match. So the release id is the whole identity, and
the import dropped it: `box_set_member_identifiers` mapped `tmdbId`/`imdbId` and
nothing else. The member landed with no identifier at all, and
`movievault_identification_plan` plans `movie_details` -- the only call that
resolves a member release directly -- from the stored `movieVaultId` it did not
have.

These tests pin the identity the import must carry, not the refresh's outcome.
"""

from __future__ import annotations

import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_app
from app.backend.next_metadata import movievault_identification_plan, query_from_payload


MEMBER = {
    "position": 1,
    "releaseId": "10000000-0000-0000-0000-000000000001",
    "filmId": "20000000-0000-0000-0000-000000000001",
    "title": "Back to the Future",
    "format": "4K UHD",
}


class MemberIdentifierTests(unittest.TestCase):
    def test_the_movievault_release_id_is_carried(self):
        self.assertEqual(
            next_app.box_set_member_identifiers(MEMBER)["movievault_v2"],
            "10000000-0000-0000-0000-000000000001",
        )

    def test_the_release_id_is_recognised_under_every_spelling(self):
        """One fact, three names: the plugin proposal says `releaseId`, a query
        round-trip says `movieVaultId`, a hand-built import body says
        `movievault_id`."""
        for key in ("releaseId", "release_id", "movieVaultId", "movievaultId", "movievault_id"):
            with self.subTest(key=key):
                member = {"title": "Back to the Future", key: "10000000-0000-0000-0000-000000000001"}
                self.assertEqual(
                    next_app.box_set_member_identifiers(member)["movievault_v2"],
                    "10000000-0000-0000-0000-000000000001",
                )

    def test_tmdb_and_imdb_still_travel(self):
        identifiers = next_app.box_set_member_identifiers({**MEMBER, "tmdbId": "105", "imdbId": "tt0088763"})
        self.assertEqual(identifiers["tmdb"], "105")
        self.assertEqual(identifiers["imdb"], "tt0088763")

    def test_a_member_with_no_ids_claims_none(self):
        """A resolver-supplied box set names no release ids at all. Nothing is
        invented for it -- an empty identifier would be a false link."""
        self.assertEqual(next_app.box_set_member_identifiers({"title": "Back to the Future"}), {})


class MemberRefreshReachabilityTests(unittest.TestCase):
    """What the carried id is *for*: it is the only thing that lets the refresh
    queued right after the import reach MovieVault at all."""

    PLUGIN = {"capabilities": ["search_barcode", "search_title", "movie_details", "box_set_candidates"]}

    def _entrypoints(self, query):
        return [item["entrypoint"] for item in movievault_identification_plan(self.PLUGIN, query)]

    def test_a_member_that_kept_its_release_id_is_resolved_directly(self):
        query = query_from_payload({
            "title": "Back to the Future",
            "barcode": "IMPORT-BACK-TO-THE-FUTURE-BOX-01",
            "movieVaultId": "10000000-0000-0000-0000-000000000001",
            "memberOfBoxSet": True,
        })
        plan = movievault_identification_plan(self.PLUGIN, query)
        self.assertIn("movie_details", [item["entrypoint"] for item in plan])
        details = next(item for item in plan if item["entrypoint"] == "movie_details")
        self.assertEqual(details["payload"]["releaseId"], "10000000-0000-0000-0000-000000000001")

    def test_a_member_that_lost_it_has_only_its_title_left(self):
        """The state this fix removes. The synthetic barcode is not an EAN, so it
        never becomes a lookup, and the member has no year to narrow a title
        search with -- which is why the refresh found nothing and said so
        nowhere."""
        query = query_from_payload({
            "title": "Back to the Future",
            "barcode": "IMPORT-BACK-TO-THE-FUTURE-BOX-01",
            "memberOfBoxSet": True,
        })
        self.assertEqual(query["externalBarcode"], "")
        self.assertEqual(self._entrypoints(query), ["search_title", "box_set_candidates"])


if __name__ == "__main__":
    unittest.main()
