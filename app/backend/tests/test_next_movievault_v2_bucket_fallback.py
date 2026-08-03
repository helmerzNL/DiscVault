"""The MovieVault v2 anonymous bucket fallback.

The locally synced index only carries what this instance has pulled down, so a
disc MovieVault knows about but has not distributed here yet misses entirely.
The bucket lookup is the fallback that resolves it. These tests cover the two
halves of that contract: the plugin asks for it at the right moments, and every
failure degrades to a miss rather than an exception.
"""

import hashlib
import importlib.util
import os
import sys
import unittest
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2

PLUGIN_PATH = os.path.join(
    repo_root, "app", "backend", "next_plugins", "movievault_v2", "plugin.py"
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("movievault_v2_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_ean(prefix="505188814248"):
    check = (
        10
        - sum(
            int(digit) * (3 if index % 2 == 0 else 1)
            for index, digit in enumerate(reversed(prefix))
        )
        % 10
    ) % 10
    return f"{prefix}{check}"


BARCODE = _valid_ean()
RELEASE = {
    "recordType": "release",
    "releaseId": "release-1",
    "canonicalTitle": "Heat",
    "releaseYear": 1995,
    "format": "blu_ray",
}
BOX_SET = {
    "recordType": "box_set",
    "boxSetId": "box-1",
    "title": "The Collection",
    "members": [{"position": 1, "releaseId": "release-1", "canonicalTitle": "Heat"}],
}


class BucketFallbackPluginTests(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_plugin()
        self.calls = []

    def _context(self, *, local=(), bucket=(), resolver=None, **overrides):
        def local_callback(request):
            self.calls.append("local")
            return {"results": list(local)}

        def bucket_callback(request):
            self.calls.append("bucket")
            return {"state": "remote_bucket", "results": list(bucket)}

        context = {
            "settings": {},
            "movievaultV2Lookup": local_callback,
            "movievaultV2BucketLookup": bucket_callback,
            "movievaultV2BucketFallback": True,
        }
        if resolver is not None:
            def resolver_callback(request):
                self.calls.append("resolver")
                return resolver

            context["movievaultV2ReleaseDetails"] = resolver_callback
        context.update(overrides)
        return context

    def test_barcode_miss_falls_through_to_the_bucket(self):
        context = self._context(local=(), bucket=(RELEASE,))
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["title"], "Heat")
        self.assertEqual(self.calls, ["local", "bucket"])

    def test_local_hit_never_reaches_the_bucket(self):
        context = self._context(local=(RELEASE,), bucket=())
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "hit")
        self.assertEqual(self.calls, ["local"])

    def test_bucket_is_asked_with_the_barcode_hash(self):
        seen = {}

        def bucket_callback(request):
            seen.update(request)
            return {"results": []}

        context = self._context(movievaultV2BucketLookup=bucket_callback)
        self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(
            seen, {"hash": hashlib.sha256(BARCODE.encode("ascii")).hexdigest()}
        )

    def test_title_lookup_never_uses_the_bucket(self):
        # Buckets are keyed by the hash of the EAN, so a title query has nothing
        # to look up.
        context = self._context(local=(), bucket=(RELEASE,))
        result = self.plugin.search_title({"title": "Heat"}, context)
        self.assertEqual(result["status"], "miss")
        self.assertEqual(self.calls, ["local"])

    def test_box_set_miss_falls_through_to_the_bucket(self):
        context = self._context(local=(), bucket=(BOX_SET,))
        result = self.plugin.box_set_candidates({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["boxSetProposal"]["boxSetId"], "box-1")
        self.assertIn("bucket", self.calls)

    def test_disabled_fallback_is_not_called(self):
        context = self._context(
            local=(), bucket=(RELEASE,), movievaultV2BucketFallback=False
        )
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "miss")
        self.assertNotIn("bucket", self.calls)

    def test_invalid_barcode_is_not_sent_to_the_bucket(self):
        context = self._context(local=(), bucket=(RELEASE,))
        result = self.plugin.search_barcode({"barcode": "not-a-barcode"}, context)
        self.assertEqual(result["status"], "miss")
        self.assertNotIn("bucket", self.calls)

    def test_a_wrong_check_digit_is_still_sent_to_the_bucket(self):
        # MovieVault assigned this barcode, not DiscVault - a check digit that
        # disagrees with the textbook mod-10 formula is not a reason to refuse
        # even attempting the lookup (DiscVaultApp's iOS hasher never validates
        # it either). Only shape (digits-only, a valid EAN/UPC/GTIN length)
        # gates the bucket call.
        wrong_check_digit = BARCODE[:-1] + str((int(BARCODE[-1]) + 1) % 10)
        context = self._context(local=(), bucket=(RELEASE,))
        result = self.plugin.search_barcode({"barcode": wrong_check_digit}, context)
        self.assertEqual(result["status"], "hit")
        self.assertIn("bucket", self.calls)

    def test_missing_bucket_callback_degrades_to_a_miss(self):
        context = self._context(local=())
        context.pop("movievaultV2BucketLookup")
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "miss")

    def test_raising_bucket_callback_degrades_to_a_miss(self):
        def boom(request):
            raise RuntimeError("bucket exploded")

        context = self._context(local=(), movievaultV2BucketLookup=boom)
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "miss")

    def test_local_hit_reports_local_index_as_match_source(self):
        context = self._context(local=(RELEASE,), bucket=())
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["matchSource"], "local_index")
        self.assertNotIn("bucketFallback", result)

    def test_bucket_hit_reports_match_source_and_diagnostic(self):
        context = self._context(local=(), bucket=(RELEASE,))
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["matchSource"], "bucket_fallback")
        self.assertEqual(
            result["bucketFallback"],
            {"attempted": True, "outcome": "hit", "errorCode": None},
        )

    def test_bucket_miss_reports_diagnostic_without_a_match_source(self):
        context = self._context(local=(), bucket=())
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertNotIn("matchSource", result)
        self.assertEqual(
            result["bucketFallback"],
            {"attempted": True, "outcome": "miss", "errorCode": None},
        )

    def test_bucket_error_reports_diagnostic_with_the_error_code(self):
        def bucket_callback(request):
            return {"state": "unavailable", "results": [], "errorCode": "bucket_unavailable"}

        context = self._context(local=(), movievaultV2BucketLookup=bucket_callback)
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "miss")
        self.assertNotIn("matchSource", result)
        self.assertEqual(
            result["bucketFallback"],
            {"attempted": True, "outcome": "error", "errorCode": "bucket_unavailable"},
        )

    def test_disabled_fallback_reports_not_attempted(self):
        context = self._context(
            local=(), bucket=(RELEASE,), movievaultV2BucketFallback=False
        )
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(
            result["bucketFallback"],
            {"attempted": False, "outcome": None, "errorCode": None},
        )

    def test_box_set_bucket_hit_reports_match_source_and_diagnostic(self):
        context = self._context(local=(), bucket=(BOX_SET,))
        result = self.plugin.box_set_candidates({"barcode": BARCODE}, context)
        self.assertEqual(result["matchSource"], "bucket_fallback")
        self.assertEqual(
            result["bucketFallback"],
            {"attempted": True, "outcome": "hit", "errorCode": None},
        )

    def test_box_set_local_hit_never_reaches_the_bucket_and_reports_local_index(self):
        context = self._context(local=(BOX_SET,), bucket=())
        result = self.plugin.box_set_candidates({"barcode": BARCODE}, context)
        self.assertEqual(result["matchSource"], "local_index")
        self.assertNotIn("bucketFallback", result)

    def test_resolver_canonical_hit_is_used_as_a_last_resort(self):
        # Local index and the anonymous bucket both missed - iOS would ask the
        # resolver here too, so the backend must not give up first.
        context = self._context(
            local=(),
            bucket=(),
            resolver={
                "status": "canonical_hit",
                "film": {"title": "Heat", "year": 1995},
                "release": {"format": "blu_ray"},
            },
        )
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["title"], "Heat")
        self.assertEqual(result["matchSource"], "resolver_fallback")
        self.assertEqual(
            result["resolverFallback"],
            {"attempted": True, "outcome": "hit", "errorCode": None},
        )
        self.assertNotIn("verificationStatus", result)
        self.assertEqual(self.calls, ["local", "bucket", "resolver"])

    def test_resolver_external_hit_is_marked_unreviewed(self):
        context = self._context(
            local=(),
            bucket=(),
            resolver={
                "status": "external_hit",
                "film": {"title": "Heat", "year": 1995},
                "release": {"format": "blu_ray"},
            },
        )
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["matchSource"], "resolver_fallback")
        self.assertEqual(result["verificationStatus"], "unreviewed_external")

    def test_resolver_is_never_asked_after_a_local_hit(self):
        # A poster and audio tracks already present means the *supplementary*
        # enrichment call (pre-existing, unrelated to the new fallback) is
        # skipped too, so this isolates the new primary-fallback behavior.
        complete_release = {**RELEASE, "posterUrl": "https://example/poster.jpg", "audioTracks": [{"languageCode": "en"}]}
        context = self._context(
            local=(complete_release,),
            resolver={"status": "canonical_hit", "film": {"title": "Heat"}, "release": {}},
        )
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["matchSource"], "local_index")
        self.assertNotIn("resolverFallback", result)
        self.assertNotIn("resolver", self.calls)

    def test_resolver_miss_reports_diagnostic(self):
        context = self._context(local=(), bucket=(), resolver={"status": "miss"})
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "miss")
        self.assertNotIn("matchSource", result)
        self.assertEqual(
            result["resolverFallback"],
            {"attempted": True, "outcome": "miss", "errorCode": None},
        )

    def test_resolver_failure_reports_diagnostic_with_the_error_code(self):
        context = self._context(
            local=(),
            bucket=(),
            resolver={"status": "failed", "errorCode": "provider_unavailable"},
        )
        result = self.plugin.search_barcode({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "miss")
        self.assertEqual(
            result["resolverFallback"],
            {"attempted": True, "outcome": "error", "errorCode": "provider_unavailable"},
        )

    def test_box_set_resolver_hit_is_used_as_a_last_resort(self):
        context = self._context(
            local=(),
            bucket=(),
            resolver={
                "status": "external_hit",
                "film": {"title": "Heat"},
                "release": {},
                "boxSet": {
                    "title": "The Collection",
                    "members": [{"position": 1, "title": "Heat"}, {"position": 2, "title": "Heat 2"}],
                },
            },
        )
        result = self.plugin.box_set_candidates({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["matchSource"], "resolver_fallback")
        self.assertEqual(result["verificationStatus"], "unreviewed_external")
        self.assertEqual(result["boxSetProposal"]["title"], "The Collection")
        self.assertEqual(len(result["boxSetProposal"]["members"]), 2)

    def test_box_set_resolver_without_a_box_set_stays_a_miss(self):
        # A resolver hit for a plain release carries no boxSet - box_set_candidates()
        # must not invent one from a release-only response.
        context = self._context(
            local=(),
            bucket=(),
            resolver={"status": "external_hit", "film": {"title": "Heat"}, "release": {}},
        )
        result = self.plugin.box_set_candidates({"barcode": BARCODE}, context)
        self.assertEqual(result["status"], "miss")
        self.assertNotIn("matchSource", result)


class BucketCallbackDegradationTests(unittest.TestCase):
    """The core-side callback must never let a MovieVaultV2Error cross into the
    plugin: a raise there would fail the whole barcode lookup, discarding the
    local result the caller already has."""

    def _bucket_callback(self):
        context = next_movievault_v2.movievault_v2_plugin_context(
            object(),
            "movievault_v2",
            {"settings": {}},
        )
        return context["movievaultV2BucketLookup"]

    def test_lookup_error_becomes_an_empty_result_set(self):
        with patch.object(
            next_movievault_v2,
            "bucket_lookup",
            side_effect=next_movievault_v2.MovieVaultV2Error("bucket_unavailable"),
        ):
            result = self._bucket_callback()({"hash": "0" * 64})
        self.assertEqual(
            result,
            {"state": "unavailable", "results": [], "errorCode": "bucket_unavailable"},
        )

    def test_every_failure_code_degrades(self):
        for code in (
            "bucket_unavailable",
            "bucket_invalid",
            "contract_incompatible",
            "lookup_invalid",
            "manifest_invalid",
        ):
            with self.subTest(code=code):
                with patch.object(
                    next_movievault_v2,
                    "bucket_lookup",
                    side_effect=next_movievault_v2.MovieVaultV2Error(code),
                ):
                    result = self._bucket_callback()({"hash": "0" * 64})
                self.assertEqual(result["results"], [])
                self.assertEqual(result["errorCode"], code)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
