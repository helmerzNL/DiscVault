"""The `candidates` answer MovieVault actually sends, pinned verbatim.

On 2026-08-09 MovieVault resolved barcode `5050583001395` (*Catch Me If You
Can*, French pressing) correctly and answered HTTP 200 with a valid
`status: "candidates"` payload. DiscVault discarded the whole thing and audited
it as `errorCode: release_details_response_invalid`, `failureKind: "server"`,
`candidateCount: 0` — a MovieVault failure, for a lookup MovieVault got right.

The cause was one key. `_release_details_release_summary` named every field it
knew and `finishes` was not among them, and the reader validated with a closed
key set, so a purely additive producer field failed the entire response. The
identical failure had happened five days earlier with `subtitles`, and the
remedy recorded then — ship the consumer before the producer — is an ordering
convention that nothing enforces.

So this file pins two things that are easy to confuse:

- the **payload**, exactly as the route serialises it (`by_alias`,
  `exclude_none`), so a fixture drifting from the wire cannot hide the next
  break the way the old one did;
- the **posture** — an unknown key is ignored, not fatal — which is what makes
  the ordering convention unnecessary rather than merely better observed.

App-Guidance `docs/apps/discvault/movievault-route-parity.md` §4 and
`docs/apps/discvault/pwa-barcode-fallback-candidates.md`.
"""

import copy
import os
import sys
import unittest


repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2 as mv2


# Verbatim from the MovieVault handover, §4 "Exacte payload". Do not tidy it:
# the empty `barcodes`, the empty `finishes`, the missing `moderation` and the
# missing `release` are each a rule this reader has to honour, and the value of
# the fixture is that it is not an idealised version of the wire.
HANDOVER_PAYLOAD = {
    "contractVersion": "release-technical-1",
    "status": "candidates",
    "verificationStatus": "unreviewed_external",
    "film": {
        "title": "Catch Me If You Can",
        "year": 2002,
        "identifiers": {
            "tmdbMovieId": "640",
            "imdbId": "tt0264464",
        },
        "links": {
            "tmdb": "https://www.themoviedb.org/movie/640",
            "imdb": "https://www.imdb.com/title/tt0264464/",
        },
    },
    "releases": [
        {
            "releaseRef": "bluray_com:24481",
            "source": "external",
            "title": "Arrête-moi si tu peux Blu-ray (Catch Me If You Can) (France)",
            "format": "Blu-ray",
            "discRegions": ["B"],
            "discCount": 1,
            "barcodes": [],
            "packaging": ["keep_case"],
            "finishes": [],
            "video": {
                "resolution": "1080p",
                "codecs": ["h264"],
                "hdrFormats": [],
                "aspectRatios": ["1.85:1"],
            },
            "audioTracks": [
                {"languageCode": "en", "codec": "dts_hd_ma", "channels": "5.1"},
                {"languageCode": "fr", "codec": "dolby_digital", "channels": "5.1"},
            ],
            "subtitles": [
                {"languageCode": "fr", "subtitleType": "full"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
            "subtitleLanguages": ["fr", "nl"],
        },
        {
            "releaseRef": "bluray_com:31703",
            "source": "external",
            "title": "Catch Me If You Can Blu-ray",
            "format": "Blu-ray",
            "discRegions": ["A"],
            "discCount": 2,
            "barcodes": [],
            "packaging": ["steelbook"],
            "finishes": [],
            "video": {
                "resolution": "1080p",
                "codecs": ["h264"],
                "hdrFormats": [],
                "aspectRatios": ["1.85:1"],
            },
            "audioTracks": [
                {"languageCode": "en", "codec": "dts_hd_ma", "channels": "5.1"}
            ],
            "subtitles": [{"languageCode": "en", "subtitleType": "sdh"}],
            "subtitleLanguages": ["en"],
        },
    ],
}


def payload(**overrides):
    result = copy.deepcopy(HANDOVER_PAYLOAD)
    result.update(overrides)
    return result


class ReleaseDetailsCandidatesFinishesTests(unittest.TestCase):
    def test_the_reported_5050583001395_answer_validates(self):
        result = mv2.validate_release_details_response(payload())

        self.assertEqual(result["status"], "candidates")
        self.assertEqual(result["verificationStatus"], "unreviewed_external")
        self.assertEqual(result["film"]["title"], "Catch Me If You Can")
        self.assertEqual(len(result["releases"]), 2)

    def test_every_distinguishing_field_survives_the_round_trip(self):
        """The picker's whole job is showing what differs between pressings."""
        first, second = mv2.validate_release_details_response(payload())["releases"]

        self.assertEqual(first["releaseRef"], "bluray_com:24481")
        self.assertEqual(first["discRegions"], ["B"])
        self.assertEqual(first["discCount"], 1)
        self.assertEqual(first["packaging"], ["keep_case"])
        self.assertEqual(first["finishes"], [])
        self.assertEqual(first["video"]["resolution"], "1080p")
        self.assertEqual(
            first["audioTracks"],
            [
                {"languageCode": "en", "codec": "dts_hd_ma", "channels": "5.1"},
                {"languageCode": "fr", "codec": "dolby_digital", "channels": "5.1"},
            ],
        )
        self.assertEqual(second["discRegions"], ["A"])
        self.assertEqual(second["discCount"], 2)
        self.assertEqual(second["packaging"], ["steelbook"])

    def test_the_release_ref_is_opaque_and_not_a_uuid(self):
        """`bluray_com:24481` is a provider reference, echoed back untouched."""
        result = mv2.validate_release_details_response(payload())

        self.assertEqual(
            [item["releaseRef"] for item in result["releases"]],
            ["bluray_com:24481", "bluray_com:31703"],
        )

    def test_a_candidate_with_no_barcodes_is_accepted(self):
        """`barcodes: []` on an external candidate is normal.

        None of these pressings was confirmed by the scanned barcode - that is
        precisely why there is a choice to make - so an empty list is the honest
        answer, not a defect.
        """
        result = mv2.validate_release_details_response(payload())

        self.assertEqual([item["barcodes"] for item in result["releases"]], [[], []])

    def test_unreviewed_external_needs_no_moderation_on_candidates(self):
        """`moderation` marks something confirmed and queued. Nothing was.

        On `external_hit` the same `verificationStatus` does require it, so the
        coupling is to the status, not to the verification value.
        """
        self.assertNotIn("moderation", HANDOVER_PAYLOAD)

        result = mv2.validate_release_details_response(payload())

        self.assertNotIn("moderation", result)
        self.assertEqual(result["verificationStatus"], "unreviewed_external")

    def test_candidates_carry_no_single_release(self):
        result = mv2.validate_release_details_response(payload())

        self.assertNotIn("release", result)

    def test_structured_subtitles_replace_the_flat_language_view(self):
        first = mv2.validate_release_details_response(payload())["releases"][0]

        self.assertEqual(
            first["subtitles"],
            [
                {"languageCode": "fr", "subtitleType": "full"},
                {"languageCode": "nl", "subtitleType": "full"},
            ],
        )
        self.assertNotIn("subtitleLanguages", first)

    def test_an_unknown_future_key_does_not_fail_the_response(self):
        """The point of the fix: the next additive field is a no-op here.

        `finishes` is now named, so it would no longer break anything. What has
        to keep holding is the case where the field is one nobody has written
        down yet.
        """
        broken = payload()
        broken["releases"][0]["surfaceTreatment"] = ["mirror"]
        broken["releases"][1]["video"]["frameRate"] = "24p"
        broken["provenance"] = {"chain": ["barcode_hub", "wikidata", "bluray_com"]}

        result = mv2.validate_release_details_response(broken)

        self.assertEqual(len(result["releases"]), 2)
        self.assertNotIn("surfaceTreatment", result["releases"][0])
        self.assertNotIn("frameRate", result["releases"][1]["video"])
        self.assertNotIn("provenance", result)

    def test_a_finish_this_repo_has_not_heard_of_is_kept(self):
        """Vocabulary is open; only the shape is refused.

        The picker renders an unrecognized value raw rather than hiding a fact
        that distinguishes two otherwise identical rows.
        """
        with_finish = payload()
        with_finish["releases"][0]["finishes"] = ["holofoil", "mirror_foil"]

        result = mv2.validate_release_details_response(with_finish)

        self.assertEqual(result["releases"][0]["finishes"], ["holofoil", "mirror_foil"])


if __name__ == "__main__":  # pragma: no cover - convenience for local runs
    unittest.main()
